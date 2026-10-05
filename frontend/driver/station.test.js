import test from "node:test";
import assert from "node:assert/strict";
import {sessionAccount,operatorRecord} from "./account-projection.js";
import {readinessView,setupAction,allowanceRows,termsList,authorityText,grantText} from "./monthly-ui.js";
import {energyMetrics,provisionalDisplay,provisionalSats} from "../ui.js";

const NOW=Date.parse("2026-10-05T12:00:00Z");
const open={session_id:"s",session_key:"p|s",ended_at:null,running_state:"Charging",import_kwh:0,export_kwh:.53,
  ocpp:{available:true,status:"Charging",checked_at:new Date(NOW).toISOString()},
  import_cost_aud:0,export_credit_aud:.06,net_amount_aud:-.06,meter_updated_at:new Date(NOW-10000).toISOString(),
  satoshis_per_aud:"100",quality_flags:[],transactions:[{id:"c",state:"waiting_for_session_end",direction:"operator_to_driver",amount_sats:null}]};

test("driver account equals the operator card for the same session",()=>{
  for(const s of [open,{...open,net_amount_aud:"0.215",import_kwh:"1.25",import_cost_aud:"0.215",export_kwh:0,export_credit_aud:0},
    {...open,net_amount_aud:0}]){
    const account=sessionAccount(s,NOW),record=operatorRecord(s),metrics=energyMetrics(record);
    const operator=provisionalDisplay(record,{},{state:s.satoshis_per_aud});
    assert.equal(account.importKwh,metrics.toEV.kwh);assert.equal(account.exportKwh,metrics.fromEV.kwh);
    assert.equal(account.averageBuy,metrics.toEV.average);assert.equal(account.averageSell,metrics.fromEV.average);
    assert.equal(account.provisionalSats,operator.sats);assert.equal(account.operatorLabel,operator.label);
    assert.equal(account.provisionalSats,provisionalSats(record,{},{state:"100"}).sats);
  }
  const credit=sessionAccount(open,NOW);
  assert.equal(credit.direction,"credit");assert.equal(credit.provisionalSats,6);assert.equal(credit.ocppStatus,"Charging");
  assert.equal(sessionAccount({...open,net_amount_aud:"0.215"},NOW).provisionalSats,22); // Half-up as operator.
});

test("stale meter or missing rate never looks like zero or a payment",()=>{
  for(const s of [{...open,meter_updated_at:new Date(NOW-600000).toISOString()},{...open,satoshis_per_aud:null},{...open,net_amount_aud:null}]){
    const a=sessionAccount(s,NOW);
    assert.equal(a.provisionalSats,null);assert.notEqual(a.outcome,"payment");
    assert.equal(a.netAud,null);assert.equal(a.estimateState,"unavailable");
  }
});

test("payments keep provider confirmation and wallet acceptance separate",()=>{
  const a=sessionAccount({...open,ended_at:"2026-10-05T11:00:00Z",transactions:[{id:"c",direction:"operator_to_driver",
    state:"provider_confirmed",amount_sats:6,txid:"a".repeat(64),wallet_receipt_status:"not_recorded"}]},NOW);
  assert.equal(a.outcome,"payment");assert.equal(a.provisional,null);
  assert.deepEqual([a.payments[0].confirmed,a.payments[0].walletAccepted],[true,false]);
  assert.match(a.payments[0].status,/receipt sync needed/);
});

test("readiness lists every gap and is never ready without server evidence",()=>{
  assert.deepEqual(readinessView({enabled:false}).ready,false);
  const view=readinessView({enabled:true,readiness:{automatic_collection:false,missing:["wallet_monthly_permission","receiving_registration"]}});
  assert.equal(view.ready,false);assert.equal(view.items.length,3);assert.match(view.items[0],/not verified/);
  const ready={enabled:true,readiness:{automatic_collection:true,missing:[]}};
  assert.equal(readinessView(ready).ready,false);
  assert.equal(readinessView(ready,{walletConnected:true}).ready,true);
  assert.equal(readinessView({enabled:true,readiness:{automatic_collection:true,missing:["x"]}}).ready,false);
});

test("setup action resumes only missing steps and does not reauthorise cancelled consent",()=>{
  const partial={authority:{state:"active"},readiness:{missing:["receiving_registration"]}};
  assert.deepEqual(setupAction(partial,{connected:true}),{enabled:true,label:"Finish wallet setup"});
  assert.equal(setupAction(partial).label,"Reconnect wallet");
  assert.equal(setupAction({authority:{state:"cancelled"}}).enabled,false);
  assert.equal(setupAction({authority:{state:"active"},readiness:{missing:[]}},{connected:true}).enabled,false);
});

test("OCPP is not inferred from Sigen running state and expires independently",()=>{
  assert.equal(sessionAccount({...open,running_state:"Discharging"},NOW).ocppStatus,"Charging");
  assert.equal(sessionAccount({...open,ocpp:null},NOW).ocppStatus,null);
  assert.equal(sessionAccount({...open,ocpp:{...open.ocpp,checked_at:new Date(NOW-61000).toISOString()}},NOW).ocppStatus,null);
  assert.equal(sessionAccount({...open,ended_at:new Date(NOW).toISOString()},NOW).ocppStatus,null);
});

test("allowance rows show limit, spent, reserved and remaining without arithmetic",()=>{
  const rows=Object.fromEntries(allowanceRows({month:{year:2026,month:10,timezone:"UTC"},limit_sats:30000,
    spent_sats:1200,reserved_sats:300,remaining_sats:28500,blocked:false}));
  assert.deepEqual([rows.Month,rows["Limit (fees included)"],rows.Spent,rows.Reserved,rows.Remaining],
    ["October 2026 (UTC)","30,000 sat","1,200 sat","300 sat","28,500 sat"]);
  assert.equal(Object.fromEntries(allowanceRows({limit_sats:30000,spent_sats:null,blocked:true})).Status,"Held for review");
});

test("public terms name the cap with fees, per-session collection, no refill and cancellation",()=>{
  const text=termsList({stationIds:["station-1"],operatorIdentity:"02"+"ab".repeat(32)}).map(r=>r.join(": ")).join("\n");
  for(const needle of [/30,000 sat each calendar month, including network fees/,/One final net payment per session/,
    /do not add to your limit/,/does not carry over/,/Stops new charges/,/station-1/])assert.match(text,needle);
  assert.equal(authorityText({enabled:true,authority:{state:"cancelled"}}),"Cancelled · wallet permission revocation not verified");
  assert.equal(grantText({native_grant:{state:"unverified"}}),"Not verified");
});
