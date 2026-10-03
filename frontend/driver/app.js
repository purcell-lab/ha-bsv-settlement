import { WalletClient } from "@bsv/sdk";
import { parseInvitation, signConsent,derivedInvitation } from "./model.js";
import { collectOnce } from "./collection.js";
import { registerCredit, importAndReportCredit, receiptReported } from "./credit.js";
import { driverView, showOngoingOverview, ongoingCreditMessage } from "./view.js";
import { BrowserPairing } from "./pairing.js";
import qrcode from "qrcode-generator";
import { describeFailure } from "./diagnostics.js";
import {chainRecordUrl} from "../ui.js";
import {warningMessage} from "../quality.js";
import {privateSessionUrl,publicEnrolmentUrl} from "./private-link.js";
import {createIcons,CarFront,PlugZap,Wallet,QrCode,ArrowLeftRight} from "lucide";
createIcons({icons:{CarFront,PlugZap,Wallet,QrCode,ArrowLeftRight}});
function drawApprovalQR(holder,url){
  const qr=qrcode(0,"M");qr.addData(url,"Byte");qr.make();
  holder.innerHTML=qr.createSvgTag({cellSize:4,margin:16,scalable:true});
  holder.querySelector("svg").setAttribute("role","img");
}

const $ = id => document.getElementById(id);
const fragment = new URLSearchParams(location.hash.slice(1));
// A new fragment identifies a different private invitation. Reset every wallet
// and session variable rather than displaying the old session in the same tab.
window.addEventListener("hashchange", () => location.reload());
let enrolment=fragment.has("join")&&fragment.has("key")?
  {join:fragment.get("join"),key:fragment.get("key")}:null;
let capability = fragment.has("budget") && fragment.has("token")
  ? {budget_id:fragment.get("budget"),token:fragment.get("token")} : enrolment;
const framed = !!capability && window.top !== window;
$("open-private-link").onclick=()=>{
  const url=privateSessionUrl($("private-link-input").value.trim(),location.origin);
  if(!url){$("private-link-feedback").textContent="Paste a complete private session link from this charging operator, including its #budget and token.";return;}
  location.href=url;
};
let checked = null, receipt = null, busy = false, live = null, accepted = false;
let connectedWallet=null, binding=null, collectionBusy=false, halted=false, pendingReport=null, collectionState=null;
let creditDirection=false, creditEnabled=false, creditRegistered=false;
const importedCredits=new Set(), attemptedImports=new Set();
const receiptImports=new Map(), receiptReportErrors=new Map();
let latestTxid=null;
let collectionFailure=null,pendingDiagnostic=null,latestCollection=null;
let selectedSession=null;
let publicQRReady=false;
$("multi-session-select").onchange=()=>{
  if(collectionBusy||busy||pendingReport||pendingDiagnostic)return;
  selectedSession=$("multi-session-select").value||null;
  latestCollection=null;collectionState=null;halted=false;
  void checkCollection();
};
function paintFailure(){
  $("collection-failure").textContent=describeFailure(collectionFailure);
  $("collection-failure").hidden=!collectionFailure || collectionState==="provider_confirmed";
}
function retainFailure(e){
  collectionFailure=e.diagnostic||collectionFailure;
  pendingDiagnostic=e.pendingDiagnostic||pendingDiagnostic;
  pendingReport=e.pendingReport||pendingReport;
  halted=true;paintFailure();
}
let ongoingRows=[],registeredIdentity=null,receivingOngoing=false,ongoingEnabled=false;
let pairing=null, pairingBusy=false;
function pairingState(state, detail) {
  $("pairing-status").textContent=detail || ({
    creating:"Creating a private, short-lived pairing code…",
    scanning:"In BSV Browser, select Connect to app and scan this code. Check the app domain before approving. This QR expires in two minutes.",
  }[state] || state);
  $("pairing-qr").replaceChildren();
  $("pairing-qr").hidden=state!=="scanning";
  if(state==="scanning" && pairing?.uri) {
    const qr=qrcode(0,"M");qr.addData(pairing.uri);qr.make();
    const img=document.createElement("img");img.src=qr.createDataURL(4,16);
    img.alt="Private BSV Browser connection QR, not a payment or session approval";
    $("pairing-qr").append(img);
  }
  $("pairing-disconnect").hidden=!pairing;
  if(["disconnected","incompatible"].includes(state) && connectedWallet===pairing?.wallet){
    connectedWallet=null;halted=true;
  }
  controls();
}
$("pairing-connect").onclick=async()=>{
  if(busy||collectionBusy||pairingBusy||framed||!capability||!checked)return;
  pairingBusy=true;controls();
  try {
    if(pairing)await pairing.disconnect();
    pairing=new BrowserPairing({api,origin:location.origin,onState:pairingState});
    await pairing.start();
  }catch(e){$("pairing-status").textContent=e.message;}
  finally{pairingBusy=false;controls();}
};
$("pairing-disconnect").onclick=async()=>{
  if(busy||collectionBusy||pairingBusy)return;
  pairingBusy=true;controls();
  if(pairing)await pairing.disconnect();
  pairing=null;pairingBusy=false;
  $("pairing-qr").hidden=true;$("pairing-disconnect").hidden=true;
  $("pairing-status").textContent="Using the local wallet fallback. Open this same private link inside BSV Browser. Disconnecting does not revoke a saved approval.";
  controls();
};
$("driver-link-copy").onclick=async()=>{
  let timer;
  try{await Promise.race([navigator.clipboard.writeText(location.href),
      new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error("Clipboard unavailable")),2000);})]);
    $("pairing-status").textContent="Private driver link copied. Paste it into BSV Browser on your phone.";}
  catch{$("pairing-status").textContent="Copy the full address from your browser, including everything after #. Keep it private.";}
  finally{clearTimeout(timer);}
};
window.addEventListener("pagehide",()=>{if(pairing)void pairing.disconnect();});
function paintOngoing(){
  $("ongoing-section").hidden=!ongoingRows.length;
  $("ongoing-list").replaceChildren();
  for(const row of ongoingRows.slice().reverse()){
    if(receiptReported(row))importedCredits.add(row.txid);
    const box=document.createElement("div");box.className="notice small";
    const p=document.createElement("p");p.className="strong";
    p.textContent=`${row.amount_sats?row.amount_sats+" sat credit · ":""}${ongoingCreditMessage(row.state,importedCredits.has(row.txid))}`;
    const note=document.createElement("p");note.textContent=`Session ${row.transaction_id.slice(0,8)}${row.amount_sats?" · "+row.fee_sats+" sat operator fee":""}. ${row.error||""}`;
    const details=document.createElement("details"),summary=document.createElement("summary"),refs=document.createElement("p");
    summary.textContent="Full session and transaction references";refs.className="mono";
    refs.textContent=`Session: ${row.transaction_id}. BSV transaction: ${row.txid||"Not submitted"}. Receiving address: ${row.recipient_address}.`;
    details.append(summary,refs);
    const url=chainRecordUrl(row.txid);
    if(url){const a=document.createElement("a");a.href=url;a.target="_blank";a.rel="noopener noreferrer";
      a.textContent="View chain-provider record";details.append(a);}
    box.append(p,note,details);
    if(warningMessage(row.quality_flags)){const warning=document.createElement("p");
      warning.textContent=`Metering warning (non-blocking): ${warningMessage(row.quality_flags)}`;box.append(warning);}
    $("ongoing-list").append(box);
  }
  const outstanding=ongoingRows.some(r=>r.state==="provider_confirmed"&&!importedCredits.has(r.txid));
  $("receive-ongoing").disabled=receivingOngoing||!outstanding;
  $("receive-ongoing").hidden=!outstanding&&!receivingOngoing;
}
$("receive-ongoing").onclick=async()=>{
  receivingOngoing=true;paintOngoing();
  try{
    const {wallet,identity}=await connectWallet();
    if(identity!==registeredIdentity)throw Error("Connect the wallet that registered this receiving address.");
    for(const row of ongoingRows.filter(r=>r.state==="provider_confirmed"&&!importedCredits.has(r.txid))){
      const receipt=await api("ongoing_credit_receipt",{credit_id:row.credit_id});
      if(receipt.credit_id!==row.credit_id || receipt.session_id!==row.session_id || receipt.txid!==row.txid)
        throw Error("The credit receipt does not match the selected session.");
      const report=await importAndReportCredit(wallet,checked,receipt,registeredIdentity,api,receiptImports);
      Object.assign(row,report);
      importedCredits.add(row.txid);
    }
    $("ongoing-feedback").textContent="Wallet receipt acceptance recorded by the operator. No payment was sent.";
  }catch(e){$("ongoing-feedback").textContent=e.walletAccepted?
    `${e.message}. Select Receive confirmed credits to retry reporting, without another import in this page.`:
    `Receipt import paused: ${e.message}. Retry imports the same payments, not new transfers.`;}
  finally{receivingOngoing=false;paintOngoing();controls();}
};
const acceptedStates = ["consent_verified_not_payment_authority", "spending_authorised_wallet_permission_required"];
const spending = () => [2,3].includes(checked?.terms.version);
const status = (message,error=false) => {
  $("status").textContent = message; $("status").className = error ? "notice error" : "notice";
  $("action-note").textContent = message;
};
function pricesValid() {
  if(checked?.terms.closed_session_review)return true; // Signed historic account, not today's indicative tariff.
  return live?.valid && ["import","export"].every(k => live[k]?.available &&
    Date.parse(live[k].start) <= Date.now() &&
    Date.parse(live[k].end) + 90000 >= Date.now());
}
function controls() {
  document.body.dataset.pairing=pairing?.state || "local";
  const expired = checked && Date.parse(checked.terms.expires_at) <= Date.now();
  const waived=collectionState==="waived";
  const receiptOnly=creditDirection&&collectionState==="provider_confirmed";
  $("multi-session-select").disabled=busy||collectionBusy||!!pendingReport||!!pendingDiagnostic;
  $("approve").disabled = waived || framed || busy || !checked || !spending() || expired || accepted || !!receipt ||
    (!!capability && !pricesValid()) || pairingBusy || (!!pairing && pairing.state!=="paired");
  $("pairing-section").hidden=waived || !capability || !checked || framed || !!enrolment;
  $("pairing-connect").disabled=busy||collectionBusy||pairingBusy||!!pairing&&["creating","scanning","paired"].includes(pairing.state);
  $("pairing-disconnect").disabled=busy||collectionBusy||pairingBusy;
  $("driver-link-copy").disabled=!capability;
  $("load").disabled = busy; $("invitation").disabled = busy;
  $("reset").disabled = busy || collectionBusy; $("download").disabled = !receipt || busy;
  $("retry").disabled = busy || !receipt || !capability || accepted;
  $("resume-collection").disabled=busy || collectionBusy || (!accepted&&!receiptOnly) || !spending() ||
    (!!connectedWallet && !halted && collectionState!=="recovery_ready") || !!pendingReport || framed ||
    pairingBusy || (!!pairing && pairing.state!=="paired") ||
    (!creditDirection && collectionState && !["waiting_for_operator_binding","waiting_for_session_end","ready","recovery_ready"].includes(collectionState));
  $("retry-collection").disabled=busy || collectionBusy || !pendingReport;
  if (expired && !accepted && !waived && !receiptOnly) status("This invitation has expired. Ask the operator for a new link.",true);
  $("approve").textContent = "Authorise EV charging budget";
  const view=driverView({accepted,registered:creditRegistered,connected:!!connectedWallet,
    state:collectionState,credit:creditDirection,creditEnabled,closedSession:!!checked?.terms.closed_session_review,
    imported:importedCredits.has(latestTxid),hasInvitation:!!checked});
  $("page-title").textContent=view.title;$("page-subtitle").textContent=view.subtitle;
  if(!capability&&!checked&&publicQRReady){
    $("page-title").textContent="Ready for the next driver";
    $("page-subtitle").textContent="Scan the invitation to review the rates and authorise your EV charging budget.";
    $("status").textContent="Registration is open. Open the new-driver invitation or scan its QR in BSV Browser.";
  }
  document.body.dataset.stage=view.stage;
  $("progress-approve").className=accepted?"done":"";
  $("progress-session").className=accepted&&collectionState!=="waiting_for_operator_binding"?"done":"";
  $("progress-settle").className=view.stage==="settled"?"done":"";
  $("approve").hidden=accepted||waived||receiptOnly;
  $("approval-action").hidden=!checked||accepted||waived||receiptOnly;
  $("approval-terms").hidden=accepted||waived||receiptOnly;$("action-note").hidden=accepted||waived||receiptOnly;
  $("approval-heading").textContent=waived?"Historical approved limit":accepted?"Your approved limit":"Your spending limit";
  $("resume-collection").textContent=view.reconnect||"Reconnect wallet";
  $("resume-collection").hidden=view.stage==="settled"||(!view.reconnect&&!!connectedWallet)||
    (!creditDirection && collectionState==="wallet_attempt_reserved");
  $("retry-collection").hidden=waived||!pendingReport;
  $("retry").hidden=waived||accepted||!receipt;
  if(checked?.terms.version!==3&&showOngoingOverview({accepted,ongoingCount:ongoingRows.length,
    sessionMode:checked?.terms.session_mode,binding,state:collectionState})){
    const last=ongoingRows[ongoingRows.length-1], imported=last.state==="provider_confirmed"&&importedCredits.has(last.txid);
    $("page-title").textContent=last.txid?(imported?"Credit accepted by your wallet":last.state==="provider_confirmed"?"Your credit is confirmed":"Credit awaiting confirmation"):
      last.state==="no_operator_credit"?"No operator credit due":ongoingEnabled?"Automatic driver credits":"Automatic credits paused";
    $("page-subtitle").textContent=last.error||"Each session has a fixed receiving wallet and its own settlement record. This policy does not authorise charges to your wallet.";
    $("status").textContent=imported?"Your wallet reports receipt acceptance, recorded by the operator. No further payment is needed.":
      last.error|| (last.state==="provider_confirmed"?"Connect your registered wallet to receive the confirmed credit receipt.":
      last.txid?"Tracking the existing payment. Do not send another transaction.":
      !ongoingEnabled?"The operator has paused ongoing credits. Contact them to review your account.":
      last.state==="no_operator_credit"?"This session does not require an operator payment.":
      "The operator will check your final session account and pay an eligible credit automatically.");
    $("approval-heading").textContent="Your separate spending approval";
    $("status").className=last.error?"notice error":"notice";
    $("collection-section").hidden=true;
    $("progress-session").className="done";
    $("progress-settle").className=imported||last.state==="no_operator_credit"?"done":"";
    document.body.dataset.stage=imported?"settled":"approved";
  }
}
async function api(action, extra={}) {
  if(enrolment && !["read","approve"].includes(action))throw Error("Open this new-driver invitation inside BSV Browser to authorise your budget first.");
  const response = await fetch("/api/bsv_settlement/driver",{
    method:"POST",credentials:"omit",cache:"no-store",referrerPolicy:"no-referrer",
    headers:{"Content-Type":"application/json"},
    body:JSON.stringify({...capability,action:enrolment?(action==="read"?"public_read":"public_approve"):action,
      ...(["collection_status","claim_collection","authorise_collection","report_collection","reconcile_collection","report_collection_failure"].includes(action)&&selectedSession?{session_id:selectedSession}:{}),
      ...extra}),
    signal:AbortSignal.timeout(action==="authorise_collection" || action==="report_collection" ? 120000 : 20000)
  });
  const result = await response.json();
  if (!response.ok) throw Error(result.error || "Cannot contact the operator.");
  return result;
}
function paintPrices() {
  $("prices").hidden = !capability || !!checked?.terms.closed_session_review;
  for (const [key,id] of [["import","import-price"],["export","export-price"]]) {
    const p=live?.[key];
    $(id).textContent = p?.available ? `${(Number(p.aud_per_kwh)*100).toFixed(2)} c/kWh${p.estimate ? " (estimated)" : ""}` : "Unavailable";
  }
  $("price-time").textContent = live?.checked_at
    ? `Checked ${new Date(live.checked_at).toLocaleTimeString()}. Rates change during charging.`
    : "Waiting for current buy and sell rates.";
  $("price-warning").textContent = pricesValid() ? "Indicative now, not a fixed tariff. The signed rule uses each interval's price." :
    "Prices are unavailable or stale. Approval is paused until current prices return.";
}
function show(invitation) {
  checked=parseInvitation(JSON.stringify(invitation),Date.now(),true);
  const t=checked.terms;
  $("budget").textContent=`${t.max_total_sats.toLocaleString()} sat`;
  $("aud").textContent=`AUD ${(t.max_total_sats/Number(t.satoshis_per_aud)).toFixed(2)} at the displayed conversion rate`;
  $("fee").textContent=`Up to ${t.max_fee_sats} sat, within the total limit. Not a fixed charge.`;
  $("mobile-fee").textContent=`Total cap ${t.max_total_sats.toLocaleString()} sat, including up to ${t.max_fee_sats} sat fee`;
  $("rate").textContent=`${t.satoshis_per_aud} sat / AUD, fixed for this approval`;
  $("session").textContent=t.version===3?"Multiple future sessions on this charger":t.session_mode==="next_session_reservation" ? "One future charging session" : t.session_id;
  $("transaction").textContent=t.transaction_id;
  $("operator").textContent=t.operator_address;
  $("operator-name").textContent=t.operator_name || "Charging operator";
  $("contact").textContent=t.operator_contact || "Contact the operator who supplied this invitation.";
  $("operator-key").textContent=t.operator_identity;
  $("expiry").textContent=new Date(t.expires_at).toLocaleString();
  $("pricing").textContent=t.pricing_rule;
  $("scope").textContent=t.account_scope;
  $("closed-account").hidden=!t.closed_session_review;
  if(t.closed_session_review){
    const c=t.closed_session_review,a=c.account,total=Number(a.import_kwh)+Number(a.export_kwh);
    const warnings=(a.quality_flags||c.accepted_flags).map(f=>({
      "import:energy_without_matching_state":"Charging energy was recorded while the charger state did not indicate charging",
      "export:energy_without_matching_state":"Export energy was recorded while the charger state did not indicate discharging"
    })[f]||f).join(". ");
    $("closed-account").textContent=`Completed session ${t.transaction_id}. Energy Imported to EV: ${a.import_kwh} kWh; Energy Imported from EV: ${a.export_kwh} kWh. Net account AUD ${a.net_amount_aud}; ${c.amount_sats} sat payment, plus actual network fee within your total limit. ${total>0?`Average net energy cost A$${(Number(a.net_amount_aud)/total).toFixed(4)}/kWh, excluding network fee. `:""}Metering warnings: ${warnings||"standard provisional interval allocation only"}. Operator reason: ${c.reason}. Approval can collect this account immediately.`;
  }
  $("approval-terms").textContent = spending() ?
    `By selecting Authorise EV charging budget, you authorise one automatic payment to the displayed operator address after your bound session ends, if the final net account is positive. Your total wallet debit must not exceed ${t.max_total_sats} sat including the network fee; the fee must not exceed ${t.max_fee_sats} sat. The displayed dynamic pricing rule, fixed conversion rate and expiry apply.` :
    "This is an old consent-only invitation. It cannot authorise spending. Ask the operator to revoke it and issue a new spending invitation. Existing signatures do not change.";
  if(t.closed_session_review)$("approval-terms").textContent=
    `You authorise one payment of ${t.closed_session_review.amount_sats} sat for this completed account, plus the actual fee, up to ${t.max_total_sats} sat total. Review the warnings above. The operator's explanation is not independent validation of the meter. This approval does not authorise another session or change your receiving wallet.`;
  if(t.version===3){
    $("approval-terms").textContent=`You authorise collection for multiple future sessions on this charger, up to ${t.max_total_sats} sat TOTAL including all network fees, until ${t.expires_at} or a newer driver registration. No per-session reset. Operator credits do not replenish this allowance. Collection still needs the driver wallet available; this is not a funds reservation or offline payment guarantee.`;
    $("credit-policy").textContent="Receiving registration covers eligible credits for multiple sessions during this approval window, ending earlier on a newer driver registration. Operator funding and separate per-credit caps still apply. No cumulative operator-credit cap is implied.";
  }
  $("credit-status").hidden=!!t.closed_session_review;
  $("credit-policy").hidden=!!t.closed_session_review;
  $("terms").hidden=false; $("wallet-section").hidden=false;
  const privateUrl=!framed&&capability?privateSessionUrl(location.href,location.origin):null;
  $("private-link-section").hidden=!privateUrl;
  $("private-link-qr").replaceChildren();
  if(privateUrl){
    drawApprovalQR($("private-link-qr"),privateUrl);
    $("private-link-qr").querySelector("svg").setAttribute("aria-label","Private link to this driver session; not wallet pairing or spending approval");
  }
  $("invitation").value=JSON.stringify(invitation,null,2);
  controls();
}
async function refresh(initial=false) {
  try {
    const result=await api("read");
    $("connection-status").hidden=true;
    if(pendingDiagnostic){
      try{await api("report_collection_failure",pendingDiagnostic);pendingDiagnostic=null;}
      catch{/* Keep the first error locally; never retry payment here. */}
    }
    ongoingRows=result.ongoing_credits||[];registeredIdentity=result.driver_identity;
    ongoingEnabled=!!result.ongoing_credit_enabled;
    paintOngoing();
    if (checked && result.invitation.payload!==checked.invitation.payload) throw Error("Invitation changed. Reopen the operator's link.");
    if (!checked) show(result.invitation);
    if(result.multi_session){
      $("collection-section").hidden=false;$("multi-session-panel").hidden=false;
      $("multi-session-allowance").textContent=`${result.multi_session.remaining_sats} sat available of ${result.multi_session.max_total_sats} sat total. ${result.multi_session.committed_sats} sat paid or reserved, including fees. Valid until ${result.multi_session.expires_at}. ${result.multi_session.error||""}`;
    }
    live=result.prices; accepted=acceptedStates.includes(result.state) || !!result.driver_identity; binding=result.binding;
    creditEnabled=result.automatic_credit_enabled;creditRegistered=result.credit_destination_registered;
    const s=result.session;
    $("energy-summary").hidden=!s;
    if(s)$("energy-summary").textContent=`${s.ended_at?"Session ended":"Session in progress"} · Energy Imported to EV: ${s.import_kwh ?? "unavailable"} kWh · Energy Imported from EV: ${s.export_kwh ?? "unavailable"} kWh${s.net_cost_aud!==null&&s.net_cost_aud!==undefined ? ` · Provisional ${Number(s.net_cost_aud)<0?"credit":"charge"} AUD ${Math.abs(Number(s.net_cost_aud)).toFixed(2)}`:""}`;
    $("credit-status").textContent=creditRegistered ?
      "Receiving wallet registered. Eligible net credits are paid automatically by the operator, even if you close this page. Reopen to import the confirmed credit into your wallet." :
      "Receiving wallet is not registered. Automatic credits are not ready for this session.";
    if(binding) {
      $("session").textContent=binding.session_id;
      $("transaction").textContent=binding.transaction_id;
    }
    paintPrices();
    if(result.closure?.state==="waived"){
      paintCollection(result.closure);
    } else if(accepted) {
      $("wallet-key").textContent=result.driver_identity;
      if(!collectionState)status(spending() ? "Approval saved. Checking your session and settlement…" :
        "Old consent is saved. It grants no spending authority. Ask the operator for a new invitation to approve spending.");
    } else if(initial) status(checked.terms.closed_session_review?
      "Review the completed account, metering warnings and fee limits before approving payment. Your wallet may ask for permission.":
      "Review the operator, current prices and budget, then select Authorise EV charging budget. Your wallet may ask for permission.");
  } catch(e) {
    live=null;paintPrices();
    $("connection-status").hidden=false;
    $("connection-status").textContent="Connection interrupted. Session and payment state are not updated. "+(e.message||"Try again later.");
    if(!checked)status("Cannot load this approval link. Check the connection.",true);
  }
  controls();
  if(accepted && spending() && collectionState!=="waived")await checkCollection();
}
const collectionMessages={
  waived:"The operator waived this charge. No further collection is authorised; any funds already received need separate accounting.",
  waiting_for_operator_binding:"Waiting for the operator to bind your approval to your charging session.",
  waiting_for_session_end:"Automatic collection is armed. Waiting for the bound session to end.",
  ready:"The session account is ready for automatic collection.",
  wallet_attempt_reserved:"A wallet attempt is reserved. Do not start another payment; reconcile with the operator if this page was closed.",
  recovery_ready:"The operator reviewed the previous attempt. Review the amount, then explicitly resume with your wallet. No payment has been retried.",
  submission_authorised:"A signing permit was issued. Keep this page open. If interrupted, reconcile with the operator.",
  broadcast_unknown:"Submission outcome is uncertain. No further broadcast will be attempted; checking the recorded transaction is safe.",
  submitted:"Payment submitted. Waiting for provider evidence.",
  provider_unconfirmed:"Awaiting block confirmation. The chain provider has the payment. Do not pay again.",
  provider_confirmed:"Payment confirmed by the chain provider.",
  operator_credit_review_required:"This session has a net credit. The operator must review and pay the credit separately.",
  no_payment_due:"The final net account is zero. No payment is due.",
  collection_blocked:"Automatic collection is blocked. Ask the operator to review the account."
};
function paintCollection(result) {
  latestCollection=result;
  if(["recovery_ready","waived"].includes(result.state)){
    collectionFailure=null;pendingDiagnostic=null;
  } else if(result.diagnostic)collectionFailure=result.diagnostic;
  creditDirection=result.direction==="operator_to_driver";
  collectionState=result.state;
  paintFailure();
  if(["wallet_attempt_reserved","recovery_ready"].includes(result.state)){
    status(result.state==="recovery_ready"
      ? "Review complete. Your explicit confirmation is required before collection can resume."
      : "Collection is held for review. Do not start another payment.",result.state==="wallet_attempt_reserved");
  }
  latestTxid=result.txid||null;
  $("collection-section").hidden=false;
  $("collection-status").textContent=(collectionMessages[result.state] || result.state)+
    (result.error ? " "+result.error : "");
  const flags=result.quote?JSON.parse(result.quote.payload).account.quality_flags:result.quality_flags;
  $("quality-warnings").hidden=!warningMessage(flags);
  $("quality-warnings").textContent=`Metering warning (non-blocking): ${warningMessage(flags)} Payment permission and safety checks still apply.`;
  if(result.quote) {
    const q=JSON.parse(result.quote.payload);
    $("collection-amount").textContent=`Session payment: ${q.amount_sats} sat${result.fee_sats!==undefined ? " + "+result.fee_sats+" sat fee" : ", effective fee cap "+q.max_fee_sats+" sat"}. Total debit limit: ${q.max_total_sats} sat. Transaction: ${q.account.ocpp_transaction_id}.`;
  }
  if(result.state==="waived"){
    $("collection-amount").textContent=result.amount_sats?
      `Waived charge: ${result.amount_sats} sat. No new payment or refund.`:"Charge waived. No new payment or refund.";
    $("credit-status").hidden=true;
    $("settlement-detail").textContent="The original attempt remains in the audit record. Contact the operator if a wallet action or late receipt needs separate accounting.";
    status("Session charge waived. Do not approve or retry this payment.");
  }
  $("collection-txid").textContent=result.txid ? `BSV transaction ID: ${result.txid}` : "";
  const recordLink=$("collection-chain-link"),recordUrl=chainRecordUrl(result.txid);
  recordLink.hidden=!recordUrl;
  if(recordUrl)recordLink.href=recordUrl;else recordLink.removeAttribute("href");
  if(creditDirection){
    $("settlement-heading").textContent="Your session credit";
    $("collection-status").textContent=({
      provider_confirmed:"Credit confirmed on chain",
      provider_unconfirmed:"Credit sent, awaiting confirmation",
      submitted:"Credit submitted",
      broadcast_unknown:"Submission uncertain. Tracking the existing payment.",
      automatic_credit_pending:"Credit will be checked when the session ends",
      credit_destination_required:"Receiving wallet registration needed",
      credit_queued:"Credit queued for funding and account checks",
    })[result.state]||"Credit needs attention";
    if(result.error)$("collection-status").textContent+=`. ${result.error}`;
    $("collection-amount").textContent=result.amount_sats ?
      `Your wallet receives ${result.amount_sats} sat. The operator pays the ${result.fee_sats} sat network fee.` :
      "Eligible credits are paid automatically. No per-payment operator approval.";
    $("settlement-detail").textContent="The operator wallet signs and pays the session credit automatically. Reopening this page imports the confirmed receipt into your wallet; it does not send another payment. Chain status is provider-reported, not independent SPV verification.";
  }
  $("settlement-badge").textContent=result.state==="waived"?"Waived":result.state==="provider_confirmed"?"Confirmed":result.txid?"Submitted":
    result.state==="recovery_ready"?"Driver confirmation":
    result.state==="wallet_attempt_reserved"||collectionFailure?"Needs review":"In progress";
  if(result.state==="provider_confirmed")status(creditDirection ?
    "Your session credit is confirmed by the chain provider. Reconnect your wallet here to import the receipt." :
    "Session payment confirmed by the chain provider. No further collection will be attempted.");
}
async function checkCollection() {
  if(!capability || framed || collectionBusy || busy)return;
  collectionBusy=true;controls();
  try {
    if(checked?.terms.version===3){
      const list=await api("collection_status",{session_id:null});
      const sessions=list.sessions||[],select=$("multi-session-select");
      if(!selectedSession || latestCollection?.state==="provider_confirmed"){
        const next=sessions.find(s=>s.state!=="provider_confirmed");
        selectedSession=next?.session_id||selectedSession||sessions[0]?.session_id||null;
      }
      select.replaceChildren(...sessions.map(s=>{
        const o=document.createElement("option");o.value=s.session_id;
        o.textContent=`${s.transaction_id.slice(0,8)} · AUD ${s.net_cost_aud} · ${s.state.replaceAll("_"," ")}`;
        o.selected=s.session_id===selectedSession;return o;
      }));
      if(!selectedSession){$("collection-section").hidden=false;$("collection-status").textContent="Waiting for an eligible closed session under this approval.";return;}
    }
    let result=await api("collection_status");
    if(result.direction!=="operator_to_driver" && ["submitted","broadcast_unknown","provider_unconfirmed"].includes(result.state)&&result.txid)
      result=await api("reconcile_collection");
    if(receiptReported(result))importedCredits.add(result.txid);
    paintCollection(result);
    if(creditDirection && result.state==="provider_confirmed" && connectedWallet &&
        !importedCredits.has(result.txid) && !attemptedImports.has(result.txid)){
      attemptedImports.add(result.txid);
      try{
        const report=await importAndReportCredit(connectedWallet,checked,await api("credit_receipt"),
          registeredIdentity,api,receiptImports);
        Object.assign(result,report);
        importedCredits.add(result.txid);
        receiptReportErrors.delete(result.txid);
      }catch(e){
        halted=true;
        receiptReportErrors.set(result.txid,e.walletAccepted?
          `${e.message}. Reconnect to retry reporting; no new payment or import is needed in this page.`:
          `Credit sent, wallet import not complete: ${e.message}. Reconnect to retry importing the same payment.`);
      }
    }
    if(creditDirection && result.state==="provider_confirmed" && importedCredits.has(result.txid)){
      $("credit-status").textContent="Wallet reports receipt accepted. Acknowledgement saved by the operator.";
      status("Your session credit is confirmed. Wallet receipt acceptance is recorded; no further payment is sent.");
    }else if(creditDirection && receiptReportErrors.has(result.txid)){
      $("credit-status").textContent=receiptReportErrors.get(result.txid);
      status(receiptReportErrors.get(result.txid),true);
    }
    if(result.state==="ready" && connectedWallet && !halted) {
      const sessionChecked=checked.terms.version===3?await derivedInvitation(result.session_invitation,checked,selectedSession):checked;
      const collectionSession=selectedSession;
      const scopedApi=(action,extra={})=>api(action,{...extra,...(checked.terms.version===3?{session_id:collectionSession}:{})});
      const paid=await collectOnce(connectedWallet,sessionChecked,checked.terms.version===3?null:binding,result.quote,scopedApi,
        msg=>{$("collection-status").textContent=msg;});
      paintCollection(paid);
    } else if(["ready","waiting_for_session_end"].includes(result.state)&&!connectedWallet) {
      $("collection-status").textContent+=" Select Reconnect wallet to resume.";
    }
  } catch(e) {
    // Never create a replacement transaction after ANY uncertain wallet interaction.
    retainFailure(e);
    $("collection-section").hidden=false;
    $("collection-status").textContent=`Collection paused: ${e.message}. No new payment will be attempted automatically.`;
  } finally {collectionBusy=false;controls();}
}
async function connectWallet() {
  if(pairing)return pairing.verifiedWallet(registeredIdentity || receipt?.driver_identity || null);
  const wallet=new WalletClient("auto");
  let timer;
  const identity=(await Promise.race([
    wallet.getPublicKey({identityKey:true}),
    new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error("Wallet connection timed out. Open this page inside BSV Browser.")),30000);})
  ]).finally(()=>clearTimeout(timer))).publicKey;
  if(!/^(02|03)[0-9a-f]{64}$/.test(identity))throw Error("Invalid wallet identity.");
  return {wallet,identity};
}
function clear() {
  if(pairing){void pairing.disconnect();pairing=null;}
  collectionFailure=null;pendingDiagnostic=null;latestCollection=null;paintFailure();
  checked=null;receipt=null;accepted=false;live=null;connectedWallet=null;binding=null;halted=false;pendingReport=null;collectionState=null;selectedSession=null;
  $("multi-session-panel").hidden=true;$("multi-session-select").replaceChildren();
  $("terms").hidden=true;$("wallet-section").hidden=true;$("result").hidden=true;
  $("receipt").value="";$("wallet-key").textContent="Not connected";
  $("collection-section").hidden=true;$("collection-amount").textContent="";$("collection-txid").textContent="";
  $("private-link-section").hidden=true;$("private-link-qr").replaceChildren();
}
$("load").onclick=()=>{
  clear();
  try {show(JSON.parse($("invitation").value));status("Signature checked. Confirm this operator is the one you intend to authorise.");}
  catch(e){status(e.message || "Invalid invitation.",true);}
  controls();
};
$("invitation").oninput=()=>{clear();controls();};
async function submitReceipt() {
  const result=await api("approve",{receipt});
  if(enrolment){
    const url=privateSessionUrl(location.origin+"/bsv_settlement/driver/index.html"+(result.private_link_fragment||""),location.origin);
    if(!url)throw Error("The operator did not return a private session link. Retry saving the existing signature.");
    const p=new URLSearchParams(new URL(url).hash.slice(1));
    capability={budget_id:p.get("budget"),token:p.get("token")};enrolment=null;
    history.replaceState(null,"",url);
    show(result.invitation);
  }
  accepted=acceptedStates.includes(result.state);
  if (!accepted || result.driver_identity!==receipt.driver_identity) throw Error("Approval was not accepted for this wallet.");
  binding=result.binding;
  creditEnabled=result.automatic_credit_enabled;creditRegistered=result.credit_destination_registered;
  if(creditEnabled && connectedWallet && !creditRegistered){
    try{
      await registerCredit(connectedWallet,checked,receipt.driver_identity,api);
      creditRegistered=true;
      $("credit-status").textContent="Receiving wallet registered for automatic session credits.";
    }catch(e){halted=true;$("credit-status").textContent=`Spending approval saved, but receiving-wallet registration failed: ${e.message}. Reconnect before the session ends.`;}
  }
  status(checked.terms.closed_session_review?
    "Completed-account approval saved. Keep BSV Browser and this page open while collection is checked now.":
    "Spending approval saved. Keep BSV Browser and this page open for automatic collection when the bound session ends.");
  $("result-note").textContent="Your spending approval is verified and saved by the operator. Keep a copy if you wish.";
}
$("approve").onclick=async()=>{
  if($("approve").disabled)return;
  busy=true;controls();
  try {
    if(capability) {
      const result=await api("read");
      if(result.invitation.payload!==checked.invitation.payload || result.state!=="awaiting_driver_consent")
        throw Error("This invitation has changed or is already used. Reload the page.");
      live=result.prices;paintPrices();
      if(!pricesValid())throw Error("Current buy and sell rates are unavailable. Try again later.");
    }
    status(checked.terms.closed_session_review?
      "Waiting for BSV Browser. Approval permits immediate collection of this completed account within the displayed limits.":
      "Waiting for BSV Browser. This action connects your identity and signs your capped spending approval. No payment is made now.");
    const {wallet,identity}=await connectWallet();
    $("wallet-key").textContent=identity;
    receipt=await signConsent(wallet,checked,identity);
    connectedWallet=wallet;
    $("receipt").value=JSON.stringify(receipt,null,2);$("result").hidden=false;
    if(capability)await submitReceipt();
    else status("Spending approval signed. Return the receipt to the operator for verification. No funds moved.");
  } catch(e) {
    status(receipt ? `Signed, but saving is not confirmed: ${e.message}. Select Retry saving; do not sign again.` :
      `Approval not completed: ${e.message || e}`,true);
  } finally {busy=false;controls();if(accepted)await checkCollection();}
};
$("retry").onclick=async()=>{
  busy=true;controls();
  try{await submitReceipt();}catch(e){status(`Saving is not confirmed: ${e.message}. The existing signature is retained.`,true);}
  finally{busy=false;controls();if(accepted)await checkCollection();}
};
$("resume-collection").onclick=async()=>{
  busy=true;controls();
  try {
    const result=await api("read");
    const {wallet,identity}=await connectWallet();
    if(identity!==result.driver_identity)throw Error("Connect the wallet that signed this session approval.");
    connectedWallet=wallet;binding=result.binding;halted=false;
    attemptedImports.clear();
    if(result.automatic_credit_enabled && !result.credit_destination_registered){
      await registerCredit(wallet,checked,identity,api);creditRegistered=true;
    }
    const current=await api("collection_status");
    if(current.state==="recovery_ready"){
      const q=JSON.parse(current.quote.payload);
      if(!window.confirm(`Resume this reviewed attempt for ${q.amount_sats} sat, with a maximum ${q.max_fee_sats} sat fee? The previous attempt must have been checked and any unsigned draft cancelled. This can charge your wallet.`)){
        halted=true;return;
      }
      collectionFailure=null;paintFailure();
      const sessionChecked=checked.terms.version===3?await derivedInvitation(current.session_invitation,checked,selectedSession):checked;
      const collectionSession=selectedSession;
      const scopedApi=(action,extra={})=>api(action,{...extra,...(checked.terms.version===3?{session_id:collectionSession}:{})});
      paintCollection(await collectOnce(wallet,sessionChecked,checked.terms.version===3?null:binding,current.quote,scopedApi,
        msg=>{$("collection-status").textContent=msg;},true));
    }
  } catch(e){retainFailure(e);status(e.message,true);}
  finally{busy=false;controls();if(connectedWallet)await checkCollection();}
};
$("retry-collection").onclick=async()=>{
  if(!pendingReport || collectionBusy)return;
  collectionBusy=true;controls();
  try {
    paintCollection(await api("report_collection",pendingReport));
    pendingReport=null;
  } catch(e){$("collection-status").textContent=`Reconciliation not complete: ${e.message}. The same signed transaction is retained.`;}
  finally{collectionBusy=false;controls();}
};
$("reset").onclick=()=>{
  clear();$("invitation").value="";
  if(capability)refresh(true);
  else status("Page cleared. This does not revoke a saved consent. Ask the operator to revoke it.");
};
$("download").onclick=()=>{
  if(!receipt)return;
  const url=URL.createObjectURL(new Blob([JSON.stringify(receipt,null,2)],{type:"application/json"}));
  const a=document.createElement("a");a.href=url;a.download=`session-budget-${receipt.budget_id}.json`;a.click();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
};
if(capability) {
  $("load-section").hidden=true;
  if(framed)status("Open the private approval link directly inside BSV Browser, not inside an embedded frame.",true);
  else refresh(true);
}
let publicBusy=false;
async function refreshPublicInvitation(){
  if(capability||checked||publicBusy||window.top!==window)return;
  publicBusy=true;
  const box=$("public-enrolment-qr"),link=$("public-enrolment-open");
  try{
    const response=await fetch("/api/bsv_settlement/driver",{
      method:"POST",credentials:"omit",cache:"no-store",referrerPolicy:"no-referrer",
      headers:{"Content-Type":"application/json"},body:JSON.stringify({action:"public_invitation"}),
      signal:AbortSignal.timeout(4500)});
    if(!response.ok)throw Error("Invitation check unavailable");
    const data=await response.json();
    const url=data.state==="available"?publicEnrolmentUrl(
      location.origin+"/bsv_settlement/driver/index.html"+data.public_link_fragment,location.origin):null;
    publicQRReady=!!url;
    box.hidden=!url;link.hidden=!url;
    if(url){
      drawApprovalQR(box,url);
      box.querySelector("svg").setAttribute("aria-label","Unassigned new-driver invitation, not a private session link");
      link.href=url;$("public-enrolment-status").textContent="Scan to review and authorise the available EV charging budget. Scanning alone does not approve spending.";
    }else{
      box.replaceChildren();link.removeAttribute("href");
      $("public-enrolment-status").textContent="No public invitation is available. Ask the operator for your private link or a new invitation.";
      $("status").textContent="Open the private session link supplied by your charging operator.";
    }
  }catch{
    publicQRReady=false;
    box.hidden=true;box.replaceChildren();link.hidden=true;link.removeAttribute("href");
    $("public-enrolment-status").textContent="Invitation check unavailable. No QR is shown; try again shortly.";
    $("status").textContent="Cannot check driver registration. Ask the operator for a private link.";
  }finally{publicBusy=false;controls();}
}
if(!capability)void refreshPublicInvitation();
setInterval(()=>void refreshPublicInvitation(),5000);
let theme=matchMedia("(prefers-color-scheme:dark)").matches?"dark":"light";
function paintTheme(){document.documentElement.dataset.theme=theme;$("theme").textContent=theme==="dark"?"Light":"Dark";$("theme").setAttribute("aria-label",`Switch to ${theme==="dark"?"light":"dark"} mode`);}
$("theme").onclick=()=>{theme=theme==="dark"?"light":"dark";paintTheme();};paintTheme();
setInterval(()=>{paintPrices();controls();},1000);
setInterval(()=>{if(capability&&!framed&&!busy&&!collectionBusy)refresh();},30000);
controls();
