import test from "node:test";
import assert from "node:assert/strict";
import {sessionStatus,num,esc,provisionalSats,provisionalDisplay} from "./ui.js";
import {qualityFlags,warningMessage} from "./quality.js";
test("provisional sat amounts use exact half-up rounding and fixed session rates",()=>{
 assert.equal(provisionalSats({net_cost_aud:"-0.38"},{},{state:"100"}).sats,38);
 assert.equal(provisionalSats({net_cost_aud:"0.29"},{},{state:"50"}).sats,15);
 assert.equal(provisionalSats({net_cost_aud:"0"},{},{state:"100"}).sats,0);
 const session={session_id:"s",net_cost_aud:"-1.24"};
 const health={ongoing_credit:{sessions:[{session_id:"s",satoshis_per_aud:"100"}]}};
 assert.deepEqual(provisionalSats(session,health,{state:"500"}),{sats:124,rate:100,fixed:true});
 for(const value of [null,undefined,"unknown","",true,"-1","0"])
  assert.equal(provisionalSats(session,{},{state:value}).sats,null);
 for(const value of [null,undefined,"",true,"bad"])
  assert.equal(provisionalSats({net_cost_aud:value},{},{state:"100"}).sats,null);
 assert.equal(provisionalSats(session,{driver_approvals:[{session_id:"s",approved:true,satoshis_per_aud:"80"}]},{state:"100"}).sats,99);
});
test("missing account has neutral direction and preserves healthy conversion",()=>{
 for(const value of [null,undefined,"","bad",true]){
  const session={net_cost_aud:value,quality_flags:["import:overlapping_tariff_periods"]};
  const view=provisionalDisplay(session,{}, {state:"100"});
  assert.equal(view.label,"Session amount unavailable");
  assert.equal(view.value,"Unavailable");
  assert.equal(view.rate,100);
  assert.equal(view.reason,"Tariff reconciliation required.");
  assert.doesNotMatch(view.value,/sat/);
  assert.equal(sessionStatus(session,{}).label,"Session amount unavailable");
 }
});
test("provisional display distinguishes charge credit zero and missing conversion",()=>{
 for(const [amount,label,value] of [[.2,"Provisional driver charge","20 sat"],
   [-.2,"Provisional credit to driver","20 sat"],[0,"Provisional session balance","0 sat"]]){
  const v=provisionalDisplay({net_cost_aud:amount},{},{state:"100"});
  assert.equal(v.label,label);assert.equal(v.value,value);assert.equal(v.reason,"");
 }
 const noRate=provisionalDisplay({net_cost_aud:.2},{},{state:"unavailable"});
 assert.equal(noRate.label,"Provisional driver charge");
 assert.equal(noRate.reason,"Conversion rate unavailable.");
 assert.equal(provisionalDisplay({net_cost_aud:null},{},{state:"100"}).reason,"Session pricing is incomplete.");
});
test("known payment remains visible when a later recorder calculation is unavailable",()=>{
 const session={session_id:"paid",net_cost_aud:null,quality_flags:["import:missing_tariff"]};
 const view=sessionStatus(session,{session_payments:[
   {session_id:"paid",state:"provider_confirmed",txid:"existing"}]});
 assert.equal(view.label,"Confirmed on chain");
 assert.equal(view.payment.txid,"existing");
});
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
test("closed timing warning does not veto settlement or existing consent",()=>{
 const ended={...s,net_cost_aud:0.05,ended_at:"2026-10-02T00:00:00Z",
   quality_flags:["import:energy_without_matching_state"]};
 assert.notEqual(sessionStatus(ended,{}).label,"Data review required");
 const h={driver_approvals:[{session_id:s.session_id,state:"awaiting_driver_consent",reviewed_closed_account:true,
   expires_at:new Date(Date.now()+60000).toISOString()}]};
 assert.equal(sessionStatus(ended,h).label,"Awaiting consent");
});
test("known quality flags are disclosed, while unknown or incomplete metering still blocks",()=>{
 const flags=["export:energy_without_matching_state","interval_energy_allocation_estimated","meter_reset"];
 assert.deepEqual(qualityFlags(flags).blockers,["meter_reset"]);
 assert.match(warningMessage(flags),/Export energy/);
 assert.match(warningMessage(flags),/allocation/);
 assert.equal(sessionStatus({...s,ended_at:"2026-10-02T00:00:00Z",quality_flags:["meter_reset"]},{}).label,"Data review required");
});
test("estimated import and export tariffs warn without requiring data review",()=>{
 const flags=["import:estimated_tariff","export:estimated_tariff"];
 assert.deepEqual(qualityFlags(flags).blockers,[]);
 assert.match(warningMessage(flags),/estimated buy rates/);
 assert.match(warningMessage(flags),/estimated sell rates/);
 const ended={...s,ended_at:"2026-10-04T12:44:23Z",quality_flags:flags};
 assert.notEqual(sessionStatus(ended,{}).label,"Data review required");
 for(const flag of ["import:missing_tariff","export:overlapping_tariff_periods",
   "export:missing_counter_baseline","unknown_quality"]){
  assert.deepEqual(qualityFlags([...flags,flag]).blockers,[flag]);
  assert.equal(sessionStatus({...ended,quality_flags:[...flags,flag]},{}).label,"Data review required");
 }
});
test("state mismatch preserves automatic credit readiness but never invents approval",()=>{
 const ended={...s,ended_at:"2026-10-02T00:00:00Z",quality_flags:["export:energy_without_matching_state"]};
 const h={ongoing_credit:{effective:true,sessions:[{session_id:s.session_id,recipient_address:"fictional"}]}};
 assert.equal(sessionStatus(ended,h).label,"Ongoing credit assigned");
 assert.equal(sessionStatus(ended,{}).label,"Review needed");
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
