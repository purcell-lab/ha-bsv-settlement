// Offline S2 fixture, not included in the driver bundle or connected to a wallet.
const fs = require("node:fs");
const { PrivateKey, ProtoWallet } = require("@bsv/sdk");
(async () => {
  const { payload, authority_id, secret } = JSON.parse(fs.readFileSync(0, "utf8"));
  const wallet = new ProtoWallet(PrivateKey.fromString(secret, "hex"));
  const { signature } = await wallet.createSignature({
    data: Array.from(Buffer.from(payload, "utf8")),
    protocolID: [2, "ev monthly spending"],
    keyID: authority_id,
    counterparty: "anyone",
  });
  process.stdout.write(JSON.stringify({ payload, signature: Buffer.from(signature).toString("hex") }));
})().catch(e => { console.error(e.message); process.exit(1); });
