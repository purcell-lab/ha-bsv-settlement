import test from "node:test";
import assert from "node:assert/strict";
import {liveSessionView} from "./session-live.js";
const now=Date.now(),fresh={now,checkedAt:new Date(now).toISOString(),updatedAt:new Date(now).toISOString(),conversionRate:"100"};
test("live totals include both directions and provisional cost",()=>{
 const v=liveSessionView({session_id:"one",import_kwh:1.23456,export_kwh:.5,net_cost_aud:-.2},fresh);
 assert.equal(v.imported,"1.235 kWh");assert.equal(v.exported,"0.500 kWh");
 assert.match(v.amount,/Provisional credit to you: 20 sat · A\$0.20/);assert.match(v.note,/15 seconds/);
 assert.match(v.amount,/Network fees excluded/);assert.match(v.amount,/Not a payment request/);
});
test("provisional charge uses signed rate and half-up sat rounding",()=>{
 assert.match(liveSessionView({net_cost_aud:"0.005"},fresh).amount,/charge to you: 1 sat/);
 assert.match(liveSessionView({net_cost_aud:0},fresh).amount,/balance: 0 sat/);
 assert.match(liveSessionView({net_cost_aud:1},{...fresh,conversionRate:null}).amount,/sat amount unavailable/);
});
test("driver account mirrors energy weighted averages without using current rates",()=>{
 const v=liveSessionView({import_kwh:2,export_kwh:1,import_cost_aud:.4,export_credit_aud:-.1,
  net_cost_aud:.5,ocpp_transaction_id:"12345678-long"},fresh);
 assert.equal(v.importAverage,"0.2000 $/kWh");assert.equal(v.exportAverage,"-0.1000 $/kWh");
 assert.equal(v.satsValue,"50 sat");assert.equal(v.reference,"12345678");
 assert.equal(liveSessionView({import_kwh:0,import_cost_aud:0},fresh).importAverage,"Unavailable");
});
test("missing data is unavailable rather than zero and empty session is explicit",()=>{
 for(const value of [null,undefined,"",true,"bad",-1])
  assert.equal(liveSessionView({import_kwh:value},fresh).imported,"Unavailable");
 assert.equal(liveSessionView({import_kwh:0},fresh).imported,"0.000 kWh");
 assert.match(liveSessionView(null,fresh).note,/linked to your approval/);
});
test("stale and failed reads withhold old totals",()=>{
 for(const options of [{failed:true},{checkedAt:new Date(now-91000).toISOString()},
  {updatedAt:new Date(now-121000).toISOString()}]){
  const v=liveSessionView({import_kwh:2,export_kwh:1,net_cost_aud:1},{...fresh,...options});
  assert.equal(v.imported,"Unavailable");assert.equal(v.amount,"Provisional amount unavailable");
 }
});
test("receiving-route telemetry never claims spending authority",()=>{
 assert.match(liveSessionView({import_kwh:2},{...fresh,basis:"registered_receiving_route"}).note,/does not authorise driver charges/);
});
test("OCPP status is separately reported and expires without changing energy",()=>{
 const ocpp={available:true,status:"Charging",checked_at:fresh.checkedAt,reason:"Separate from payment status"};
 const v=liveSessionView({import_kwh:2},{...fresh,ocpp});
 assert.equal(v.ocppStatus,"Charging");assert.match(v.ocppNote,/Separate/);
 assert.equal(liveSessionView({import_kwh:2},{...fresh,ocpp:{...ocpp,checked_at:new Date(now-61000).toISOString()}}).ocppStatus,"Unavailable");
});
