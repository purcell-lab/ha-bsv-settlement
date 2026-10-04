import test from "node:test";
import assert from "node:assert/strict";
import {ownerActionRows} from "./owner-actions.js";
const now=Date.parse("2026-10-04T13:00:00Z");
const session={session_id:"session-one",ocpp_transaction_id:"one",net_cost_aud:0.24,ended_at:"2026-10-04T11:00:00Z"};
const collection=(state,extra={})=>({session_id:session.session_id,direction:"driver_to_operator",source:"driver",budget_id:"original-budget",amount_sats:24,state,...extra});
const view=(row,sessions=[session],extra={})=>ownerActionRows({session_payments:[row],...extra},sessions,now)[0];
const ids=v=>v.actions.map(a=>a.id);
test("each closed session gets exact consent and waiver entry point",()=>{
 const rows=ownerActionRows({},[session,{...session,session_id:"session-two"}],now);
 assert.equal(rows.length,2);
 for(const v of rows)assert.deepEqual(ids(v),["closure"]);
 assert.notEqual(rows[0].row.session_id,rows[1].row.session_id);
});
test("existing collection uses original budget not a newer driver approval",()=>{
 const v=view(collection("ready"),[],{driver_approvals:[{session_id:"new-session",budget_id:"new-driver"}]});
 assert.equal(v.budgetId,"original-budget");assert.deepEqual(ids(v),["approval","waiver"]);
});
test("expired ready collection offers inspection and guarded waiver, not replacement",()=>{
 const v=view(collection("ready",{expires_at:"2026-10-03T00:00:00Z"}));
 assert.equal(v.title,"Approval expired");assert.deepEqual(ids(v),["approval","waiver"]);
});
test("signed uncertain and submitted collections never offer new consent or waiver",()=>{
 for(const state of ["claimed","submission_authorised","broadcast_unknown","provider_unconfirmed","submitted"]){
  const v=view(collection(state,{txid:state==="broadcast_unknown"?"a".repeat(64):undefined}));
  assert.deepEqual(ids(v),["check","recovery"]);
  assert.equal(v.complete,false);
 }
});
test("pre-signing hold exposes recovery review, not one-click release",()=>{
 assert.deepEqual(ids(view(collection("wallet_attempt_reserved"))),["check","recovery"]);
 assert.deepEqual(ids(view(collection("recovery_ready"))),["check","recovery","approval"]);
});
test("terminal rows are separated and confirmation requires a transaction",()=>{
 for(const state of ["waived","closed_zero","provider_confirmed"]){
  const v=view(collection(state,{txid:state==="provider_confirmed"?"a".repeat(64):undefined}));
  assert.equal(v.complete,true);assert.deepEqual(ids(v),[]);
 }
 assert.equal(view(collection("provider_confirmed")).complete,false);
});
test("owner queue excludes driver credits and retains historical owner rows",()=>{
 const h={session_payments:[collection("ready")],automatic_credit:{payments:[{session_id:"credit",state:"provider_confirmed",txid:"b".repeat(64)}]}};
 assert.equal(ownerActionRows(h,[],now).length,1);
});
test("manual review is exact and old payloads cannot target the latest review accidentally",()=>{
 const r=collection("awaiting_driver_payment",{source:"manual",budget_id:undefined,review_id:"saved-review"});
 assert.deepEqual(ids(view(r)),["manual","waiver"]);
 assert.deepEqual(ids(view({...r,review_id:undefined})),[]);
});
test("finished records follow unfinished ones and open sessions cannot be closed",()=>{
 const h={session_payments:[collection("waived"),collection("ready",{session_id:"other"})]};
 const rows=ownerActionRows(h,[session],now);assert.equal(rows[0].row.session_id,"other");
 assert.deepEqual(ids(ownerActionRows({},[{...session,ended_at:null}],now)[0]),["drivers"]);
});
test("known estimated tariffs do not generate extra review blockers",()=>{
 const s={...session,quality_flags:["import:estimated_tariff"]};
 const v=ownerActionRows({},[s],now)[0];
 assert.notEqual(v.title,"Data review required");assert.deepEqual(ids(v),["closure"]);
});
