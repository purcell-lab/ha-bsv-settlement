import test from "node:test";
import assert from "node:assert/strict";
import {awaitingApproval,approvalUrl,pendingForSession} from "./approval-qr.js";
const id="11111111-1111-4111-8111-111111111111",token="x".repeat(43),clock=Date.now();
const pending=()=>({state:"awaiting_driver_consent",terms:{budget_id:id,expires_at:new Date(clock+60000).toISOString()}});
test("QR requires a current pending approval, never terminal or paid",()=>{
  assert.equal(awaitingApproval(pending(),clock),true);
  for(const state of ["expired","revoked","spending_authorised_wallet_permission_required"])
    assert.equal(awaitingApproval({...pending(),state},clock),false);
  for(const change of [{receipt:{}},{collection:{txid:"paid"}},{automatic_credit:{txid:"paid"}},{terms:{expires_at:"bad"}}])
    assert.equal(awaitingApproval({...pending(),...change},clock),false);
  assert.equal(awaitingApproval(pending(),clock+60001),false);
});
test("approval URL is same-origin with exact bound budget and fragment capability",()=>{
  const url=approvalUrl(`#budget=${id}&token=${token}`,id,"https://operator.example");
  assert.equal(new URL(url).pathname,"/bsv_settlement/driver/index.html");
  assert.equal(new URL(url).search,"");
  for(const f of ["https://evil.example",`#budget=other&token=${token}`,`#budget=${id}&token=bad`,
    `#budget=${id}&token=${token}&token=${token}`])
    assert.equal(approvalUrl(f,id,"https://operator.example"),null);
});
test("overview chooses only the current open session's pending invitation",()=>{
  const session={session_id:"now"},a={budget_id:id,session_id:"now",state:"awaiting_driver_consent",
    approved:false,expires_at:new Date(clock+60000).toISOString()};
  assert.equal(pendingForSession(session,{driver_approvals:[{...a,session_id:"old"},a]},clock),a);
  assert.equal(pendingForSession({...session,ended_at:"closed"},{driver_approvals:[a]},clock),null);
  assert.equal(pendingForSession(session,{driver_approvals:[{...a,approved:true}]},clock),null);
});
