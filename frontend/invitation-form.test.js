import test from "node:test";
import assert from "node:assert/strict";
import {invitationDefaults,validateInvitationLimits,sameInvitationScope,pendingReplacement} from "./invitation-form.js";
test("default fee cap is 1000 within an unchanged 1000 total",()=>{
 assert.equal(invitationDefaults.fee,1000);assert.equal(invitationDefaults.total,1000);
 assert.equal(validateInvitationLimits(invitationDefaults),"");
});
test("invalid and over-total fee settings are rejected before a service call",()=>{
 for(const changes of [{total:""},{fee:""},{fee:1001},{minutes:0},{total:100.5},{total:999}]){
   assert.notEqual(validateInvitationLimits({...invitationDefaults,...changes}),"");
 }
 assert.equal(validateInvitationLimits({total:"1000",fee:"1000",minutes:"720"}),"");
});
const pending={state:"awaiting_driver_consent",terms:{budget_id:"fictional-id",session_mode:"next_session_reservation"},
 invitation:{payload:"fictional signed terms"}};
test("replacement targets only the displayed matching scope",()=>{
 assert.equal(sameInvitationScope(pending,"next"),true);
 assert.equal(sameInvitationScope(pending,"current",{session_id:"new"}),false);
 assert.equal(sameInvitationScope({...pending,binding:{session_id:"old"}},"next"),false);
 assert.equal(sameInvitationScope({...pending,state:"revoked"},"next"),false);
});
test("replacement carries exact old ID and hash, never silently edits signed consent",async()=>{
 const p=await pendingReplacement(pending);
 assert.equal(p.replace_pending_budget_id,"fictional-id");
 assert.match(p.expected_invitation_hash,/^[a-f0-9]{64}$/);
 assert.equal(p.confirm_replace_pending,true);
 for(const change of [{receipt:{}},{collection:{}},{state:"spending_authorised_wallet_permission_required"}]){
   await assert.rejects(pendingReplacement({...pending,...change}));
 }
});
