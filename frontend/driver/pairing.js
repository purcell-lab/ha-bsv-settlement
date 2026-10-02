import { PrivateKey, ProtoWallet, Utils } from "@bsv/sdk";

export const pairingProtocol = [2, "ev wallet relay"];
export const requiredMethods = ["getPublicKey", "getNetwork", "createSignature",
  "createAction", "signAction", "internalizeAction"];
const bytes = s => Array.from(new TextEncoder().encode(s));
const keyPattern = /^(02|03)[0-9a-f]{64}$/;
const MAX_WIRE = 1_000_000;
const to64 = a => Utils.toBase64(a).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
const from64 = s => Utils.toArray(s.replaceAll("-", "+").replaceAll("_", "/"), "base64");

export function signatureMessage(s) {
  return [s.topic, s.backendIdentityKey, s.origin, s.expiry].join("|");
}

export async function pairingUri(wallet, session) {
  const { signature } = await wallet.createSignature({
    protocolID: [0, "qr pairing"], keyID: session.topic, counterparty: "anyone",
    data: bytes(signatureMessage(session)),
  });
  const params = new URLSearchParams({
    topic: session.topic, backendIdentityKey: session.backendIdentityKey,
    protocolID: JSON.stringify(pairingProtocol), origin: session.origin,
    expiry: session.expiry, sig: to64(signature),
  });
  return `bsv-wallet://pair?${params}`;
}

export function decodeEnvelope(raw, topic) {
  if (typeof raw !== "string" || !raw.length || raw.length > MAX_WIRE)
    throw Error("Wallet message exceeds the pairing transport limit.");
  const e = JSON.parse(raw);
  if (!e || Array.isArray(e) || Object.keys(e).some(k => !["topic", "ciphertext", "mobileIdentityKey"].includes(k)) ||
      e.topic !== topic || typeof e.ciphertext !== "string" ||
      !/^[A-Za-z0-9_-]+$/.test(e.ciphertext) || e.ciphertext.length % 4 === 1 ||
      (e.mobileIdentityKey !== undefined && !keyPattern.test(e.mobileIdentityKey)))
    throw Error("Invalid wallet pairing envelope.");
  return e;
}

/** All authority stays in the existing consent/quote/one-use permit flow.
 * Secrets and RPC state are memory-only. No automatic resend or reconnect.
 */
export class BrowserPairing {
  constructor({ api, origin, WebSocketClass = WebSocket, onState = () => {},
    rpcTimeout = 60000, cryptoWallet = new ProtoWallet(PrivateKey.fromRandom()) }) {
    Object.assign(this, { api, origin, WebSocketClass, onState, rpcTimeout, cryptoWallet });
    this.state = "idle"; this.pending = new Map(); this.seq = 0;
    this.lastReply = 0; this.chain = Promise.resolve(); this.generation = 0;
    this.wallet = Object.fromEntries(requiredMethods.map(method => [method, params => this.request(method, params || {})]));
  }
  stateChanged(state, detail = "") {
    this.state = state; this.onState(state, detail);
  }
  async start() {
    const generation = ++this.generation;
    const { publicKey } = await this.cryptoWallet.getPublicKey({ identityKey: true });
    this.stateChanged("creating");
    let s;
    try {
      s = await this.api("pairing_create", { backend_identity: publicKey });
      if (generation !== this.generation) {
        await this.api("pairing_cancel", { topic: s.topic }); return;
      }
      if (!s || s.origin !== this.origin || !/^https:\/\//.test(s.origin) ||
          s.backendIdentityKey !== publicKey || !/^[A-Za-z0-9_-]{16,128}$/.test(s.topic) ||
          !/^[A-Za-z0-9_-]{43}$/.test(s.desktop_token) ||
          !/^[1-9][0-9]{0,10}$/.test(s.expiry) || Number(s.expiry) * 1000 <= Date.now() ||
          Number(s.expiry) * 1000 > Date.now() + 180000 ||
          s.relay !== s.origin.replace("https:", "wss:"))
        throw Error("Pairing server returned invalid connection details.");
      this.session = s;
      this.uri = await pairingUri(this.cryptoWallet, s);
      if (generation !== this.generation) return;
      const ws = this.socket = new this.WebSocketClass(
        `${s.relay}/ws?topic=${s.topic}&role=desktop`,
        ["bsv-wallet-relay", `bsv-wallet-relay-token.${s.desktop_token}`]);
      ws.onopen = () => {
        if (generation !== this.generation) return;
        this.stateChanged("scanning");
        this.timer = setTimeout(() => this.fail("Pairing QR expired. Create a new code."),
          Math.max(1, Number(s.expiry) * 1000 - Date.now()));
      };
      ws.onmessage = event => {
        // Serialize decrypt, replay check and RPC resolution.
        this.chain = this.chain.then(() => generation === this.generation && this.receive(event.data))
          .catch(() => this.fail("Invalid wallet response. Pairing ended; no payment will be retried."));
      };
      ws.onclose = () => {
        if (generation === this.generation) this.fail("Wallet disconnected. Reconnect explicitly; do not repeat a pending payment.");
      };
      ws.onerror = () => this.fail("Wallet connection failed. Use the driver-link fallback.");
    } catch (e) {
      this.fail(e.message);
      if (s?.topic) void this.api("pairing_cancel", { topic: s.topic }).catch(() => {});
      throw e;
    }
  }
  async encrypt(payload) {
    const { ciphertext } = await this.cryptoWallet.encrypt({
      protocolID: pairingProtocol, keyID: this.session.topic, counterparty: this.mobileIdentity,
      plaintext: bytes(JSON.stringify(payload)),
    });
    return JSON.stringify({ topic: this.session.topic, ciphertext: to64(ciphertext) });
  }
  async receive(raw) {
    const generation = this.generation;
    const e = decodeEnvelope(raw, this.session.topic);
    const identity = this.mobileIdentity || e.mobileIdentityKey;
    if (!identity || (e.mobileIdentityKey && e.mobileIdentityKey !== identity))
      throw Error("Wallet identity changed.");
    const { plaintext } = await this.cryptoWallet.decrypt({
      protocolID: pairingProtocol, keyID: this.session.topic,
      counterparty: identity, ciphertext: from64(e.ciphertext),
    });
    if (generation !== this.generation) return;
    const msg = JSON.parse(new TextDecoder().decode(new Uint8Array(plaintext)));
    if (!msg || !Number.isSafeInteger(msg.seq) || msg.seq <= this.lastReply)
      throw Error("Replayed wallet response.");
    if (!this.mobileIdentity) {
      if (msg.method !== "pairing_approved" || msg.params?.mobileIdentityKey !== identity ||
          msg.params.protocolID !== JSON.stringify(pairingProtocol) ||
          !Array.isArray(msg.params.permissions) || this.state !== "scanning")
        throw Error("Invalid pairing approval.");
      this.mobileIdentity = identity; this.lastReply = msg.seq;
      this.seq = msg.seq + 1;
      const ack = await this.encrypt({ id: crypto.randomUUID(), seq: this.seq, method: "pairing_ack", params: {} });
      if (generation !== this.generation) return;
      this.socket.send(ack);
      clearTimeout(this.timer);
      this.timer = setTimeout(() => this.fail("Connection expired. Pair again before further wallet actions."), 1800000);
      const missing = requiredMethods.filter(m => !msg.params.permissions.includes(m));
      if (missing.length) {
        this.stateChanged("incompatible",
          `Wallet paired, but this version does not expose ${missing.join(", ")}. Open the driver link inside BSV Browser instead. No spending approval was signed.`);
      } else {
        this.stateChanged("paired", "Wallet paired. Check mainnet before approving this session.");
      }
      return;
    }
    const pending = this.pending.get(msg.id);
    if (!pending || msg.seq !== pending.seq || msg.method ||
        (Object.hasOwn(msg, "result") === Object.hasOwn(msg, "error")))
      throw Error("Unexpected wallet response.");
    this.lastReply = msg.seq; this.pending.delete(msg.id); clearTimeout(pending.timer);
    if (msg.error) pending.reject(Error("Wallet rejected or could not complete the request. No automatic retry."));
    else pending.resolve(msg.result);
  }
  async request(method, params = {}) {
    if (!requiredMethods.includes(method) || this.state !== "paired" || this.pending.size)
      throw Error("Wallet is not ready, or another wallet request is still pending.");
    const generation = this.generation, id = crypto.randomUUID(), seq = ++this.seq;
    let resolve, reject;
    const result = new Promise((ok, no) => { resolve = ok; reject = no; });
    // Encryption is asynchronous; observe a timeout even before request()
    // hands the promise to its caller.
    result.catch(() => {});
    // Reserve before encryption to prevent concurrent signing requests.
    const pending = { seq, resolve, reject };
    this.pending.set(id, pending);
    pending.timer = setTimeout(() => this.fail(
      "Wallet response timed out. The outcome may be unknown; reconcile before any new payment."), this.rpcTimeout);
    try {
      const wire = await this.encrypt({ id, seq, method, params });
      if (generation !== this.generation || this.state !== "paired")
        throw Error("Connection ended before the wallet request.");
      if (wire.length > MAX_WIRE) throw Error("Wallet request exceeds the pairing transport limit.");
      this.socket.send(wire);
    } catch (e) { this.fail(e.message); }
    return result;
  }
  async verifiedWallet(expectedIdentity = null) {
    const { publicKey } = await this.wallet.getPublicKey({ identityKey: true });
    if (publicKey !== this.mobileIdentity || (expectedIdentity && publicKey !== expectedIdentity))
      throw Error("Connect the wallet registered for this session.");
    if ((await this.wallet.getNetwork()).network !== "mainnet")
      throw Error("This charging session requires a verified mainnet wallet.");
    return { wallet: this.wallet, identity: publicKey };
  }
  fail(message) {
    ++this.generation; clearTimeout(this.timer);
    const ws = this.socket; this.socket = null;
    if (ws) { ws.onclose = null; ws.onerror = null; ws.onmessage = null; ws.close(); }
    for (const p of this.pending.values()) { clearTimeout(p.timer); p.reject(Error(message)); }
    this.pending.clear(); this.uri = null;
    this.stateChanged("disconnected", message);
  }
  async disconnect() {
    this.fail("Disconnected. Saved session approval is unchanged; this does not revoke it.");
    if (this.session) await this.api("pairing_cancel", { topic: this.session.topic }).catch(() => {});
    this.session = null; this.cryptoWallet = null;
  }
}
