import test from "node:test";
import assert from "node:assert/strict";
import {KeyDeriver,PrivateKey,Signature,Utils} from "@bsv/sdk";
import {MonthlySetup,cancelMonthly,checkMonthlyChallenge,checkCancelChallenge,walletCapabilities,monthlyProtocol} from "./monthly-wallet.js";
import {fixtureWallet,pairedMethods} from "./wallet-fixtures.js";
import {bytes,canonical} from "./model.js";

const HOST="charging.example.com";
const iso=ms=>new Date(ms).toISOString().replace("Z","+00:00");
const verify=(payload,keyID,identity,signature)=>new KeyDeriver("anyone")
  .derivePublicKey(monthlyProtocol,keyID,identity).verify(bytes(payload),Signature.fromDER(Utils.toArray(signature,"hex")));

/** In-memory stand-in for the S3 portal endpoints, mirroring their refusals. */
function fakeServer({enabled=true,grant=true,receiving=true,mutate=t=>t}={}){
  const s={revision:0,challenge:null,authority:null,cancelled:false,calls:[]};
  const refuse=code=>{const e=Error(code);e.status=409;e.code=code;throw e;};
  const status=identity=>{
    if(!enabled)return {enabled:false,readiness:{automatic_collection:false,missing:["monthly_disabled"]}};
    const missing=[];
    if(!s.authority||s.cancelled)missing.push("monthly_authority");
    else if(!grant)missing.push("wallet_monthly_permission");
    if(!receiving)missing.push("receiving_registration");
    return {enabled:true,revision:s.revision,station_ids:["station-1"],monthly_limit_sats:30000,
      authority:s.authority&&{authority_id:s.authority.authority_id,state:s.cancelled?"cancelled":"active"},
      native_grant:{state:s.authority&&!s.cancelled?grant?"verified":"unverified":"not_applicable"},
      receiving:{registered:receiving},readiness:{automatic_collection:!missing.length,missing}};
  };
  s.api=identity=>async(action,data={})=>{
    s.calls.push(action);
    if(action==="monthly_status")return status(identity);
    if(!enabled)refuse("disabled");
    if(data.revision!==s.revision&&action!=="monthly_cancel_challenge")refuse("revision_conflict");
    if(action==="monthly_challenge"){
      if(s.authority)refuse(s.cancelled?"cancelled":"exists");
      const now=Date.now();
      const terms=mutate({version:4,scope:"recurring_calendar_month_charging_including_driver_fees",network:"BSV mainnet",
        authority_id:"auth-1",nonce:"ab".repeat(32),driver_identity:identity,operator_identity:new PrivateKey(23).toPublicKey().toString(),
        operator_address:"1Operator",origin:HOST,station_ids:["station-1"],monthly_limit_sats:30000,
        period_policy:{policy_id:"fixture-utc",wallet_version:"fictional-wallet-1",timezone:"UTC",
          booking_event:"verified_spend_commit",fee_basis:"all_driver_paid_wallet_debits",evidence_ref:"offline-fixture-only"},
        issued_at:iso(now),accept_before:iso(now+600000),effective_at:iso(now),recurs_until_cancelled:true,
        collection_policy:"one_final_net_payment_per_session_on_closure",credits_refill:false,unused_carries_forward:false,
        conversion_policy:"freeze_configured_sat_per_aud_at_session_binding",included_session:null});
      s.revision++;s.challenge=terms;
      return {authority_id:terms.authority_id,terms,payload:canonical({version:4,action:"authorise_monthly_charging",terms}),
        protocolID:monthlyProtocol,keyID:terms.authority_id,revision:s.revision};
    }
    if(action==="monthly_accept"){
      const payload=canonical({version:4,action:"authorise_monthly_charging",terms:s.challenge});
      if(data.proof.payload!==payload||!verify(payload,data.authority_id,identity,data.proof.signature))refuse("invalid_proof");
      s.revision++;s.authority={authority_id:data.authority_id,terms:s.challenge};
      return {accepted:true,authority_id:data.authority_id,revision:s.revision};
    }
    if(!s.authority)refuse("none");
    const cancel=canonical({version:4,action:"cancel_monthly_charging",authority_id:s.authority.authority_id,
      driver_identity:identity,terms_hash:"cd".repeat(32)});
    if(action==="monthly_cancel_challenge")return {authority_id:s.authority.authority_id,payload:cancel,
      protocolID:monthlyProtocol,keyID:s.authority.authority_id,revision:s.revision};
    if(action==="monthly_cancel"){
      if(!verify(cancel,s.authority.authority_id,identity,data.proof.signature))refuse("invalid_proof");
      s.revision++;s.cancelled=true;return {cancelled:true,wallet_permission_revoked:"not_verified",revision:s.revision};
    }
    refuse("unavailable");
  };
  return s;
}

async function identityOf(wallet){return (await wallet.getPublicKey({identityKey:true})).publicKey;}

function setup(server,identity,{signedIn=null}={}){
  const log={signIn:0,sync:0};
  const flow=new MonthlySetup({api:server.api(identity),
    signIn:async()=>{log.signIn++;return identity;},
    syncReceipts:async()=>{log.sync++;}});
  return {flow,log,signedIn};
}

test("fresh driver: one action signs in, authorises, receives; ready only on server evidence",async()=>{
  const wallet=fixtureWallet("full"),identity=await identityOf(wallet),server=fakeServer();
  const {flow,log}=setup(server,identity);
  const out=await flow.run({wallet,identity:null,hostname:HOST,confirmTerms:async t=>t.monthly_limit_sats===30000});
  assert.equal(out.ready,true);
  assert.deepEqual(out.steps.map(s=>[s.id,s.state]),[["wallet","done"],["sign_in","done"],["authority","done"],
    ["receipts","done"],["wallet_permission","done"]]);
  assert.equal(log.signIn,1);assert.equal(log.sync,1);
  assert.equal(wallet.calls.createSignature,1); // Login prompt is in the injected signIn here.
  assert.deepEqual(server.calls,["monthly_status","monthly_challenge","monthly_accept","monthly_status"]);
});

test("returning driver: only missing steps run, no new signature",async()=>{
  const wallet=fixtureWallet("full"),identity=await identityOf(wallet),server=fakeServer();
  await setup(server,identity).flow.run({wallet,identity:null,hostname:HOST,confirmTerms:async()=>true});
  const before=wallet.calls.createSignature;
  const {flow,log}=setup(server,identity);
  const out=await flow.run({wallet,identity,hostname:HOST,confirmTerms:async()=>{throw Error("not asked");}});
  assert.equal(out.ready,true);
  assert.equal(wallet.calls.createSignature,before);
  assert.equal(log.signIn,0);assert.equal(log.sync,1); // Receipts still sync for the original identity.
  assert.deepEqual(out.steps.filter(s=>s.state==="skipped").map(s=>s.id),["sign_in","authority"]);
});

test("partial setup resumes reviewed adapters without re-signing or trusting their return",async()=>{
  const wallet=fixtureWallet("full"),identity=await identityOf(wallet),server=fakeServer({grant:false,receiving:false});
  await setup(server,identity).flow.run({wallet,identity,hostname:HOST,confirmTerms:async()=>true});
  const before=wallet.calls.createSignature,authority=server.authority.authority_id,steps=[];
  const flow=new MonthlySetup({api:server.api(identity),signIn:async()=>identity,syncReceipts:async()=>{},
    setupAdapters:{
      receiving_registration:async()=>{steps.push("receiving");return {registered:true};},
      wallet_monthly_permission:async()=>{steps.push("permission");return {verified:true};},
    }});
  const out=await flow.run({wallet,identity,hostname:HOST,confirmTerms:async()=>{throw Error("must not re-sign");}});
  assert.deepEqual(steps,["receiving","permission"]);
  assert.equal(wallet.calls.createSignature,before);
  assert.equal(server.authority.authority_id,authority);
  assert.equal(out.ready,false); // Adapter assertions cannot replace server verification.
  assert.ok(out.missing.includes("receiving_registration"));
  assert.ok(out.missing.includes("wallet_monthly_permission"));
});

test("missing setup adapters are explicit and permit later retry",async()=>{
  const wallet=fixtureWallet("full"),identity=await identityOf(wallet),server=fakeServer({grant:false,receiving:false});
  const {flow}=setup(server,identity);
  for(let i=0;i<2;i++){
    const out=await flow.run({wallet,identity,hostname:HOST,confirmTerms:async()=>true});
    assert.equal(out.ready,false);
    assert.equal(out.steps.filter(s=>s.state==="unavailable").length,2);
  }
  assert.equal(wallet.calls.createSignature,1);
});

test("missing native grant evidence or receiving registration is never reported ready",async()=>{
  for(const [opts,missing] of [[{grant:false},"wallet_monthly_permission"],[{receiving:false},"receiving_registration"]]){
    const wallet=fixtureWallet("full"),identity=await identityOf(wallet),server=fakeServer(opts);
    const out=await setup(server,identity).flow.run({wallet,identity,hostname:HOST,confirmTerms:async()=>true});
    assert.equal(out.ready,false);assert.ok(out.missing.includes(missing));
  }
});

test("a sign-only paired wallet cannot receive: not ready and receipt sync is not attempted",async()=>{
  const wallet=fixtureWallet("sign_only"),identity=await identityOf(wallet),server=fakeServer();
  const {flow,log}=setup(server,identity);
  const out=await flow.run({wallet,supportedMethods:pairedMethods.sign_only,identity,hostname:HOST,confirmTerms:async()=>true});
  assert.equal(out.ready,false);assert.ok(out.missing.includes("wallet_receiving"));assert.equal(log.sync,0);
});

test("wrong network, missing API, declined signature and locked wallet fail closed",async()=>{
  for(const profile of ["testnet","missing_api","declines"]){
    const wallet=fixtureWallet(profile),server=fakeServer();
    const identity=profile==="missing_api"?"02"+"11".repeat(32):await identityOf(wallet);
    await assert.rejects(setup(server,identity).flow.run({wallet,identity,hostname:HOST,confirmTerms:async()=>true}));
    assert.equal(server.authority,null);
  }
  const locked=fixtureWallet("full",{unlockAfter:5});
  const out=await setup(fakeServer(),await identityOf(locked)).flow.run({wallet:locked,identity:await identityOf(locked),
    hostname:HOST,confirmTerms:async()=>true});
  assert.equal(out.ready,true);assert.equal(locked.calls.waitForAuthentication,1);
  await assert.rejects(walletCapabilities(fixtureWallet("locked_forever",{unlockAfter:1}),{timeoutMs:20}),/timed out/);
});

test("tampered or unexpected terms are refused before any wallet prompt",async()=>{
  const cases=[t=>({...t,monthly_limit_sats:30001}),t=>({...t,origin:"evil.example.com"}),
    t=>({...t,included_session:{account_key:"p|s",transaction_id:"1",opened_at:t.issued_at}}),
    t=>({...t,credits_refill:true}),t=>({...t,station_ids:["station-1","station-2"]}),
    t=>({...t,accept_before:t.issued_at}),t=>({...t,extra:1}),
    t=>({...t,period_policy:{...t.period_policy,fee_basis:"amount_only"}})];
  for(const mutate of cases){
    const wallet=fixtureWallet("full"),identity=await identityOf(wallet),server=fakeServer({mutate});
    await assert.rejects(setup(server,identity).flow.run({wallet,identity,hostname:HOST,confirmTerms:async()=>true}),
      /did not match/);
    assert.equal(wallet.calls.createSignature,0);assert.equal(server.authority,null);
  }
});

test("declining the displayed terms, a disabled station and a cancelled authority sign nothing",async()=>{
  let wallet=fixtureWallet("full"),identity=await identityOf(wallet),server=fakeServer();
  let out=await setup(server,identity).flow.run({wallet,identity,hostname:HOST,confirmTerms:async()=>false});
  assert.equal(out.ready,false);assert.equal(wallet.calls.createSignature,0);
  server=fakeServer({enabled:false});
  out=await setup(server,identity).flow.run({wallet,identity,hostname:HOST,confirmTerms:async()=>true});
  assert.equal(out.ready,false);assert.deepEqual(out.missing,["monthly_disabled"]);
  server=fakeServer();
  await setup(server,identity).flow.run({wallet,identity,hostname:HOST,confirmTerms:async()=>true});
  const result=await cancelMonthly({api:server.api(identity),wallet,identity,authorityId:"auth-1"});
  assert.deepEqual(result,{cancelled:true,walletPermissionRevoked:"not_verified"});
  const signatures=wallet.calls.createSignature;
  out=await setup(server,identity).flow.run({wallet,identity,hostname:HOST,confirmTerms:async()=>true});
  assert.equal(out.ready,false);assert.equal(wallet.calls.createSignature,signatures);
  assert.equal(out.steps.find(s=>s.id==="authority").state,"blocked");
});

test("a stale second tab surfaces a revision conflict instead of signing",async()=>{
  const wallet=fixtureWallet("full"),identity=await identityOf(wallet),server=fakeServer();
  const api=server.api(identity);
  const flow=new MonthlySetup({api:async(action,data)=>action==="monthly_challenge"?api(action,{...data,revision:data.revision-1}):api(action,data),
    signIn:async()=>identity,syncReceipts:async()=>{}});
  await assert.rejects(flow.run({wallet,identity,hostname:HOST,confirmTerms:async()=>true}),e=>e.code==="revision_conflict");
  assert.equal(wallet.calls.createSignature,0);
});

test("page visibility or sign-in changes abort before the monthly signature",async()=>{
  const wallet=fixtureWallet("full"),identity=await identityOf(wallet),server=fakeServer();
  let active=true;
  const flow=new MonthlySetup({api:async(a,d)=>{const r=await server.api(identity)(a,d);if(a==="monthly_challenge")active=false;return r;},
    signIn:async()=>identity,syncReceipts:async()=>{}});
  await assert.rejects(flow.run({wallet,identity,hostname:HOST,confirmTerms:async()=>true,
    assertActive:()=>{if(!active)throw Error("Page hidden");}}),/Page hidden/);
  assert.equal(wallet.calls.createSignature,0);
});

test("sign-in to a different identity than the connected wallet stops the flow",async()=>{
  const wallet=fixtureWallet("full"),server=fakeServer();
  const flow=new MonthlySetup({api:server.api(await identityOf(wallet)),signIn:async()=>"03"+"22".repeat(32),syncReceipts:async()=>{}});
  await assert.rejects(flow.run({wallet,identity:null,hostname:HOST,confirmTerms:async()=>true}),/identity changed/);
});

test("cancellation request must name this authority and identity",async()=>{
  const identity="02"+"11".repeat(32);
  const good={authority_id:"a",keyID:"a",protocolID:monthlyProtocol,revision:3,
    payload:canonical({version:4,action:"cancel_monthly_charging",authority_id:"a",driver_identity:identity,terms_hash:"cd".repeat(32)})};
  assert.equal(checkCancelChallenge(good,{identity,authorityId:"a"}).action,"cancel_monthly_charging");
  for(const bad of [{...good,keyID:"b"},{...good,payload:good.payload.replace("cancel_","renew_")},
    {...good,payload:canonical({...JSON.parse(good.payload),driver_identity:"03"+"11".repeat(32)})}])
    assert.throws(()=>checkCancelChallenge(bad,{identity,authorityId:"a"}));
});

test("challenge validation accepts Python isoformat microseconds and rejects expiry",()=>{
  const identity="02"+"11".repeat(32),now=Date.parse("2026-10-05T12:00:00Z");
  const terms={version:4,scope:"recurring_calendar_month_charging_including_driver_fees",network:"BSV mainnet",
    authority_id:"x",nonce:"ab".repeat(32),driver_identity:identity,operator_identity:"03"+"33".repeat(32),operator_address:"1",
    origin:HOST,station_ids:["station-1"],monthly_limit_sats:30000,period_policy:{policy_id:"p",wallet_version:"w",timezone:"UTC",
      booking_event:"b",fee_basis:"all_driver_paid_wallet_debits",evidence_ref:"e"},
    issued_at:"2026-10-05T12:00:00.123456+00:00",accept_before:"2026-10-05T12:10:00.123456+00:00",
    effective_at:"2026-10-05T12:00:00.123456+00:00",recurs_until_cancelled:true,
    collection_policy:"one_final_net_payment_per_session_on_closure",credits_refill:false,unused_carries_forward:false,
    conversion_policy:"freeze_configured_sat_per_aud_at_session_binding",included_session:null};
  const challenge={authority_id:"x",keyID:"x",protocolID:monthlyProtocol,revision:1,terms,
    payload:canonical({version:4,action:"authorise_monthly_charging",terms})};
  assert.equal(checkMonthlyChallenge(challenge,{hostname:HOST,identity,stations:["station-1"],now}).authority_id,"x");
  assert.throws(()=>checkMonthlyChallenge(challenge,{hostname:HOST,identity,stations:["station-1"],now:now+600200}));
});
