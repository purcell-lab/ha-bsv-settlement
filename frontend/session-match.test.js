import test from "node:test";
import assert from "node:assert/strict";
import {confirmSessionMatch} from "./session-match.js";
const config={proxy_entity:"sensor.proxy",config_entry_id:"wallet"};
function fixture(){
 const now=Date.now(),calls=[];
 const session={session_id:"session",ocpp_transaction_id:"tx",opened_at:new Date(now-1000).toISOString()};
 const budget={state:"spending_authorised_wallet_permission_required",accepted_at:new Date(now-2000).toISOString(),
  terms:{budget_id:"budget",version:2,session_mode:"next_session_reservation",expires_at:new Date(now+60000).toISOString(),max_total_sats:1000}};
 const hass={user:{is_admin:true},states:{"sensor.proxy":{state:"recording",attributes:{latest_session:session}}},
  callWS:async req=>{calls.push(req);return {response:req.service==="session_budget_status"?budget:{binding:{session_id:"session"}}};}};
 return {hass,budget,session,calls};
}
test("matching reads signed terms, confirms exact cap and calls only binding",async()=>{
 const {hass,calls}=fixture();let prompt;
 assert.deepEqual(await confirmSessionMatch(hass,config,"budget","session",s=>(prompt=s,true)),{matched:true});
 assert.match(prompt,/1000 sat TOTAL/);assert.match(prompt,/tx/);
 assert.deepEqual(calls.map(x=>x.service),["session_budget_status","bind_session_budget"]);
 assert.equal(calls[1].service_data.confirm_driver_present,true);
});
test("cancelled confirmation does not bind",async()=>{
 const {hass,calls}=fixture();
 assert.deepEqual(await confirmSessionMatch(hass,config,"budget","session",()=>false),{cancelled:true});
 assert.equal(calls.length,1);
});
test("non-admin, ended, changed and unready sessions cannot bind",async()=>{
 for(const mutate of [f=>f.hass.user.is_admin=false,f=>f.session.ended_at="now",
  f=>f.session.session_id="other",f=>f.budget.state="revoked",
  f=>f.budget.terms.expires_at="2000-01-01T00:00:00Z",f=>f.budget.binding={session_id:"other"}]){
  const f=fixture();mutate(f);
  await assert.rejects(confirmSessionMatch(f.hass,config,"budget","session",()=>true));
  assert.ok(!f.calls.some(c=>c.service==="bind_session_budget"));
 }
});
test("session ending while confirmation is open aborts",async()=>{
 const f=fixture();
 await assert.rejects(confirmSessionMatch(f.hass,config,"budget","session",()=>{f.session.ended_at="now";return true;}));
 assert.equal(f.calls.length,1);
});
test("a replaced Home Assistant state snapshot is checked before binding",async()=>{
 const f=fixture();let current=f.hass;
 await assert.rejects(confirmSessionMatch(()=>current,config,"budget","session",()=>{
   current={...f.hass,states:{"sensor.proxy":{state:"recording",attributes:{latest_session:{...f.session,session_id:"new"}}}}};
   return true;
 }));
 assert.equal(f.calls.length,1);
});
