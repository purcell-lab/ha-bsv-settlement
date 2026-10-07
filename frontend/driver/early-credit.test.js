import test from "node:test";
import assert from "node:assert/strict";
import {PrivateKey,ProtoWallet,PublicKey,P2PKH,Transaction} from "@bsv/sdk";
import {earlyCreditStage,earlyCreditTransaction,importCredit,importAndReportCredit} from "./credit.js";
import {transactionStatus,sessionSummary} from "./portal-model.js";
import {pendingCreditJobs} from "./portal-credit-jobs.js";

async function fixture(){
  const operator=new PrivateKey(7),driver=new ProtoWallet(new PrivateKey(9));
  const r={protocolID:[2,"3241645161d8"],derivationPrefix:"YWJj",derivationSuffix:"ZGVm"};
  const identity=operator.toPublicKey().toString();
  const {publicKey}=await driver.getPublicKey({protocolID:r.protocolID,keyID:"YWJj ZGVm",counterparty:identity,forSelf:true});
  const address=PublicKey.fromString(publicKey).toAddress();
  const parent=new Transaction();
  parent.addOutput({lockingScript:new P2PKH().lock(operator.toAddress()),satoshis:5000});
  const tx=new Transaction();
  tx.addInput({sourceTransaction:parent,sourceOutputIndex:0,unlockingScriptTemplate:new P2PKH().unlock(operator)});
  tx.addOutput({lockingScript:new P2PKH().lock(address),satoshis:54});
  tx.addOutput({lockingScript:new P2PKH().lock(operator.toAddress()),satoshis:4923});
  await tx.sign();
  const receipt={delivery_stage:earlyCreditStage,state:"provider_unconfirmed",confirmations:0,
    txid:tx.id("hex"),raw_tx:tx.toHex(),amount_sats:54,fee_sats:23,recipient_address:address,
    sender_identity:identity,remittance:r,budget_id:"fixture",
    ancestry:[{txid:parent.id("hex"),raw_tx:parent.toHex(),
      proof:{txOrId:parent.id("hex"),index:0,nodes:[],target:"22".repeat(32)},
      block:{hash:"22".repeat(32),height:800000,merkleroot:parent.id("hex")}}]};
  driver.getNetwork=async()=>({network:"mainnet"});
  return {receipt,address,driver,checked:{terms:{operator_identity:identity,credit_receiving:r,budget_id:"fixture"}}};
}
test("unconfirmed child becomes Atomic BEEF with proven parent, never fake child proof",async()=>{
  const {receipt,address,driver,checked}=await fixture();
  const tx=await earlyCreditTransaction(receipt,address);
  const mobile=await import("@bsv/sdk-mobile");
  const cross=mobile.Transaction.fromAtomicBEEF(tx.toAtomicBEEF());
  assert.equal(cross.id("hex"),receipt.txid);
  assert.equal(await cross.verify("scripts only"),true);
  assert.equal(tx.merklePath,undefined);
  assert.ok(tx.inputs[0].sourceTransaction.merklePath);
  let imports=0;
  driver.internalizeAction=async args=>{
    imports++;
    const received=Transaction.fromAtomicBEEF(args.tx);
    assert.equal(received.id("hex"),receipt.txid);
    assert.equal(received.merklePath,undefined);
    assert.ok(received.inputs[0].sourceTransaction.merklePath);
    return {accepted:true};
  };
  driver.createAction=driver.signAction=async()=>{throw Error("Forbidden new payment");};
  await importCredit(driver,checked,receipt);
  assert.equal(imports,1);
});
test("wrong root, amount, parent, stage or altered signed transaction is rejected",async()=>{
  for(const kind of ["root","amount","parent","stage","signature"]){
    const {receipt,address}=await fixture();
    if(kind==="root")receipt.ancestry[0].block.merkleroot="44".repeat(32);
    if(kind==="amount")receipt.amount_sats++;
    if(kind==="parent")receipt.ancestry[0].txid="55".repeat(32);
    if(kind==="stage")receipt.state="broadcast_unknown";
    if(kind==="signature"){
      const tx=Transaction.fromHex(receipt.raw_tx);
      tx.outputs[1].satoshis--;receipt.fee_sats++;
      receipt.raw_tx=tx.toHex();receipt.txid=tx.id("hex");
    }
    await assert.rejects(()=>earlyCreditTransaction(receipt,address));
  }
});
test("early discovery needs explicit eligibility; accepted-unconfirmed never says confirmed",async()=>{
  const row={id:"credit",txid:"a".repeat(64),state:"provider_unconfirmed",direction:"operator_to_driver"};
  const fetch=async()=>({identity:"owner",total:1,sessions:[{transactions:[row]}]});
  assert.equal((await pendingCreditJobs(fetch,"owner")).length,0);
  row.early_receipt_available=true;
  assert.match((await pendingCreditJobs(fetch,"owner"))[0].key,/:unconfirmed$/);
  row.wallet_receipt_status="wallet_reported_accepted";row.wallet_imported_at="2026-10-07T09:01:00Z";
  assert.equal((await pendingCreditJobs(fetch,"owner")).length,0);
  assert.equal(transactionStatus(row),"Wallet received credit · awaiting block confirmation");
  assert.equal(sessionSummary({transactions:[row]}).status,"Received · unconfirmed");
});
test("acknowledgement retry keeps early signed stage after confirmation without reimport",async()=>{
  const {receipt,driver,checked}=await fixture();
  checked.invitation={payload:"fictional invitation"};
  receipt.session_id="session";receipt.transaction_id="transaction";
  let imports=0,reports=0;
  driver.internalizeAction=async()=>{imports++;return {accepted:true};};
  const identity=(await driver.getPublicKey({identityKey:true})).publicKey,cache=new Map();
  const api=async(action,data)=>{
    reports++;
    assert.equal(data.delivery_stage,earlyCreditStage);
    assert.equal(JSON.parse(data.acknowledgement.payload).version,2);
    if(reports===1)throw Error("Transport failure reporting acceptance");
    return {txid:receipt.txid,wallet_receipt_status:"wallet_reported_accepted",
      wallet_imported_at:"2026-10-07T09:01:00Z"};
  };
  await assert.rejects(()=>importAndReportCredit(driver,checked,receipt,identity,api,cache),
    e=>e.walletAccepted===true);
  const confirmed={...receipt,state:"provider_confirmed"};
  delete confirmed.delivery_stage;
  await importAndReportCredit(driver,checked,confirmed,identity,api,cache);
  assert.equal(imports,1);assert.equal(reports,2);
});
