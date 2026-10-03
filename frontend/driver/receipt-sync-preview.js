// Offline browser QA fixture, never imported by the production entry point.
import {PrivateKey,ProtoWallet,PublicKey,Transaction,P2PKH} from "@bsv/sdk";
import {canonical,bytes,paymentAuthority,spendingScope} from "./model.js";
const mode=new URLSearchParams(location.search).get("scenario")||"plain";
const banner=document.createElement("section");
banner.innerHTML=`<h2>Receipt sync preview</h2><p>Fictional credits and wallet only. No real funds, network calls or spending approval.</p>
<label for="preview-mode">Scenario</label><select id="preview-mode">
<option value="plain">Ordinary browser: manual sync</option><option value="auto">BSV Browser: automatic sync</option>
<option value="direct">Direct session credit</option><option value="ready">Ready debit plus existing credits</option>
<option value="wrong">Wrong wallet</option><option value="declined">Import declined</option>
<option value="report">Import accepted, reporting interrupted</option><option value="accepted">Already acknowledged</option>
<option value="unconfirmed">Awaiting block confirmation</option><option value="late">Late wallet injection</option></select>`;
document.querySelector("main").prepend(banner);
document.getElementById("preview-mode").value=mode;
document.getElementById("preview-mode").onchange=e=>{location.search="?scenario="+e.target.value;};
const operator=new PrivateKey(101),driver=new ProtoWallet(new PrivateKey(102));
const identity=(await driver.getPublicKey({identityKey:true})).publicKey;
const terms={
  version:2,budget_id:"11111111-2222-4333-8444-555555555555",
  session_id:"fictional-session",transaction_id:"fictional-proxy",session_mode:"existing_session",
  network:"BSV mainnet",operator_identity:operator.toPublicKey().toString(),
  operator_address:operator.toPublicKey().toAddress(),operator_name:"Community charging demo",
  operator_contact:"Fictional operator · no real payments",max_total_sats:1000,max_fee_sats:1000,
  satoshis_per_aud:"100",pricing_rule:"Interval energy × dynamic rate",account_scope:"One session",
  import_price_entity:"sensor.demo_import",export_price_entity:"sensor.demo_export",
  created_at:new Date(Date.now()-86400000).toISOString(),expires_at:new Date(Date.now()-3600000).toISOString(),
  scope:spendingScope,credit_receiving:{protocolID:[2,"3241645161d8"],derivationPrefix:"YWJj",derivationSuffix:"ZGVm"},
};
terms.payment_authority=paymentAuthority(terms);
const payload=canonical(terms),invitation={version:1,payload,signature:operator.sign(bytes(payload)).toDER("hex")};
const {publicKey}=await driver.getPublicKey({protocolID:terms.credit_receiving.protocolID,keyID:"YWJj ZGVm",
  counterparty:terms.operator_identity,forSelf:true});
const address=PublicKey.fromString(publicKey).toAddress(),rows=[],receipts=[];
for(const [index,amount] of [119,38].entries()){
  const tx=new Transaction();tx.addOutput({lockingScript:new P2PKH().lock(address),satoshis:amount});
  const txid=tx.id("hex"),session_id=mode==="direct"?terms.session_id:`fictional-credit-${index}`;
  const row={credit_id:`fictional-credit-${index}`,session_id,transaction_id:session_id,txid,
    amount_sats:amount,fee_sats:23,recipient_address:address,
    state:mode==="unconfirmed"?"provider_unconfirmed":"provider_confirmed",
    wallet_receipt_status:"not_recorded"};
  if(mode==="accepted")Object.assign(row,{wallet_receipt_status:"wallet_reported_accepted",wallet_imported_at:new Date().toISOString()});
  rows.push(row);receipts.push({...row,raw_tx:tx.toHex(),budget_id:terms.budget_id,
    remittance:terms.credit_receiving,sender_identity:terms.operator_identity,
    proof:{txOrId:txid,index:0,nodes:[],target:"11".repeat(32)},
    block:{hash:"11".repeat(32),height:800000,merkleroot:txid}});
}
if(mode==="direct"){rows.splice(1);receipts.splice(1);delete rows[0].credit_id;delete receipts[0].credit_id;}
const calls=window.previewCalls={imports:0,acks:0,identity:0,forbidden:[],actions:[]};
let failedReport=false,declined=false;
const injected={
  getVersion:async()=>({version:"offline-fixture"}),
  getNetwork:async()=>({network:"mainnet"}),
  getPublicKey:async args=>{
    if(args.identityKey)calls.identity++;
    if(mode==="wrong"&&args.identityKey)return {publicKey:operator.toPublicKey().toString()};
    return driver.getPublicKey(args);
  },
  createSignature:async args=>{
    if(args.protocolID[1]!=="ev credit receipt"){calls.forbidden.push("spending signature");throw Error("No spending in preview");}
    return driver.createSignature(args);
  },
  internalizeAction:async()=>{
    calls.imports++;
    if(mode==="declined"&&!declined){declined=true;return {accepted:false};}
    return {accepted:true};
  },
  createAction:async()=>{calls.forbidden.push("createAction");throw Error("No payment in preview");},
  signAction:async()=>{calls.forbidden.push("signAction");throw Error("No payment in preview");},
};
if(!["plain","late"].includes(mode))window.CWI=injected;
// Manual fixture injects before the app's real click handler runs.
document.addEventListener("click",e=>{
  if(["receive-ongoing","resume-collection"].includes(e.target.id))window.CWI=injected;
},true);
window.injectPreviewWallet=()=>{window.CWI=injected;};
window.fetch=async(url,options)=>{
  if(url!=="/api/bsv_settlement/driver")throw Error("Offline fixture blocks external requests");
  const body=JSON.parse(options.body);calls.actions.push(body.action);
  if(body.action==="read")return Response.json({invitation,state:"spending_authorised_wallet_permission_required",
    driver_identity:identity,automatic_credit_enabled:true,credit_destination_registered:true,
    ongoing_credit_enabled:true,ongoing_credits:mode==="direct"?[]:rows,
    session:{ended_at:new Date().toISOString(),import_kwh:"0.01",export_kwh:"12.1",net_cost_aud:"-1.19"}});
  if(body.action==="collection_status")return Response.json(mode==="direct"?
    {...rows[0],direction:"operator_to_driver"}:{state:mode==="ready"?"ready":"provider_confirmed",
      direction:"driver_to_operator",session_id:terms.session_id,txid:"a".repeat(64)});
  if(["credit_receipt","ongoing_credit_receipt"].includes(body.action))
    return Response.json(receipts.find(r=>r.credit_id===body.credit_id)||receipts[0]);
  if(body.action==="acknowledge_credit_receipt"){
    calls.acks++;
    if(mode==="report"&&!failedReport){failedReport=true;throw Error("Fictional interrupted report");}
    const ack=JSON.parse(body.acknowledgement.payload),row=rows.find(r=>r.txid===ack.txid);
    Object.assign(row,{wallet_receipt_status:"wallet_reported_accepted",wallet_imported_at:new Date().toISOString()});
    return Response.json(row);
  }
  calls.forbidden.push(body.action);
  return Response.json({error:"Fixture refused unexpected operation"},{status:400});
};
await import("./app.js");
