import test from "node:test";
import assert from "node:assert/strict";
import {adjustmentConfig,payAdjustment,adjustmentFeedback,adjustmentRequest,adjustmentFinished,adjustmentAmount} from "./energy-adjustment.js";
const config={proxy_entity:"sensor.proxy",wallet_entity:"sensor.wallet",config_entry_id:"wallet",rate_entity:"sensor.rate"};
function fixture(){
 const calls=[];
 const hass={user:{is_admin:true},states:{
  "sensor.proxy":{state:"recording",attributes:{config_entry_id:"recorder"}},
  "sensor.wallet":{state:"ready"}},
  callWS:async request=>{calls.push(request);return {response:{review_id:"review",state:"credit_submitted"}}}};
 return {hass,calls};
}
test("single click uses only the bounded payment service and stable request ID",async()=>{
 const {hass,calls}=fixture();
 await payAdjustment(hass,config,"export","same-id");
 await payAdjustment(hass,config,"export","same-id");
 assert.equal(calls.length,2);
 assert.ok(calls.every(c=>c.service==="pay_energy_adjustment"&&c.service_data.request_id==="same-id"));
 assert.equal(calls[0].service_data.proxy_config_entry_id,"recorder");
 assert.equal(calls[0].service_data.confirm_mainnet_payment,true);
});
test("viewer, missing recorder and unavailable states cannot submit",async()=>{
 for(const mutate of [h=>h.user.is_admin=false,h=>delete h.states["sensor.proxy"].attributes.config_entry_id,
   h=>h.states["sensor.wallet"].state="unavailable",h=>delete h.states["sensor.proxy"]]){
  const {hass,calls}=fixture();mutate(hass);
  await assert.rejects(payAdjustment(hass,config,"export","id"));
  assert.equal(calls.length,0);
 }
});
test("explicit recorder configuration remains supported",()=>{
 assert.equal(adjustmentConfig(fixture().hass,{...config,proxy_config_entry_id:"explicit"}).proxy_config_entry_id,"explicit");
});
test("negative price direction chooses the actual recipient tab, no false paid claim",()=>{
 const debit=adjustmentFeedback({direction:"driver_to_operator",state:"awaiting_driver_payment",amount_sats:125});
  assert.equal(debit.path,"payments");assert.match(debit.text,/Legacy manual.*no automatic wallet collection/);
 const credit=adjustmentFeedback({direction:"operator_to_driver",state:"credit_submitted",amount_sats:125});
 assert.equal(credit.path,"operator-credits");assert.match(credit.text,/Awaiting block confirmation/);
 assert.match(adjustmentFeedback({direction:"operator_to_driver",state:"credit_broadcast_unknown",amount_sats:125}).text,/Do not resend/);
});
test("lost response and reload reuse a persisted UUID; only explicit new intent rotates it",()=>{
 const rows=new Map(),storage={getItem:k=>rows.get(k),setItem:(k,v)=>rows.set(k,v)};
 assert.equal(adjustmentRequest(storage,"scope","export",false,()=>"first"),"first");
 assert.equal(adjustmentRequest(storage,"scope","export",false,()=>"second"),"first");
 assert.equal(adjustmentRequest(storage,"scope","export",true,()=>"second"),"second");
 assert.equal(adjustmentFinished({state:"credit_broadcast_unknown"}),false);
 assert.equal(adjustmentFinished({state:"credit_submitted"}),false);
 assert.equal(adjustmentFinished({state:"credit_provider_confirmed"}),true);
 assert.throws(()=>adjustmentRequest({getItem:()=>null,setItem:()=>{}},"s","export",false,()=>"id"));
});
test("buttons show AUD and sat with actual direction including negative tariffs",()=>{
 const rate={state:"100",attributes:{unit_of_measurement:"sat/AUD"}};
 assert.equal(adjustmentAmount({available:true,value:.0998},rate,"import").label,"Debit 5 kWh import · A$0.50 · 50 sat");
 assert.equal(adjustmentAmount({available:true,value:.0607},rate,"export").label,"Credit 5 kWh export · A$0.30 · 30 sat");
 assert.match(adjustmentAmount({available:true,value:-.25},rate,"import").label,/Credit.*A\$1.25.*125 sat/);
 assert.equal(adjustmentAmount({available:false,value:.25},rate,"import").available,false);
 assert.equal(adjustmentAmount({available:true,value:.25},{state:"100"},"import").available,false);
 assert.equal(adjustmentAmount({available:true,value:2},rate,"export").available,false);
});
