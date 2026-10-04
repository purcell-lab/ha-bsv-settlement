import "./session-review-card.js";
const now=Date.now(),future=new Date(now+3600000).toISOString(),past=new Date(now-3600000).toISOString();
const config={direction:"driver_to_operator",config_entry_id:"demo-wallet",proxy_config_entry_id:"demo-proxy",
  wallet_entity:"sensor.wallet",proxy_entity:"sensor.proxy",rate_entity:"sensor.rate"};
const sid=n=>`sigen-proxy-demo-${n}`;
const budget=n=>`${String(n).padStart(8,"0")}-1111-4111-8111-111111111111`;
const s={session_id:sid(1),ocpp_transaction_id:"demo-001",ended_at:past,import_kwh:2.4,export_kwh:0,
  import_cost_aud:0.24,export_credit_aud:0,net_cost_aud:.24,quality_flags:["import:estimated_tariff"]};
const pay=(n,state,extra={})=>({session_id:sid(n),transaction_id:`demo-00${n}`,direction:"driver_to_operator",source:"driver",
  budget_id:budget(n),state,amount_sats:n===3?71:24,expires_at:n===2?past:future,...extra});
const health={session_payments:[pay(6,"provider_confirmed",{txid:"f".repeat(64)}),pay(5,"wallet_attempt_reserved"),
  pay(3,"broadcast_unknown",{txid:"a".repeat(64),fee_sats:23}),pay(2,"ready"),pay(4,"ready")],
  driver_approvals:[{budget_id:budget(4),session_id:sid(4),state:"awaiting_driver_consent",expires_at:future}],
  automatic_credit:{enabled:true,payments:[]}};
window.previewCalls=[];
const h={user:{is_admin:true},states:{"sensor.wallet":{state:"ready",attributes:health},
  "sensor.proxy":{state:"ready",attributes:{latest_session:s}},"sensor.rate":{state:"100"}},
  callWS:async({service,service_data:d})=>{
    window.previewCalls.push({service,data:structuredClone(d)});
    document.getElementById("preview-log").textContent=`Preview only: ${service}. Target ${d.session_id||d.budget_id||d.review_id||"provider refresh"}. No live call.`;
    const number=Number(d.budget_id?.slice(0,8)),id=d.session_id||sid(number);
    if(service==="wallet_refresh_chain")return {response:{}};
    if(service==="prepare_session_closure")return {response:{account:{...s,session_id:id,net_amount_aud:"0.24"},amount_sats:24,
      accepted_flags:[],warning_flags:["import:estimated_tariff"],review_hash:"demo-hash",satoshis_per_aud:"100"}};
    if(service==="session_budget_status")return {response:{state:number===4?"awaiting_driver_consent":"spending_authorised_wallet_permission_required",
      terms:{session_id:id,budget_id:d.budget_id,expires_at:number===2?past:future},
      driver_link_fragment:`#budget=${d.budget_id}&token=${"x".repeat(43)}`}};
    if(service==="prepare_existing_charge_waiver")return {response:{session_id:id,review_hash:"demo-waiver-hash",amount_sats:24}};
    if(service==="waive_existing_charge"||service==="waive_session_charge"){
      const row=health.session_payments.find(r=>r.session_id===id);if(row)row.state="waived";
      card.hass=h;return {response:{state:"waived"}};
    }
    if(service==="prepare_collection_recovery")return {response:{session_id:id,budget_id:d.budget_id,quote_hash:"demo-quote",
      claimed_at:past,amount_sats:24,eligible_for_review:number===5,reason:number===5?
      "No signing permit recorded. Wallet and recipient-history evidence must still be reviewed.":"Reconcile the existing signed/authorised attempt; do not release it."}};
    if(service==="recover_driver_collection"){
      health.session_payments.find(r=>r.session_id===id).state="recovery_ready";card.hass=h;
      return {response:{released_for_driver_review:true}};
    }
    if(service==="request_closed_session_consent")return {response:{state:"awaiting_driver_consent",terms:{session_id:id,budget_id:budget(1),expires_at:future},
      driver_link_fragment:`#budget=${budget(1)}&token=${"x".repeat(43)}`}};
    throw Error("This workflow is not part of this fictional preview. No live action was performed.");
  }};
const card=document.querySelector("bsv-session-review-card");card.setConfig(config);card.hass=h;
document.getElementById("theme").onclick=()=>{document.body.classList.toggle("dark");};
document.getElementById("role").onchange=e=>{h.user.is_admin=e.target.value!=="viewer";h.states["sensor.wallet"].state=e.target.value==="offline"?"unavailable":"ready";card.hass=h;};
document.getElementById("refresh").onclick=()=>{health.chain_checked_at=new Date().toISOString();card.hass=h;};
