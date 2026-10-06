import "./operator-card.js";
import "./budget-card.js";
import "./session-review-card.js";
// Preview only: in-memory request IDs for the sandboxed, no-network fixture.
const requestIds=new Map();
window.previewAdjustmentStorage={getItem:key=>requestIds.get(key),setItem:(key,value)=>requestIds.set(key,value)};
const $=s=>document.querySelector(s);
const ongoingOption=document.createElement("option");ongoingOption.value="ongoing";ongoingOption.textContent="Ongoing credits";$("#scenario").append(ongoingOption);
const debitOption=document.createElement("option");debitOption.value="debit";debitOption.textContent="Driver payment due";$("#scenario").append(debitOption);
const heldOption=document.createElement("option");heldOption.value="held";heldOption.textContent="Driver collection interrupted";$("#scenario").append(heldOption);
const warningOption=document.createElement("option");warningOption.value="metering-warning";warningOption.textContent="Metering warning: settlement allowed";$("#scenario").append(warningOption);
const feeOption=document.createElement("option");feeOption.value="fee-aware";feeOption.textContent="Fee-aware operator credit";$("#scenario").append(feeOption);
for(const [value,label] of [["closure","Completed account: data review"],["zero","Completed account: zero balance"],
  ["waived","Waived charge: funds received separately"],["waived-held","Waived charge: held attempt closed"],
  ["evidence-paid","Paid credit with historical blocked route"],["evidence-conflict","Conflicting transaction records"],
  ["evidence-lost","Confirmation evidence lost"]]){
 const option=document.createElement("option");option.value=value;option.textContent=label;$("#scenario").append(option);
}
const config={config_entry_id:"fictional-wallet",proxy_config_entry_id:"fictional-proxy",wallet_entity:"sensor.wallet",proxy_entity:"sensor.proxy",rate_entity:"sensor.rate",balance_entity:"sensor.balance",operator_name:"Demonstration operator",operator_contact:"operator@example.test"};
const session={session_id:"fictional-session",ocpp_transaction_id:"demo-8427-transaction",opened_at:new Date().toISOString(),ended_at:new Date().toISOString(),import_kwh:1.06,export_kwh:14.33,import_cost_aud:0.212,export_credit_aud:1.452,net_cost_aud:-1.24};
const payment={session_id:session.session_id,transaction_id:session.ocpp_transaction_id,state:"provider_confirmed",amount_sats:124,fee_sats:10,txid:"fictional-transaction-reference",recipient_address:"Fictional driver address",updated_at:new Date().toISOString()};
let budget=null,tab="overview",closedRecord=null;
const closedAccount=()=>({session_id:session.session_id,ocpp_transaction_id:session.ocpp_transaction_id,
  currency:"AUD",ended_at:session.ended_at,import_kwh:1.94,export_kwh:0,
  net_amount_aud:$("#scenario").value==="zero"?"0.00":"0.05",
  quality_flags:$("#scenario").value==="zero"?[]:["import:energy_without_matching_state"]});
window.previewCalls=[];
const hass={user:{is_admin:true},states:{},callWS:async({service,service_data:d})=>{
  window.previewCalls.push({service,data:structuredClone(d)});
  $("#notice").textContent=`Preview only: ${service.replaceAll("_"," ")}. No live call was made.`;
  if(service==="pay_energy_adjustment"){
    window.adjustmentFixtures ||= {};
    window.adjustmentFixtures[d.request_id] ||= {review_id:d.request_id,
      direction:d.energy_direction==="export"?"operator_to_driver":"driver_to_operator",
      state:d.energy_direction==="export"?"credit_submitted":"awaiting_driver_payment",
      amount_sats:d.energy_direction==="export"?41:62};
    return {response:window.adjustmentFixtures[d.request_id]};
  }
  if(service==="prepare_session_closure"){
    if(!["closure","zero"].includes($("#scenario").value))throw Error("Preview: existing credit or payment requires reconciliation. Select a completed-account scenario.");
    return {response:{closed:!!closedRecord,state:closedRecord?.state||"data_review_required",reason:closedRecord?.reason,
      account:closedAccount(),amount_sats:$("#scenario").value==="zero"?0:5,satoshis_per_aud:"100",
      accepted_flags:closedAccount().quality_flags,review_hash:"fictional-review-hash",pending_budget_id:budget?.terms.budget_id}};
  }
  if(service==="waive_session_charge"){
    closedRecord={session_id:session.session_id,transaction_id:session.ocpp_transaction_id,state:$("#scenario").value==="zero"?"closed_zero":"waived",
      net_amount_aud:closedAccount().net_amount_aud,reason:d.reason,closed_at:new Date().toISOString()};
    if(budget)budget.state="revoked";
    hass.states["sensor.wallet"].attributes.closed_sessions=[closedRecord];
    return {response:closedRecord};
  }
  if(service==="request_closed_session_consent"){
    const id=crypto.randomUUID();
    budget={state:"awaiting_driver_consent",terms:{budget_id:id,session_id:session.session_id,
      session_mode:"existing_session",max_total_sats:d.max_total_sats,max_fee_sats:d.max_fee_sats,
      expires_at:new Date(Date.now()+d.valid_minutes*60000).toISOString(),closed_session_review:{account:closedAccount()}},
      driver_link_fragment:`#budget=${id}&token=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`};
    return {response:budget};
  }
  if(service==="session_budget_status"&&!budget)throw Error("No fictional approval yet.");
  if(service==="create_session_budget"){
    const mode=d.multi_session?"multi_session":d.session_id?"existing_session":"next_session_reservation";
    const same=budget&&!budget.binding&&!["revoked","expired"].includes(budget.state)&&budget.terms.session_mode===mode&&(!d.session_id||budget.terms.session_id===d.session_id);
    let reuse=false;
    if(same){
      if(budget.state!=="awaiting_driver_consent")throw Error("This session already has a signed approval.");
      if(d.confirm_replace_pending){
        const hash=Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256",new TextEncoder().encode(budget.invitation.payload))),b=>b.toString(16).padStart(2,"0")).join("");
        if(d.replace_pending_budget_id!==budget.terms.budget_id||d.expected_invitation_hash!==hash)throw Error("The displayed invitation changed. Refresh and review it again.");
      }else{
        if(["max_total_sats","max_fee_sats","valid_minutes","operator_name","operator_contact"].some(k=>budget.terms[k]!==d[k]))throw Error("Confirm replacement of the unapproved invitation to change its settings.");
        reuse=true;
      }
    }
    if(reuse){budget.invitation_reused=true;}
    else{
      const id=crypto.randomUUID(),terms={version:d.multi_session?3:2,budget_id:id,session_id:d.session_id||"reservation:fictional",
        expires_at:new Date(Date.now()+d.valid_minutes*60000).toISOString(),session_mode:mode,
        max_total_sats:d.max_total_sats,max_fee_sats:d.max_fee_sats,valid_minutes:d.valid_minutes,
        operator_name:d.operator_name,operator_contact:d.operator_contact};
      budget={state:"awaiting_driver_consent",terms,
        public_registration:{available:false,context_hash:"fictional-registration-context"},
        driver_link_fragment:`#budget=${id}&token=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`,
        invitation:{payload:JSON.stringify(terms),notice:"Fictional preview, not a valid signed invitation"}};
    }
  }
  if(service==="open_public_registration"){
    budget.public_registration={available:true,context_hash:"fictional-registration-context",
      expires_at:new Date(Date.now()+15*60000).toISOString()};
  }
  if(service==="close_public_registration")budget.public_registration.available=false;
  if(service==="bind_session_budget")budget.binding={session_id:session.session_id};
  if(service==="revoke_session_budget")budget.state="revoked";
  if(service==="configure_automatic_credit")hass.states["sensor.wallet"].attributes.automatic_credit.enabled=d.enabled;
  if(service==="configure_ongoing_credit"){
    hass.states["sensor.wallet"].attributes.ongoing_credit.enabled=d.enabled;
    hass.states["sensor.wallet"].attributes.ongoing_credit.effective=d.enabled;
    $("bsv-session-review-card").hass=hass;
  }
  if(service==="prepare_session_review")throw Error("Preview only. No payment review is created.");
  const result=budget?structuredClone(budget):{};
  // The real fresh-create response lacks admin-only registration context.
  if(service==="create_session_budget"&&!budget.invitation_reused)delete result.public_registration;
  if(budget&&budget.state!=="awaiting_driver_consent")delete result.driver_link_fragment;
  return {response:result};
}};
function state(){
 const s=$("#scenario").value;
 hass.states={"sensor.rate":{state:"100"},"sensor.balance":{state:s==="pending"?"76":"4652"},"sensor.proxy":{state:"ready",attributes:{latest_session:{...session,ended_at:s==="missing"?null:session.ended_at},updated_at:new Date().toISOString()}},
 "sensor.wallet":{state:s==="unavailable"?"unavailable":"ready",attributes:{pending_change_sats:s==="pending"?4576:0,chain_checked_at:new Date().toISOString(),automatic_credit:{enabled:true,max_total_sats:1000,fee_sats:10,payments:["missing","ongoing"].includes(s)?[]:[{...payment,state:s==="pending"?"provider_unconfirmed":"provider_confirmed"}]},session_payments:[],driver_approvals:budget?[{budget_id:budget.terms.budget_id,session_id:budget.terms.session_id,state:budget.state,approved:budget.state!=="awaiting_driver_consent",expires_at:budget.terms.expires_at}]:[],
 ongoing_credit:s==="ongoing"?{enabled:true,effective:true,recipient:{address:"Fictional registered driver address"},sessions:[{session_id:session.session_id,transaction_id:session.ocpp_transaction_id,recipient_address:"Fictional registered driver address",satoshis_per_aud:"100",state:"waiting_for_session_end"}]}:null}}};
 const price=(value)=>({state:String(value),attributes:{unit_of_measurement:"$/kWh",estimate:false,
   start_time:new Date(Date.now()-60000).toISOString(),end_time:new Date(Date.now()+240000).toISOString()}});
 hass.states["sensor.buy"]=price(0.1234);hass.states["sensor.sell"]=price(0.0826);
 hass.states["sensor.rate"].attributes={unit_of_measurement:"sat/AUD"};
 hass.states["sensor.proxy"].attributes.source_entities={import_price:"sensor.buy",export_price:"sensor.sell"};
 if(s==="fee-aware"){
   hass.states["sensor.wallet"].attributes.automatic_credit={
     enabled:true,max_total_sats:1000,fee_sats:null,fee_mode:"provider_quote_per_transaction",
     payments:[{...payment,fee_sats:23,fee_quote:{rate_sat_per_kb:"100",
       estimated_signed_bytes:227,observed_at:new Date().toISOString()}}]};
 }
 if(s==="metering-warning"){
   hass.states["sensor.proxy"].attributes.latest_session.quality_flags=[
     "export:energy_without_matching_state","interval_energy_allocation_estimated"];
   hass.states["sensor.wallet"].attributes.automatic_credit.payments=[];
   hass.states["sensor.wallet"].attributes.ongoing_credit={enabled:true,effective:true,
     sessions:[{session_id:session.session_id,transaction_id:session.ocpp_transaction_id,
       recipient_address:"Fictional registered driver address",state:"waiting_for_session_end"}]};
 }
 if(s==="debit"){
   hass.states["sensor.proxy"].attributes.latest_session={...session,import_kwh:2.02,export_kwh:0.18,import_cost_aud:0.202,export_credit_aud:0.012,net_cost_aud:0.19};
   Object.assign(hass.states["sensor.wallet"].attributes,{
     automatic_credit:{enabled:true,max_total_sats:1000,fee_sats:10,payments:[]},
     ongoing_credit:{enabled:true,effective:true,recipient:{address:"Fictional registered driver address"},sessions:[
       {session_id:session.session_id,transaction_id:session.ocpp_transaction_id,state:"no_operator_credit",satoshis_per_aud:"100"}]},
     session_payments:[{session_id:session.session_id,transaction_id:session.ocpp_transaction_id,
       state:"ready",direction:"driver_to_operator",source:"driver",amount_sats:19,max_fee_sats:10,
       satoshis_per_aud:"100",expires_at:new Date(Date.now()+3600000).toISOString()}],
   });
 }
 if(["closure","zero"].includes(s)){
   hass.states["sensor.proxy"].attributes.latest_session={...session,import_kwh:1.94,export_kwh:0,
     net_cost_aud:s==="zero"?0:0.05,quality_flags:closedAccount().quality_flags};
   hass.states["sensor.wallet"].attributes.automatic_credit.payments=[];
   hass.states["sensor.wallet"].attributes.closed_sessions=closedRecord?[closedRecord]:[];
   for(const a of hass.states["sensor.wallet"].attributes.driver_approvals)a.reviewed_closed_account=true;
 }
 if(["waived","waived-held"].includes(s)){
   const amount=s==="waived"?76:89;
   hass.states["sensor.proxy"].attributes.latest_session={...session,import_kwh:3.8,export_kwh:0,net_cost_aud:amount/100};
   Object.assign(hass.states["sensor.wallet"].attributes,{
     automatic_credit:{enabled:true,payments:[]},
     session_payments:[{source:"driver",session_id:session.session_id,state:"waived",
       amount_sats:amount,diagnostic:{message:"Retained historical failure, not a current hold"}}],
     closed_sessions:[{session_id:session.session_id,transaction_id:session.ocpp_transaction_id,
       state:"waived",net_amount_aud:String(amount/100),amount_sats:amount,
       reason:"Operator instructed charge waiver. Original attempt retained for audit.",
       received_funds:s==="waived"?{amount_sats:76,state:"received_unallocated",refund_authorised:false}:null}]
   });
 }
}
function card(name,extra={}){const el=document.createElement(name);el.setConfig({...config,...extra});el.hass=hass;$("#content").append(el);
 if(name==="bsv-budget-card"){const link=el.shadowRoot.getElementById("public-page");link.href="./driver-preview.html";link.textContent="View driver page preview";}
 return el;}
function panel(html){const p=document.createElement("section");p.className="panel";p.innerHTML=html;$("#content").append(p);}
function render(){
 state();
 if($("#scenario").value.startsWith("evidence-")){
   const kind=$("#scenario").value;
   const known={...payment,txid:"ab".repeat(32),amount_sats:213,fee_sats:23,
     direction:"operator_to_driver",checked_at:"2026-10-06T00:00:00Z"};
   Object.assign(hass.states["sensor.wallet"].attributes,{
     ongoing_credit:{enabled:true,sessions:[{session_id:session.session_id,state:"credit_blocked",error:"Old superseded route"}]},
     automatic_credit:{enabled:true,payments:[known]},
     session_payments:kind==="evidence-paid"?
       [{session_id:session.session_id,state:"cancelled",source:"manual",amount_sats:999}]:
       kind==="evidence-conflict"?[{...known,txid:"cd".repeat(32),amount_sats:81}]:
       [{...known,state:"broadcast_unknown",checked_at:"2026-10-06T01:00:00Z"}]
   });
 }
 if($("#scenario").value==="held"){
   hass.states["sensor.wallet"].attributes.automatic_credit.payments=[];
   hass.states["sensor.proxy"].attributes.latest_session={...session,import_kwh:3.8,export_kwh:0.2,net_cost_aud:0.89};
   hass.states["sensor.wallet"].attributes.session_payments=[{
     source:"driver",direction:"driver_to_operator",transaction_id:session.ocpp_transaction_id,
     session_id:"fictional-session",state:"wallet_attempt_reserved",
     amount_sats:89,diagnostic:{step:"Create the unsigned wallet draft",
       message:"A network request failed; the response or wallet outcome may be unknown."}}];
 }
 $("#content").replaceChildren();$("#notice").textContent="";
 const labels={overview:["Charging & settlement","Live prices, session energy and average prices, next action and operator funds."],drivers:["Set up a driver","Open next-driver registration, or share a private invitation. Existing approvals keep their original terms."],payments:["Owner credits","Money received from drivers. Review each session and its actual collection status."],"operator-credits":["Driver credits","Money paid to drivers. Follow each credit through funding and confirmation."],wallet:["Operator wallet","Confirmed funds and pending change are different."],testing:["Settings & diagnostics","Keep fictional tests separate from real mainnet settlements."]};
 $("#title").textContent=labels[tab][0];$("#intro").textContent=labels[tab][1];
 document.querySelectorAll("nav button").forEach(b=>b.setAttribute("aria-current",String(b.dataset.tab===tab)));
 if(tab==="overview"){card("bsv-operator-card");card("bsv-operator-card",{mode:"wallet"});}
 if(tab==="drivers"){card("bsv-budget-card");panel('<h2>Receiving credits and approving charges</h2><p>Open public registration only when the intended driver is ready. Historical settlements retain their original approvals and recipients.</p><ol><li>Open registration or share a private invitation.</li><li>Ask the driver to authorise the budget and register their wallet in BSV Browser.</li><li>Check the signed scope, expiry and remaining spending limit.</li><li>Check the receiving wallet and policy in Driver credits.</li></ol><p>A fresh multi-session approval covers up to seven days and 1,000 sat total, including fees. Credits do not refill it. Driver collection needs the connected wallet; public registration never changes payment policies.</p><button id="simulate">Simulate driver approval</button><p>This preview button is not part of the live dashboard.</p>');$("#simulate").onclick=()=>{if(!budget){$("#notice").textContent="Create a fictional invitation first.";return;}budget.state="spending_authorised_wallet_permission_required";budget.credit_destination={registered:true};if(budget.public_registration)budget.public_registration.available=false;const c=$("bsv-budget-card");c.budget=structuredClone(budget);c.paint();$("#notice").textContent="Fictional driver approved. Select Approval needed to demonstrate an open session.";};}
 if(tab==="payments"||tab==="operator-credits"){
   const c=card("bsv-session-review-card",{direction:tab==="payments"?"driver_to_operator":"operator_to_driver"});
   c.style.gridColumn="1 / -1";
 }
 if(tab==="wallet"){card("bsv-operator-card",{mode:"wallet"});panel("<h2>Receive operator funds</h2><p>The live dashboard retains the operator's receiving QR and full public identity here. Real wallet addresses are deliberately omitted from this preview.</p><p>Mainnet only. Confirm the address and protect the local wallet backup before funding.</p>");}
 if(tab==="testing")panel("<h2>Demonstration conversion</h2><p>100 sat per AUD. This is a demonstration conversion, not market FX.</p><p>The live Diagnostics view retains the editable conversion setting, offline self-test and mock-service results. Those controls are not connected in this preview.</p>");
 document.querySelectorAll("bsv-operator-card").forEach(c=>c.shadowRoot.addEventListener("click",e=>{const a=e.composedPath().find(n=>n.tagName==="A");if(a){e.preventDefault();tab=a.getAttribute("href").split("/").pop();render();}}));
}
document.querySelectorAll("nav button").forEach(b=>b.onclick=()=>{tab=b.dataset.tab;render();});
$("#scenario").onchange=()=>{closedRecord=null;budget=null;render();};$("#theme").onclick=()=>{$("body").classList.toggle("dark");$("#theme").textContent=$("body").classList.contains("dark")?"Light theme":"Dark theme";};
window.previewRefresh=()=>{for(const c of document.querySelectorAll("bsv-session-review-card,bsv-operator-card"))c.hass=hass;};
// Open the current staged workflow, not an unrelated historical-credit fixture.
$("#scenario").value="pending";tab="overview";
render();
