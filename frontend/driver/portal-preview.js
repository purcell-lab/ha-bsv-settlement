// Offline UI fixture only. No real wallet, relay, provider, payments or credentials.
import {PrivateKey,ProtoWallet,PublicKey,Transaction,P2PKH} from "@bsv/sdk";
import {canonical,bytes,paymentAuthority,spendingScope} from "./model.js";
import {loginProtocol,loginScope} from "./portal-model.js";
const wallet=new ProtoWallet(new PrivateKey(19)),params=new URLSearchParams(location.search);
window.CWI=wallet;
window.portalPreviewCalls=[];
window.portalPreviewWallet={imports:0,acks:0};
let signedIn=params.has("restored");
if(params.has("restored")){
  // A history cookie is present; the injected wallet arrives only after the
  // explicit reconnect gesture. It must not be treated as already connected.
  window.CWI=undefined;
  document.addEventListener("click",event=>{
    if(event.target.id==="portal-reconnect")window.CWI=wallet;
  },true);
}
const identity=(await wallet.getPublicKey({identityKey:true})).publicKey;
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
if(params.has("active")){
  Object.assign(rows[0],{ended_at:null,import_kwh:0,export_kwh:.530,
    import_cost_aud:0,export_credit_aud:.06,net_amount_aud:-.06,
    meter_updated_at:new Date().toISOString(),satoshis_per_aud:"100",
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
  if(url==="/api/bsv_settlement/driver"&&JSON.parse(options.body).action==="public_invitation")
    return Response.json(params.has("registration")?{state:"available",
      public_link_fragment:"#join=11111111-2222-4333-8444-555555555555&key="+"x".repeat(43)}:{state:"unavailable"});
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
    if(!signedIn)return response({error:"Sign in"},401);
    const all=params.has("empty")?[]:rows,offset=data.offset||0;
    return response({identity,sessions:all.slice(offset,offset+25),total:all.length,
      expires_in:params.has("expire")?2:900});
  }
  if(data.action==="pairing_create")return response({error:"Offline preview: real QR pairing is disabled"},401);
  throw Error("Offline preview: no payment or receipt mutations supported");
};
const banner=document.createElement("p");
banner.className="notice";banner.textContent="OFFLINE DESIGN PREVIEW · Fictional wallet and sessions. No payments or live connections.";
const previewNav=document.createElement("p");
previewNav.innerHTML='<a href="./session/index.html?scenario=approval">Try budget approval</a> · <a href="./session/index.html?scenario=active">Charging</a> · <a href="./session/index.html?scenario=unconfirmed">Settlement</a>';
banner.append(previewNav);
banner.style.cssText="max-width:1012px;width:calc(100% - 32px);margin:16px auto";
await import("./portal.js");
document.querySelector("main").append(banner);
