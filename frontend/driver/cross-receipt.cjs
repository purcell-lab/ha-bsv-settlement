// Offline fixture only: Python-generated fictional key, no wallet or network.
const fs=require("node:fs");
const {PrivateKey,ProtoWallet}=require("@bsv/sdk");
(async()=>{
  const {bytes,hex}=await import("./model.js");
  const {receiptProtocol}=await import("./credit.js");
  const {fictional_key,budget_id,payload}=JSON.parse(fs.readFileSync(0,"utf8"));
  const wallet=new ProtoWallet(PrivateKey.fromHex(fictional_key));
  const {signature}=await wallet.createSignature({
    protocolID:receiptProtocol,keyID:budget_id,counterparty:"anyone",data:bytes(payload),
  });
  process.stdout.write(JSON.stringify({acknowledgement:{payload,signature:hex(signature)}}));
})().catch(e=>{console.error(e.message);process.exit(1);});
