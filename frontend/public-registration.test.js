import test from "node:test";
import assert from "node:assert/strict";
import {registrationOpenRequest} from "./public-registration.js";

const fresh=()=>({state:"awaiting_driver_consent",terms:{
  budget_id:"test-budget",version:3,session_mode:"multi_session"},
  invitation:{payload:'{"budget_id":"test-budget"}'}});
const status=()=>({...fresh(),public_registration:{context_hash:"current-context"}});

test("fresh creation needs an exact status read before opening",async()=>{
  const created=fresh(),reads=[];
  const request=await registrationOpenRequest(created,async data=>{
    reads.push(data);return status();
  });
  assert.deepEqual(reads,[{budget_id:"test-budget"}]);
  assert.equal(created.public_registration,undefined);
  assert.equal(request.budget_id,"test-budget");
  assert.equal(request.expected_context_hash,"current-context");
  assert.match(request.expected_invitation_hash,/^[0-9a-f]{64}$/);
  assert.equal(request.confirm_public_registration,true);
});
test("stale create metadata is ignored",async()=>{
  const created={...fresh(),public_registration:{context_hash:"stale"}};
  assert.equal((await registrationOpenRequest(created,async()=>status())).expected_context_hash,"current-context");
});
for(const [name,mutate] of [
  ["changed ID",s=>{s.terms.budget_id="other";}],
  ["approved",s=>{s.state="spending_authorised_wallet_permission_required";}],
  ["receipt",s=>{s.receipt={};}],
  ["collection",s=>{s.collection={state:"held"};}],
  ["credit",s=>{s.automatic_credit={txid:"existing"};}],
  ["bound",s=>{s.binding={session_id:"existing"};}],
  ["wrong version",s=>{s.terms.version=2;}],
  ["wrong scope",s=>{s.terms.session_mode="existing_session";}],
  ["missing context",s=>{delete s.public_registration;}],
  ["empty context",s=>{s.public_registration.context_hash="";}],
])test(`${name} fails closed`,async()=>{
  const current=status();mutate(current);
  await assert.rejects(registrationOpenRequest(fresh(),async()=>current));
});
test("missing status response fails closed",async()=>{
  await assert.rejects(registrationOpenRequest(fresh(),async()=>undefined));
});
test("failed read is not retried or replaced",async()=>{
  let calls=0;
  await assert.rejects(registrationOpenRequest(fresh(),async()=>{
    calls++;throw Error("Unavailable");
  }),/Unavailable/);
  assert.equal(calls,1);
});
test("missing creation ID does not read",async()=>{
  await assert.rejects(registrationOpenRequest({},async()=>assert.fail("Unexpected read")));
});
