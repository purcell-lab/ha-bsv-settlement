// Offline UI fixture only. No real wallet, relay, provider, payments or credentials.
import {PrivateKey,ProtoWallet,PublicKey,Transaction,P2PKH} from "@bsv/sdk";
import {canonical,bytes,paymentAuthority,spendingScope,multiScope} from "./model.js";
import {loginProtocol,loginScope} from "./portal-model.js";
const wallet=new ProtoWallet(new PrivateKey(19)),params=new URLSearchParams(location.search);
window.CWI=wallet;
window.previewNavigate=()=>{window.location.href=new URL("./session/index.html?scenario=active",window.location.href).href;};
window.portalPreviewCalls=[];
window.portalPreviewWallet={imports:0,acks:0};
let signedIn=params.has("restored")||params.has("cookie");
let publicApproved=false;
if(params.has("restored")){
  // A history cookie is present but the injected wallet is absent. It must
  // not be treated as already connected or used to restore private history.
  window.CWI=undefined;
  document.addEventListener("click",event=>{
    if(event.target.id==="portal-reconnect")window.CWI=wallet;
  },true);
}
const identity=(await wallet.getPublicKey({identityKey:true})).publicKey;
const sign=wallet.createSignature.bind(wallet);
window.portalPreviewWallet.signatures=0;
wallet.createSignature=async args=>{
  window.portalPreviewWallet.signatures++;
  if(params.has("deny"))throw Error("Wallet permission declined");
  if(params.has("slow"))await new Promise(resolve=>{window.releasePreviewWallet=resolve;});
  return sign(args);
};
const rows=Array.from({length:27},(_,i)=>({
  session_key:`fixture|session-${i}`,session_id:`sigen-proxy-fictional-${i}`,
  transaction_id:`EV-20261004-${String(i+1).padStart(3,"0")}`,
  opened_at:new Date(Date.UTC(2026,9,4,7)-i*86400000).toISOString(),
  ended_at:new Date(Date.UTC(2026,9,4,8)-i*86400000).toISOString(),
  import_kwh:i%2?7.8:0,export_kwh:i%2?0:6.4,
  import_cost_aud:i%2?2.184:0,export_credit_aud:i%2?0:1.19,
  net_amount_aud:i%2?2.184:-1.19,quality_flags:i===1?["provisional_metering"]:[],
  agreements:[{state:"expired",expires_at:"2026-10-03T00:00:00Z"}],
  transactions:[{id:`fixture-${i}`,direction:i%2?"driver_to_operator":"operator_to_driver",
    state:i===2?"provider_unconfirmed":"provider_confirmed",
    wallet_receipt_status:i%2?null:"wallet_reported_accepted",wallet_imported_at:"2026-10-04T08:01:00Z",amount_sats:i%2?218:119,
    fee_sats:36,txid:"a".repeat(64)}],
}));
if(params.has("adjustments")){
  for(const [i,direction,amount,rate] of [[0,"export",60,"0.1207512"],[1,"import",185,"0.3691626"]]){
    Object.assign(rows[i],{account_kind:"manual_energy_adjustment",adjustment_kwh:"5",
      adjustment_direction:direction,adjustment_price_aud_per_kwh:rate,
      wallet_connected_adjustment:i===1,import_kwh:null,export_kwh:null,
      import_cost_aud:null,export_credit_aud:null,quality_flags:["manual_energy_adjustment"],
      net_amount_aud:i===0?"-0.6037560":"1.8458130",agreements:[],
      opened_at:new Date().toISOString(),ended_at:new Date().toISOString(),
      transactions:[{id:"adjustment:fictional-"+i,direction:i===0?"operator_to_driver":"driver_to_operator",
        state:i===0?"provider_unconfirmed":"ready",amount_sats:amount,fee_sats:i===0?23:null,
        txid:i===0?"a".repeat(64):null,wallet_receipt_status:"not_recorded"}]});
  }
}
if(params.has("timing")){
  Object.assign(rows[0].transactions[0],{
    state:"provider_confirmed",wallet_receipt_status:"wallet_reported_accepted",
    broadcast_attempted_at:"2026-10-07T08:57:52.171Z",
    broadcast_acknowledged_at:"2026-10-07T08:57:53.400Z",
    provider_first_confirmed_at:"2026-10-07T09:08:10.000Z",
    wallet_accepted_reported_at:"2026-10-07T09:08:15.300Z",
  });
}
if(params.has("active")){
  Object.assign(rows[0],{ended_at:null,running_state:"Charging",import_kwh:0,export_kwh:.530,
    ocpp:{available:true,status:"Charging",checked_at:new Date().toISOString()},
    import_cost_aud:0,export_credit_aud:.06,net_amount_aud:-.06,
    meter_updated_at:new Date(Date.now()-(params.has("stalemeter")?3600000:0)).toISOString(),satoshis_per_aud:"100",
    transactions:[{id:"fixture-active",state:"waiting_for_session_end",direction:"operator_to_driver",amount_sats:null}]});
}
const operator=new PrivateKey(101),terms={
  version:2,budget_id:"11111111-2222-4333-8444-555555555555",
  session_id:rows[0].session_id,transaction_id:rows[0].transaction_id,session_mode:"existing_session",
  network:"BSV mainnet",operator_identity:operator.toPublicKey().toString(),
  operator_address:operator.toPublicKey().toAddress(),operator_name:"Fictional charging operator",
  operator_contact:"Offline fixture",max_total_sats:1000,max_fee_sats:1000,satoshis_per_aud:"100",
  pricing_rule:"Interval energy × dynamic rate",account_scope:"One session",
  import_price_entity:"sensor.demo_import",export_price_entity:"sensor.demo_export",
  created_at:new Date(Date.now()-86400000).toISOString(),expires_at:new Date(Date.now()-3600000).toISOString(),
  scope:spendingScope,credit_receiving:{protocolID:[2,"3241645161d8"],derivationPrefix:"YWJj",derivationSuffix:"ZGVm"},
};
terms.payment_authority=paymentAuthority(terms);
const payload=canonical(terms),invitation={version:1,payload,signature:operator.sign(bytes(payload)).toDER("hex")};
const publicTerms={...terms,version:3,scope:multiScope,session_mode:"multi_session",
  account_scope:"Future sessions under one seven-day total",
  created_at:new Date().toISOString(),expires_at:new Date(Date.now()+7*86400000).toISOString()};
publicTerms.payment_authority=paymentAuthority(publicTerms);
const publicPayload=canonical(publicTerms),publicInvitation={version:1,payload:publicPayload,
  signature:operator.sign(bytes(publicPayload)).toDER("hex")};
const {publicKey}=await wallet.getPublicKey({protocolID:terms.credit_receiving.protocolID,keyID:"YWJj ZGVm",
  counterparty:terms.operator_identity,forSelf:true});
const address=PublicKey.fromString(publicKey).toAddress(),tx=new Transaction();
tx.addOutput({lockingScript:new P2PKH().lock(address),satoshis:119});
const receipt={...rows[0].transactions[0],session_id:rows[0].session_id,transaction_id:rows[0].transaction_id,
  raw_tx:tx.toHex(),txid:tx.id("hex"),budget_id:terms.budget_id,credit_id:"fixture-0",
  recipient_address:address,remittance:terms.credit_receiving,sender_identity:terms.operator_identity,
  proof:{txOrId:tx.id("hex"),index:0,nodes:[],target:"11".repeat(32)},
  block:{hash:"11".repeat(32),height:800000,merkleroot:tx.id("hex")}};
if(params.has("sync")||params.has("report")||params.has("automatic")){
  Object.assign(rows[0].transactions[0],{txid:receipt.txid,wallet_receipt_status:"not_recorded",wallet_imported_at:null});
}
wallet.getNetwork=async()=>({network:"mainnet"});
if(params.has("wrongwallet"))wallet.getNetwork=async()=>({network:"testnet"});
wallet.internalizeAction=async()=>{window.portalPreviewWallet.imports++;return {accepted:true};};
wallet.createAction=wallet.signAction=async()=>{throw Error("Forbidden payment in offline preview");};
let failedReport=false;
window.fetch=async(url,options)=>{
  if(url==="/api/bsv_settlement/driver"){
    const d=JSON.parse(options.body);window.portalPreviewCalls.push(d.action);
    if(d.action==="public_invitation")return Response.json(params.has("registration")&&!publicApproved?{state:"available",
      public_link_fragment:"#join=11111111-2222-4333-8444-555555555555&key="+"x".repeat(43)}:{state:"unavailable"});
    if(d.action==="public_read")return Response.json({state:"awaiting_driver_consent",invitation:publicInvitation,
      prices:{valid:true,checked_at:new Date().toISOString(),...Object.fromEntries(["import","export"].map(k=>[k,{
        available:true,aud_per_kwh:k==="import"?"0.285":"-0.052",start:new Date(Date.now()-60000).toISOString(),
        end:new Date(Date.now()+300000).toISOString()}]))}});
    if(d.action==="public_approve"){publicApproved=true;return Response.json({state:"spending_authorised_wallet_permission_required",driver_identity:identity,
      automatic_credit_enabled:true,credit_destination_registered:false,
      private_link_fragment:"#budget=11111111-2222-4333-8444-555555555555&token="+"p".repeat(43)});}
    if(d.action==="register_credit_destination")return Response.json({state:"credit_destination_registered"});
    throw Error("Offline preview: unsupported driver action");
  }
  if(url!=="/api/bsv_settlement/portal")throw Error("Offline preview: network disabled");
  const data=JSON.parse(options.body);window.portalPreviewCalls.push(data.action);
  const response=(body,status=200)=>new Response(JSON.stringify(body),{status,headers:{"Content-Type":"application/json"}});
  if(data.action==="prices"){
    if(params.has("pricefail"))throw Error("Fictional price outage");
    const stamp=Date.now(),rate={available:true,estimate:params.has("estimated"),
      start:new Date(stamp-300000).toISOString(),end:new Date(stamp+(params.has("stale")?-1000:300000)).toISOString()};
    return response({checked_at:new Date(stamp).toISOString(),valid:true,
      import:{...rate,aud_per_kwh:"0.2850"},export:{...rate,aud_per_kwh:"-0.0520"}});
  }
  if(data.action==="challenge"){
    const now=Math.floor(Date.now()/1000),nonce="a".repeat(43);
    return response({payload:canonical({action:"sign_in_driver_portal",version:1,origin:location.origin,
      nonce,browser_binding:"b".repeat(64),scope:loginScope,issued_at:now,expires_at:now+120}),
      protocolID:loginProtocol,keyID:nonce});
  }
  if(data.action==="login"){
    if(params.has("reject"))return response({error:"Mock wallet sign-in rejected"},401);
    signedIn=true;return response({identity,expires_in:900});
  }
  if(data.action==="logout"){signedIn=false;return response({signed_out:true});}
  if(data.action==="credit_receipt"&&signedIn)return response({invitation,receipt});
  if(data.action==="acknowledge_credit_receipt"&&signedIn){
    window.portalPreviewWallet.acks++;
    if(params.has("report")&&!failedReport){failedReport=true;throw Error("Offline fixture: receipt report interrupted");}
    const row=rows[0].transactions[0];
    Object.assign(row,{wallet_receipt_status:"wallet_reported_accepted",wallet_imported_at:new Date().toISOString()});
    return response({...row,session_id:rows[0].session_id,transaction_id:rows[0].transaction_id,
      recipient_address:address,credit_id:"fixture-0",budget_id:terms.budget_id});
  }
  if(data.action==="sessions"){
    // Reproduce a real HA response crossing the one-second expiry watchdog.
    if(params.has("slowhistory"))await new Promise(resolve=>setTimeout(resolve,1500));
    if(!signedIn)return response({error:"Sign in"},401);
    const all=params.has("empty")?[]:rows,offset=data.offset||0;
    return response({identity,sessions:all.slice(offset,offset+25),total:all.length,
      authorisations:params.has("approved")||publicApproved?[{scope:"weekly",spending_active:true,limit_sats:1000,
        expires_at:new Date(Date.now()+6*86400000).toISOString(),receiving_registered:true}]:[],
      wallet_metadata:{addresses:[{budget_id:terms.budget_id,scope:"weekly",
        state:"spending_authorised_wallet_permission_required",expires_at:publicTerms.expires_at,
        operator_identity:terms.operator_identity,payment_address:terms.operator_address,
        receiving_address:address,receiving_verified:true}]},
      expires_in:params.has("expire")?2:params.has("renew")?240:900});
  }
  if(data.action==="debit_jobs"&&signedIn)return response({jobs:params.has("held")?
    [{budget_id:terms.budget_id,session_id:rows[0].session_id}]:[],has_more:false});
  if(data.action==="debit_status"&&signedIn)return response({collection:{state:"broadcast_unknown"}});
  if(data.action==="registration_offer")return params.has("registration")&&!publicApproved
    ?response({state:"awaiting_driver_consent",invitation:publicInvitation})
    :response({state:"existing_approval"});
  if(data.action==="registration_read")return window.fetch("/api/bsv_settlement/driver",
    {body:JSON.stringify({action:"public_read"})});
  if(data.action==="registration_accept")return window.fetch("/api/bsv_settlement/driver",
    {body:JSON.stringify({action:"public_approve",receipt:data.receipt})});
  if(data.action==="registration_receive")return window.fetch("/api/bsv_settlement/driver",
    {body:JSON.stringify({action:"register_credit_destination"})});
  if(data.action==="pairing_create")return response({error:"Offline preview: real QR pairing is disabled"},401);
  const scenario=params.get("monthly")||"off";
  if(data.action==="station")return response(scenario==="off"?{monthly_enabled:false,station_ids:[]}:
    {monthly_enabled:true,station_ids:["garage-1"],operator_identity:operator.toPublicKey().toString(),monthly_limit_sats:30000});
  if(data.action.startsWith("monthly_")){
    if(!signedIn)return response({error:"Sign in"},401);
    if(scenario==="off")return data.action==="monthly_status"?response({enabled:false,readiness:{automatic_collection:false,missing:["monthly_disabled"]}}):
      response({error:"Monthly charging is not enabled at this station.",code:"disabled"},409);
    return monthlyPreview(data,response);
  }
  throw Error("Offline preview: no payment or receipt mutations supported");
};
// Offline monthly fixture: mirrors S3 responses; nothing is persisted or paid.
const month={year:2026,month:10,timezone:"Australia/Brisbane"};
const monthly={revision:4,authority:["active","unverified","cancelled"].includes(params.get("monthly"))?
  {authority_id:"preview-authority",state:params.get("monthly")==="cancelled"?"cancelled":"active",
   accepted_at:"2026-10-01T08:00:00+10:00",station_ids:["garage-1"],
   period:{policy_id:"fixture",wallet_version:"fictional-wallet-1",timezone:month.timezone}}:null,terms:null};
function monthlyStatus(){
  const a=monthly.authority,missing=[];
  if(!a||a.state==="cancelled")missing.push("monthly_authority");
  const grant=a&&a.state==="active"?(params.get("monthly")==="unverified"?"unverified":"verified"):"not_applicable";
  if(grant==="unverified")missing.push("wallet_monthly_permission");
  return {enabled:true,revision:monthly.revision,station_ids:["garage-1"],monthly_limit_sats:30000,challenge_pending:false,
    authority:a&&{...a,wallet_permission_revoked:a.state==="cancelled"?"not_verified":null},
    allowance:a?{month,limit_sats:30000,spent_sats:1240,reserved_sats:255,remaining_sats:28505,over_limit_sats:0,blocked:false,reasons:[]}:null,
    native_grant:grant==="verified"?{state:"verified",remaining_sats:28505,month,observed_at:new Date().toISOString()}:{state:grant},
    receiving:{registered:true,routes_new_credits:true},readiness:{automatic_collection:!missing.length,missing}};
}
async function monthlyPreview(data,response){
  window.portalPreviewCalls.push("monthly:"+data.action);
  if(data.action==="monthly_status")return response(monthlyStatus());
  if(data.action==="monthly_challenge"){
    const now=Date.now(),iso=ms=>new Date(ms).toISOString().replace("Z","+00:00");
    monthly.terms={version:4,scope:"recurring_calendar_month_charging_including_driver_fees",network:"BSV mainnet",
      authority_id:"preview-authority",nonce:"ab".repeat(32),driver_identity:identity,operator_identity:operator.toPublicKey().toString(),
      operator_address:operator.toPublicKey().toAddress(),origin:location.hostname,station_ids:["garage-1"],monthly_limit_sats:30000,
      period_policy:{policy_id:"fixture",wallet_version:"fictional-wallet-1",timezone:month.timezone,booking_event:"fixture",
        fee_basis:"all_driver_paid_wallet_debits",evidence_ref:"offline-preview"},issued_at:iso(now),accept_before:iso(now+600000),
      effective_at:iso(now),recurs_until_cancelled:true,collection_policy:"one_final_net_payment_per_session_on_closure",
      credits_refill:false,unused_carries_forward:false,conversion_policy:"freeze_configured_sat_per_aud_at_session_binding",
      included_session:null};
    monthly.revision++;
    return response({authority_id:"preview-authority",terms:monthly.terms,keyID:"preview-authority",
      payload:canonical({version:4,action:"authorise_monthly_charging",terms:monthly.terms}),
      protocolID:[2,"ev monthly spending"],revision:monthly.revision});
  }
  if(data.action==="monthly_accept"){
    monthly.revision++;
    monthly.authority={authority_id:"preview-authority",state:"active",accepted_at:new Date().toISOString(),
      station_ids:["garage-1"],period:{policy_id:"fixture",wallet_version:"fictional-wallet-1",timezone:month.timezone}};
    return response({accepted:true,authority_id:"preview-authority",accepted_at:monthly.authority.accepted_at,revision:monthly.revision});
  }
  if(data.action==="monthly_cancel_challenge")return response({authority_id:"preview-authority",keyID:"preview-authority",
    protocolID:[2,"ev monthly spending"],revision:monthly.revision,
    payload:canonical({version:4,action:"cancel_monthly_charging",authority_id:"preview-authority",driver_identity:identity,terms_hash:"cd".repeat(32)})});
  if(data.action==="monthly_cancel"){monthly.revision++;monthly.authority.state="cancelled";
    return response({cancelled:true,wallet_permission_revoked:"not_verified",revision:monthly.revision});}
  return response({error:"unavailable",code:"unavailable"},409);
}
const banner=document.createElement("p");
banner.className="notice";banner.textContent="OFFLINE DESIGN PREVIEW · Fictional wallet and sessions. No payments or live connections.";
const previewNav=document.createElement("p");
previewNav.innerHTML='<a href="?active&approved">Weekly approval and live session</a> · <a href="?registration">New registration</a> · <a href="./session/index.html?scenario=approval">Private budget approval</a> · <a href="./session/index.html?scenario=unconfirmed">Settlement</a>';
banner.append(previewNav);
banner.style.cssText="max-width:1012px;width:calc(100% - 32px);margin:16px auto";
await import("./portal.js");
document.querySelector("main").append(banner);
