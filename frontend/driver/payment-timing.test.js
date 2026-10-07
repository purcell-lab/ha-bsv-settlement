import test from "node:test";
import assert from "node:assert/strict";
import {paymentTimingRows} from "./payment-timing.js";

test("credit event times are explicit UTC and missing events are not inferred",()=>{
  const rows=paymentTimingRows({direction:"operator_to_driver",
    broadcast_attempted_at:"2026-10-07T18:57:52.171+10:00",
    approved_at:"2026-10-07T08:57:52Z",state:"provider_confirmed"});
  assert.equal(rows.length,4);
  assert.equal(rows[0][1],"2026-10-07 08:57:52.171 UTC");
  assert.deepEqual(rows.slice(1).map(r=>r[1]),Array(3).fill("Not recorded"));
});
test("debits do not imply an incoming wallet receipt; invalid times fail closed",()=>{
  const rows=paymentTimingRows({direction:"driver_to_operator",
    broadcast_attempted_at:"<script>",broadcast_acknowledged_at:null,
    provider_first_confirmed_at:42});
  assert.equal(rows.length,3);
  assert.ok(rows.every(r=>r[1]==="Not recorded"));
});
