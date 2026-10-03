import test from "node:test";
import assert from "node:assert/strict";
import {settlementRows,paymentStatus} from "./payment-status.js";
const now=Date.now(),s={session_id:"s",ocpp_transaction_id:"tx",ended_at:"closed",net_cost_aud:0.19};
const p={session_id:"s",direction:"driver_to_operator",amount_sats:19,state:"ready",expires_at:new Date(now+60000).toISOString()};
test("quoted debit overrides no-credit route and gives the requested wording",()=>{
 const rows=settlementRows({ongoing_credit:{sessions:[{session_id:"s",state:"no_operator_credit"}]},session_payments:[p]});
 assert.equal(rows.length,1);
 assert.deepEqual([paymentStatus(rows[0],[s],now).title,paymentStatus(rows[0],[s],now).detail],
  ["Driver payment due: 19 sat","Awaiting wallet collection."]);
});
test("quote, not current conversion or net estimate, controls payment amount",()=>{
 assert.equal(paymentStatus({...p,amount_sats:27},[{...s,net_cost_aud:99}],now).title,"Driver payment due: 27 sat");
});
test("missing amount is not fabricated as zero or calculated from mutable price",()=>{
 for(const amount_sats of [null,undefined,"19",NaN,-1]){
  const r=paymentStatus({...p,amount_sats},[s],now);
  assert.equal(r.title,"Driver payment due");assert.match(r.detail,/unavailable/);
 }
});
test("closed zero and unknown balances are distinct",()=>{
 const row={session_id:"s",state:"no_operator_credit"};
 assert.equal(paymentStatus(row,[{...s,net_cost_aud:0}]).title,"No payment due");
 assert.equal(paymentStatus(row,[{...s,net_cost_aud:null}]).title,"No operator credit due");
});
test("open session never implies a final debit or credit",()=>{
 const row={session_id:"s",state:"waiting_for_session_end"};
 assert.equal(paymentStatus(row,[{...s,ended_at:null}]).title,"Driver charge accumulating");
 assert.equal(paymentStatus(row,[{...s,ended_at:null,net_cost_aud:-1}]).title,"Operator credit accumulating");
});
test("submitted confirmed and uncertain payments are not outstanding new requests",()=>{
 for(const direction of ["driver_to_operator","operator_to_driver"]){
  assert.match(paymentStatus({...p,direction,state:"provider_confirmed",txid:"txid"}).title,/confirmed/);
  assert.match(paymentStatus({...p,direction,state:"provider_unconfirmed",txid:"txid"}).detail,/Awaiting provider confirmation/);
  assert.match(paymentStatus({...p,direction,state:"broadcast_unknown",txid:"txid"}).detail,/do not pay again/);
 }
});
test("expired ready quote is not presented as awaiting wallet collection",()=>{
 assert.match(paymentStatus({...p,expires_at:new Date(now-1).toISOString()},[s],now).detail,/expired/);
});
test("errors and missing backend status are visible",()=>{
 assert.match(paymentStatus({...p,error:"Permission refused"},[s]).detail,/Permission refused/);
 assert.match(paymentStatus({...p,state:"future_state"},[s]).detail,/future state/);
});
test("newer unsigned state never hides an existing transaction",()=>{
 const rows=settlementRows({session_payments:[{...p,state:"broadcast_unknown",txid:"existing"},p]});
 assert.equal(rows[0].txid,"existing");
});
test("payment summaries remain present when ongoing-credit policy is disabled",()=>{
 assert.equal(settlementRows({ongoing_credit:{enabled:false},session_payments:[p]}).length,1);
});
test("claimed wallet attempt and missing confirmation evidence are explicit",()=>{
 assert.match(paymentStatus({...p,state:"wallet_attempt_reserved"}).detail,/collection in progress/);
 for(const state of ["provider_confirmed","provider_unconfirmed","submitted"])
  assert.match(paymentStatus({...p,state}).detail,/Transaction reference missing/);
});
