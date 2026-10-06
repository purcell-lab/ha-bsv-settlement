import {WalletClient} from "@bsv/sdk";
import {BrowserPairing} from "./pairing.js";
import {signPortalLogin,transactionStatus,sessionSummary,compactIdentity,provisionalSession} from "./portal-model.js";
import {parseInvitation} from "./model.js";
import {importAndReportCredit,receiptReported} from "./credit.js";
import {chainRecordUrl} from "../ui.js";
import qrcode from "qrcode-generator";
import {priceCard} from "./portal-prices.js";
import {mountDriverToolbar,qrText} from "./navigation.js";
import {publicEnrolmentUrl} from "./private-link.js";
import {ReceiptSync} from "./receipt-sync.js";
import {pendingCreditJobs} from "./portal-credit-jobs.js";
import {walletStatus,verifyReceivingWallet} from "./portal-wallet.js";
import {MonthlySetup,cancelMonthly} from "./monthly-wallet.js";
import {sessionAccount,formatRate,formatKwh} from "./account-projection.js";
import {readinessView,setupAction,allowanceRows,termsList,signedTermsRows,authorityText,grantText} from "./monthly-ui.js";
import {stationFlow} from "./station-flow.js";

const priceCards=prefix=>`<div class="price-grid portal-prices" aria-label="Current energy rates">
<div><span title="Buy (Import/ EV Charging) rate">Buy / EV charging</span><strong id="${prefix}-buy">Unavailable</strong></div>
<div><span title="Sell (Export/ V2G) rate">Sell / V2G export</span><strong id="${prefix}-sell">Unavailable</strong></div>
</div><p id="${prefix}-price-note" class="small portal-price-note">Checking current rates. Indicative only, not a fixed session quote.</p>`;
document.title="Your EV charging | BSV Settlement";
const rateHelp=`<details class="rate-help"><summary>How the rates affect your wallet</summary>
<p><strong>Buy (Import/ EV Charging) rate:</strong> energy into your EV is charged to you, unless this rate is negative. A negative buy rate credits you.</p>
<p><strong>Sell (Export/ V2G) rate:</strong> energy from your EV is credited to you, unless this rate is negative. A negative sell rate charges you.</p>
<p>Each session combines into one final net charge or credit when it ends. Current rates are not a fixed quote.</p></details>`;
document.querySelector("main").innerHTML=`
<div class="intro"><p class="eyebrow">EV CHARGING STATION</p><h1 id="portal-title">Charge. Export. Settle.</h1>
<p id="portal-intro">Check this station's rates, review your charging budget, then follow your sessions.</p></div>
<div class="station-nav">
<div class="station-tabs" role="tablist" aria-label="Driver sections">
<button role="tab" id="tab-station" aria-controls="panel-station" aria-selected="true">Station</button>
<button role="tab" id="tab-charging" aria-controls="panel-charging" aria-selected="false" tabindex="-1">My charging</button>
<button role="tab" id="tab-history" aria-controls="panel-history" aria-selected="false" tabindex="-1">History</button>
</div>
<button id="wallet-open" class="secondary wallet-button" aria-controls="wallet-drawer" aria-expanded="false">Wallet</button>
</div>
<aside id="wallet-drawer" class="wallet-drawer" aria-labelledby="wallet-drawer-title" hidden>
<div class="section-head"><h2 id="wallet-drawer-title" tabindex="-1">Wallet</h2><button id="wallet-close" class="secondary small-button" aria-label="Close wallet">Close</button></div>
<p id="wallet-signed-out" class="small">Not signed in. Sign in on the Station tab with your BSV wallet.</p>
<section id="portal-account" hidden aria-label="Verified wallet">
<div class="portal-wallet-heading"><h3>History signed in</h3><span id="portal-identity-short" class="mono" aria-label="Wallet identity preview"></span></div>
<div id="portal-connection" class="portal-connection" aria-label="Wallet connection">
<p id="portal-connection-state" role="status" aria-live="polite"></p>
<p id="portal-connection-note" class="small"></p>
<button id="portal-reconnect" class="wide">Reconnect wallet</button>
</div>
<h3>Permissions</h3>
<dl class="terms-list"><dt id="perm-authority-label">Spending approval</dt><dd id="perm-authority">Not checked</dd>
<dt id="perm-grant-label">Wallet payment permissions</dt><dd id="perm-grant">Not checked</dd>
<dt>Receiving credits</dt><dd id="perm-receiving">Not checked</dd></dl>
<div id="allowance" hidden><h3>This month's allowance</h3><dl id="allowance-rows" class="terms-list"></dl>
<p class="small">Spent and Reserved include network fees you pay. Credits you receive never add to the allowance.</p></div>
<div id="monthly-cancel-area" hidden>
<button id="monthly-cancel" class="secondary wide">Cancel monthly charging</button>
<div id="monthly-cancel-confirm" class="confirm-panel" role="group" aria-labelledby="monthly-cancel-title" hidden>
<p id="monthly-cancel-title" tabindex="-1"><strong>Stop new monthly charges?</strong></p>
<p class="small">Payments already signed or submitted, and credits owed to you, are unchanged. Your wallet's own permission is revoked separately in the wallet.</p>
<div class="actions"><button id="monthly-cancel-yes">Cancel monthly charging</button><button id="monthly-cancel-no" class="secondary">Keep it</button></div></div>
</div>
<div class="portal-wallet-actions"><button id="portal-refresh" class="secondary" aria-label="Refresh session history">Refresh</button><button id="portal-sync" aria-label="Retry receiving all confirmed credits">Retry receiving credits</button><button id="portal-logout" class="secondary">Sign out</button></div>
<details id="portal-wallet-details"><summary>Wallet details <span id="portal-expiry"></span></summary>
<p class="small">Full wallet identity</p><p id="portal-identity" class="mono"></p>
<p class="small">This is verified sign-in, not a live wallet connection or spending approval. Signing out or restarting the operator service ends private access.</p>
<p class="small">Confirmed credits are received automatically while your wallet is connected and you are signed in, including older sessions not shown on this page. Wallet permission prompts can still require approval. This imports existing payments and records acceptance; it never sends another payment or collects a driver charge.</p></details>
</section>
</aside>
<p id="portal-status" class="driver-feedback" role="status" aria-live="polite"></p>
<section id="panel-station" role="tabpanel" aria-labelledby="tab-station" tabindex="0">
<section id="portal-signin" aria-labelledby="station-name">
<p class="eyebrow">Station</p><h2 id="station-name">This charging station</h2>
<p id="station-note" class="small">Public rates. Your sessions stay private until you sign in with your wallet.</p>
${priceCards("signin")}
${rateHelp}
</section>
<section id="monthly-offer" aria-labelledby="monthly-offer-title">
<h2 id="monthly-offer-title">Charging approval</h2>
<dl id="monthly-terms" class="terms-list"></dl>
<button id="monthly-authorise" class="wide" disabled hidden>Authorise monthly charging</button>
<p id="monthly-offer-note" class="small" role="status">Checking the station's approval options. No spending approval is implied.</p>
<div id="monthly-confirm" class="confirm-panel" role="group" aria-labelledby="monthly-confirm-title" hidden>
<h3 id="monthly-confirm-title" tabindex="-1">Confirm the terms your wallet will sign</h3>
<dl id="monthly-confirm-terms" class="terms-list"></dl>
<p class="small">Your wallet may still ask its own permission questions. Nothing is paid now.</p>
<div class="actions"><button id="monthly-confirm-yes">Continue to wallet</button><button id="monthly-confirm-no" class="secondary">Not now</button></div>
</div>
</section>
<section id="station-access">
<h2>Already charging here?</h2>
<p class="small">Sign in to see your own sessions, payments and credits. Sign-in alone never approves spending or claims a session.</p>
<div class="actions"><button id="portal-login">Sign in to view my charging</button><button id="portal-pair" class="secondary">Pair and sign in with BSV Browser</button></div>
<p id="portal-registration-note" class="small">Checking whether new-driver registration is open…</p>
<details id="portal-registration-qr-details" hidden><summary>Open new-driver registration on another device</summary><div id="portal-registration-qr" class="session-link-qr"></div><p class="small">Scan with a camera to open the invitation. This is not a Connect to App pairing code and does not approve spending.</p></details>
<details id="station-qr-details"><summary>Station QR code</summary><div id="station-qr" class="session-link-qr"></div>
<p class="small">The permanent public link to this station page. It shows public rates only. It never claims a vehicle, a session or a private invitation.</p></details>
</section>
<section id="portal-pairing" hidden><h2>Connect BSV Browser</h2><p id="portal-pair-status" role="status"></p>
<div id="portal-qr" class="session-link-qr"></div>
<label for="portal-uri">Pairing URI for Connect to App → Paste URI</label>
<textarea id="portal-uri" readonly rows="3" spellcheck="false"></textarea>
<div class="actions"><button id="portal-copy" class="secondary">Copy pairing URI</button><button id="portal-disconnect" class="secondary">Cancel pairing</button></div>
<p class="small">The code expires after two minutes. Keep this page open. Saved connections cannot restore an expired code; create a fresh one here.</p></section>
<details class="portal-recovery"><summary>Have an invitation link?</summary>
<p>New spending approval remains a separate action. If the operator supplied an invitation, open its complete link. Sign-in cannot assign an unclaimed charging session to you.</p>
<label for="portal-private">Existing private session or registration link</label><textarea id="portal-private" rows="2" spellcheck="false"></textarea>
<button id="portal-open" class="secondary">Open existing invitation</button></details>
</section>
<section id="panel-charging" role="tabpanel" aria-labelledby="tab-charging" tabindex="0" hidden>
<p id="charging-signed-out" class="notice">Sign in on the Station tab to see your charging.</p>
<section id="portal-current" hidden><p class="eyebrow" id="portal-current-eyebrow">Latest session</p><h2 id="portal-current-title"></h2>
<p id="portal-current-amount" class="settlement-amount"></p><p id="portal-current-energy" hidden></p><p id="portal-current-note" class="small"></p>
<dl id="charging-metrics" class="metric-grid">
<div><dt>OCPP status</dt><dd id="charging-ocpp">Unavailable</dd></div>
<div><dt>Energy to EV</dt><dd id="charging-import">Unavailable</dd></div>
<div><dt>Energy from EV</dt><dd id="charging-export">Unavailable</dd></div>
<div><dt>Average buy</dt><dd id="charging-avg-buy">Unavailable</dd></div>
<div><dt>Average sell</dt><dd id="charging-avg-sell">Unavailable</dd></div>
<div><dt>Net account</dt><dd id="charging-net">Unavailable</dd></div>
</dl>
<p class="small">Averages are energy-weighted for this session, in AUD. Network fees excluded. Unknown values show as Unavailable, never zero.</p></section>
<section id="monthly-readiness" aria-labelledby="monthly-readiness-title" hidden>
<h2 id="monthly-readiness-title">Monthly charging</h2><ul id="monthly-readiness-items"></ul></section>
<section id="charging-rates" hidden><h2>Current rates</h2>${priceCards("account")}${rateHelp}</section>
</section>
<section id="panel-history" role="tabpanel" aria-labelledby="tab-history" tabindex="0" hidden>
<p id="history-signed-out" class="notice">Sign in on the Station tab to see your history.</p>
<section id="portal-history" hidden><div class="section-head"><h2>Your session history</h2><span id="portal-count" class="badge"></span></div>
<p class="small">Tap a session for details. ! indicates a metering warning.</p>
<p id="portal-empty" hidden>No sessions are linked to this wallet yet. New charging authority must be registered separately by the operator.</p>
<div id="portal-sessions"></div><button id="portal-more" class="secondary wide" hidden>Load more sessions</button>
<details><summary>About these records</summary><p class="small">Only retained charging records with verified wallet ownership are shown. Unattributed legacy payments and unrelated wallet activity are excluded. Average net $/kWh excludes network fees. Chain confirmation is provider-reported; wallet acceptance is a separate signed report.</p></details></section>
</section>
`;
const $=id=>document.getElementById(id),message=text=>{$("portal-status").textContent=text;};
const toolbar=mountDriverToolbar("portal");
document.querySelector("main").append(document.querySelector(".journey-more"));
const framed=window.top!==window;
let wallet=null,identity=null,pairing=null,busy=false,sessions=[],total=0,expires=0,generation=0;
let prices=null,pricesBusy=false;
let registrationUrl=null;
const imports=new Map();
const expandedSessions=new Set();
let syncGeneration=null;
let connectionState="unverified",connectionCheckedAt=null;
let station={monthly_enabled:false,station_ids:[]},monthly=null,tab="station",tabChosen=false;
let stationLoaded=false,stationUnavailable=false;
function connection(state){
  connectionState=state;
  connectionCheckedAt=state==="connected"?Date.now():null;
}
async function connectReceivingWallet(candidate,assertActive){
  connection("checking");controls();
  const before=generation;
  try{
    if(pairing&&(!pairing.supportedMethods?.includes("getNetwork")||!pairing.supportedMethods?.includes("internalizeAction")))
      throw Error("This paired wallet cannot receive verified credits. Open the portal inside a compatible BSV wallet.");
    const verified=await verifyReceivingWallet(candidate,identity,assertActive);
    wallet=verified;connection("connected");return verified;
  }catch(error){
    if(before===generation){wallet=null;connection("unavailable");receiptSync.paused=true;}
    throw error;
  }finally{controls();}
}
const assertSyncActive=()=>{
  if(generation!==syncGeneration||!identity||Date.now()>=expires||document.hidden||framed)
    throw Error("Private access or wallet visibility changed. Reconnect to receive credits.");
};
const receiptSync=new ReceiptSync({
  connect:async({automatic})=>{
    assertSyncActive();
    const candidate=wallet||new WalletClient(automatic?"window.CWI":window.CWI?"window.CWI":"auto");
    await connectReceivingWallet(candidate,assertSyncActive);
    return {wallet:candidate,identity};
  },
  importReceipt:async(candidate,{row,session},driver)=>{
    assertSyncActive();
    const response=await api("credit_receipt",{credit_id:row.id});
    assertSyncActive();
    if(response.receipt.txid!==row.txid||response.receipt.session_id!==session.session_id)
      throw Error("Receipt does not match this session.");
    const checked=parseInvitation(JSON.stringify(response.invitation),Date.now(),true);
    const scoped=(action,data)=>{assertSyncActive();return api(action,{...data,credit_id:row.id});};
    const report=await importAndReportCredit(candidate,checked,response.receipt,driver,scoped,imports);
    assertSyncActive();
    for(const s of sessions)for(const t of s.transactions)if(t.id===row.id&&t.txid===row.txid)Object.assign(t,report);
    render();
  },
  onState:(state,error)=>{
    if(generation!==syncGeneration||!identity)return;
    if(state==="syncing")message("Receiving confirmed credits into your wallet… Approve any wallet permission prompts.");
    if(state==="synced")message("Confirmed credits received and wallet acceptance recorded. No new payment was sent.");
    if(state==="paused"){
      if(error?.status===401){clearPrivate();message("Private access ended. Sign in again to receive credits.");}
      else message(error?.walletAccepted?error.message:
        `Receiving credits paused: ${error?.message||"Wallet unavailable"}. Use Retry receiving credits when ready. No new payment was sent.`);
    }
  },
});
async function syncCredits(manual=false){
  const available=!!wallet||!!window.CWI||pairing?.state==="paired";
  if(busy||receiptSync.running||!identity||framed||document.hidden||Date.now()>=expires||
    (!manual&&(!available||receiptSync.paused)))return;
  busy=true;syncGeneration=generation;controls();
  try{
    const jobs=await pendingCreditJobs(offset=>api("sessions",{offset}),identity,assertSyncActive);
    await receiptSync.run({jobs,identity,available,manual});
  }catch(error){
    receiptSync.paused=true;
    if(generation===syncGeneration&&identity){
      if(error.status===401){clearPrivate();message("Private access ended. Sign in again to receive credits.");}
      else message(`Credit check paused: ${error.message}. Use Retry receiving credits when ready.`);
    }
  }finally{busy=false;controls();}
}
function controls(){
  const status=walletStatus(connectionState,receiptSync.paused);
  $("portal-connection-state").textContent=status.title;
  $("portal-connection").dataset.state=connectionState;
  $("portal-connection-note").textContent=status.note+(connectionCheckedAt
    ?` Connection checked ${new Date(connectionCheckedAt).toLocaleTimeString([],{hour:"2-digit",minute:"2-digit"})}.`:"");
  $("portal-reconnect").textContent=status.action;
  $("portal-reconnect").disabled=status.disabled||busy||framed||!identity;
  $("portal-reconnect").classList.toggle("secondary",connectionState==="connected"&&!receiptSync.paused);
  for(const id of ["portal-login","portal-pair","portal-refresh","portal-sync","portal-logout","portal-more","portal-open"])
    $(id).disabled=busy||framed;
  $("station-access").hidden=!!identity;
  $("portal-sync").disabled=busy||framed||!identity;
  toolbar.update({
    // Sign-in stays visible on the Station panel for returning drivers; the
    // toolbar copy is only a secondary shortcut.
    connect:{run:()=>$("portal-login").click(),label:"View my charging history",enabled:!busy&&!framed&&!identity,primary:false,reason:identity?"Already signed in to this wallet.":"Wait for the current action to finish."},
    pair:{target:"portal-pair",enabled:!busy&&!framed&&!pairing,reason:"A pairing is active, or another action is in progress."},
    approve:{enabled:false,reason:"History sign-in does not authorise spending. Open an operator invitation."},
    refresh:{target:"portal-refresh",enabled:!busy&&!framed&&!!identity,reason:"Sign in to load private session history."},
    sync:{target:"portal-sync",label:"Retry receiving credits",primary:!!identity&&receiptSync.paused,
      reason:"Credits arrive automatically when the wallet is available. Retry after a paused wallet action."},
    signout:{target:"portal-logout",enabled:!busy&&!framed&&!!identity,reason:"No wallet is signed in."},
    register:{enabled:!busy&&!framed&&!identity&&!!registrationUrl&&!station.monthly_enabled,run:()=>location.assign(registrationUrl)},
    monthly:{target:"monthly-authorise",enabled:monthlyActionEnabled(),
      label:setupAction(monthly,{connected:monthlyConnected()}).label,
      reason:station.monthly_enabled?"Monthly charging is already set up, or another action is in progress.":"Monthly charging is not enabled at this station."},
  },{step:0,hint:monthlyActionEnabled()?"":  // The monthly panel explains its own action.
    identity?"":registrationUrl?"Review the budget on the next screen before approving.":"Wallet sign-in only. No spending approval."});
  // Tabs replace the old step indicator; the primary action follows the visible panel.
  const primary=document.querySelector(".journey-primary");
  const destination=tab==="station"?$("monthly-offer"):$("panel-"+tab);
  if(primary.parentElement!==destination)destination.append(primary);
  document.querySelector(".journey-steps").hidden=true;
  document.querySelector("main").dataset.mode="portal";
  $("monthly-authorise").disabled=!monthlyActionEnabled();
  paintReadiness();
  $("portal-title").textContent=identity?"Your charging":"Charge. Export. Settle.";
  $("portal-intro").textContent=identity?"Your station, current session and history in one place.":
    stationFlow({station,loaded:stationLoaded,unavailable:stationUnavailable}).intro;
  $("portal-copy").disabled=!$("portal-uri").value;
}
async function registration(){
  if(framed)return;
  try{
    const response=await fetch("/api/bsv_settlement/driver",{method:"POST",credentials:"omit",cache:"no-store",
      referrerPolicy:"no-referrer",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({action:"public_invitation"}),signal:AbortSignal.timeout(4500)});
    if(!response.ok)throw Error("Registration unavailable");
    const data=await response.json();
    registrationUrl=data.state==="available"?publicEnrolmentUrl(
      location.origin+"/bsv_settlement/driver/index.html"+data.public_link_fragment,location.origin):null;
    $("portal-registration-note").textContent=registrationUrl?"New-driver registration is open. Review the invitation before approving.":
      "Starting a new session? Open your operator's invitation below.";
  }catch{
    registrationUrl=null;
    $("portal-registration-note").textContent="New-driver registration is unavailable. Ask the operator for an invitation; history sign-in is separate.";
  }
  const holder=$("portal-registration-qr");
  holder.replaceChildren();$("portal-registration-qr-details").hidden=!registrationUrl;
  qrText(holder,registrationUrl,"Registration URL",message);
  if(registrationUrl){const code=qrcode(0,"M");code.addData(registrationUrl,"Byte");code.make();
    holder.innerHTML=code.createSvgTag({cellSize:4,margin:16,scalable:true});
    holder.querySelector("svg").setAttribute("aria-label","Unassigned driver registration invitation");}
  controls();
}
async function api(action,extra={}){
  const r=await fetch("/api/bsv_settlement/portal",{method:"POST",credentials:"same-origin",cache:"no-store",
    referrerPolicy:"no-referrer",headers:{"Content-Type":"application/json"},body:JSON.stringify({action,...extra}),
    signal:AbortSignal.timeout(20000)});
  const data=await r.json();
  if(!r.ok){const error=Error(data.error||"Portal request failed");error.status=r.status;error.code=data.code;throw error;}
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
  connection("unverified");
  receiptSync.paused=false;receiptSync.completed.clear();
  expandedSessions.clear();
  $("portal-sessions").replaceChildren();$("portal-identity").textContent="";
  $("portal-identity-short").textContent="";$("portal-identity-short").removeAttribute("title");
  $("portal-expiry").textContent="";$("portal-wallet-details").open=false;
  $("portal-account").hidden=true;$("portal-history").hidden=true;$("portal-signin").hidden=false;
  $("portal-current").hidden=true;
  monthly=null;tabChosen=false;paintMonthly();
  if(tab!=="station")selectTab("station",false);
  controls();
}
function node(tag,text,cls){const e=document.createElement(tag);e.textContent=text;if(cls)e.className=cls;return e;}
const number=(v,suffix="")=>v===null||v===undefined||v===""||!Number.isFinite(Number(v))?"Unavailable":`${Number(v).toFixed(3)}${suffix}`;
function render(){
  if(identity&&!tabChosen&&tab==="station"){tabChosen=true;selectTab("charging",false);}
  $("portal-account").hidden=!identity;$("portal-history").hidden=!identity;$("wallet-signed-out").hidden=!!identity;
  $("charging-signed-out").hidden=!!identity;$("history-signed-out").hidden=!!identity;$("charging-rates").hidden=!identity;
  $("portal-identity").textContent=identity||"";$("portal-count").textContent=`${sessions.length} of ${total}`;
  $("portal-identity-short").textContent=compactIdentity(identity);
  $("portal-identity-short").title=identity||"";
  $("portal-empty").hidden=sessions.length>0;$("portal-more").hidden=sessions.length>=total;
  $("portal-current").hidden=!sessions.length;
  if(sessions.length){
    const current=sessions[0],summary=sessionSummary(current),provisional=provisionalSession(current),account=sessionAccount(current);
    const payment=current.transactions.length===1?current.transactions[0]:null;
    $("portal-current-title").textContent=!current.ended_at?"Session in progress":
      payment?.state==="provider_confirmed"?payment.direction==="operator_to_driver"?
        receiptReported(payment)?"Credit received":"Credit confirmed":"Payment confirmed":
      payment?.state==="provider_unconfirmed"?"Awaiting block confirmation":summary.status;
    $("portal-current-amount").textContent=provisional?provisional.amount:payment&&Number.isSafeInteger(payment.amount_sats)?
      `${payment.amount_sats} sat ${payment.direction==="operator_to_driver"?"to your wallet":"to the operator"}`:summary.payment;
    $("portal-current-energy").textContent=`Energy Imported to EV: ${formatKwh(account.importKwh)}. Energy Imported from EV: ${formatKwh(account.exportKwh)}.`;
    $("portal-current-note").textContent=provisional?provisional.note:current.transactions.length?
      current.transactions.map(transactionStatus).join(". "):"No payment is recorded. Signing in does not collect this session.";
    $("portal-current-eyebrow").textContent=account.live?"Current session":"Latest session";
    $("charging-ocpp").textContent=account.live?(account.ocppStatus||"Unavailable"):"Session ended";
    $("charging-import").textContent=formatKwh(account.importKwh);$("charging-export").textContent=formatKwh(account.exportKwh);
    $("charging-avg-buy").textContent=formatRate(account.averageBuy);$("charging-avg-sell").textContent=formatRate(account.averageSell);
    $("charging-net").textContent=account.netAud===null?"Unavailable":
      `${account.netAud<0?"Credit":account.netAud>0?"Charge":"Balance"} AUD ${Math.abs(account.netAud).toFixed(2)}`+
      (account.provisionalSats!==null?` · ${account.provisionalSats} sat provisional`:
        account.live?" · provisional":"");
  }
  $("portal-expiry").textContent=`Expires ${new Date(expires).toLocaleTimeString([],{hour:"2-digit",minute:"2-digit"})}`;
  const list=$("portal-sessions");list.replaceChildren();
  for(const s of sessions){
    const box=node("details","","portal-session"),summary=node("summary","","portal-session-summary");
    box.open=expandedSessions.has(s.session_key);
    box.addEventListener("toggle",()=>{if(box.open)expandedSessions.add(s.session_key);else expandedSessions.delete(s.session_key);});
    const overview=sessionSummary(s),account=sessionAccount(s),date=new Date(s.opened_at||s.ended_at||"");
    const shortDate=Number.isFinite(date.getTime())?date.toLocaleDateString("en-AU",{day:"2-digit",month:"2-digit"})+" "+
      date.toLocaleTimeString("en-AU",{hour:"2-digit",minute:"2-digit",hour12:false}):"No date";
    const arrow=node("span","›","portal-chevron");arrow.setAttribute("aria-hidden","true");
    summary.append(arrow,node("span",shortDate,"portal-row-date"),
      node("span",overview.payment,"portal-row-payment"),
      node("span",`${number(account.importKwh)} in / ${number(account.exportKwh)} out kWh`,"portal-row-energy"),
      node("span",overview.status+(overview.warning?" !":""),"portal-row-status"));
    summary.setAttribute("aria-label",`${shortDate}, ${overview.payment}, ${overview.status}${overview.warning?", metering warning":""}. Session ${s.transaction_id||s.session_id}. Expand details.`);
    summary.title=`${s.transaction_id||s.session_id}: ${overview.payment}, ${overview.status}${overview.warning?", metering warning":""}`;
    box.append(summary);
    const table=node("table","","portal-detail-table"),caption=node("caption",s.account_kind==="manual_energy_adjustment"?"Separate adjustment details":"Session details");
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
    const provisional=provisionalSession(s);
    if(provisional){addRow("Provisional session amount",provisional.amount);addRow("Estimate basis",provisional.note);}
    const buy=account.averageBuy,sell=account.averageSell;
    for(const [label,value] of [
      ["Energy Imported to EV",number(account.importKwh," kWh")],["Energy Imported from EV",number(account.exportKwh," kWh")],
      ["Average buy price",buy===null?"Unavailable":`${buy.toFixed(4)} $/kWh`],
      ["Average sell price",sell===null?"Unavailable":`${sell.toFixed(4)} $/kWh`],
      ["Net energy account",number(account.netAud,account.live?" AUD · provisional":" AUD")],
      ["Average net price",account.averageNet===null?"Unavailable":`${account.averageNet.toFixed(4)} $/kWh`]])
      addRow(label,value);
    if(s.account_kind==="manual_energy_adjustment")addRow("Separate adjustment","5 kWh-equivalent monetary adjustment. Not measured session energy.","portal-warning");
    else if(s.quality_flags?.length)addRow("Metering warning",s.quality_flags.join(", "),"portal-warning");
    if(s.closure)addRow("Account",s.closure.state.replaceAll("_"," "));
    if(provisional)addRow("Payment","Not created. Session is still in progress.");
    else if(!s.transactions.length)addRow("Payment","No recorded payment. Sign-in does not collect this session.");
    for(const t of provisional?[]:s.transactions){
      addRow(t.direction==="operator_to_driver"?"Credit to driver":"Payment to operator",
        Number.isSafeInteger(t.amount_sats)?t.amount_sats+" sat":"Amount not recorded","portal-payment-heading");
      addRow("Settlement status",transactionStatus(t));
      addRow("Network fee",Number.isSafeInteger(t.fee_sats)?t.fee_sats+" sat":"Not recorded");
      const url=chainRecordUrl(t.txid);
      if(url){const a=node("a","View chain-provider record");a.href=url;a.target="_blank";a.rel="noopener noreferrer";addRow("Transaction",a);}
    }
    addRow("Session ID",s.session_id,"portal-reference");
    addRow("Approval history",s.agreements.map(a=>a.authority_id?
      `${a.state.replaceAll("_"," ")} · monthly authority`:
      `${a.state.replaceAll("_"," ")} · expires ${new Date(a.expires_at).toLocaleString()}`).join("; ")||"Original operator-credit routing");
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
  if(!more)await loadMonthly();
}
async function run(fn){
  if(framed){message("Open the driver portal directly in your browser to sign in.");return;}
  if(busy)return;busy=true;controls();
  try{await fn();}catch(e){
    if(e.status===401){clearPrivate();message("Private access ended or the request could not be verified. Sign in again.");}
    else message(e.walletAccepted?e.message:"Action paused: "+e.message);
  }finally{busy=false;controls();void syncCredits();}
}
async function signIn(candidate){
  const before=generation;
  message("Verify wallet identity. This signature is for sign-in only, not spending.");
  const proof=await signPortalLogin(candidate,await api("challenge"),location.origin);
  const result=await api("login",proof);
  if(before!==generation)throw Error("Sign-in was interrupted. Please try again.");
  if(result.identity!==proof.identity)throw Error("Sign-in identity mismatch.");
  wallet=candidate;identity=result.identity;imports.clear();await load();
  receiptSync.paused=false;receiptSync.completed.clear();
  await connectReceivingWallet(candidate,()=>{
    if(before!==generation||!identity||Date.now()>=expires||document.hidden)throw Error("Sign-in changed. Reconnect your wallet.");
  });
  message("Signed in. Confirmed credits will be received automatically. No new spending approved.");
}
function monthlyActionEnabled(){
  return !busy&&!framed&&stationLoaded&&!stationUnavailable&&station.monthly_enabled===true&&
    setupAction(monthly,{connected:monthlyConnected()}).enabled;
}
function monthlyConnected(){
  const can=method=>pairing?pairing.supportedMethods?.includes(method):typeof wallet?.[method]==="function";
  return connectionState==="connected"&&!document.hidden&&Date.now()<expires&&
    Date.now()-connectionCheckedAt<=60000&&can("createAction")&&can("signAction");
}
function paintReadiness(){
  const view=readinessView(monthly,{walletConnected:monthlyConnected()});
  $("monthly-readiness").hidden=!identity||!station.monthly_enabled;
  $("monthly-readiness-title").textContent=view.title;
  const items=$("monthly-readiness-items");items.replaceChildren();
  for(const text of view.ready?
    ["Wallet available for per-session collection. Ownership, final amount and remaining allowance are checked when each session ends. Keep the wallet connected."]:view.items)
    items.append(node("li",text));
}
// Tabs: arrow keys, Home and End move between sections (automatic activation).
const tabs=["station","charging","history"];
function selectTab(name,focus=true){
  tab=name;
  for(const t of tabs){
    const button=$("tab-"+t),selected=t===name;
    button.setAttribute("aria-selected",String(selected));button.tabIndex=selected?0:-1;
    $("panel-"+t).hidden=!selected;
  }
  if(focus)$("tab-"+name).focus();
  controls();
}
for(const t of tabs){
  $("tab-"+t).onclick=()=>{tabChosen=true;selectTab(t,false);};
  $("tab-"+t).onkeydown=event=>{
    const i=tabs.indexOf(t),next={ArrowRight:(i+1)%3,ArrowLeft:(i+2)%3,Home:0,End:2}[event.key];
    if(next===undefined)return;
    event.preventDefault();tabChosen=true;selectTab(tabs[next]);
  };
}
// Wallet drawer: a non-modal panel. Escape closes it and returns focus.
function drawer(open){
  $("wallet-drawer").hidden=!open;$("wallet-open").setAttribute("aria-expanded",String(open));
  if(open)$("wallet-drawer-title").focus();else $("wallet-open").focus();
}
$("wallet-open").onclick=()=>drawer($("wallet-drawer").hidden);
$("wallet-close").onclick=()=>drawer(false);
document.addEventListener("keydown",event=>{
  if(event.key==="Escape"&&!$("wallet-drawer").hidden){event.preventDefault();drawer(false);}
});
function list(holder,rows){
  holder.replaceChildren();
  for(const [label,value] of rows)holder.append(node("dt",label),node("dd",value));
}
async function loadStation(){
  try{
    const result=await api("station");
    stationUnavailable=typeof result?.monthly_enabled!=="boolean";
    station=stationUnavailable?{monthly_enabled:false,station_ids:[]}:result;
  }catch{station={monthly_enabled:false,station_ids:[]};stationUnavailable=true;}
  stationLoaded=true;
  paintStation();
}
function paintStation(){
  const ids=station.station_ids||[];
  $("station-name").textContent=ids.length?`Station ${ids.join(", ")}`:"This charging station";
  paintMonthly();
  const url=location.origin+"/bsv_settlement/driver/index.html",holder=$("station-qr");
  if(!holder.querySelector("svg")){
    const code=qrcode(0,"M");code.addData(url,"Byte");code.make();
    holder.innerHTML=code.createSvgTag({cellSize:4,margin:16,scalable:true});
    holder.querySelector("svg").setAttribute("aria-label","Public station page link");
    qrText(holder,url,"Station page link",message);
  }
  controls();
}
async function loadMonthly(){
  if(!identity){monthly=null;paintMonthly();return;}
  const before=generation;
  try{const result=await api("monthly_status");if(before===generation)monthly=result;}
  catch(error){if(error.status===401)throw error;if(before===generation)monthly=null;}
  paintMonthly();
}
function paintMonthly(){
  const flow=stationFlow({station,loaded:stationLoaded,unavailable:stationUnavailable});
  const enabled=flow.mode==="monthly",status=monthly;
  $("monthly-offer-title").textContent=flow.title;
  $("monthly-authorise").hidden=!enabled;
  if(!enabled)$("monthly-confirm").hidden=true;
  list($("monthly-terms"),enabled?termsList({stationIds:station.station_ids||[],operatorIdentity:station.operator_identity}):flow.rows);
  $("monthly-offer-note").textContent=!enabled?flow.note:
    !identity?"One action connects your wallet, signs you in and shows the exact terms. Any missing wallet setup is reported separately.":
    status?.authority?.state==="active"?"Monthly charging is authorised for this wallet. Manage it in Wallet.":
    status?.authority?.state==="cancelled"?"Monthly charging was cancelled for this wallet.":
    "Review the terms above, then authorise. Your wallet may ask its own questions.";
  paintReadiness();
  $("perm-authority-label").textContent=enabled?"Monthly charging":"Spending approval";
  $("perm-grant-label").textContent=enabled?"Wallet monthly permission":"Wallet payment permissions";
  $("perm-authority").textContent=!identity?"Not signed in":enabled?authorityText(status):
    flow.mode==="weekly"?"Check your signed invitation for the approved limit and expiry.":"Approval status unavailable";
  $("perm-grant").textContent=identity&&enabled?grantText(status):"Not applicable";
  if(identity&&!enabled)$("perm-grant").textContent="Not verified here. Your wallet may ask for each payment.";
  $("perm-receiving").textContent=!identity||!status?.enabled?"Not checked":
    status.receiving?.registered?"Registered":"Not registered";
  $("allowance").hidden=!enabled||!status?.allowance;
  list($("allowance-rows"),allowanceRows(status?.allowance));
  $("monthly-cancel-area").hidden=!enabled||status?.authority?.state!=="active";
  if($("monthly-cancel-area").hidden)$("monthly-cancel-confirm").hidden=true;
  controls();
}
// Resolve only from the driver's explicit choice on the exact server terms.
function askTerms(terms){
  selectTab("station",false);
  list($("monthly-confirm-terms"),signedTermsRows(terms));
  $("monthly-confirm").hidden=false;$("monthly-confirm-title").focus();
  return new Promise(resolve=>{
    const done=answer=>{$("monthly-confirm").hidden=true;$("monthly-confirm-yes").onclick=$("monthly-confirm-no").onclick=null;resolve(answer);};
    $("monthly-confirm-yes").onclick=()=>done(true);$("monthly-confirm-no").onclick=()=>done(false);
  });
}
function setupWallet(){
  if(pairing?.state==="paired")return {candidate:pairing.wallet,supportedMethods:pairing.supportedMethods||null};
  return {candidate:wallet||new WalletClient(window.CWI?"window.CWI":"auto"),supportedMethods:null};
}
async function syncForSetup(){
  syncGeneration=generation;receiptSync.paused=false;
  const jobs=await pendingCreditJobs(offset=>api("sessions",{offset}),identity,assertSyncActive);
  await receiptSync.run({jobs,identity,available:true,manual:true});
  return {paused:receiptSync.paused};
}
const stepText={wallet:"Wallet connected.",sign_in:"Signed in.",authority:"Monthly terms signed.",
  receipts:"Credits checked.",wallet_permission:"Wallet permission checked."};
$("monthly-authorise").onclick=()=>run(async()=>{
  tabChosen=true; // Keep the terms in view while sign-in completes mid-setup.
  const {candidate,supportedMethods}=setupWallet();
  if(identity)await connectReceivingWallet(candidate,()=>{
    if(document.hidden||!identity||Date.now()>=expires)throw Error("Reconnect your wallet.");
  });
  const flow=new MonthlySetup({api,
    signIn:async w=>{await signIn(w);return identity;},
    syncReceipts:async()=>syncForSetup(),
    onStep:(id,state,detail)=>message(state==="done"?stepText[id]:detail||`${id.replace("_"," ")}: ${state}`)});
  const result=await flow.run({wallet:candidate,supportedMethods,identity,hostname:location.hostname,confirmTerms:askTerms,
    assertActive:()=>{if(document.hidden)throw Error("The page was hidden. Nothing more was signed.");}});
  monthly=result.status;paintMonthly();
  tabChosen=true;selectTab("charging",false);
  const unavailable=result.steps.filter(s=>s.state==="unavailable");
  message(unavailable.length?"Authority saved. Wallet setup support is not installed for all missing steps. Ask the operator to complete setup; no payment was attempted.":
    result.ready&&monthlyConnected()?"Wallet ready for per-session collection while connected. Each session is checked again at closure.":
    result.missing.length?`Monthly setup saved. Still needed: ${readinessView({enabled:true,readiness:{missing:result.missing}}).items.join(" ")}`:
    "Monthly charging was not authorised. Nothing was signed or paid.");
});
$("monthly-cancel").onclick=()=>{$("monthly-cancel-confirm").hidden=false;$("monthly-cancel-title").focus();};
$("monthly-cancel-no").onclick=()=>{$("monthly-cancel-confirm").hidden=true;$("monthly-cancel").focus();};
$("monthly-cancel-yes").onclick=()=>run(async()=>{
  const {candidate}=setupWallet();
  const result=await cancelMonthly({api,wallet:candidate,identity,authorityId:monthly.authority.authority_id});
  await loadMonthly();
  $("wallet-drawer-title").focus(); // The cancel controls are gone; keep focus in the drawer.
  message(result.cancelled?"Monthly charging cancelled. New charges stop; existing payments and credits are unchanged. "+
    "Your wallet's own permission is not confirmed revoked; check it in your wallet.":"Cancellation was not confirmed.");
});
$("portal-reconnect").onclick=()=>{
  if(connectionState==="connected"&&receiptSync.paused){void syncCredits(true);return;}
  void run(async()=>{
    const before=generation,expected=identity;
    const candidate=wallet||new WalletClient(window.CWI?"window.CWI":"auto");
    await connectReceivingWallet(candidate,()=>{
      if(before!==generation||identity!==expected||!identity||Date.now()>=expires||document.hidden)
        throw Error("Private access changed. Sign in again before reconnecting.");
    });
    receiptSync.paused=false;
    message("Wallet connected. Checking confirmed credits automatically. No new spending approved.");
  });
};
$("portal-login").onclick=()=>run(()=>signIn(new WalletClient(window.CWI?"window.CWI":"auto")));
$("portal-refresh").onclick=()=>run(async()=>{await load();await refreshPrices();});
$("portal-more").onclick=()=>run(()=>load(true));
$("portal-logout").onclick=()=>run(async()=>{
  try{await api("logout");if(pairing)await pairing.disconnect();pairing=null;message("Signed out. Spending approvals and payments are unchanged.");}
  finally{clearPrivate();$("portal-pairing").hidden=true;}
});
$("portal-sync").onclick=()=>syncCredits(true);
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
    if(state==="disconnected"){wallet=null;connection("unavailable");message("Pairing ended. Your history is still signed in. Reconnect your wallet to receive credits.");}
    controls();
  }});
  await pairing.start();
  $("portal-pairing").scrollIntoView({behavior:"smooth",block:"start"});
});
$("portal-copy").onclick=async()=>{
  if(!$("portal-uri").value)return;
  let timer;
  try{await Promise.race([navigator.clipboard.writeText($("portal-uri").value),
    new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error("Clipboard unavailable")),2000);})]);
    message("Pairing URI copied. Paste it into BSV Browser → Connect to App → Paste URI now.");}
  catch{$("portal-uri").focus();$("portal-uri").select();message("Select and copy the pairing URI. Keep it private.");}
  finally{clearTimeout(timer);}
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
document.addEventListener("visibilitychange",()=>{paintPrices();if(!document.hidden){
  if(!busy&&!receiptSync.running){connection("unverified");controls();}
  void refreshPrices();void syncCredits();
}});
setInterval(()=>void refreshPrices(),60000);
setInterval(paintPrices,1000); // Expire displayed data even if a fetch is stalled.
void refreshPrices(); // No wallet prompt, cookie creation or private history required.
setInterval(()=>{if(identity&&Date.now()>=expires){clearPrivate();message("Private access expired. Sign in again.");}},1000);
// Restore authenticated history only; auto-receive requires an available wallet.
try{if(!framed){await load();message("History sign-in restored. Wallet connection is checked separately below.");}}
catch{clearPrivate();}controls();
void syncCredits();
setInterval(()=>{if(identity&&!busy&&!framed&&!document.hidden)void run(()=>load());},30000);
// Do not leave an old provisional value looking live when history refresh fails.
setInterval(()=>{if(identity&&!document.hidden)render();},15000);
if(framed)message("Open the driver portal directly in your browser to sign in.");
void registration();
setInterval(()=>void registration(),30000);
void loadStation(); // Public facts only; no cookie or wallet prompt.
