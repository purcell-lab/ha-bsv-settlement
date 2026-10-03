import test from "node:test";
import assert from "node:assert/strict";
import {driverView,showOngoingOverview,ongoingCreditMessage} from "./view.js";
test("waived charge is terminal, not paid or ready to retry",()=>{
 const v=driverView({hasInvitation:true,accepted:true,state:"waived"});
 assert.equal(v.stage,"settled");
 assert.match(v.title,/waived/);assert.match(v.subtitle,/does not refund/);
 assert.equal(v.reconnect,undefined);
});
test("post-session approval explains immediate collection without starting a session",()=>{
 const v=driverView({hasInvitation:true,closedSession:true});
 assert.equal(v.title,"Review your completed session");
 assert.match(v.subtitle,/immediately/);
});
test("confirmed credit is not wallet acceptance until import succeeds",()=>{
 const args={hasInvitation:true,accepted:true,registered:true,credit:true,state:"provider_confirmed"};
 assert.equal(driverView(args).reconnect,"Receive credit in wallet");
 assert.equal(driverView(args).stage,"approved");
 assert.equal(driverView({...args,imported:true}).stage,"settled");
});
test("wrong or incomplete registration remains visible",()=>{
 assert.equal(driverView({hasInvitation:true,accepted:true}).reconnect,"Register receiving wallet");
});
test("unknown broadcast never offers another payment",()=>{
 const v=driverView({hasInvitation:true,accepted:true,registered:true,state:"broadcast_unknown"});
 assert.match(v.subtitle,/Do not send another/);
});
test("blocked settlement does not imply payment readiness",()=>{
 assert.equal(driverView({hasInvitation:true,accepted:true,registered:true,state:"collection_blocked"}).title,"Settlement needs attention");
});
test("held attempts and reviewed recovery are not normal automatic collection",()=>{
 const base={hasInvitation:true,accepted:true,registered:true,connected:true};
 assert.equal(driverView({...base,state:"wallet_attempt_reserved"}).title,"Collection needs review");
 assert.equal(driverView({...base,state:"wallet_attempt_reserved"}).reconnect,undefined);
 assert.equal(driverView({...base,state:"recovery_ready"}).reconnect,"Review and resume collection");
});
const ongoingBase={accepted:true,ongoingCount:1,sessionMode:"next_session_reservation",
  binding:null,state:"waiting_for_operator_binding"};
test("only an accepted, unbound future reservation shows the ongoing overview",()=>{
 assert.equal(showOngoingOverview(ongoingBase),true);
 for(const changes of [
   {accepted:false},{ongoingCount:0},{sessionMode:"existing_session"},
   {sessionMode:undefined},{binding:{session_id:"fictional-session"}},
 ])assert.equal(showOngoingOverview({...ongoingBase,...changes}),false);
});
test("existing-session approval without a binding keeps recovery primary",()=>{
 const args={...ongoingBase,sessionMode:"existing_session",state:"recovery_ready"};
 assert.equal(showOngoingOverview(args),false);
 assert.equal(driverView({...args,hasInvitation:true,registered:true}).reconnect,"Review and resume collection");
});
for(const state of ["recovery_ready","wallet_attempt_reserved","ready","waiting_for_session_end",
 "submitted","provider_unconfirmed","provider_confirmed","broadcast_unknown",
 "collection_blocked","no_payment_due",null]){
 test(`ongoing credits cannot override the current collection state ${state}`,()=>{
   for(const sessionMode of ["existing_session","next_session_reservation",undefined]){
     assert.equal(showOngoingOverview({...ongoingBase,sessionMode,state}),false);
   }
 });
}
test("ongoing session wording describes operator credit, not driver collection",()=>{
 assert.equal(ongoingCreditMessage("waiting_for_session_end"),
   "Session in progress. Any operator credit will be checked when the session ends.");
 assert.doesNotMatch(ongoingCreditMessage("waiting_for_session_end"),/collection|armed/i);
 assert.equal(ongoingCreditMessage("provider_confirmed",true),"Receipt accepted by wallet");
 assert.match(ongoingCreditMessage("provider_confirmed"),/chain provider/);
 assert.match(ongoingCreditMessage("unexpected_state"),/needs review/);
});
