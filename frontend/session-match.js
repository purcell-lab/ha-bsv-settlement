// Reuse the existing administrator-only binding service. Never signs or pays.
export async function confirmSessionMatch(source, config, budgetId, sessionId, ask) {
  const hass=()=>typeof source==="function"?source():source;
  const current=()=>hass().states[config.proxy_entity];
  const check=()=>{
    const p=current(),s=p?.attributes?.latest_session;
    if(!hass().user?.is_admin||!p||["unknown","unavailable"].includes(p.state)||
      p.attributes?.issues?.length||s?.session_id!==sessionId||s.ended_at)
      throw Error("The open session changed or is unavailable. Refresh before matching.");
    return s;
  };
  check();
  const call=(service,data)=>hass().callWS({type:"call_service",domain:"bsv_settlement",service,
    service_data:{config_entry_id:config.config_entry_id,...data},return_response:true});
  const {response:b}=await call("session_budget_status",{budget_id:budgetId});
  const s=check(),t=b?.terms;
  if(t?.budget_id!==budgetId||t.version!==2||t.session_mode!=="next_session_reservation"||
    b.state!=="spending_authorised_wallet_permission_required"||b.binding||
    !(Date.parse(b.accepted_at)<=Date.parse(s.opened_at)&&Date.parse(s.opened_at)<Date.parse(t.expires_at))||
    !(Date.parse(t.expires_at)>Date.now())||!Number.isSafeInteger(t.max_total_sats)||t.max_total_sats<=0)
    throw Error("This approval is no longer available for matching. Refresh driver setup.");
  if(!ask(`Confirm the consenting driver is using session ${s.ocpp_transaction_id || sessionId}. Match their existing ${t.max_total_sats} sat TOTAL budget, including fees, to this session only? This enables settlement checks within the signed limits. No new wallet approval is requested.`))
    return {cancelled:true};
  check();
  const {response:result}=await call("bind_session_budget",{
    budget_id:budgetId,session_id:sessionId,confirm_driver_present:true});
  if(result?.binding?.session_id!==sessionId)
    throw Error("Matching could not be verified. Refresh status before taking another action.");
  return {matched:true};
}
