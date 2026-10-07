// Isolated UI fixture. Never included in the installed HACS bundle.
import { PrivateKey, ProtoWallet } from "@bsv/sdk";
import { canonical, bytes, hash, paymentAuthority, spendingScope } from "./model.js";
import {loginProtocol,loginScope} from "./portal-model.js";
const banner=document.createElement("section");
banner.className="preview-controls";
banner.innerHTML=`<p>Design preview. Fictional sessions, no real payments.</p><details><summary>Try another state</summary>
<label for="preview-mode">Collection scenario</label>
<select id="preview-mode" style="font:inherit;padding:10px;width:100%">
<option value="approval">New session: approve budget</option>
<option value="active">Charging in progress</option>
<option value="confirmed">Payment confirmed</option>
<option value="unknown">Broadcast uncertain</option>
<option value="stale">Rates unavailable</option>
<option value="expired">Approval expired</option>
<option value="decline">Wallet permission declined</option>
<option value="wallet-unavailable">Wallet unavailable: connection recovery</option>
<option value="held">Interrupted wallet draft</option>
<option value="fee">Draft exceeds an existing 10 sat fee cap</option>
<option value="unconfirmed">Awaiting block confirmation</option>
<option value="recovery">Operator reviewed, driver confirmation needed</option>
<option value="ongoing">Reviewed collection with a separate ongoing session</option>
<option value="reservation">Unbound future reservation with ongoing credits</option>
<option value="offline">Status connection interrupted</option></select>
<p class="small">Changing the scenario reloads this fixture only. The real page polls every 15 seconds.</p>
<p><a href="../index.html">Sign-in preview</a></p></details>`;
document.querySelector("main").append(banner);
const mode=new URLSearchParams(location.search).get("scenario")||"unconfirmed";
const select=document.getElementById("preview-mode");select.value=mode;
const closedOption=document.createElement("option");closedOption.value="closed";closedOption.textContent="Completed account: fresh consent";select.append(closedOption);select.value=mode;
const waivedOption=document.createElement("option");waivedOption.value="waived";waivedOption.textContent="Charge waived: no collection";select.append(waivedOption);select.value=mode;
select.onchange=()=>{location.search="?scenario="+select.value;};
const operator=PrivateKey.fromRandom(),driver=new ProtoWallet(PrivateKey.fromRandom());
const identity=(await driver.getPublicKey({identityKey:true})).publicKey;
const terms={
  version:2,budget_id:"11111111-2222-4333-8444-555555555555",session_id:"fictional-session",
  session_mode:mode==="reservation"?"next_session_reservation":"existing_session",
  transaction_id:"fictional-proxy-id",network:"BSV mainnet",
  operator_identity:operator.toPublicKey().toString(),operator_address:operator.toPublicKey().toAddress(),
  operator_name:"Community charging demo",operator_contact:"Fictional operator",
  max_total_sats:1000,max_fee_sats:10,satoshis_per_aud:"100",
  pricing_rule:"Interval energy × dynamic rate",account_scope:"One charging session",
  import_price_entity:"sensor.demo_import",export_price_entity:"sensor.demo_export",
  created_at:new Date().toISOString(),expires_at:new Date(Date.now()+3600000).toISOString(),scope:spendingScope,
};
if(mode==="expired"){terms.created_at=new Date(Date.now()-3600000).toISOString();terms.expires_at=new Date(Date.now()-60000).toISOString();}
const unsignedMode=["closed","approval","stale","expired","decline","wallet-unavailable"].includes(mode);
const closedAccount={session_id:terms.session_id,ocpp_transaction_id:terms.transaction_id,currency:"AUD",
  ended_at:new Date(Date.now()-60000).toISOString(),net_cost_aud_unrounded:"0.05",net_amount_aud:"0.05",
  import_kwh:"1.94",export_kwh:"0",quality_flags:["import:energy_without_matching_state"]};
if(mode==="closed"){
 terms.max_fee_sats=1000;
 terms.closed_session_review={account:closedAccount,amount_sats:5,satoshis_per_aud:"100",
   accepted_flags:closedAccount.quality_flags,reason:"Reviewed charger timing mismatch"};
 terms.account_scope="This completed account only. No new session or receiving-wallet registration.";
}
terms.payment_authority=paymentAuthority(terms);
const payload=canonical(terms),invitation={version:1,payload,signature:operator.sign(bytes(payload)).toDER("hex")};
const account={session_id:terms.session_id,ocpp_transaction_id:terms.transaction_id,
  ended_at:new Date(Date.now()-60000).toISOString(),net_cost_aud_unrounded:"0.89",net_amount_aud:"0.89"};
const quotePayload=canonical({version:1,network:"BSV mainnet",budget_id:terms.budget_id,
  invitation_hash:await hash(payload),recipient_address:terms.operator_address,
  operator_identity:terms.operator_identity,driver_identity:identity,
  max_total_sats:1000,max_fee_sats:10,satoshis_per_aud:"100",expires_at:terms.expires_at,
  amount_sats:89,account,recovery_generation:1,created_at:terms.created_at});
const quote={payload:quotePayload,hash:await hash(quotePayload),signature:operator.sign(bytes(quotePayload)).toDER("hex")};
let state=["active","approval","wallet-unavailable"].includes(mode)?"waiting_for_session_end":mode==="confirmed"?"provider_confirmed":mode==="unknown"?"broadcast_unknown":mode==="unconfirmed"?"provider_unconfirmed":mode==="waived"?"waived":mode==="reservation"?"waiting_for_operator_binding":
  ["recovery","ongoing"].includes(mode)?"recovery_ready":"wallet_attempt_reserved",reads=0;
let diagnostic=state!=="wallet_attempt_reserved"?null:{
  event_id:"11111111-2222-4333-8444-555555555555",stage:"create_draft",code:"network_request_failed"};
if(mode==="fee")diagnostic={event_id:"11111111-2222-4333-8444-555555555555",
  stage:"inspect_draft",code:"validation_failed",reason:"fee_limit_exceeded",
  details:{payment_sats:89,fee_sats:38,fee_cap_sats:10,total_debit_sats:127,total_cap_sats:1000}};
window.previewCalls={claims:0,drafts:0,signs:0,reports:0,approvals:0};
let closedApproved=false;
window.CWI={
  getVersion:async()=>({version:"fictional-preview"}),
  getPublicKey:async args=>{
    if(mode==="wallet-unavailable")throw Error("No wallet available over any communication substrate. Install a BSV wallet today!");
    return driver.getPublicKey(args);
  },createSignature:async args=>{
    if(mode==="decline")throw Error("Wallet permission declined.");
    return driver.createSignature(args);
  },
  getNetwork:async()=>({network:"mainnet"}),
  createAction:async()=>{window.previewCalls.drafts++;throw new TypeError("Failed to fetch");},
  signAction:async()=>{window.previewCalls.signs++;throw Error("No signing in preview");},
};
window.fetch=async(url,options)=>{
  if(url==="/api/bsv_settlement/portal"){
    const d=JSON.parse(options.body),stamp=Math.floor(Date.now()/1000);
    if(d.action==="challenge")return Response.json({protocolID:loginProtocol,keyID:"a".repeat(43),
      payload:canonical({action:"sign_in_driver_portal",version:1,origin:"https://charging.example.com",
        nonce:"a".repeat(43),browser_binding:"b".repeat(64),scope:loginScope,issued_at:stamp,expires_at:stamp+120})});
    if(d.action==="login")return Response.json({identity,expires_in:900});
    if(d.action==="sessions")return Response.json({identity,expires_in:900,total:1,
      authorisations:[{spending_active:!["expired","waived"].includes(mode),limit_sats:1000,expires_at:terms.expires_at,
        receiving_registered:mode!=="closed"}],
      sessions:[{session_id:terms.session_id,opened_at:terms.created_at,ended_at:account.ended_at,
        import_kwh:3.8,export_kwh:.2,net_amount_aud:.89,quality_flags:[],agreements:[],transactions:[]}]});
    throw Error("Offline history fixture refused this operation");
  }
  // Also refuse WalletClient's localhost discovery probes: no external connections.
  if(url!=="/api/bsv_settlement/driver")throw new TypeError("Preview network disabled");
  const body=JSON.parse(options.body);
  if(body.action==="read"){
    if(mode==="offline" && reads++>0)throw new TypeError("Failed to fetch");
    const p={available:true,start:new Date(Date.now()-60000).toISOString(),
      end:new Date(Date.now()+3600000).toISOString(),estimate:false};
    return Response.json({invitation,state:mode==="waived"?"charge_waived":unsignedMode&&!closedApproved?"awaiting_driver_consent":"spending_authorised_wallet_permission_required",
      closure:mode==="waived"?{state:"waived",amount_sats:89,reason:"Operator waived the charge"}:null,
      driver_identity:unsignedMode&&!closedApproved?null:identity,automatic_credit_enabled:false,credit_destination_registered:mode!=="closed",
      binding:null,ongoing_credit_enabled:true,
      session_checked_at:new Date().toISOString(),session_updated_at:new Date().toISOString(),
      session_basis:mode==="reservation"?"registered_receiving_route":"signed_session",
      ocpp:{available:mode==="active"||mode==="reservation",status:window.previewOcppStatus||"Charging",checked_at:new Date().toISOString(),
        reason:"Current OCPP connector state. Separate from payment status and energy direction."},
      ongoing_credits:["recovery","closed","waived","approval","active","confirmed","unknown"].includes(mode)?[]:[{
        credit_id:"fictional-ongoing-credit",session_id:"fictional-other-session",
        transaction_id:"other-session-reference",state:"waiting_for_session_end",
        recipient_address:terms.operator_address,
      }],
      session:mode==="closed"?{...closedAccount,net_cost_aud:"0.05"}:{...account,opened_at:new Date(Date.now()-600000).toISOString(),ended_at:["active","approval","reservation"].includes(mode)?null:account.ended_at,import_kwh:3.8,export_kwh:0.2,import_cost_aud:.95,export_credit_aud:.06,net_cost_aud:"0.89",...(window.previewEnergyOverride||{})},
      prices:{valid:!["closed","stale"].includes(mode),checked_at:new Date().toISOString(),
        import:{...p,aud_per_kwh:"0.25"},export:{...p,aud_per_kwh:"0.12"}}});
  }
  if(body.action==="approve"&&["closed","approval"].includes(mode)){
    window.previewCalls.approvals++;closedApproved=true;return Response.json({state:"spending_authorised_wallet_permission_required",
      driver_identity:identity,automatic_credit_enabled:false,credit_destination_registered:false});
  }
  if(body.action==="collection_status"&&mode==="closed")return Response.json({
    state:"collection_blocked",error:"Fictional preview stops before wallet drafting. No real payment."});
  if(body.action==="collection_status" || (mode==="unconfirmed"&&body.action==="reconcile_collection"))
    return Response.json({state,...(["waiting_for_session_end","waiting_for_operator_binding"].includes(state)?{}:{quote}),diagnostic,...(mode==="unconfirmed"?{txid:"a".repeat(64),confirmations:0}:{})});
  if(body.action==="claim_collection" && state==="recovery_ready" && body.confirm_recovered_attempt===true){
    window.previewCalls.claims++;state="wallet_attempt_reserved";return Response.json({claimed:true});
  }
  if(body.action==="report_collection_failure"){
    window.previewCalls.reports++;diagnostic=body.diagnostic;return Response.json({diagnostic_saved:true});
  }
  return Response.json({error:"Preview refused an unexpected operation."},{status:400});
};
await import("./app.js");
