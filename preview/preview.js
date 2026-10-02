import "./operator-card.js";
import "./budget-card.js";
import "./session-review-card.js";
const $=s=>document.querySelector(s);
const ongoingOption=document.createElement("option");ongoingOption.value="ongoing";ongoingOption.textContent="Ongoing credits";$("#scenario").append(ongoingOption);
const config={config_entry_id:"fictional-wallet",proxy_config_entry_id:"fictional-proxy",wallet_entity:"sensor.wallet",proxy_entity:"sensor.proxy",rate_entity:"sensor.rate",balance_entity:"sensor.balance",operator_name:"Demonstration operator",operator_contact:"operator@example.test"};
const session={session_id:"fictional-session",ocpp_transaction_id:"demo-8427-transaction",opened_at:new Date().toISOString(),ended_at:new Date().toISOString(),import_kwh:1.06,export_kwh:14.33,net_cost_aud:-1.24};
const payment={session_id:session.session_id,state:"provider_confirmed",amount_sats:124,fee_sats:10,txid:"fictional-transaction-reference",recipient_address:"Fictional driver address",updated_at:new Date().toISOString()};
let budget=null,tab="overview";
const hass={user:{is_admin:true},states:{},callWS:async({service,service_data:d})=>{
  $("#notice").textContent=`Preview only: ${service.replaceAll("_"," ")}. No live call was made.`;
  if(service==="session_budget_status"&&!budget)throw Error("No fictional approval yet.");
  if(service==="create_session_budget")budget={state:"awaiting_driver_consent",terms:{budget_id:"11111111-1111-4111-8111-111111111111",session_id:d.session_id||"reservation:fictional",expires_at:new Date(Date.now()+7200000).toISOString(),session_mode:d.session_id?"existing_session":"next_session_reservation"},driver_link_fragment:"#budget=11111111-1111-4111-8111-111111111111&token=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",invitation:{notice:"Fictional preview, not a valid signed invitation"}};
  if(service==="bind_session_budget")budget.binding={session_id:session.session_id};
  if(service==="revoke_session_budget")budget.state="revoked";
  if(service==="configure_automatic_credit")hass.states["sensor.wallet"].attributes.automatic_credit.enabled=d.enabled;
  if(service==="configure_ongoing_credit"){
    hass.states["sensor.wallet"].attributes.ongoing_credit.enabled=d.enabled;
    hass.states["sensor.wallet"].attributes.ongoing_credit.effective=d.enabled;
    $("bsv-session-review-card").hass=hass;
  }
  if(service==="prepare_session_review")throw Error("Preview only. No payment review is created.");
  const result=budget?structuredClone(budget):{};if(budget&&budget.state!=="awaiting_driver_consent")delete result.driver_link_fragment;return {response:result};
}};
function state(){
 const s=$("#scenario").value;
 hass.states={"sensor.rate":{state:"100"},"sensor.balance":{state:s==="pending"?"76":"4652"},"sensor.proxy":{state:"ready",attributes:{latest_session:{...session,ended_at:s==="missing"?null:session.ended_at},updated_at:new Date().toISOString()}},
 "sensor.wallet":{state:s==="unavailable"?"unavailable":"ready",attributes:{pending_change_sats:s==="pending"?4576:0,chain_checked_at:new Date().toISOString(),automatic_credit:{enabled:true,max_total_sats:1000,fee_sats:10,payments:["missing","ongoing"].includes(s)?[]:[{...payment,state:s==="pending"?"provider_unconfirmed":"provider_confirmed"}]},session_payments:[],driver_approvals:budget?[{budget_id:budget.terms.budget_id,session_id:budget.terms.session_id,state:budget.state,approved:budget.state!=="awaiting_driver_consent",expires_at:budget.terms.expires_at}]:[],
 ongoing_credit:s==="ongoing"?{enabled:true,effective:true,recipient:{address:"Fictional registered driver address"},sessions:[{session_id:session.session_id,transaction_id:session.ocpp_transaction_id,recipient_address:"Fictional registered driver address",satoshis_per_aud:"100",state:"waiting_for_session_end"}]}:null}}};
}
function card(name,extra={}){const el=document.createElement(name);el.setConfig({...config,...extra});el.hass=hass;$("#content").append(el);return el;}
function panel(html){const p=document.createElement("section");p.className="panel";p.innerHTML=html;$("#content").append(p);}
function render(){
 state();$("#content").replaceChildren();$("#notice").textContent="";
 const labels={overview:["Charging & settlement","The latest session, its next action and the operator's funds."],drivers:["Set up a driver","Register a receiving wallet for operator credits. Driver spending approval remains session-specific."],payments:["Settle a session","Automatic credits and manual exceptions, with exact amounts and transaction status."],wallet:["Operator wallet","Confirmed funds and pending change are different."],testing:["Settings & diagnostics","Keep fictional tests separate from real mainnet settlements."]};
 $("#title").textContent=labels[tab][0];$("#intro").textContent=labels[tab][1];
 document.querySelectorAll("nav button").forEach(b=>b.setAttribute("aria-current",String(b.dataset.tab===tab)));
 if(tab==="overview"){card("bsv-operator-card");card("bsv-operator-card",{mode:"wallet"});}
 if(tab==="drivers"){card("bsv-budget-card");panel('<h2>Receiving credits and approving charges</h2><p>With ongoing credits enabled, the latest registered receiving wallet is selected for each new session. Its recipient stays fixed. No new spending approval is needed for these operator-funded credits.</p><ol><li>Share a private link with the intended driver.</li><li>Ask them to approve and register their wallet in BSV Browser.</li><li>For driver charges, match a valid spending approval to the correct session.</li><li>Check the receiving wallet and policy in Payments.</li></ol><p>Driver charges need the open browser. Operator credits can run with it closed.</p><button id="simulate">Simulate driver approval</button><p>This preview button is not part of the live dashboard.</p>');$("#simulate").onclick=()=>{if(!budget){$("#notice").textContent="Create a fictional invitation first.";return;}budget.state="spending_authorised_wallet_permission_required";budget.credit_destination={registered:true};const c=$("bsv-budget-card");c.budget=structuredClone(budget);c.paint();$("#notice").textContent="Fictional driver approved. Select Approval needed to demonstrate an open session.";};}
 if(tab==="payments"){card("bsv-session-review-card");panel("<h2>Follow the existing payment</h2><p>Submitted does not mean confirmed. A confirmed operator credit does not prove that the driver's wallet has imported its receipt.</p><p>Never create a second payment to resolve a status problem.</p>");}
 if(tab==="wallet"){card("bsv-operator-card",{mode:"wallet"});panel("<h2>Receive operator funds</h2><p>The live dashboard retains the operator's receiving QR and full public identity here. Real wallet addresses are deliberately omitted from this preview.</p><p>Mainnet only. Confirm the address and protect the local wallet backup before funding.</p>");}
 if(tab==="testing")panel("<h2>Demonstration conversion</h2><p>100 sat per AUD. This is a demonstration conversion, not market FX.</p><p>The live Diagnostics view retains the editable conversion setting, offline self-test and mock-service results. Those controls are not connected in this preview.</p>");
 document.querySelectorAll("bsv-operator-card").forEach(c=>c.shadowRoot.addEventListener("click",e=>{const a=e.composedPath().find(n=>n.tagName==="A");if(a){e.preventDefault();tab=a.getAttribute("href").split("/").pop();render();}}));
}
document.querySelectorAll("nav button").forEach(b=>b.onclick=()=>{tab=b.dataset.tab;render();});
$("#scenario").onchange=render;$("#theme").onclick=()=>{$("body").classList.toggle("dark");$("#theme").textContent=$("body").classList.contains("dark")?"Light theme":"Dark theme";};
render();
