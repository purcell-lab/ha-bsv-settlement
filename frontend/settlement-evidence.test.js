import test from "node:test";
import assert from "node:assert/strict";
import {settlementRows,paymentStatus} from "./payment-status.js";
import {ownerActionRows} from "./owner-actions.js";
import {evidenceDetails,sessionTableRows} from "./session-table.js";
const txid="aa".repeat(32);
const credit={session_id:"s",txid,state:"provider_confirmed",amount_sats:213,fee_sats:23,
  direction:"operator_to_driver",checked_at:"2026-10-06T00:00:00Z"};
test("confirmed credit wins over blocked route and cancelled review without blending fields",()=>{
  const h={ongoing_credit:{sessions:[{session_id:"s",state:"credit_blocked",error:"old error"}]},
    automatic_credit:{payments:[credit]},
    session_payments:[{session_id:"s",source:"manual",state:"cancelled",amount_sats:999}]};
  const before=JSON.stringify(h),r=settlementRows(h)[0];
  assert.equal(r.txid,txid);assert.equal(r.amount_sats,213);assert.equal(r.error,undefined);
  assert.equal(r.audit_records.length,3);assert.match(paymentStatus(r).title,/confirmed/);
  assert.equal(JSON.stringify(h),before);
});
test("same transaction receipt metadata survives duplicate indexes, not a second payment",()=>{
  const r=settlementRows({automatic_credit:{payments:[{...credit,
    wallet_receipt_status:"wallet_reported_accepted",wallet_imported_at:"2026-10-06T00:00:00Z"}]},
    session_payments:[{...credit,wallet_receipt_status:"not_recorded"}]});
  assert.equal(r.length,1);assert.match(paymentStatus(r[0]).detail,/reports receipt accepted/);
});
test("different transactions are preserved as conflict rather than overwritten or summed",()=>{
  const r=settlementRows({session_payments:[credit,{...credit,txid:"bb".repeat(32),amount_sats:81}]})[0];
  assert.equal(r.evidence_conflict,true);assert.equal(r.txid,undefined);
  assert.equal(r.amount_sats,undefined);assert.equal(r.audit_records.length,2);
  assert.match(paymentStatus(r).title,/Conflicting/);
});
test("same transaction contradictory financial fields are not merged",()=>{
  for(const patch of [{amount_sats:81},{fee_sats:99},{direction:"driver_to_operator"},{output_index:1}]){
    const r=settlementRows({session_payments:[{...credit,output_index:0},{...credit,output_index:0,...patch}]})[0];
    assert.equal(r.evidence_conflict,true);
  }
});
test("newer uncertainty beats earlier confirmation; equal or undated disagreement stays conflict",()=>{
  const later={...credit,state:"broadcast_unknown",confirmations:null,checked_at:"2026-10-06T01:00:00Z"};
  for(const rows of [[credit,later],[later,credit]]){
    const r=settlementRows({session_payments:rows})[0];
    assert.equal(r.state,"broadcast_unknown");assert.match(paymentStatus(r).detail,/uncertain/);
  }
  for(const checked_at of [undefined,credit.checked_at]){
    const r=settlementRows({session_payments:[credit,{...later,checked_at}]})[0];
    assert.equal(r.evidence_conflict,true);
  }
});
test("second held signing attempt is visible and exposes no new collection or waiver",()=>{
  const h={session_payments:[{...credit,direction:"driver_to_operator"},
    {session_id:"s",state:"submission_authorised",direction:"driver_to_operator",budget_id:"other"}]};
  const v=ownerActionRows(h,[])[0];
  assert.equal(v.complete,false);assert.deepEqual(v.actions.map(a=>a.id),["check"]);
});
test("mixed-direction conflicts stay visible in both recipient views",()=>{
  const h={session_payments:[credit,{...credit,direction:"driver_to_operator"}]};
  for(const direction of ["driver_to_operator","operator_to_driver"])
    assert.equal(sessionTableRows(h,[],direction).length,1);
});
test("unsigned ready row cannot hide a held attempt; explicit audited waiver can close it",()=>{
  const hold={session_id:"s",state:"wallet_attempt_reserved",budget_id:"original",direction:"driver_to_operator"};
  const ready={...hold,state:"ready",budget_id:"new"};
  assert.equal(settlementRows({session_payments:[hold,ready]})[0].budget_id,"original");
  const closed=settlementRows({session_payments:[hold],closed_sessions:[{session_id:"s",state:"waived"}]})[0];
  assert.equal(closed.state,"waived");assert.equal(closed.audit_records.length,2);
  assert.equal(settlementRows({session_payments:[hold,{...hold,budget_id:"other"}]})[0].evidence_conflict,true);
});
test("audit display escapes metadata and never treats history as extra debt",()=>{
  const r=settlementRows({session_payments:[credit,{session_id:"s",state:"<script>",source:"<img>"}]})[0];
  const html=evidenceDetails(r);
  assert.match(html,/Historical projections, not additional amounts due/);
  assert.match(html,/&lt;script&gt;/);assert.match(html,/&lt;img&gt;/);
  assert.doesNotMatch(html,/<script>|<img>/);
});
