import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { PrivateKey, ProtoWallet, PublicKey, Transaction, P2PKH } from "@bsv/sdk";
import { registerCredit, creditTransaction, importCredit, creditDescription } from "./credit.js";
import { canonical } from "./model.js";

const operator=PrivateKey.fromRandom(), driver=PrivateKey.fromRandom();
const remittance={protocolID:[2,"3241645161d8"],derivationPrefix:"YWJj",derivationSuffix:"ZGVm"};
const wallet=new ProtoWallet(driver);
wallet.getNetwork=async()=>({network:"mainnet"});
const terms={budget_id:"fictional-budget",operator_identity:operator.toPublicKey().toString(),credit_receiving:remittance};
const checked={terms,invitation:{payload:canonical(terms)}};
async function fixture(amount=189,fee=10){
  const {publicKey}=await wallet.getPublicKey({protocolID:remittance.protocolID,keyID:"YWJj ZGVm",
    counterparty:terms.operator_identity,forSelf:true});
  const address=PublicKey.fromString(publicKey).toAddress();
  const tx=new Transaction();
  tx.addOutput({lockingScript:new P2PKH().lock(address),satoshis:amount});
  // A fictional single-leaf block is sufficient for an offline import test.
  const id=tx.id("hex");
  return {address,receipt:{raw_tx:tx.toHex(),txid:id,recipient_address:address,amount_sats:amount,fee_sats:fee,
    budget_id:terms.budget_id,remittance,sender_identity:terms.operator_identity,
    proof:{txOrId:id,index:0,nodes:[],target:"11".repeat(32)},
    block:{hash:"11".repeat(32),height:800000,merkleroot:id}}};
}
test("credit receipts enforce actual amount plus variable fee within the total cap",async()=>{
  const {address,receipt}=await fixture(999,1);
  assert.equal(creditTransaction(receipt,address).id("hex"),receipt.txid);
  for(const fee of [undefined,null,0,2,-1,1.5,NaN,Infinity])
    assert.throws(()=>creditTransaction({...receipt,fee_sats:fee},address));
});
test("registers a BRC-29 own key, not the operator counterparty key",async()=>{
  let saved;
  await registerCredit(wallet,checked,driver.toPublicKey().toString(),async(action,data)=>{saved={action,...data};});
  assert.equal(saved.action,"register_credit_destination");
  const payload=JSON.parse(saved.proof.payload);
  assert.equal(payload.address,(await fixture()).address);
  assert.notEqual(payload.address,driver.toAddress());
});
test("confirmed credit imports Atomic BEEF without creating a payment",async()=>{
  const {address,receipt}=await fixture();
  assert.equal(creditTransaction(receipt,address).id("hex"),receipt.txid);
  let imported;
  wallet.internalizeAction=async x=>{imported=x;return {accepted:true};};
  await importCredit(wallet,checked,receipt);
  assert.equal(Transaction.fromAtomicBEEF(imported.tx).id("hex"),receipt.txid);
  assert.equal(imported.outputs[0].protocol,"wallet payment");
  assert.equal(imported.outputs[0].paymentRemittance.senderIdentityKey,terms.operator_identity);
  assert.equal(imported.description,"EV session energy credit");
});
function energyReceipt(imported,exported,net="-2.80"){
  return {session_id:"session-1",transaction_id:"proxy-1",net_amount_aud:net,
    energy_account:{session_id:"session-1",ocpp_transaction_id:"proxy-1",currency:"AUD",
      import_kwh:imported,export_kwh:exported,net_amount_aud:net}};
}
test("wallet credit title includes total energy and net average, not the fee or FX rate",()=>{
  const r=energyReceipt(0,10);
  assert.equal(creditDescription({...r,fee_sats:10,amount_sats:280}),
    "EV session credit | 10.00 kWh exported | avg net credit A$0.2800/kWh");
  assert.equal(creditDescription(energyReceipt(2,8)),
    "EV session credit | 10.00 kWh total (2.00 in, 8.00 out) | avg net credit A$0.2800/kWh");
  assert.equal(creditDescription(energyReceipt(10,0)),
    "EV session credit | 10.00 kWh charged | avg net credit A$0.2800/kWh");
});
test("missing, invalid or mismatched energy never fabricates a wallet average",()=>{
  for(const change of [
    {import_kwh:null},{export_kwh:""},{export_kwh:NaN},{import_kwh:-1},
    {import_kwh:0,export_kwh:0},{export_kwh:Infinity},{net_amount_aud:"0"},
    {net_amount_aud:"-9"},{currency:"USD"},{session_id:"another"},{ocpp_transaction_id:"another"},
  ]){
    const r=energyReceipt(0,10);Object.assign(r.energy_account,change);
    assert.equal(creditDescription(r),"EV session energy credit");
  }
  assert.equal(creditDescription(energyReceipt(0,0.00001)),"EV session energy credit");
});
test("receiving wallet gets the enriched title without changing the credit transaction",async()=>{
  const {receipt}=await fixture();
  Object.assign(receipt,energyReceipt(0,10,"-1.89"));
  let imported;
  wallet.internalizeAction=async x=>{imported=x;return {accepted:true};};
  await importCredit(wallet,checked,receipt);
  assert.equal(imported.description,
    "EV session credit | 10.00 kWh exported | avg net credit A$0.1890/kWh");
  assert.equal(Transaction.fromAtomicBEEF(imported.tx).id("hex"),receipt.txid);
});
test("wrong destination, amount, txid and Merkle root fail closed",async()=>{
  const {address,receipt}=await fixture();
  for(const change of [{recipient_address:operator.toAddress()},{amount_sats:190},
    {txid:"00".repeat(32)},{block:{...receipt.block,merkleroot:"00".repeat(32)}}])
    assert.throws(()=>creditTransaction({...receipt,...change},address));
});
test("wrong remittance or operator cannot be imported",async()=>{
  const {receipt}=await fixture();
  await assert.rejects(()=>importCredit(wallet,checked,{...receipt,sender_identity:driver.toPublicKey().toString()}));
});
test("TSC duplicate nodes and nonzero transaction index convert to BUMP",async()=>{
  const {address,receipt}=await fixture();
  const pair=(left,right)=>{
    const data=Buffer.concat([Buffer.from(left,"hex").reverse(),Buffer.from(right,"hex").reverse()]);
    return createHash("sha256").update(createHash("sha256").update(data).digest()).digest().reverse().toString("hex");
  };
  const sibling="22".repeat(32);
  receipt.proof.index=2;
  receipt.proof.nodes=["*",sibling];
  receipt.block.merkleroot=pair(sibling,pair(receipt.txid,receipt.txid));
  assert.equal(creditTransaction(receipt,address).merklePath.computeRoot(receipt.txid),receipt.block.merkleroot);
});
