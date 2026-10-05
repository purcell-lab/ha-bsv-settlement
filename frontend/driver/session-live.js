// Presentation only. Never uses a missing/invalid reading as zero energy.
import {provisionalSats,energyMetrics,stamp,short} from "../ui.js";
export function liveSessionView(session,{checkedAt,updatedAt,basis,ocpp,conversionRate,failed=false,now=Date.now()}={}) {
  const checked=Date.parse(checkedAt),updated=Date.parse(updatedAt);
  const stale=failed||!Number.isFinite(checked)||now-checked>90000||
    (Number.isFinite(updated)&&now-updated>120000);
  const kwh=v=>!stale&&v!==null&&v!==undefined&&v!==""&&
    ["number","string"].includes(typeof v)&&Number.isFinite(Number(v))&&Number(v)>=0?
    `${Number(v).toFixed(3)} kWh`:"Unavailable";
  const total=session?.net_cost_aud;
  const priced=!stale&&total!==null&&total!==undefined&&total!==""&&
    ["number","string"].includes(typeof total)&&Number.isFinite(Number(total));
  const sats=priced?provisionalSats(session,{}, {state:conversionRate}).sats:null;
  const energy=energyMetrics(session||{}),average=v=>!stale&&v!==null?`${v.toFixed(4)} $/kWh`:"Unavailable";
  const amountLabel=priced?Number(total)<0?"Provisional credit to you":Number(total)>0?"Provisional charge to you":"Provisional session balance":"Provisional amount unavailable";
  return {
    amountLabel,satsValue:sats===null?"Unavailable":`${sats} sat`,
    audValue:priced?`AUD ${Math.abs(Number(total)).toFixed(2)} · ${conversionRate||"Unavailable"} sat/AUD (signed session rate)`:"Awaiting valid session pricing",
    importAverage:average(energy.toEV.average),exportAverage:average(energy.fromEV.average),
    started:stamp(session?.energy_started_at||session?.opened_at),
    reference:short(session?.ocpp_transaction_id||session?.session_id),
    fullReference:session?.ocpp_transaction_id||session?.session_id||"No linked session",
    quality:(session?.quality_flags||[]).join(", ")||"No meter flags reported",
    ocppStatus:!stale&&ocpp?.available&&now-Date.parse(ocpp.checked_at)<=60000?ocpp.status:"Unavailable",
    ocppNote:stale?"Live charger status is not updated.":ocpp?.reason||"OCPP status unavailable. Energy totals use the existing session recorder.",
    imported:kwh(session?.import_kwh),exported:kwh(session?.export_kwh),
    amount:priced?`Provisional ${Number(total)<0?"credit to you":Number(total)>0?"charge to you":"balance"}: `+
      `${sats===null?"sat amount unavailable":`${sats} sat`} · A$${Math.abs(Number(total)).toFixed(2)}. Network fees excluded. Not a payment request.`:
      "Provisional amount unavailable",
    note:stale?"Live update unavailable. Previous totals are withheld until fresh data returns.":
      !session?"Session totals will appear when a session is linked to your approval or verified receiving registration.":
      `${session.ended_at?"Final recorded energy":"Session in progress"}. `+
      (Number.isFinite(updated)?`Meter updated ${new Date(updated).toLocaleTimeString([], {hour:"2-digit",minute:"2-digit",second:"2-digit"})}. `:"Meter update time unavailable. ")+
      "Refreshes every 15 seconds. Current rates are not session-average prices."+
      (basis==="registered_receiving_route"?" Receiving registration identifies this session; it does not authorise driver charges.":""),
  };
}
