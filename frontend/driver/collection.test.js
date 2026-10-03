import test from "node:test";
import assert from "node:assert/strict";
import { PrivateKey, ProtoWallet, Transaction, P2PKH, UnlockingScript } from "@bsv/sdk";
import { canonical, bytes, hash, parseInvitation, paymentAuthority, spendingScope } from "./model.js";
import { checkQuote, collectOnce, shape, inspectDraft } from "./collection.js";

async function fixture({fee=5,amount=189,feeCap=10,total=1000}={}) {
  const operator=PrivateKey.fromRandom(), key=PrivateKey.fromRandom(), proto=new ProtoWallet(key);
  const identity=(await proto.getPublicKey({identityKey:true})).publicKey;
  const t={version:2,budget_id:"11111111-2222-4333-8444-555555555555",session_id:"fictional-session",
    session_mode:"existing_session",transaction_id:"proxy-test",network:"BSV mainnet",
    operator_identity:operator.toPublicKey().toString(),operator_address:operator.toPublicKey().toAddress(),
    max_total_sats:total,max_fee_sats:feeCap,satoshis_per_aud:"100",pricing_rule:"Test",
    account_scope:"Entire named session",import_price_entity:"sensor.test",export_price_entity:"sensor.test",
    created_at:new Date(Date.now()-60000).toISOString(),expires_at:new Date(Date.now()+3600000).toISOString(),
    scope:spendingScope};
  t.payment_authority=paymentAuthority(t);
  const payload=canonical(t);
  const checked=parseInvitation(JSON.stringify({version:1,payload,signature:operator.sign(bytes(payload)).toDER("hex")}));
  const q={version:1,budget_id:t.budget_id,network:t.network,
    invitation_hash:await hash(payload),driver_identity:identity,
    recipient_address:t.operator_address,operator_identity:t.operator_identity,
    amount_sats:189,max_fee_sats:Math.min(feeCap,total-189),max_total_sats:total,satoshis_per_aud:"100",expires_at:t.expires_at,
    account:{session_id:t.session_id,ocpp_transaction_id:"proxy-test",ended_at:new Date().toISOString(),
      net_amount_aud:"1.89",net_cost_aud_unrounded:"1.89000"}};
  const qp=canonical(q);
  const quote={payload:qp,hash:await hash(qp),signature:operator.sign(bytes(qp)).toDER("hex")};
  const source=new Transaction(1,[],[{lockingScript:new P2PKH().lock(key.toPublicKey().toAddress()),satoshis:50000}],0);
  const tx=new Transaction(1,[{sourceTransaction:source,sourceTXID:source.id("hex"),
    sourceOutputIndex:0,sequence:0xffffffff,unlockingScript:UnlockingScript.fromHex(""),
    unlockingScriptTemplate:new P2PKH().unlock(key)}],[
      {lockingScript:new P2PKH().lock(t.operator_address),satoshis:amount},
      {lockingScript:new P2PKH().lock(key.toPublicKey().toAddress()),satoshis:50000-amount-fee}],0);
  const calls=[];
  const wallet={
    getPublicKey:proto.getPublicKey.bind(proto),createSignature:proto.createSignature.bind(proto),
    getNetwork:async()=>({network:"mainnet"}),
    createAction:async args=>{
      calls.push(["createAction",args]);
      assert.equal(args.options.signAndProcess,false);
      assert.equal(args.options.noSend,true);
      return {signableTransaction:{tx:tx.toAtomicBEEF(true),reference:"fictional"}};
    },
    signAction:async args=>{
      calls.push(["signAction",args]);assert.equal(args.options.noSend,true);
      await tx.sign();return {tx:tx.toAtomicBEEF(true),txid:tx.id("hex")};
    }
  };
  const api=async(action,args)=>{
    calls.push([action,args]);
    if(action==="claim_collection")return {claimed:true};
    if(action==="authorise_collection")return {submit_once:true,draft_hash:await hash(canonical(args.draft)),fee_sats:fee};
    if(action==="report_collection")return {state:"provider_confirmed",txid:tx.id("hex")};
    throw Error("Unexpected action");
  };
  return {operator,key,t,checked,q,quote,tx,wallet,api,calls};
}

test("automatic collection checks draft before signing and never asks wallet to broadcast",async()=>{
  const f=await fixture();
  const result=await collectOnce(f.wallet,f.checked,null,f.quote,f.api);
  assert.equal(result.state,"provider_confirmed");
  assert.deepEqual(f.calls.map(c=>c[0]),["claim_collection","createAction","authorise_collection","signAction","report_collection"]);
  assert.equal(f.calls.at(-1)[1].raw_tx,f.tx.toHex());
});
test("high wallet fee and altered recipient amount stop before signing",async()=>{
  for(const options of [{fee:11},{amount:190}]) {
    const f=await fixture(options);
    await assert.rejects(collectOnce(f.wallet,f.checked,null,f.quote,f.api));
    assert.equal(f.calls.filter(c=>c[0]==="signAction").length,0);
    assert.equal(f.calls.filter(c=>c[0]==="report_collection").length,0);
  }
});
test("higher configured fee cap uses only the remaining total budget",async()=>{
 const f=await fixture({fee:38,feeCap:1000});
 assert.equal(f.q.max_fee_sats,811);
 assert.equal((await collectOnce(f.wallet,f.checked,null,f.quote,f.api)).state,"provider_confirmed");
 const rejected=await fixture({fee:812,feeCap:1000});
 await assert.rejects(collectOnce(rejected.wallet,rejected.checked,null,rejected.quote,rejected.api),e=>{
   assert.equal(e.diagnostic.reason,"fee_limit_exceeded");
   assert.equal(e.diagnostic.details.fee_sats,812);
   assert.equal(e.diagnostic.details.fee_cap_sats,811);
   return true;
 });
 assert.equal(rejected.calls.filter(c=>c[0]==="signAction").length,0);
});
test("a legacy 10 sat mandate retains its fee ceiling and precise failure",async()=>{
 const f=await fixture({fee:38});
 await assert.rejects(collectOnce(f.wallet,f.checked,null,f.quote,f.api),e=>{
   assert.equal(e.diagnostic.reason,"fee_limit_exceeded");
   assert.equal(e.diagnostic.details.fee_sats,38);
   assert.equal(e.diagnostic.details.fee_cap_sats,10);
   return true;
 });
});
test("wrong network, refused permit and lost claim response never sign or submit",async()=>{
  for(const mode of ["network","permit","claim"]) {
    const f=await fixture();
    if(mode==="network")f.wallet.getNetwork=async()=>({network:"testnet"});
    const api=async(a,b)=>{
      if(mode==="claim"&&a==="claim_collection")throw Error("Lost response");
      if(mode==="permit"&&a==="authorise_collection")return {submit_once:false};
      return f.api(a,b);
    };
    await assert.rejects(collectOnce(f.wallet,f.checked,null,f.quote,api));
    assert.equal(f.calls.filter(c=>c[0]==="signAction").length,0);
  }
});
test("failed submission retains exact signed bytes for idempotent reporting, never recreates",async()=>{
  const f=await fixture();
  let error;
  try {await collectOnce(f.wallet,f.checked,null,f.quote,async(a,b)=>{
    if(a==="report_collection")throw Error("Lost response");return f.api(a,b);
  });}catch(e){error=e;}
  assert.equal(error.pendingReport.raw_tx,f.tx.toHex());
  assert.equal(f.calls.filter(c=>c[0]==="createAction").length,1);
  assert.equal(f.calls.filter(c=>c[0]==="signAction").length,1);
});
test("quote amount and frozen conversion cannot be changed even with operator signature",async()=>{
  const f=await fixture();
  for(const patch of [{amount_sats:190},{satoshis_per_aud:"200"},{max_fee_sats:11},
      {recipient_address:f.key.toPublicKey().toAddress()}]) {
    const payload=canonical({...f.q,...patch});
    const quote={payload,hash:await hash(payload),signature:f.operator.sign(bytes(payload)).toDER("hex")};
    await assert.rejects(checkQuote(quote,f.checked,null));
  }
});
test("unsigned and signed transaction shapes are identical for the Python preflight contract",async()=>{
  const f=await fixture();
  const before=inspectDraft(f.tx.toAtomicBEEF(true),f.q);
  await f.tx.sign();
  const after=inspectDraft(f.tx.toAtomicBEEF(true),f.q);
  assert.equal(canonical(shape(before.tx)),canonical(shape(after.tx)));
  assert.equal(after.fee,5);
});
test("fetch failures identify the precise step without retrying payment",async()=>{
  for(const stage of ["claim_collection","create_draft","authorise_draft","submit_payment"]){
    const f=await fixture();
    if(stage==="create_draft")f.wallet.createAction=async()=>{throw TypeError("Failed to fetch");};
    let report, error;
    const failing=async(a,b)=>{
      if(a==="report_collection_failure"){report=b;return {diagnostic_saved:true};}
      if(a===({claim_collection:"claim_collection",authorise_draft:"authorise_collection",
        submit_payment:"report_collection"}[stage]))throw TypeError("Failed to fetch");
      return f.api(a,b);
    };
    try{await collectOnce(f.wallet,f.checked,null,f.quote,failing);}catch(e){error=e;}
    assert.equal(error.diagnostic.stage,stage);
    assert.equal(error.diagnostic.code,"network_request_failed");
    assert.equal(report.diagnostic.event_id,error.diagnostic.event_id);
    assert.deepEqual(Object.keys(report.diagnostic).sort(),["code","event_id","stage"]);
    if(stage==="submit_payment")assert.equal(error.pendingReport.raw_tx,f.tx.toHex());
    assert.ok(f.calls.filter(x=>x[0]==="createAction").length<=1);
  }
});
test("offline failure report is retained, not confused with payment retry",async()=>{
  const f=await fixture();let error;
  try{await collectOnce(f.wallet,f.checked,null,f.quote,async()=>{throw TypeError("Failed to fetch");});}
  catch(e){error=e;}
  assert.equal(error.pendingDiagnostic.diagnostic.stage,"claim_collection");
  assert.equal(f.calls.filter(x=>x[0]==="createAction").length,0);
});
test("recovered collection requires caller's explicit confirmation flag",async()=>{
  const f=await fixture();
  await collectOnce(f.wallet,f.checked,null,f.quote,f.api,()=>{},true);
  assert.equal(f.calls.find(x=>x[0]==="claim_collection")[1].confirm_recovered_attempt,true);
});
