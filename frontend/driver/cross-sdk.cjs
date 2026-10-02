// Test helper: reads a fictional Python-SDK invitation and returns TS-SDK consent.
// Never connects to a wallet or a chain provider.
const fs = require("node:fs");
const { PrivateKey, ProtoWallet } = require("@bsv/sdk");
(async () => {
  const {parseInvitation, signConsent} = await import("./model.js");
  const checked = parseInvitation(fs.readFileSync(0, "utf8"));
  const wallet = new ProtoWallet(PrivateKey.fromRandom());
  const identity = (await wallet.getPublicKey({identityKey:true})).publicKey;
  process.stdout.write(JSON.stringify(await signConsent(wallet, checked, identity)));
})().catch(e => { console.error(e.message); process.exit(1); });
