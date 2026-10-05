import test from "node:test";
import assert from "node:assert/strict";
import {nextAction,sessionJourney,renderSteps} from "./journey.js";

test("one authorisation step combines connection and budget approval",()=>{
 const j=sessionJourney({accepted:false});
 assert.equal(j.step,0);assert.match(j.hint,/connects your wallet and authorises this budget/);
 assert.match(j.hint,/Charging starts separately; no funds are reserved/);
 const steps=renderSteps(0);
 assert.equal((steps.match(/<li /g)||[]).length,3);
 assert.equal((steps.match(/aria-current/g)||[]).length,1);
 assert.match(steps,/Authorise budget/);
});
test("only one eligible primary action, with uncertain reports and saved signatures first",()=>{
 assert.equal(nextAction({approve:{enabled:true},sync:{enabled:true},save:{enabled:true}}),"save");
 assert.equal(nextAction({approve:{enabled:true},report:{enabled:true}}),"report");
 assert.equal(nextAction({approve:{enabled:true},connect:{enabled:true}}),"approve");
 assert.equal(nextAction({register:{enabled:true},connect:{enabled:true}}),"register");
 assert.equal(nextAction({connect:{enabled:true,primary:false},approve:{enabled:false}}),null);
 assert.equal(nextAction({pair:{enabled:true},refresh:{enabled:true}}),null);
});
test("sign-in availability never makes spending available",()=>{
 assert.equal(nextAction({connect:{enabled:true},approve:{enabled:false}}),"connect");
});
test("closed-session approval discloses immediate collection",()=>{
 assert.match(sessionJourney({ended:true}).hint,/can collect this completed account now/);
});
test("expired terms do not offer an approval path",()=>{
 const j=sessionJourney({expired:true});assert.match(j.title,/expired/);assert.match(j.hint,/No new payment/);
});
for(const state of ["provider_unconfirmed","submitted","broadcast_unknown","wallet_attempt_reserved","collection_blocked"]){
 test(`${state} never implies a successful payment or new payment permission`,()=>{
  const j=sessionJourney({accepted:true,state,ended:true});
  assert.equal(j.step,2);assert.match(j.hint,/Do not|do not/);assert.doesNotMatch(j.title,/received|Payment confirmed/);
 });
}
test("confirmed credit and accepted receipt remain separate",()=>{
 const args={accepted:true,credit:true,state:"provider_confirmed"};
 assert.equal(sessionJourney(args).title,"Credit confirmed");
 assert.equal(sessionJourney({...args,imported:true}).title,"Credit received");
 assert.match(sessionJourney(args).hint,/does not send another payment/);
});
test("waiver is not a refund and is not a payment",()=>{
 const j=sessionJourney({state:"waived"});assert.equal(j.title,"Charge waived");assert.match(j.hint,/not a refund/);
});
test("active session guidance retains connected-wallet requirement",()=>{
 assert.match(sessionJourney({accepted:true}).hint,/page and wallet available/);
});
test("unknown or failed collection stays visibly paused",()=>{
 assert.match(sessionJourney({accepted:true,failure:true,ended:true}).hint,/paused for safety/);
});
