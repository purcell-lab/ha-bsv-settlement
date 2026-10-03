import {finite,num,stateLabel} from "./ui.js";

const validAmount=v=>Number.isSafeInteger(v)&&v>=0;
const confirmed=new Set(["provider_confirmed","driver_payment_provider_confirmed","credit_provider_confirmed"]);
const submitted=new Set(["submitted","provider_unconfirmed","driver_payment_provider_unconfirmed","credit_provider_unconfirmed"]);
const uncertain=new Set(["broadcast_unknown","expired_awaiting_reconciliation","credit_broadcast_unknown"]);

export function settlementRows(health){
  const rows=new Map();
  for(const r of health.ongoing_credit?.sessions||[])rows.set(r.session_id,r);
  for(const p of health.session_payments||[]){
    const old=rows.get(p.session_id);
    // A signed/submitted attempt must not disappear behind a newer unsigned row.
    if(old?.txid&&!p.txid)continue;
    rows.set(p.session_id,{...old,...p});
  }
  for(const r of health.closed_sessions||[]){
    if(!rows.get(r.session_id)?.txid)rows.set(r.session_id,{...r,direction:"driver_to_operator"});
  }
  return [...rows.values()].reverse().slice(0,8);
}

export function paymentStatus(row,sessions=[],now=Date.now()){
  const s=sessions.find(s=>s.session_id===row.session_id);
  const direction=row.direction||(row.state==="no_operator_credit"?
    (s&&finite(s.net_cost_aud)&&Number(s.net_cost_aud)>0?"driver_to_operator":null):
    "operator_to_driver");
  const driver=direction==="driver_to_operator";
  const amount=validAmount(row.amount_sats)?row.amount_sats:null;
  const suffix=amount===null?"":`: ${num(amount)} sat`;
  const result=(title,detail,tone="info")=>({title,detail,tone,direction,
    reference:row.transaction_id||s?.ocpp_transaction_id||row.session_id});
  if(row.state==="waived")return result("Waived"+suffix,row.received_funds?
    `Charge waived. ${num(row.received_funds.amount_sats)} sat already received, held as unallocated funds for separate accounting. No refund authorised. ${row.reason||""}`:
    "Charge waived; no further collection. "+(row.reason||""),"quiet");
  if(row.state==="closed_zero")return result("Closed: no payment due",row.reason,"quiet");
  if(confirmed.has(row.state)&&row.txid)
    return result((driver?"Driver payment confirmed":"Operator credit confirmed")+suffix,
      "Confirmed by the provider. Do not pay again.","good");
  if((confirmed.has(row.state)||submitted.has(row.state))&&!row.txid)
    return result((driver?"Driver payment":"Operator credit")+suffix,
      "Transaction reference missing. Reconcile the saved record; payment is not verified.","warn");
  if(uncertain.has(row.state)||(row.txid&&!submitted.has(row.state)))
    return result((driver?"Driver payment":"Operator credit")+suffix,
      "Payment outcome uncertain. Reconcile the existing attempt; do not pay again.","warn");
  if(submitted.has(row.state)&&row.txid)
    return result((driver?"Driver payment submitted":"Operator credit submitted")+suffix,
      "Awaiting provider confirmation. Do not pay again.");
  if(row.error)return result((driver?"Driver payment":"Operator credit")+suffix,
    `Needs attention: ${row.error}`,"warn");
  if(row.state==="no_payment_due"||(row.state==="no_operator_credit"&&s?.ended_at&&
      finite(s.net_cost_aud)&&Number(s.net_cost_aud)===0))
    return result("No payment due","The session balance is zero. No wallet transfer is required.","good");
  if(driver){
    if(row.state==="ready"&&Number.isFinite(Date.parse(row.expires_at))&&Date.parse(row.expires_at)<=now)
      return result("Driver payment due"+suffix,"Payment request expired. Review the existing request before proceeding.","warn");
    if(row.state==="ready")return result("Driver payment due"+suffix,
      amount===null?"Quoted amount unavailable. Check the saved payment request.":"Awaiting wallet collection.",
      amount===null?"warn":"info");
    const details={
      claimed:"Wallet collection in progress. Do not start another payment.",
      wallet_attempt_reserved:"Collection held for review. Do not start another payment.",
      recovery_ready:"Operator review complete. Awaiting explicit driver confirmation; no automatic retry.",
      submission_authorised:"Wallet submission authorised. Awaiting transaction evidence; do not pay again.",
      awaiting_driver_payment:"Awaiting driver payment.",
      awaiting_account_approval:"Awaiting account review.",
      cancelled:"Payment request cancelled. No payment is confirmed.",
      expired:"Payment request expired. Review before proceeding.",
      no_operator_credit:"No operator credit is due. A driver payment request has not been recorded.",
    };
    return result("Driver payment due"+suffix,details[row.state]||
      `Collection status: ${stateLabel(row.state)}. No payment is confirmed.`,
      ["cancelled","expired","wallet_attempt_reserved"].includes(row.state)?"warn":"info");
  }
  if(row.state==="waiting_for_session_end"){
    const known=s&&finite(s.net_cost_aud),aud=known?Number(s.net_cost_aud):null;
    return result(aud===null?"Session in progress":aud>0?"Driver charge accumulating":
      aud<0?"Operator credit accumulating":"Session balance is zero",
      "Session is still open. Final amount and payment direction are not fixed.");
  }
  if(row.state==="no_operator_credit")return result("No operator credit due",
    "Driver balance unavailable. This does not confirm payment by the driver.");
  return result("Operator credit"+suffix,({
    credit_queued:"Waiting for operator funding and final checks.",
    credit_blocked:"Credit blocked. Review the account and receiving registration.",
  })[row.state]||`Credit status: ${stateLabel(row.state)}. No payment is confirmed.`);
}
