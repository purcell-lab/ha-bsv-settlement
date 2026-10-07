import test from "node:test";
import assert from "node:assert/strict";
import {PrivateKey,ProtoWallet,Transaction,P2PKH,UnlockingScript} from "@bsv/sdk";
import {canonical,bytes,hash} from "./model.js";
import {checkAdjustmentQuote,collectAdjustmentOnce} from "./collection.js";
import {PortalCollections} from "./portal-collections.js";
import {sessionSummary,transactionStatus} from "./portal-model.js";

async function fixture(){
  const operator=new PrivateKey(123),driver=new PrivateKey(456),proto=new ProtoWallet(driver);
  const review_id="11111111-2222-4333-8444-555555555555",budget_id="adjustment:"+review_id;
  const job={kind:"adjustment",review_id,budget_id,session_id:budget_id};
  const q={...job,version:1,kind:"wallet_connected_energy_adjustment_v1",network:"BSV mainnet",
    terms_hash:"a".repeat(64),driver_identity:driver.toPublicKey().toString(),
    operator_identity:operator.toPublicKey().toString(),recipient_address:operator.toPublicKey().toAddress(),
    amount_sats:185,max_total_sats:1000,max_fee_sats:815,satoshis_per_aud:"100",
    price_aud_per_kwh:"0.3691626",created_at:new Date().toISOString(),
    expires_at:new Date(Date.now()+590000).toISOString(),
    account:{session_id:budget_id,ocpp_transaction_id:budget_id,currency:"AUD",
      import_kwh:null,export_kwh:null,adjustment_kwh:"5",adjustment_direction:"import",
      net_amount_aud:"1.8458130",net_cost_aud_unrounded:"1.8458130",ended_at:new Date().toISOString()}};
  delete q.session_id;
  const envelope=async()=>{const payload=canonical(q);return {payload,hash:await hash(payload),
    signature:operator.sign(bytes(payload)).toDER("hex")};};
  const source=new Transaction(1,[],[{lockingScript:new P2PKH().lock(driver.toPublicKey().toAddress()),satoshis:50000}],0);
  const tx=new Transaction(1,[{sourceTransaction:source,sourceTXID:source.id("hex"),
    sourceOutputIndex:0,sequence:0xffffffff,unlockingScript:UnlockingScript.fromHex(""),
    unlockingScriptTemplate:new P2PKH().unlock(driver)}],[
    {lockingScript:new P2PKH().lock(q.recipient_address),satoshis:185},
    {lockingScript:new P2PKH().lock(driver.toPublicKey().toAddress()),satoshis:49810}],0);
  const calls=[],wallet={
    getPublicKey:proto.getPublicKey.bind(proto),getNetwork:async()=>({network:"mainnet"}),
    createSignature:async args=>{calls.push(["signature",args]);return proto.createSignature(args);},
    createAction:async args=>{calls.push(["draft",args]);return {signableTransaction:{tx:tx.toAtomicBEEF(true),reference:"test"}};},
    signAction:async args=>{calls.push(["sign",args]);await tx.sign();return {tx:tx.toAtomicBEEF(true)};}
  };
  const api=async(action,data)=>{
    calls.push([action,data]);
    if(action==="claim_collection")return {claimed:true};
    if(action==="authorise_collection")return {submit_once:true,fee_sats:5,draft_hash:await hash(canonical(data.draft))};
    if(action==="report_collection")return {state:"provider_unconfirmed"};
    return {};
  };
  return {q,job,operator,envelope,wallet,api,calls};
}

test("separate adjustment uses signed quote, native proof, noSend draft and one permit",async()=>{
  const f=await fixture();
  const result=await collectAdjustmentOnce(f.wallet,f.job,f.q.operator_identity,await f.envelope(),f.api);
  assert.equal(result.state,"provider_unconfirmed");
  assert.deepEqual(f.calls.map(x=>x[0]),["signature","claim_collection","draft","authorise_collection","sign","report_collection"]);
  assert.match(f.calls[0][1].description,/185 sat.*1000 sat maximum.*Not charged to weekly/);
  const draft=f.calls.find(x=>x[0]==="draft")[1];
  assert.equal(draft.options.noSend,true);assert.equal(draft.options.signAndProcess,false);
  assert.match(draft.description,/5 kWh import equivalent.*0.3691626/);
  assert.equal(f.calls.find(x=>x[0]==="sign")[1].options.noSend,true);
});

test("quote rejects substituted operator, scope, amount, fee ceiling and fabricated metering",async()=>{
  for(const mutate of [
    f=>{f.job.review_id="other";},f=>{f.q.amount_sats=186;},
    f=>{f.q.max_total_sats=2000;},f=>{f.q.max_fee_sats=900;},
    f=>{f.q.account.import_kwh=5;},f=>{f.q.price_aud_per_kwh="-0.3691626";},
    f=>{f.q.expires_at=new Date(Date.now()-1).toISOString();},
    f=>{f.q.recipient_address=new PrivateKey(789).toPublicKey().toAddress();}
  ]){
    const f=await fixture();mutate(f);
    await assert.rejects(checkAdjustmentQuote(await f.envelope(),f.job,f.operator.toPublicKey().toString()));
    assert.deepEqual(f.calls,[]);
  }
  const f=await fixture();
  await assert.rejects(checkAdjustmentQuote(await f.envelope(),f.job,new PrivateKey(789).toPublicKey().toString()));
});

test("negative export tariff is still a debit with explicit adjustment scope",async()=>{
  const f=await fixture();f.q.account.adjustment_direction="export";f.q.price_aud_per_kwh="-0.3691626";
  assert.equal((await checkAdjustmentQuote(await f.envelope(),f.job,f.q.operator_identity)).amount_sats,185);
});

test("wrong wallet or testnet cannot create a payment",async()=>{
  for(const patch of [{getPublicKey:async()=>({publicKey:new PrivateKey(999).toPublicKey().toString()})},
    {getNetwork:async()=>({network:"testnet"})}]){
    const f=await fixture();Object.assign(f.wallet,patch);
    await assert.rejects(collectAdjustmentOnce(f.wallet,f.job,f.q.operator_identity,await f.envelope(),f.api));
    assert.equal(f.calls.some(x=>x[0]==="draft"),false);
  }
});

test("portal discovers adjustments without treating login or weekly budget as payment consent",async()=>{
  const f=await fixture(),calls=[];
  const api=async(action,data)=>{
    calls.push([action,data]);
    if(action==="debit_jobs")return {jobs:[f.job]};
    if(action==="debit_status")return {kind:"adjustment",collection:{state:"ready",quote:await f.envelope()}};
    const names={debit_claim:"claim_collection",debit_authorise:"authorise_collection",debit_report:"report_collection"};
    return f.api(names[action],data);
  };
  const worker=new PortalCollections({api,assertActive(){},operatorIdentity:()=>f.q.operator_identity});
  await worker.run(f.wallet);assert.deepEqual(calls,[]);
  worker.start();await worker.run(f.wallet);await worker.run(f.wallet);
  assert.equal(calls.filter(x=>x[0]==="debit_claim").length,1);
  assert.ok(calls.filter(x=>x[0]==="debit_claim").every(x=>x[1].kind==="adjustment"&&x[1].review_id===f.job.review_id));
});

test("wallet refusal stays latched without automatic re-prompt or transaction",async()=>{
  const f=await fixture();let attempts=0;
  f.wallet.createSignature=async()=>{attempts++;throw Error("Declined");};
  const worker=new PortalCollections({assertActive(){},operatorIdentity:()=>f.q.operator_identity,
    api:async action=>action==="debit_jobs"?{jobs:[f.job]}:{collection:{state:"ready",quote:await f.envelope()}}});
  worker.start();await worker.run(f.wallet);await worker.run(f.wallet);
  assert.equal(attempts,1);assert.equal(worker.paused.size,1);assert.deepEqual(f.calls,[]);
});

test("adjustment request is not labelled a metering warning or a completed payment",()=>{
  const row={direction:"driver_to_operator",state:"ready",amount_sats:185};
  const summary=sessionSummary({ended_at:new Date().toISOString(),quality_flags:["manual_energy_adjustment"],
    transactions:[row]});
  assert.equal(summary.status,"Wallet approval");assert.equal(summary.warning,false);
  assert.equal(transactionStatus(row),"Awaiting wallet approval");
});
