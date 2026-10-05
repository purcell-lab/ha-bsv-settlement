// One account view for the driver page, built from the operator card's own
// helpers so both show the same direction, energy, cost, conversion and outcome.
import {energyMetrics,provisionalDisplay} from "../ui.js";
import {provisionalSession,transactionStatus} from "./portal-model.js";

/** Map an owner-history session to the operator recorder's field names. */
export const operatorRecord=s=>({session_id:s.session_id,net_cost_aud:s.net_amount_aud,
  import_kwh:s.import_kwh,export_kwh:s.export_kwh,import_cost_aud:s.import_cost_aud,
  export_credit_aud:s.export_credit_aud,quality_flags:s.quality_flags||[]});

const numeric=v=>(typeof v==="number"||typeof v==="string"&&v.trim()!=="")&&Number.isFinite(Number(v));

export function sessionAccount(s,now=Date.now()){
  const record=operatorRecord(s),metrics=energyMetrics(record);
  const provisional=provisionalSession(s,now);
  const meterAt=Date.parse(s.meter_updated_at);
  const fresh=Number.isFinite(meterAt)&&now-meterAt>=-5000&&now-meterAt<=120000;
  const ocppAt=Date.parse(s.ocpp?.checked_at);
  const ocppFresh=s.ocpp?.available&&Number.isFinite(ocppAt)&&now-ocppAt>=-5000&&now-ocppAt<=60000;
  const validEstimate=!s.ended_at?fresh&&provisional?.payment.includes(" sat"):true;
  const net=validEstimate&&numeric(s.net_amount_aud)?Number(s.net_amount_aud):null;
  // Direction of money, from the operator's sign convention: negative net credits the driver.
  const direction=net===null?null:net<0?"credit":net>0?"charge":"balance";
  const payments=(s.transactions||[]).filter(t=>t.txid||Number.isSafeInteger(t.amount_sats));
  const outcome=s.closure?.state?.includes("waiv")?"waived":
    payments.length?"payment":provisional?"provisional":s.ended_at?"recorded":"open";
  // Live estimate uses exactly the operator conversion; finality comes only from payments.
  const operator=provisional?provisionalDisplay(record,{},{state:s.satoshis_per_aud}):null;
  return {
    live:!s.ended_at,ocppStatus:!s.ended_at&&ocppFresh?s.ocpp.status:null,direction,netAud:net,
    estimateState:s.ended_at?"final":validEstimate?"provisional":"unavailable",
    importKwh:!s.ended_at&&!fresh?null:metrics.toEV.kwh,
    exportKwh:!s.ended_at&&!fresh?null:metrics.fromEV.kwh,
    averageBuy:!s.ended_at&&!fresh?null:metrics.toEV.average,
    averageSell:!s.ended_at&&!fresh?null:metrics.fromEV.average,
    averageNet:net!==null&&(metrics.toEV.kwh||0)+(metrics.fromEV.kwh||0)>0?
      net/((metrics.toEV.kwh||0)+(metrics.fromEV.kwh||0)):null,
    provisional,provisionalSats:operator&&provisional.payment.includes(" sat")?operator.sats:null,
    operatorLabel:operator?.label||null,outcome,
    payments:payments.map(t=>({id:t.id,direction:t.direction,amountSats:Number.isSafeInteger(t.amount_sats)?t.amount_sats:null,
      status:transactionStatus(t),
      // Provider confirmation and wallet acceptance stay separate facts.
      confirmed:t.state==="provider_confirmed",
      walletAccepted:t.wallet_receipt_status==="wallet_reported_accepted"})),
    warnings:s.quality_flags||[],
  };
}

const aud=v=>v===null?"Unavailable":`${v<0?"−":""}$${Math.abs(v).toFixed(4)}/kWh`;
export const formatRate=aud;
export const formatKwh=v=>v===null?"Unavailable":`${v.toFixed(3)} kWh`;
