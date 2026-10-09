// Test-only fictional keys: read a Python-built MessageBox payment with the official SDK,
// exactly as a PeerPay-compatible driver wallet would before internalizeAction.
import {PrivateKey,ProtoWallet,Transaction,P2PKH,PublicKey} from "@bsv/sdk";
let input="";
for await(const chunk of process.stdin)input+=chunk;
const {driver_secret,operator_secret,request,signature}=JSON.parse(input);
const driver=new ProtoWallet(new PrivateKey(driver_secret,"hex"));
const operator=new ProtoWallet(new PrivateKey(operator_secret,"hex"));
const operatorKey=(await operator.getPublicKey({identityKey:true})).publicKey;
const driverKey=(await driver.getPublicKey({identityKey:true})).publicKey;
const m=request.message;
const {plaintext}=await driver.decrypt({protocolID:[1,"messagebox"],keyID:"1",counterparty:operatorKey,
  ciphertext:Array.from(Buffer.from(JSON.parse(m.body).encryptedMessage,"base64"))});
const body=Buffer.from(plaintext).toString("utf8"),token=JSON.parse(body);
const tx=Transaction.fromAtomicBEEF(token.transaction);
const {publicKey}=await driver.getPublicKey({protocolID:[2,"3241645161d8"],forSelf:true,counterparty:operatorKey,
  keyID:`${token.customInstructions.derivationPrefix} ${token.customInstructions.derivationSuffix}`});
const out=tx.outputs[token.outputIndex];
const {hmac}=await operator.createHmac({data:Array.from(Buffer.from(body,"utf8")),protocolID:[1,"messagebox"],keyID:"1",counterparty:driverKey});
const {valid}=await driver.verifySignature({protocolID:[2,"auth message signature"],keyID:"x y",counterparty:operatorKey,
  data:Array.from(Buffer.from("signed")),signature:Array.from(Buffer.from(signature,"hex"))}).catch(()=>({valid:false}));
console.log(JSON.stringify({txid:tx.id("hex"),amount:token.amount,
  output_matches:out.satoshis===token.amount&&out.lockingScript.toHex()===new P2PKH().lock(PublicKey.fromString(publicKey).toAddress()).toHex(),
  merkle_root_matches:tx.merklePath.computeRoot(tx.id("hex"))===tx.merklePath.computeRoot(),
  message_id_matches:Buffer.from(hmac).toString("hex")===m.messageId,signature_valid:valid}));
