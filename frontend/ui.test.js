import test from "node:test";
import assert from "node:assert/strict";
import {sessionStatus,num,esc} from "./ui.js";
const s={session_id:"new-session",net_cost_aud:-1.24};
test("zero-balance closure is not labelled a waived charge",()=>{
 const view=sessionStatus({...s,net_cost_aud:0}, {closed_sessions:[
   {session_id:s.session_id,state:"closed_zero",net_amount_aud:"0",reason:"No payment due"}]});
 assert.equal(view.label,"Closed: no payment due");
 assert.doesNotMatch(view.detail,/waived/i);
});
test("waived ledger does not tell the operator to continue collection",()=>{
 const h={session_payments:[{session_id:s.session_id,state:"waived"}],
   closed_sessions:[{session_id:s.session_id,state:"waived",net_amount_aud:"0.76",received_funds:{amount_sats:76}}]};
 const view=sessionStatus({...s,net_cost_aud:0.76},h);
 assert.equal(view.label,"Waived");assert.match(view.detail,/76 sat received remains unallocated/);
 assert.doesNotMatch(view.detail,/Continue/);
});
test("waiver is not paid and a changed closed amount requires review",()=>{
 const ended={...s,net_cost_aud:0.05,ended_at:"2026-10-02T00:00:00Z"};
 const h={closed_sessions:[{session_id:s.session_id,state:"waived",net_amount_aud:"0.05",reason:"Operator goodwill waiver"}]};
 assert.equal(sessionStatus(ended,h).label,"Waived");
 assert.equal(sessionStatus({...ended,net_cost_aud:0.06},h).label,"Closed account changed");
});
test("closed timing warning needs review unless a reviewed consent exists",()=>{
 const ended={...s,net_cost_aud:0.05,ended_at:"2026-10-02T00:00:00Z",
   quality_flags:["import:energy_without_matching_state"]};
 assert.equal(sessionStatus(ended,{}).label,"Data review required");
 const h={driver_approvals:[{session_id:s.session_id,state:"awaiting_driver_consent",reviewed_closed_account:true,
   expires_at:new Date(Date.now()+60000).toISOString()}]};
 assert.equal(sessionStatus(ended,h).label,"Awaiting consent");
});
test("previous session approval cannot imply readiness",()=>{
 assert.equal(sessionStatus(s,{driver_approvals:[{session_id:"old",approved:true}]}).label,"Driver approval needed");
});
test("submitted and confirmed payments override metering not_requested",()=>{
 const ended={...s,ended_at:"2026-10-02T00:00:00Z",payment_state:"not_requested"};
 const health={session_payments:[{session_id:s.session_id,state:"provider_unconfirmed",txid:"abc"}]};
 assert.equal(sessionStatus(ended,health).label,"Awaiting block confirmation");
 health.session_payments[0].state="provider_confirmed";
 assert.equal(sessionStatus(ended,health).label,"Confirmed on chain");
});
test("credit readiness needs live approval, policy and receiving registration",()=>{
 const now=Date.now(),a={session_id:s.session_id,version:2,approved:true,state:"spending_authorised_wallet_permission_required",
   expires_at:new Date(now+10000).toISOString(),created_at:new Date(now-5000).toISOString(),credit_terms:true};
 const h={driver_approvals:[a],automatic_credit:{enabled:true,enabled_at:new Date(now-10000).toISOString()}};
 assert.equal(sessionStatus(s,h,now).label,"Credit not ready");
 a.receiving_registered_at=new Date(now-1000).toISOString();
 assert.equal(sessionStatus(s,h,now).label,"Automatic credit checks enabled");
 a.expires_at=new Date(now-1).toISOString();
 assert.equal(sessionStatus(s,h,now).label,"Driver approval needed");
});
test("missing numbers remain unavailable and HTML values are escaped",()=>{
 assert.equal(num(null),"Unavailable");assert.equal(num("unavailable"),"Unavailable");
 assert.equal(num(0),"0");assert.equal(esc('<img onerror="x">'),"&lt;img onerror=&quot;x&quot;&gt;");
});
test("ongoing operator credit does not imply driver debit authority",()=>{
 const h={ongoing_credit:{effective:true,sessions:[{session_id:s.session_id,recipient_address:"registered-wallet"}]}};
 assert.equal(sessionStatus(s,h).label,"Ongoing credit assigned");
 assert.equal(sessionStatus({...s,net_cost_aud:1},h).label,"Driver approval needed");
 h.ongoing_credit.effective=false;
 assert.equal(sessionStatus(s,h).label,"Ongoing credits paused");
});
