import test from "node:test";
import assert from "node:assert/strict";
import {PrivateKey} from "@bsv/sdk";
import {canonical,bytes,paymentAuthority,spendingScope} from "./model.js";
import {PortalCollections,collectionOutcome} from "./portal-collections.js";

test("broadcast uncertainty is never reported as submitted or confirmed",()=>{
  assert.match(collectionOutcome({state:"broadcast_unknown"}),/uncertain/);
  assert.match(collectionOutcome({state:"provider_unconfirmed"}),/Awaiting block confirmation/);
  assert.equal(collectionOutcome({state:"provider_confirmed"}),"Session payment confirmed by the provider.");
});

function fixture(){
  const key=new PrivateKey(12),calls=[],jobs=["one","two"].map((session_id,i)=>({
    session_id,budget_id:`11111111-2222-4333-8444-55555555555${i}`}));
  const envelope=job=>{
    const terms={version:2,scope:spendingScope,network:"BSV mainnet",...job,
      transaction_id:job.session_id,session_mode:"existing_session",
      operator_identity:key.toPublicKey().toString(),operator_address:key.toPublicKey().toAddress(),
      max_total_sats:1000,max_fee_sats:1000,satoshis_per_aud:"100",pricing_rule:"Dynamic",account_scope:"One session",
      import_price_entity:"sensor.buy",export_price_entity:"sensor.sell",
      created_at:new Date().toISOString(),expires_at:new Date(Date.now()+3600000).toISOString()};
    terms.payment_authority=paymentAuthority(terms);
    const payload=canonical(terms);
    return {version:1,payload,signature:key.sign(bytes(payload)).toDER("hex")};
  };
  const states=new Map(jobs.map(j=>[j.session_id,"ready"]));
  const api=async(action,job)=>{
    calls.push(action);
    if(action==="debit_jobs")return {jobs};
    if(action==="debit_status")return {invitation:envelope(job),collection:{state:states.get(job.session_id),quote:{}}};
    return {ok:true};
  };
  return {api,jobs,states,calls};
}
test("one explicit start services multiple sessions without repeat attempts",async()=>{
  const f=fixture(),paid=[];
  const worker=new PortalCollections({...f,assertActive(){},collect:async(_w,checked,_b,_q,api)=>{
    paid.push(checked.terms.session_id);
    await api("claim_collection",{attempt_token:"fictional"});
    return {state:"provider_unconfirmed"};
  }});
  await worker.run({});assert.deepEqual(f.calls,[]); // Cookie restoration alone is inert.
  worker.start();await worker.run({});await worker.run({});
  assert.deepEqual(paid,["one","two"]);assert.equal(f.calls.filter(x=>x==="debit_claim").length,2);
});
test("holds, submitted payments and confirmed records never draft or retry",async()=>{
  for(const state of ["wallet_attempt_reserved","broadcast_unknown","provider_unconfirmed","provider_confirmed","recovery_ready","waived"]){
    const f=fixture();f.states.set("one",state);f.states.set("two",state);
    const worker=new PortalCollections({...f,assertActive(){},collect:()=>assert.fail("No collection allowed")});
    worker.start();await worker.run({});await worker.run({});
    assert.equal(f.calls.includes("debit_claim"),false);
  }
});
test("failed or uncertain wallet action stays latched across runs and restart gesture",async()=>{
  const f=fixture();let attempts=0;
  const worker=new PortalCollections({...f,assertActive(){},collect:async()=>{
    attempts++;throw Object.assign(Error("Uncertain"),{pendingReport:{raw_tx:"fictional"}});
  }});
  worker.start();await worker.run({});worker.stop();worker.start();await worker.run({});
  assert.equal(attempts,2);assert.equal(worker.paused.size,2);
});
test("serial execution and context guard prevent concurrent and hidden-page actions",async()=>{
  const f=fixture();let active=true,release,attempts=0;
  const blocked=new Promise(resolve=>{release=resolve;});
  const worker=new PortalCollections({...f,assertActive(){if(!active)throw Error("Hidden");},
    collect:async wallet=>{attempts++;await blocked;await wallet.signAction();}});
  const wallet={signAction:()=>assert.fail("Hidden page must not sign")};
  worker.start();const first=worker.run(wallet);await new Promise(r=>setTimeout(r,0));
  await worker.run(wallet);active=false;release();
  await assert.rejects(first,/Hidden/);
  assert.equal(attempts,1);
});
test("scoped API cannot route a claim to another session",async()=>{
  const f=fixture();const seen=[];
  const api=async(action,data)=>{if(action==="debit_claim")seen.push(data);return f.api(action,data);};
  const worker=new PortalCollections({api,assertActive(){},collect:async(_w,_c,_b,_q,scoped)=>{
    await scoped("claim_collection",{budget_id:"attacker",session_id:"other"});return {};
  }});
  worker.start();await worker.run({});
  assert.deepEqual(seen.map(x=>x.session_id),["one","two"]);
  assert.ok(seen.every(x=>x.budget_id!=="attacker"));
});
test("permit and failure steps are sent signed; other steps stay on the plain API",async()=>{
  const f=fixture(),signedCalls=[];const wallet={id:"raw"};
  const signedApi=async(signer,action,data)=>{signedCalls.push({signer,action,data});return {ok:true};};
  const worker=new PortalCollections({...f,signedApi,assertActive(){},collect:async(_w,_c,_b,_q,scoped)=>{
    await scoped("claim_collection",{attempt_token:"t"});
    await scoped("authorise_collection",{attempt_token:"t",draft:{}});
    await scoped("report_collection_failure",{attempt_token:"t",diagnostic:{}});
    return {};
  }});
  worker.start();await worker.run(wallet);
  assert.equal(f.calls.filter(x=>x==="debit_claim").length,2);
  assert.ok(!f.calls.includes("debit_authorise")&&!f.calls.includes("debit_failure"));
  assert.deepEqual(signedCalls.map(c=>c.action),["debit_authorise","debit_failure","debit_authorise","debit_failure"]);
  // The permit goes through the guarded wallet; the failure report may use the raw wallet.
  assert.notEqual(signedCalls[0].signer,wallet);assert.equal(signedCalls[1].signer,wallet);
  assert.equal(signedCalls[0].data.session_id,"one");assert.equal(signedCalls[2].data.session_id,"two");
});
test("without a signer, permit steps fail closed instead of falling back to the cookie",async()=>{
  const f=fixture();
  const worker=new PortalCollections({...f,assertActive(){},collect:async(_w,_c,_b,_q,scoped)=>{
    await scoped("authorise_collection",{attempt_token:"t",draft:{}});
  }});
  worker.start();await worker.run({});
  assert.ok(!f.calls.includes("debit_authorise"));
  assert.match(worker.paused.values().next().value.message,/wallet-signed/);
});
