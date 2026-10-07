import test from "node:test";
import assert from "node:assert/strict";
import {authorisationRows,approvePublicBudget} from "./portal-setup.js";
import {PrivateKey,ProtoWallet} from "@bsv/sdk";
import {canonical,bytes,parseInvitation,paymentAuthority,multiScope} from "./model.js";

function fixture(){
  const operator=new PrivateKey(123),wallet=new ProtoWallet(new PrivateKey(456));
  wallet.getNetwork=async()=>({network:"mainnet"});
  const terms={version:3,scope:multiScope,budget_id:"11111111-2222-4333-8444-555555555555",
    session_id:"fictional",transaction_id:"fictional",network:"BSV mainnet",session_mode:"multi_session",
    operator_identity:operator.toPublicKey().toString(),operator_address:operator.toPublicKey().toAddress(),
    max_total_sats:1000,max_fee_sats:1000,satoshis_per_aud:"100",pricing_rule:"Dynamic",account_scope:"Future sessions",
    import_price_entity:"sensor.import",export_price_entity:"sensor.export",
    created_at:new Date().toISOString(),expires_at:new Date(Date.now()+7*86400000).toISOString(),
    credit_receiving:{protocolID:[2,"3241645161d8"],derivationPrefix:"YWJj",derivationSuffix:"ZGVm"}};
  terms.payment_authority=paymentAuthority(terms);
  const payload=canonical(terms),invitation={version:1,payload,signature:operator.sign(bytes(payload)).toDER("hex")};
  const checked=parseInvitation(JSON.stringify(invitation));
  const prices={valid:true,...Object.fromEntries(["import","export"].map(k=>[k,{available:true,aud_per_kwh:"0.1",
    start:new Date(Date.now()-60000).toISOString(),end:new Date(Date.now()+300000).toISOString()}]))};
  return {wallet,checked,read:async()=>({invitation,state:"awaiting_driver_consent",prices})};
}
test("one journey signs the budget and receiving proof, never a payment",async()=>{
  const f=fixture(),identity=(await f.wallet.getPublicKey({identityKey:true})).publicKey,calls=[];
  f.wallet.createAction=f.wallet.signAction=()=>{throw Error("No payment allowed");};
  const result=await approvePublicBudget({...f,candidate:f.wallet,identity,origin:"https://charging.example.com",assertActive(){},
    approve:async receipt=>{calls.push("approve");assert.equal(receipt.driver_identity,identity);
      assert.equal(JSON.parse(receipt.payload).payment_authority.max_total_sats_including_fees,1000);
      return {state:"spending_authorised_wallet_permission_required",driver_identity:identity,automatic_credit_enabled:true,
        private_link_fragment:`#budget=${receipt.budget_id}&token=${"x".repeat(43)}`};},
    driverApi:async action=>{calls.push(action);return {state:"credit_destination_registered"};}});
  assert.deepEqual(calls,["approve","register_credit_destination"]);
  assert.equal(new URL(result.url).origin,"https://charging.example.com");
  assert.equal(result.receivingError,null);
});
test("ambiguous saving never retries or proceeds to receiving registration",async()=>{
  const f=fixture(),identity=(await f.wallet.getPublicKey({identityKey:true})).publicKey;
  let submissions=0;
  await assert.rejects(approvePublicBudget({...f,candidate:f.wallet,identity,origin:"https://charging.example.com",assertActive(){},
    approve:async()=>{submissions++;throw Error("Network interrupted");},
    driverApi(){throw Error("Must not register");}}),/saving is not confirmed/);
  assert.equal(submissions,1);
});

test("identity, connection, budget and wallet-native spending are independent",()=>{
  const rows=authorisationRows({identity:"verified",connected:false,approvals:[]});
  assert.deepEqual(rows.map(r=>r.ok),[true,false,false,false,false,false]);
  const complete=authorisationRows({identity:"verified",connected:true,supported:["createAction","signAction"],
    approvals:[{spending_active:true,limit_sats:1000,expires_at:"2099-01-01T00:00:00Z",receiving_registered:true}]});
  assert.deepEqual(complete.map(r=>r.ok),[true,true,true,true,true,false]);
  assert.match(complete.at(-1).text,/may still ask/);
});
test("expired, superseded and exhausted approvals do not get a budget check",()=>{
  for(const state of ["expired","superseded","exhausted"]){
    const rows=authorisationRows({identity:"driver",connected:true,paused:true,
      approvals:[{state,spending_active:false,receiving_registered:true}]});
    assert.equal(rows[2].ok,false);assert.equal(rows[4].ok,false);
  }
});
test("changed invitation stops before wallet signing or submitting",async()=>{
  let writes=0;
  await assert.rejects(approvePublicBudget({
    checked:{invitation:{payload:"reviewed"}},assertActive(){},
    read:async()=>({invitation:{payload:"replacement"},state:"awaiting_driver_consent"}),
    candidate:{createSignature(){writes++;}},approve(){writes++;}
  }),/invitation changed/);
  assert.equal(writes,0);
});
test("missing live tariffs cannot sign spending terms",async()=>{
  let writes=0;
  await assert.rejects(approvePublicBudget({
    checked:{invitation:{payload:"reviewed"}},assertActive(){},
    read:async()=>({invitation:{payload:"reviewed"},state:"awaiting_driver_consent",prices:{valid:false}}),
    candidate:{createSignature(){writes++;}},approve(){writes++;}
  }),/rates are unavailable/);
  assert.equal(writes,0);
});
test("hidden or expired context stops before any read",async()=>{
  let reads=0;
  await assert.rejects(approvePublicBudget({assertActive(){throw Error("Context ended");},
    read(){reads++;}}),/Context ended/);
  assert.equal(reads,0);
});
