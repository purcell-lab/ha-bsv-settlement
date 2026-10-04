import test from "node:test";
import assert from "node:assert/strict";
import { PrivateKey, ProtoWallet, Signature } from "@bsv/sdk";
import { canonical, bytes, parseInvitation, signConsent, paymentAuthority, spendingScope, multiScope, derivedInvitation,hash } from "./model.js";

const operator = PrivateKey.fromRandom();
const driver = new ProtoWallet(PrivateKey.fromRandom());
const terms = {
  version:2, budget_id:"11111111-2222-4333-8444-555555555555",session_id:"fictional-session",
  transaction_id:"fictional-proxy-id",network:"BSV mainnet",
  operator_identity:operator.toPublicKey().toString(),operator_address:operator.toPublicKey().toAddress(),
  max_total_sats:1000,max_fee_sats:10,satoshis_per_aud:"100",
  pricing_rule:"Fictional test only",account_scope:"Entire session",
  import_price_entity:"sensor.demo_import",export_price_entity:"sensor.demo_export",
  created_at:new Date().toISOString(),expires_at:new Date(Date.now()+3600000).toISOString(),
  scope:spendingScope
};
function invitation(t = terms) {
  const payload = canonical([2,3].includes(t.version) ? {...t,payment_authority:t.payment_authority || paymentAuthority(t)} : t);
  return {version:1,payload,signature:operator.sign(bytes(payload)).toDER("hex")};
}
test("current-plus-future scope is explicit in the fresh wallet signature",async()=>{
 const initial={session_id:"current",transaction_id:"current-tx",
   opened_at:new Date(Date.now()-3600000).toISOString(),account:null,amount_sats:null};
 const t={...terms,version:3,scope:multiScope,session_mode:"multi_session",included_session:initial};
 const checked=parseInvitation(JSON.stringify(invitation(t)));
 const identity=(await driver.getPublicKey({identityKey:true})).publicKey;
 const receipt=await signConsent(driver,checked,identity);
 assert.equal(JSON.parse(receipt.payload).payment_authority.included_session_id,"current");
 assert.equal(JSON.parse(receipt.payload).payment_authority.max_total_sats_including_fees,1000);
 assert.throws(()=>parseInvitation(JSON.stringify(invitation({...t,
   payment_authority:paymentAuthority({...t,included_session:undefined})}))));
 for(const patch of [{opened_at:"invalid"},{session_id:""},{amount_sats:10}]){
   assert.throws(()=>parseInvitation(JSON.stringify(invitation({...t,included_session:{...initial,...patch}}))));
 }
});
test("closed current account must match its derived ticket",async()=>{
 const opened=new Date(Date.now()-3600000).toISOString();
 const a={session_id:"current",ocpp_transaction_id:"current-tx",opened_at:opened,
   ended_at:new Date(Date.now()-1000).toISOString(),currency:"AUD",
   import_kwh:13.38,export_kwh:1.53,net_amount_aud:"0.71"};
 const initial={session_id:"current",transaction_id:"current-tx",opened_at:opened,account:a,amount_sats:71};
 const p={...terms,version:3,scope:multiScope,session_mode:"multi_session",included_session:initial};
 const parent=parseInvitation(JSON.stringify(invitation(p)));
 const child={...terms,session_mode:"existing_session",session_id:"current",transaction_id:"current-tx",
   weekly_parent_hash:await hash(parent.invitation.payload),closed_session_review:{
     account:a,amount_sats:71,satoshis_per_aud:"100",accepted_flags:[],reason:"Explicit current account included"}};
 await derivedInvitation(invitation(child),parent,"current");
 await assert.rejects(derivedInvitation(invitation({...child,closed_session_review:{
   ...child.closed_session_review,account:{...a,import_kwh:99}}}),parent,"current"));
 for(const patch of [{amount_sats:1000},{account:{...a,session_id:"wrong"}},{account:{...a,import_kwh:-1}}])
   assert.throws(()=>parseInvitation(JSON.stringify(invitation({...p,included_session:{...initial,...patch}}))));
});
test("weekly root signature grants aggregate authority, not an individual reset",async()=>{
 const t={...terms,version:3,scope:multiScope,session_mode:"multi_session",
  expires_at:new Date(Date.now()+7*86400000).toISOString()};
 const parent=parseInvitation(JSON.stringify(invitation(t)));
 const identity=(await driver.getPublicKey({identityKey:true})).publicKey;
 const receipt=await signConsent(driver,parent,identity);
 assert.equal(receipt.version,3);
 const authority=JSON.parse(receipt.payload).payment_authority;
 assert.equal(authority.aggregate_limit,true);
 assert.equal(authority.credits_replenish_budget,false);
 assert.equal(authority.max_total_sats_including_fees,1000);
 assert.equal(authority.terminates_on_new_driver_registration,true);
 const childTerms={...terms,created_at:new Date().toISOString(),expires_at:t.expires_at,
  session_mode:"existing_session",weekly_parent_hash:await hash(parent.invitation.payload)};
 const child=await derivedInvitation(invitation(childTerms),parent,terms.session_id);
 await assert.rejects(signConsent(driver,child,identity),/standalone/);
 for(const patch of [{weekly_parent_hash:"ab".repeat(32)},{max_total_sats:1001},
   {satoshis_per_aud:"101"},{pricing_rule:"other"},{session_id:"wrong"}]){
  await assert.rejects(derivedInvitation(invitation({...childTerms,...patch}),parent,terms.session_id));
 }
 assert.throws(()=>parseInvitation(JSON.stringify(invitation({...t,expires_at:new Date(Date.now()+8*86400000).toISOString()}))));
});
test("operator signature and driver receipt verify with official SDK", async () => {
  const checked = parseInvitation(JSON.stringify(invitation()));
  const {publicKey} = await driver.getPublicKey({identityKey:true});
  const receipt = await signConsent(driver, checked, publicKey);
  assert.equal(receipt.driver_identity, publicKey);
  const payload = JSON.parse(receipt.payload);
  assert.equal(payload.version,2);
  assert.equal(payload.action,"authorise_one_session_spending");
  assert.deepEqual(payload.payment_authority,paymentAuthority(terms));
});
test("legacy terms remain readable but cannot silently become a spending approval", async () => {
  const old = {...terms,version:1,scope:"one_session_consent_only_no_payment_or_charger_authority"};
  const checked = parseInvitation(JSON.stringify(invitation(old)));
  const {publicKey} = await driver.getPublicKey({identityKey:true});
  await assert.rejects(signConsent(driver,checked,publicKey), /old invitation/);
});
test("operator-signed policy with altered scope, recipient or limits is rejected", () => {
  for (const patch of [{max_payments:2},{max_total_sats_including_fees:1001},
      {recipient_address:"other"},{wallet_transaction_permission_required:false},
      {satoshis_per_aud:"200"},{expires_at:"2099-01-01T00:00:00Z"}]) {
    assert.throws(()=>parseInvitation(JSON.stringify(invitation({
      ...terms,payment_authority:{...paymentAuthority(terms),...patch}
    }))));
  }
});
test("tampering, wrong address, expired and invalid fee limits rejected", () => {
  const item=invitation();item.payload=item.payload.replace('"max_total_sats":1000','"max_total_sats":9999');
  assert.throws(()=>parseInvitation(JSON.stringify(item)));
  for (const updates of [{operator_address:PrivateKey.fromRandom().toPublicKey().toAddress()},
      {max_fee_sats:1001},{max_total_sats:-1},{expires_at:new Date(1).toISOString()}]) {
    assert.throws(()=>parseInvitation(JSON.stringify(invitation({...terms,...updates}))));
  }
});
test("fee ceiling may equal total without increasing the total spending authority",()=>{
 const checked=parseInvitation(JSON.stringify(invitation({...terms,max_fee_sats:1000})));
 assert.equal(checked.terms.payment_authority.max_total_sats_including_fees,1000);
 assert.equal(checked.terms.payment_authority.max_fee_sats,1000);
});
test("changed wallet and rejected signature do not return consent", async () => {
  const checked=parseInvitation(JSON.stringify(invitation()));
  await assert.rejects(signConsent(driver,checked,operator.toPublicKey().toString()),/changed/);
  const key=(await driver.getPublicKey({identityKey:true})).publicKey;
  const deny={getPublicKey:driver.getPublicKey.bind(driver),createSignature:async()=>{throw Error("Denied");}};
  await assert.rejects(signConsent(deny,checked,key),/Denied/);
});
test("expiry during wallet prompt is rejected", async () => {
  const checked=parseInvitation(JSON.stringify(invitation({...terms,expires_at:new Date(Date.now()+50).toISOString()})));
  const key=(await driver.getPublicKey({identityKey:true})).publicKey;
  const slow={getPublicKey:driver.getPublicKey.bind(driver),createSignature:async args=>{
    await new Promise(resolve=>setTimeout(resolve,80));return driver.createSignature(args);
  }};
  await assert.rejects(signConsent(slow,checked,key),/expired/);
});
test("completed-account consent binds the account and supported warning",async()=>{
 const t={...terms,session_mode:"existing_session",closed_session_review:{
   account:{session_id:terms.session_id,ocpp_transaction_id:terms.transaction_id,currency:"AUD",
     ended_at:new Date(Date.now()-10000).toISOString(),import_kwh:"1.94",export_kwh:"0",net_amount_aud:"0.05",
     quality_flags:["import:energy_without_matching_state"]},
   accepted_flags:["import:energy_without_matching_state"],amount_sats:5,satoshis_per_aud:"100",
   reason:"Reviewed charger timing mismatch"
 }};
 const checked=parseInvitation(JSON.stringify(invitation(t)));
 const key=(await driver.getPublicKey({identityKey:true})).publicKey;
 assert.ok(await signConsent(driver,checked,key));
 for(const patch of [{amount_sats:0},{satoshis_per_aud:"200"},{reason:""},
   {accepted_flags:["missing_prices"]},{account:{...t.closed_session_review.account,net_amount_aud:"-1"}}]){
   assert.throws(()=>parseInvitation(JSON.stringify(invitation({...t,closed_session_review:{...t.closed_session_review,...patch}}))));
 }
 const changed=invitation(t);changed.payload=changed.payload.replace("timing mismatch","metering mismatch");
 assert.throws(()=>parseInvitation(JSON.stringify(changed)),/signature/);
});
