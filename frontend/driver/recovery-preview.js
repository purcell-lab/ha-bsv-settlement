// Isolated UI fixture. Never included in the installed HACS bundle.
import { PrivateKey, ProtoWallet } from "@bsv/sdk";
import { canonical, bytes, hash, paymentAuthority, spendingScope } from "./model.js";
const banner=document.createElement("section");
banner.innerHTML=`<h2>Fictional recovery preview</h2><p>No real wallet or payment. All requests stay in this simulated page.</p>
<label for="preview-mode">Collection scenario</label>
<select id="preview-mode" style="font:inherit;padding:10px;width:100%">
<option value="held">Interrupted wallet draft</option>
<option value="fee">Draft exceeds an existing 10 sat fee cap</option>
<option value="recovery">Operator reviewed, driver confirmation needed</option>
<option value="ongoing">Reviewed collection with a separate ongoing session</option>
<option value="reservation">Unbound future reservation with ongoing credits</option>
<option value="offline">Status connection interrupted</option></select>
<p class="small">Changing the scenario reloads this fixture only. The real page polls every 30 seconds.</p>`;
document.querySelector("main").prepend(banner);
const mode=new URLSearchParams(location.search).get("scenario")||"ongoing";
const select=document.getElementById("preview-mode");select.value=mode;
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
let state=mode==="reservation"?"waiting_for_operator_binding":
  ["recovery","ongoing"].includes(mode)?"recovery_ready":"wallet_attempt_reserved",reads=0;
let diagnostic=state!=="wallet_attempt_reserved"?null:{
  event_id:"11111111-2222-4333-8444-555555555555",stage:"create_draft",code:"network_request_failed"};
if(mode==="fee")diagnostic={event_id:"11111111-2222-4333-8444-555555555555",
  stage:"inspect_draft",code:"validation_failed",reason:"fee_limit_exceeded",
  details:{payment_sats:89,fee_sats:38,fee_cap_sats:10,total_debit_sats:127,total_cap_sats:1000}};
window.previewCalls={claims:0,drafts:0,signs:0,reports:0};
window.CWI={
  getVersion:async()=>({version:"fictional-preview"}),
  getPublicKey:driver.getPublicKey.bind(driver),createSignature:driver.createSignature.bind(driver),
  getNetwork:async()=>({network:"mainnet"}),
  createAction:async()=>{window.previewCalls.drafts++;throw new TypeError("Failed to fetch");},
  signAction:async()=>{window.previewCalls.signs++;throw Error("No signing in preview");},
};
window.fetch=async(url,options)=>{
  // Also refuse WalletClient's localhost discovery probes: no external connections.
  if(url!=="/api/bsv_settlement/driver")throw new TypeError("Preview network disabled");
  const body=JSON.parse(options.body);
  if(body.action==="read"){
    if(mode==="offline" && reads++>0)throw new TypeError("Failed to fetch");
    const p={available:true,start:new Date(Date.now()-60000).toISOString(),
      end:new Date(Date.now()+3600000).toISOString(),estimate:false};
    return Response.json({invitation,state:"spending_authorised_wallet_permission_required",
      driver_identity:identity,automatic_credit_enabled:false,credit_destination_registered:true,
      binding:null,ongoing_credit_enabled:true,
      ongoing_credits:mode==="recovery"?[]:[{
        credit_id:"fictional-ongoing-credit",session_id:"fictional-other-session",
        transaction_id:"other-session-reference",state:"waiting_for_session_end",
        recipient_address:terms.operator_address,
      }],
      session:{...account,import_kwh:3.8,export_kwh:0.2,net_cost_aud:"0.89"},
      prices:{valid:true,checked_at:new Date().toISOString(),
        import:{...p,aud_per_kwh:"0.25"},export:{...p,aud_per_kwh:"0.12"}}});
  }
  if(body.action==="collection_status")return Response.json({state,quote,diagnostic});
  if(body.action==="claim_collection" && state==="recovery_ready" && body.confirm_recovered_attempt===true){
    window.previewCalls.claims++;state="wallet_attempt_reserved";return Response.json({claimed:true});
  }
  if(body.action==="report_collection_failure"){
    window.previewCalls.reports++;diagnostic=body.diagnostic;return Response.json({diagnostic_saved:true});
  }
  return Response.json({error:"Preview refused an unexpected operation."},{status:400});
};
await import("./app.js");
