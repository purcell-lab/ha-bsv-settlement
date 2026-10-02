import qrcode from "qrcode-generator";

/**
 * Local-only receiving QR. No network requests, wallet actions or private keys.
 * Bundle with qrcode-generator 2.0.4 before registering as an HA module resource.
 */
class BsvReceiveQrCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
  }

  setConfig(config) {
    if (!config.entity) throw new Error("A wallet status entity is required");
    this._config = { ...config };
    this._last = undefined;
    this.render();
  }

  set hass(hass) {
    this._hass = hass;
    this.render();
  }

  getCardSize() { return 7; }
  getGridOptions() { return { columns: 12, rows: "auto", min_columns: 6 }; }

  render() {
    if (!this._config) return;
    const entity = this._hass?.states?.[this._config.entity];
    const address = entity?.attributes?.receive_address;
    let reason = "";
    if (!entity || ["unknown", "unavailable"].includes(entity.state)) {
      reason = "Wallet unavailable. Receiving QR withheld.";
    } else if (entity.attributes.network !== "mainnet") {
      reason = "Network mismatch. Receiving QR withheld.";
    } else if (typeof address !== "string" || !/^1[1-9A-HJ-NP-Za-km-z]{25,34}$/.test(address)) {
      reason = "A valid mainnet receiving address is not available.";
    }
    const key = JSON.stringify([reason, reason ? null : address]);
    if (key === this._last) return;
    this._last = key;
    this.shadowRoot.innerHTML = `
      <style>
        :host { display:block; height:100%; }
        ha-card { display:block; box-sizing:border-box; height:100%; padding:24px;
          color:var(--primary-text-color,#202124); }
        h2 { margin:0 0 6px; font-size:20px; line-height:1.3; font-weight:500; }
        .network { color:var(--secondary-text-color,#606368); font-size:14px; margin:0 0 20px; }
        .qr { margin:0 auto 18px; max-width:260px; background:white; line-height:0; }
        svg { width:100%; height:auto; display:block; }
        code { display:block; overflow-wrap:anywhere; text-align:center; font-size:14px;
          line-height:1.6; user-select:all; }
        .note,.warning { font-size:13px; line-height:1.5; color:var(--secondary-text-color,#606368);
          margin:16px 0 0; }
        .warning { color:var(--error-color,#b3261e); }
      </style>
      <ha-card>
        <h2>Receive BSV</h2>
        <p class="network">BSV mainnet only</p>
        <div class="body"></div>
      </ha-card>`;
    const body = this.shadowRoot.querySelector(".body");
    if (reason) {
      const warning = document.createElement("p");
      warning.className = "warning";
      warning.setAttribute("role", "status");
      warning.textContent = reason;
      body.append(warning);
      return;
    }
    const qr = qrcode(0, "M");
    qr.addData(address, "Byte");
    qr.make();
    const holder = document.createElement("div");
    holder.className = "qr";
    // Four-module quiet zone, included within the SVG's white background.
    holder.innerHTML = qr.createSvgTag({ cellSize: 4, margin: 16, scalable: true });
    const svg = holder.querySelector("svg");
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", "QR code for BSV mainnet receiving address " + address);
    const text = document.createElement("code");
    text.textContent = address;
    const note = document.createElement("p");
    note.className = "note";
    note.textContent = "Scan with a BSV wallet and check the address before sending. The QR contains only this address, with no amount or payment authorisation. Do not send BTC or testnet coins.";
    body.append(holder, text, note);
  }
}

if (!customElements.get("bsv-receive-qr-card")) {
  customElements.define("bsv-receive-qr-card", BsvReceiveQrCard);
}
window.customCards = window.customCards || [];
if (!window.customCards.some((card) => card.type === "bsv-receive-qr-card")) {
  window.customCards.push({
    type: "bsv-receive-qr-card",
    name: "BSV receiving QR",
    description: "Local-only QR for a live mainnet operator wallet receiving address.",
    preview: false,
  });
}
