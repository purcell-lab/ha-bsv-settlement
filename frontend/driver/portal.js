import {WalletClient} from "@bsv/sdk";
import {BrowserPairing} from "./pairing.js";
import {signPortalLogin,averageNet,averageRate,transactionStatus,sessionSummary,compactIdentity} from "./portal-model.js";
import {parseInvitation} from "./model.js";
import {importAndReportCredit,receiptReported} from "./credit.js";
import {chainRecordUrl} from "../ui.js";
import qrcode from "qrcode-generator";
import {priceCard} from "./portal-prices.js";

const priceCards=prefix=>`<div class="price-grid portal-prices" aria-label="Current energy rates">
<div><span>Buy (Import/ EV Charging) rate</span><strong id="${prefix}-buy">Unavailable</strong></div>
<div><span>Sell (Export/ V2G) rate</span><strong id="${prefix}-sell">Unavailable</strong></div>
</div><p id="${prefix}-price-note" class="small portal-price-note">Checking current rates. Indicative only, not a fixed session quote.</p>`;
document.title="My charging sessions | BSV Settlement";
document.querySelector("main").innerHTML=`
<div class="intro"><p class="eyebrow">DRIVER PORTAL</p><h1>My charging sessions</h1>
<p>One address for your charging history, payments and credit receipts.</p></div>
<section id="portal-signin"><h2>Your wallet is your sign-in</h2>
<p>Prove control of your wallet to see only your recorded sessions. Signing in does not authorise spending, reserve funds or start charging.</p>
${priceCards("signin")}
<div class="actions"><button id="portal-login">Sign in with wallet</button><button id="portal-pair" class="secondary">Pair and sign in with BSV Browser</button></div>
<p class="small">Use wallet sign-in on this device. If your wallet is on a phone, pair it using a fresh QR or connection URI. No private session link is needed.</p></section>
<p id="portal-status" class="notice" role="status" aria-live="polite">Sign in to load your private history. Wallet permission prompts may appear.</p>
<section id="portal-pairing" hidden><h2>Connect BSV Browser</h2><p id="portal-pair-status" role="status"></p>
<div id="portal-qr" class="session-link-qr"></div>
<label for="portal-uri">Pairing URI for Connect to App → Paste URI</label>
<textarea id="portal-uri" readonly rows="3" spellcheck="false"></textarea>
<div class="actions"><button id="portal-copy" class="secondary">Copy pairing URI</button><button id="portal-disconnect" class="secondary">Cancel pairing</button></div>
<p class="small">The code expires after two minutes. Keep this page open. Saved connections cannot restore an expired code; create a fresh one here.</p></section>
<section id="portal-account" hidden aria-label="Verified wallet">
<div class="portal-wallet-toolbar">
<div class="portal-wallet-heading"><h2>Wallet verified</h2><span id="portal-identity-short" class="mono" aria-label="Wallet identity preview"></span></div>
<div class="portal-wallet-actions"><button id="portal-refresh" class="secondary" aria-label="Refresh session history">Refresh</button><button id="portal-sync" aria-label="Sync confirmed credits on this page">Sync credits</button><button id="portal-logout" class="secondary">Sign out</button></div>
</div>
${priceCards("account")}
<details id="portal-wallet-details"><summary>Wallet details <span id="portal-expiry"></span></summary>
<p class="small">Full wallet identity</p><p id="portal-identity" class="mono"></p>
<p class="small">This is verified sign-in, not a live wallet connection or spending approval. Signing out or restarting the operator service ends private access.</p>
<p class="small">Sync credits applies to sessions loaded on this page. It imports existing confirmed payments and records wallet acceptance; it never sends another payment.</p></details></section>
<section id="portal-history" hidden><div class="section-head"><h2>Sessions and transactions</h2><span id="portal-count" class="badge"></span></div>
<p class="small">Tap a session for details. ! indicates a metering warning.</p>
<p id="portal-empty" hidden>No sessions are linked to this wallet yet. New charging authority must be registered separately by the operator.</p>
<div id="portal-sessions"></div><button id="portal-more" class="secondary wide" hidden>Load more sessions</button>
<p class="small">Only retained charging records with verified wallet ownership are shown. Unattributed legacy payments and unrelated wallet activity are excluded. Average net $/kWh excludes network fees. Chain confirmation is provider-reported; wallet acceptance is a separate signed report.</p></section>
<details class="portal-recovery"><summary>Existing invitation or private-link recovery</summary>
<p>New spending approval remains a separate action. If the operator supplied an invitation, open its complete link. Sign-in cannot assign an unclaimed charging session to you.</p>
<label for="portal-private">Existing private session or registration link</label><textarea id="portal-private" rows="2" spellcheck="false"></textarea>
<button id="portal-open" class="secondary">Open existing invitation</button></details>`;
const $=id=>document.getElementById(id),message=text=>{$("portal-status").textContent=text;};
const framed=window.top!==window;
let wallet=null,identity=null,pairing=null,busy=false,sessions=[],total=0,expires=0,generation=0;
let prices=null,pricesBusy=false;
const imports=new Map();
const expandedSessions=new Set();
function controls(){
  for(const id of ["portal-login","portal-pair","portal-refresh","portal-sync","portal-logout","portal-more","portal-open"])
    $(id).disabled=busy||framed;
  $("portal-sync").disabled=busy||framed||!sessions.some(s=>s.transactions.some(t=>t.direction==="operator_to_driver"&&t.state==="provider_confirmed"&&!receiptReported(t)));
}
async function api(action,extra={}){
  const r=await fetch("/api/bsv_settlement/portal",{method:"POST",credentials:"same-origin",cache:"no-store",
    referrerPolicy:"no-referrer",headers:{"Content-Type":"application/json"},body:JSON.stringify({action,...extra}),
    signal:AbortSignal.timeout(20000)});
  const data=await r.json();
  if(!r.ok){const error=Error(data.error||"Portal request failed");error.status=r.status;throw error;}
  return data;
}
function paintPrices(){
  const buy=priceCard(prices,"import"),sell=priceCard(prices,"export");
  const note=buy.available&&sell.available
    ?`Checked ${new Date(prices.checked_at).toLocaleTimeString([],{hour:"2-digit",minute:"2-digit"})}. Indicative only, not a fixed session quote.`
    :"A current verified rate is unavailable. Indicative only, not a fixed session quote.";
  for(const prefix of ["signin","account"]){
    $(`${prefix}-buy`).textContent=buy.value;$(`${prefix}-sell`).textContent=sell.value;
    $(`${prefix}-price-note`).textContent=note;
  }
}
async function refreshPrices(){
  if(pricesBusy||framed||document.hidden)return;
  pricesBusy=true;
  try{prices=await api("prices");}catch{prices=null;}
  finally{pricesBusy=false;paintPrices();}
}
function clearPrivate(){
  if(pairing){void pairing.disconnect();pairing=null;$("portal-pairing").hidden=true;}
  generation++;identity=null;wallet=null;sessions=[];total=0;expires=0;imports.clear();
  expandedSessions.clear();
  $("portal-sessions").replaceChildren();$("portal-identity").textContent="";
  $("portal-identity-short").textContent="";$("portal-identity-short").removeAttribute("title");
  $("portal-expiry").textContent="";$("portal-wallet-details").open=false;
  $("portal-account").hidden=true;$("portal-history").hidden=true;$("portal-signin").hidden=false;
}
function node(tag,text,cls){const e=document.createElement(tag);e.textContent=text;if(cls)e.className=cls;return e;}
const number=(v,suffix="")=>v===null||v===undefined||v===""||!Number.isFinite(Number(v))?"Unavailable":`${Number(v).toFixed(3)}${suffix}`;
function render(){
  $("portal-signin").hidden=!!identity;$("portal-account").hidden=!identity;$("portal-history").hidden=!identity;
  $("portal-identity").textContent=identity||"";$("portal-count").textContent=`${sessions.length} of ${total}`;
  $("portal-identity-short").textContent=compactIdentity(identity);
  $("portal-identity-short").title=identity||"";
  $("portal-empty").hidden=sessions.length>0;$("portal-more").hidden=sessions.length>=total;
  $("portal-expiry").textContent=`Expires ${new Date(expires).toLocaleTimeString([],{hour:"2-digit",minute:"2-digit"})}`;
  const list=$("portal-sessions");list.replaceChildren();
  for(const s of sessions){
    const box=node("details","","portal-session"),summary=node("summary","","portal-session-summary");
    box.open=expandedSessions.has(s.session_key);
    box.addEventListener("toggle",()=>{if(box.open)expandedSessions.add(s.session_key);else expandedSessions.delete(s.session_key);});
    const overview=sessionSummary(s),date=new Date(s.opened_at||s.ended_at||"");
    const shortDate=Number.isFinite(date.getTime())?date.toLocaleDateString("en-AU",{day:"2-digit",month:"2-digit"})+" "+
      date.toLocaleTimeString("en-AU",{hour:"2-digit",minute:"2-digit",hour12:false}):"No date";
    const arrow=node("span","›","portal-chevron");arrow.setAttribute("aria-hidden","true");
    summary.append(arrow,node("span",shortDate,"portal-row-date"),
      node("span",overview.payment,"portal-row-payment"),
      node("span",`${number(s.import_kwh)} in / ${number(s.export_kwh)} out kWh`,"portal-row-energy"),
      node("span",overview.status+(overview.warning?" !":""),"portal-row-status"));
    summary.setAttribute("aria-label",`${shortDate}, ${overview.payment}, ${overview.status}${overview.warning?", metering warning":""}. Session ${s.transaction_id||s.session_id}. Expand details.`);
    summary.title=`${s.transaction_id||s.session_id}: ${overview.payment}, ${overview.status}${overview.warning?", metering warning":""}`;
    box.append(summary);
    const table=node("table","","portal-detail-table"),caption=node("caption","Session details");
    table.append(caption);
    const body=document.createElement("tbody");table.append(body);
    const addRow=(label,value,cls="")=>{
      const tr=node("tr","",cls),th=node("th",label);th.scope="row";
      const td=document.createElement("td");
      if(value instanceof Node)td.append(value);else td.textContent=value;
      tr.append(th,td);body.append(tr);
    };
    addRow("Opened",s.opened_at?new Date(s.opened_at).toLocaleString():"Unavailable");
    addRow("Ended",s.ended_at?new Date(s.ended_at).toLocaleString():"Not recorded");
    addRow("Transaction ID",s.transaction_id||"Unavailable","portal-reference");
    const buy=averageRate(s.import_cost_aud,s.import_kwh),sell=averageRate(s.export_credit_aud,s.export_kwh);
    for(const [label,value] of [
      ["Energy Imported to EV",number(s.import_kwh," kWh")],["Energy Imported from EV",number(s.export_kwh," kWh")],
      ["Average buy price",buy===null?"Unavailable":`${buy.toFixed(4)} $/kWh`],
      ["Average sell price",sell===null?"Unavailable":`${sell.toFixed(4)} $/kWh`],
      ["Net energy account",number(s.net_amount_aud," AUD")],
      ["Average net price",averageNet(s)===null?"Unavailable":`${averageNet(s).toFixed(4)} $/kWh`]])
      addRow(label,value);
    if(s.quality_flags?.length)addRow("Metering warning",s.quality_flags.join(", "),"portal-warning");
    if(s.closure)addRow("Account",s.closure.state.replaceAll("_"," "));
    if(!s.transactions.length)addRow("Payment","No recorded payment. Sign-in does not collect this session.");
    for(const t of s.transactions){
      addRow(t.direction==="operator_to_driver"?"Credit to driver":"Payment to operator",
        Number.isSafeInteger(t.amount_sats)?t.amount_sats+" sat":"Amount not recorded","portal-payment-heading");
      addRow("Settlement status",transactionStatus(t));
      addRow("Network fee",Number.isSafeInteger(t.fee_sats)?t.fee_sats+" sat":"Not recorded");
      const url=chainRecordUrl(t.txid);
      if(url){const a=node("a","View chain-provider record");a.href=url;a.target="_blank";a.rel="noopener noreferrer";addRow("Transaction",a);}
    }
    addRow("Session ID",s.session_id,"portal-reference");
    addRow("Approval history",s.agreements.map(a=>`${a.state.replaceAll("_"," ")} · expires ${new Date(a.expires_at).toLocaleString()}`).join("; ")||"Original operator-credit routing");
    box.append(table);list.append(box);
  }
  controls();
}
async function load(more=false){
  const before=generation;
  const result=await api("sessions",{offset:more?sessions.length:0});
  if(before!==generation)return;
  if(identity&&result.identity!==identity)throw Error("Wallet sign-in changed. Sign out and sign in again.");
  identity=result.identity;expires=Date.now()+result.expires_in*1000;
  sessions=more?[...sessions,...result.sessions]:result.sessions;total=result.total;render();
}
async function run(fn){
  if(framed){message("Open the driver portal directly in your browser to sign in.");return;}
  if(busy)return;busy=true;controls();
  try{await fn();}catch(e){
    if(e.status===401){clearPrivate();message("Private access ended or the request could not be verified. Sign in again.");}
    else message(e.walletAccepted?e.message:"Action paused: "+e.message);
  }finally{busy=false;controls();}
}
async function signIn(candidate){
  const before=generation;
  message("Verify wallet identity. This signature is for sign-in only, not spending.");
  const proof=await signPortalLogin(candidate,await api("challenge"),location.origin);
  const result=await api("login",proof);
  if(before!==generation)throw Error("Sign-in was interrupted. Please try again.");
  if(result.identity!==proof.identity)throw Error("Sign-in identity mismatch.");
  wallet=candidate;identity=result.identity;imports.clear();await load();
  message("Signed in. No spending approved.");
}
$("portal-login").onclick=()=>run(()=>signIn(new WalletClient(window.CWI?"window.CWI":"auto")));
$("portal-refresh").onclick=()=>run(async()=>{await load();await refreshPrices();});
$("portal-more").onclick=()=>run(()=>load(true));
$("portal-logout").onclick=()=>run(async()=>{
  try{await api("logout");if(pairing)await pairing.disconnect();pairing=null;message("Signed out. Spending approvals and payments are unchanged.");}
  finally{clearPrivate();$("portal-pairing").hidden=true;}
});
$("portal-sync").onclick=()=>run(async()=>{
  const before=generation,driver=identity;
  const candidate=wallet||new WalletClient(window.CWI?"window.CWI":"auto");
  const actual=(await candidate.getPublicKey({identityKey:true})).publicKey;
  if(actual!==driver||before!==generation)throw Error("Use the same wallet that signed in.");
  if(pairing&&(!pairing.supportedMethods?.includes("getNetwork")||!pairing.supportedMethods?.includes("internalizeAction")))
    throw Error("This paired wallet supports sign-in but not verified receipt import. Use a compatible local wallet; payments remain unchanged.");
  for(const s of sessions)for(const t of s.transactions){
    if(t.direction!=="operator_to_driver"||t.state!=="provider_confirmed"||receiptReported(t))continue;
    if(before!==generation)throw Error("Private access ended. Sign in again to continue.");
    const response=await api("credit_receipt",{credit_id:t.id});
    if(response.receipt.txid!==t.txid||response.receipt.session_id!==s.session_id)throw Error("Receipt does not match this session.");
    const checked=parseInvitation(JSON.stringify(response.invitation),Date.now(),true);
    const scoped=(action,data)=>api(action,{...data,credit_id:t.id});
    const report=await importAndReportCredit(candidate,checked,response.receipt,driver,scoped,imports);
    if(before!==generation)return;
    Object.assign(t,report);
    render();
  }
  message("Confirmed receipts synced and acceptance recorded. No new payment was sent.");
});
$("portal-pair").onclick=()=>run(async()=>{
  if(pairing)await pairing.disconnect();
  $("portal-pairing").hidden=false;
  pairing=new BrowserPairing({api,origin:location.origin,receiptOnly:true,onState:(state,text)=>{
    $("portal-pair-status").textContent=text;
    $("portal-qr").replaceChildren();$("portal-uri").value="";
    if(state==="scanning"&&pairing.uri){
      const qr=qrcode(0,"M");qr.addData(pairing.uri,"Byte");qr.make();
      $("portal-qr").innerHTML=qr.createSvgTag({cellSize:4,margin:16,scalable:true});
      $("portal-qr").querySelector("svg").setAttribute("aria-label","Private portal wallet pairing code");
      $("portal-uri").value=pairing.uri;
    }
    if(state==="paired")void run(()=>signIn(pairing.wallet));
    if(state==="disconnected"){wallet=null;message("Pairing ended. Your private history remains available until sign-out or expiry; create a fresh code for wallet actions.");}
  }});
  await pairing.start();
});
$("portal-copy").onclick=async()=>{
  if(!$("portal-uri").value)return;
  try{await navigator.clipboard.writeText($("portal-uri").value);message("Pairing URI copied. Paste it into BSV Browser → Connect to App → Paste URI now.");}
  catch{$("portal-uri").focus();$("portal-uri").select();message("Select and copy the pairing URI. Keep it private.");}
};
$("portal-disconnect").onclick=()=>run(async()=>{if(pairing)await pairing.disconnect();pairing=null;$("portal-pairing").hidden=true;});
$("portal-open").onclick=async()=>{
  const {privateSessionUrl,publicEnrolmentUrl}=await import("./private-link.js");
  const input=$("portal-private").value.trim(),url=privateSessionUrl(input,location.origin)||publicEnrolmentUrl(input,location.origin);
  if(url)location.href=url;else message("Use a complete invitation from this operator. Never enter wallet keys.");
};
let theme=matchMedia("(prefers-color-scheme:dark)").matches?"dark":"light";
function paintTheme(){document.documentElement.dataset.theme=theme;$("theme").textContent=theme==="dark"?"Light":"Dark";}
$("theme").onclick=()=>{theme=theme==="dark"?"light":"dark";paintTheme();};paintTheme();
window.addEventListener("pagehide",()=>{if(pairing)void pairing.disconnect();});
document.addEventListener("visibilitychange",()=>{paintPrices();if(!document.hidden)void refreshPrices();});
setInterval(()=>void refreshPrices(),60000);
setInterval(paintPrices,1000); // Expire displayed data even if a fetch is stalled.
void refreshPrices(); // No wallet prompt, cookie creation or private history required.
setInterval(()=>{if(identity&&Date.now()>=expires){clearPrivate();message("Private access expired. Sign in again.");}},1000);
// Only restore a server-authenticated session; never prompt a wallet on public page load.
try{if(!framed){await load();message("Private history restored. Connect your wallet only to sync receipts.");}}
catch{clearPrivate();}controls();
if(framed)message("Open the driver portal directly in your browser to sign in.");
