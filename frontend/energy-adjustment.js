/** A click authorises one bounded attempt, never an automatic retry. */
export function adjustmentConfig(hass, config) {
  if (!hass?.user?.is_admin) throw Error("Only an administrator can send adjustments.");
  const proxy=hass.states[config.proxy_entity],wallet=hass.states[config.wallet_entity];
  if (![proxy,wallet].every(s=>s&&!["unknown","unavailable"].includes(s.state)))
    throw Error("Recorder and wallet must be available.");
  const recorder=config.proxy_config_entry_id||proxy.attributes?.config_entry_id;
  if (!recorder||!config.config_entry_id||!config.rate_entity)
    throw Error("Install the matching recorder update or configure its entry ID.");
  return {config_entry_id:config.config_entry_id,proxy_config_entry_id:recorder,
    conversion_rate_entity:config.rate_entity};
}
export async function payAdjustment(hass,config,direction,requestId) {
  if (!["import","export"].includes(direction)||!requestId) throw Error("Invalid adjustment request.");
  const data=adjustmentConfig(hass,config);
  const result=await hass.callWS({type:"call_service",domain:"bsv_settlement",
    service:"pay_energy_adjustment",service_data:{...data,energy_direction:direction,
      request_id:requestId,confirm_mainnet_payment:true},return_response:true});
  if (!result?.response?.review_id) throw Error("No payment status received. Do not send again; check settlement.");
  return result.response;
}
export const adjustmentFinished=review=>["credit_provider_confirmed","driver_payment_provider_confirmed",
  "cancelled","no_payment_due"].includes(review?.state);
export function adjustmentRequest(storage,scope,direction,fresh=false,uuid=()=>crypto.randomUUID()) {
  const key="bsv-adjustment:"+scope+":"+direction;
  let id=storage.getItem(key);
  if(!id||fresh){id=uuid();storage.setItem(key,id);}
  if(storage.getItem(key)!==id)throw Error("Cannot retain the payment request ID safely.");
  return id;
}
export function adjustmentAmount(price,rateState,direction) {
  const rate=Number(rateState?.state),value=Number(price?.value);
  if(!price?.available||price.value===null||!Number.isFinite(value)||
    !Number.isFinite(rate)||rate<=0||rateState?.attributes?.unit_of_measurement!=="sat/AUD")
    return {available:false,label:"Amount unavailable"};
  const net=5*value*(direction==="import"?1:-1),sats=Math.round(Math.abs(net)*rate);
  if(sats<1||sats>=1000)return {available:false,label:"Outside payment limit"};
  return {available:true,label:`${net<0?"Credit":"Debit"} 5 kWh ${direction} · A$${Math.abs(net).toFixed(2)} · ${sats.toLocaleString("en-AU")} sat`};
}
export function adjustmentFeedback(review) {
  const path=review.direction==="operator_to_driver"?"operator-credits":"payments";
  const amount=`${review.amount_sats} sat`;
  const messages={
    awaiting_driver_payment:`Driver payment requested: ${amount}. Driver wallet approval and payment are still required.`,
    credit_submitted:`Driver credit submitted: ${amount}. Awaiting block confirmation.`,
    credit_provider_unconfirmed:`Driver credit submitted: ${amount}. Awaiting block confirmation.`,
    credit_provider_confirmed:`Driver credit provider-confirmed: ${amount}. Wallet receipt acceptance is separate.`,
    credit_broadcast_unknown:`Driver credit outcome unknown: ${amount}. Do not resend.`,
  };
  return {path,text:messages[review.state]||`Adjustment ${amount}: ${review.state}. Check settlement before taking further action.`};
}
