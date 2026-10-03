import test from "node:test";
import assert from "node:assert/strict";
import {energyMetrics,currentPrice,chainRecordUrl,chainRecordLink} from "./ui.js";
import {sessionTableRows,sessionTable} from "./session-table.js";

test("energy-weighted averages keep negative prices and do not infer missing data",()=>{
 assert.deepEqual(energyMetrics({import_kwh:2,export_kwh:4,import_cost_aud:"-0.2",export_credit_aud:"0.8"}),
   {toEV:{kwh:2,average:-0.1},fromEV:{kwh:4,average:0.2}});
 for(const value of [null,undefined,"",true,"bad",Infinity]){
   assert.equal(energyMetrics({import_kwh:value,import_cost_aud:0}).toEV.kwh,null);
   assert.equal(energyMetrics({import_kwh:2,import_cost_aud:value}).toEV.average,null);
 }
 assert.equal(energyMetrics({import_kwh:0,import_cost_aud:0}).toEV.average,null);
});
test("directions are separated and historical records remain visible without fabricated energy",()=>{
 const health={session_payments:[{session_id:"debit",direction:"driver_to_operator",state:"ready",amount_sats:13,max_fee_sats:987}],
   automatic_credit:{payments:[{session_id:"credit",state:"provider_unconfirmed",amount_sats:38,fee_sats:10,txid:"fictional"}]}};
 assert.equal(sessionTableRows(health,[],"driver_to_operator").length,1);
 assert.equal(sessionTableRows(health,[],"operator_to_driver").length,1);
 const html=sessionTable(health,[],"operator_to_driver");
 assert.match(html,/Awaiting block confirmation/);assert.match(html,/Unavailable/);
 assert.doesNotMatch(html,/987/);assert.match(html,/38 sat/);
});
test("unsettled closed account appears and display escapes untrusted strings",()=>{
 const s={session_id:"<script>",ocpp_transaction_id:"<img>",ended_at:new Date().toISOString(),
   net_cost_aud:1,import_kwh:2,export_kwh:0,import_cost_aud:1};
 assert.equal(sessionTableRows({},[s],"driver_to_operator").length,1);
 const html=sessionTable({},[s],"driver_to_operator");
 assert.match(html,/&lt;img&gt;/);assert.doesNotMatch(html,/<img>/);
 assert.match(html,/0\.5000/);
});
test("live rates require a current interval and correct units",()=>{
 const t=Date.now(),s={state:"-0.15",attributes:{unit_of_measurement:"$/kWh",start_time:new Date(t-1000).toISOString(),end_time:new Date(t+1000).toISOString(),estimate:false}};
 assert.equal(currentPrice(s,t).value,-0.15);
 assert.equal(currentPrice(s,t+1000).available,false);
 assert.equal(currentPrice({...s,state:"unavailable"},t).available,false);
 assert.equal(currentPrice({...s,attributes:{...s.attributes,unit_of_measurement:"c/kWh"}},t).available,false);
 assert.equal(currentPrice({...s,attributes:{...s.attributes,start_time:null}},t).available,false);
});
test("chain links are restricted to real-shape mainnet transaction IDs",()=>{
 const id="ab".repeat(32);
 assert.equal(chainRecordUrl(id),`https://api.whatsonchain.com/v1/bsv/main/tx/hash/${id}`);
 for(const bad of [null,"pending","javascript:alert(1)","<script>","ab".repeat(31)]){
  assert.equal(chainRecordUrl(bad),null);assert.doesNotMatch(chainRecordLink(bad),/href=/);
 }
 assert.match(chainRecordLink(id),/noopener noreferrer/);
});
