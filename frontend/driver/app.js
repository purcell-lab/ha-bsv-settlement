import { WalletClient } from "@bsv/sdk";
import { parseInvitation, signConsent } from "./model.js";
import { collectOnce } from "./collection.js";
import { registerCredit, importCredit } from "./credit.js";
import { driverView } from "./view.js";

const $ = id => document.getElementById(id);
const fragment = new URLSearchParams(location.hash.slice(1));
// A new fragment identifies a different private invitation. Reset every wallet
// and session variable rather than displaying the old session in the same tab.
window.addEventListener("hashchange", () => location.reload());
const capability = fragment.has("budget") && fragment.has("token")
  ? {budget_id:fragment.get("budget"),token:fragment.get("token")} : null;
const framed = !!capability && window.top !== window;
let checked = null, receipt = null, busy = false, live = null, accepted = false;
let connectedWallet=null, binding=null, collectionBusy=false, halted=false, pendingReport=null, collectionState=null;
let creditDirection=false, creditEnabled=false, creditRegistered=false;
const importedCredits=new Set(), attemptedImports=new Set();
let latestTxid=null;
let ongoingRows=[],registeredIdentity=null,receivingOngoing=false,ongoingEnabled=false;
function paintOngoing(){
  $("ongoing-section").hidden=!ongoingRows.length;
  $("ongoing-list").replaceChildren();
  for(const row of ongoingRows.slice().reverse()){
    const box=document.createElement("div");box.className="notice small";
    const p=document.createElement("p");p.className="strong";
    p.textContent=`${row.amount_sats?row.amount_sats+" sat credit · ":""}${importedCredits.has(row.txid)?"Receipt accepted by wallet":(collectionMessages[row.state]||row.state.replaceAll("_"," "))}`;
    const note=document.createElement("p");note.textContent=`Session ${row.transaction_id.slice(0,8)}${row.amount_sats?" · "+row.fee_sats+" sat operator fee":""}. ${row.error||""}`;
    const details=document.createElement("details"),summary=document.createElement("summary"),refs=document.createElement("p");
    summary.textContent="Full session and transaction references";refs.className="mono";
    refs.textContent=`Session: ${row.transaction_id}. BSV transaction: ${row.txid||"Not submitted"}. Receiving address: ${row.recipient_address}.`;
    details.append(summary,refs);box.append(p,note,details);$("ongoing-list").append(box);
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
      await importCredit(wallet,checked,receipt);
      importedCredits.add(row.txid);
    }
    $("ongoing-feedback").textContent="Confirmed credit receipts accepted by your wallet. No payment was sent.";
  }catch(e){$("ongoing-feedback").textContent=`Receipt import paused: ${e.message}. Retry imports the same payments, not new transfers.`;}
  finally{receivingOngoing=false;paintOngoing();controls();}
};
const acceptedStates = ["consent_verified_not_payment_authority", "spending_authorised_wallet_permission_required"];
const spending = () => checked?.terms.version === 2;
const status = (message,error=false) => {
  $("status").textContent = message; $("status").className = error ? "notice error" : "notice";
  $("action-note").textContent = message;
};
function pricesValid() {
  return live?.valid && ["import","export"].every(k => live[k]?.available &&
    Date.parse(live[k].start) <= Date.now() &&
    Date.parse(live[k].end) + 90000 >= Date.now());
}
function controls() {
  const expired = checked && Date.parse(checked.terms.expires_at) <= Date.now();
  $("approve").disabled = framed || busy || !checked || !spending() || expired || accepted || !!receipt ||
    (!!capability && !pricesValid());
  $("load").disabled = busy; $("invitation").disabled = busy;
  $("reset").disabled = busy || collectionBusy; $("download").disabled = !receipt || busy;
  $("retry").disabled = busy || !receipt || !capability || accepted;
  $("resume-collection").disabled=busy || collectionBusy || !accepted || !spending() ||
    (!!connectedWallet && !halted) || !!pendingReport || framed ||
    (!creditDirection && collectionState && !["waiting_for_operator_binding","waiting_for_session_end","ready"].includes(collectionState));
  $("retry-collection").disabled=busy || collectionBusy || !pendingReport;
  if (expired && !accepted) status("This invitation has expired. Ask the operator for a new link.",true);
  $("approve").textContent = accepted ? (spending() ? "Spending approval saved" : "Old consent saved, no spending authority") :
    receipt ? "Spending approval signed" : !spending() && checked ? "New invitation required to approve spending" :
    `Approve spending up to ${checked ? checked.terms.max_total_sats.toLocaleString() : "…"} sat`;
  const view=driverView({accepted,registered:creditRegistered,connected:!!connectedWallet,
    state:collectionState,credit:creditDirection,creditEnabled,imported:importedCredits.has(latestTxid),hasInvitation:!!checked});
  $("page-title").textContent=view.title;$("page-subtitle").textContent=view.subtitle;
  document.body.dataset.stage=view.stage;
  $("progress-approve").className=accepted?"done":"";
  $("progress-session").className=accepted&&collectionState!=="waiting_for_operator_binding"?"done":"";
  $("progress-settle").className=view.stage==="settled"?"done":"";
  $("approve").hidden=accepted;
  $("approval-action").hidden=accepted;
  $("approval-terms").hidden=accepted;$("action-note").hidden=accepted;
  $("approval-heading").textContent=accepted?"Your approved limit":"Your spending limit";
  $("resume-collection").textContent=view.reconnect||"Reconnect wallet";
  $("resume-collection").hidden=view.stage==="settled"||(!view.reconnect&&!!connectedWallet);
  $("retry-collection").hidden=!pendingReport;
  $("retry").hidden=accepted||!receipt;
  if(ongoingRows.length && (!binding || collectionState==="waiting_for_operator_binding")){
    const last=ongoingRows[ongoingRows.length-1], imported=importedCredits.has(last.txid);
    $("page-title").textContent=last.txid?(imported?"Credit accepted by your wallet":last.state==="provider_confirmed"?"Your credit is confirmed":"Credit awaiting confirmation"):
      last.state==="no_operator_credit"?"No operator credit due":ongoingEnabled?"Automatic driver credits":"Automatic credits paused";
    $("page-subtitle").textContent=last.error||"Each session has a fixed receiving wallet and its own settlement record. This policy does not authorise charges to your wallet.";
    $("status").textContent=imported?"Your confirmed credit receipt is accepted by your wallet. No further payment is needed.":
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
  const response = await fetch("/api/bsv_settlement/driver",{
    method:"POST",credentials:"omit",cache:"no-store",referrerPolicy:"no-referrer",
    headers:{"Content-Type":"application/json"},
    body:JSON.stringify({...capability,action,...extra}),
    signal:AbortSignal.timeout(action==="authorise_collection" || action==="report_collection" ? 120000 : 20000)
  });
  const result = await response.json();
  if (!response.ok) throw Error(result.error || "Cannot contact the operator.");
  return result;
}
function paintPrices() {
  $("prices").hidden = !capability;
  for (const [key,id] of [["import","import-price"],["export","export-price"]]) {
    const p=live?.[key];
    $(id).textContent = p?.available ? `${(Number(p.aud_per_kwh)*100).toFixed(2)} c/kWh${p.estimate ? " (estimated)" : ""}` : "Unavailable";
  }
  $("price-time").textContent = live?.checked_at
    ? `Checked ${new Date(live.checked_at).toLocaleTimeString()}. Rates change during charging.`
    : "Waiting for current Amber prices.";
  $("price-warning").textContent = pricesValid() ? "Indicative now, not a fixed tariff. The signed rule uses each interval's price." :
    "Prices are unavailable or stale. Approval is paused until current prices return.";
}
function show(invitation) {
  checked=parseInvitation(JSON.stringify(invitation),Date.now(),true);
  const t=checked.terms;
  $("budget").textContent=`${t.max_total_sats.toLocaleString()} sat`;
  $("aud").textContent=`AUD ${(t.max_total_sats/Number(t.satoshis_per_aud)).toFixed(2)} at the displayed conversion rate`;
  $("fee").textContent=`${t.max_fee_sats} sat maximum, included in total`;
  $("mobile-fee").textContent=`Total cap ${t.max_total_sats.toLocaleString()} sat, including up to ${t.max_fee_sats} sat fee`;
  $("rate").textContent=`${t.satoshis_per_aud} sat / AUD, fixed for this approval`;
  $("session").textContent=t.session_mode==="next_session_reservation" ? "One future charging session" : t.session_id;
  $("transaction").textContent=t.transaction_id;
  $("operator").textContent=t.operator_address;
  $("operator-name").textContent=t.operator_name || "Charging operator";
  $("contact").textContent=t.operator_contact || "Contact the operator who supplied this invitation.";
  $("operator-key").textContent=t.operator_identity;
  $("expiry").textContent=new Date(t.expires_at).toLocaleString();
  $("pricing").textContent=t.pricing_rule;
  $("scope").textContent=t.account_scope;
  $("approval-terms").textContent = spending() ?
    `By selecting Approve spending, you authorise one automatic payment to the displayed operator address after your bound session ends, if the final net account is positive. Your total wallet debit must not exceed ${t.max_total_sats} sat including the network fee; the fee must not exceed ${t.max_fee_sats} sat. The displayed dynamic pricing rule, fixed conversion rate and expiry apply.` :
    "This is an old consent-only invitation. It cannot authorise spending. Ask the operator to revoke it and issue a new spending invitation. Existing signatures do not change.";
  $("terms").hidden=false; $("wallet-section").hidden=false;
  $("invitation").value=JSON.stringify(invitation,null,2);
  controls();
}
async function refresh(initial=false) {
  try {
    const result=await api("read");
    ongoingRows=result.ongoing_credits||[];registeredIdentity=result.driver_identity;
    ongoingEnabled=!!result.ongoing_credit_enabled;
    paintOngoing();
    if (checked && result.invitation.payload!==checked.invitation.payload) throw Error("Invitation changed. Reopen the operator's link.");
    if (!checked) show(result.invitation);
    live=result.prices; accepted=acceptedStates.includes(result.state) || !!result.driver_identity; binding=result.binding;
    creditEnabled=result.automatic_credit_enabled;creditRegistered=result.credit_destination_registered;
    const s=result.session;
    $("energy-summary").hidden=!s;
    if(s)$("energy-summary").textContent=`${s.ended_at?"Session ended":"Session in progress"} · Charged ${s.import_kwh ?? "unavailable"} kWh · Exported ${s.export_kwh ?? "unavailable"} kWh${s.net_cost_aud!==null&&s.net_cost_aud!==undefined ? ` · Provisional ${Number(s.net_cost_aud)<0?"credit":"charge"} AUD ${Math.abs(Number(s.net_cost_aud)).toFixed(2)}`:""}`;
    $("credit-status").textContent=creditRegistered ?
      "Receiving wallet registered. Eligible net credits are paid automatically by the operator, even if you close this page. Reopen to import the confirmed credit into your wallet." :
      "Receiving wallet is not registered. Automatic credits are not ready for this session.";
    if(binding) {
      $("session").textContent=binding.session_id;
      $("transaction").textContent=binding.transaction_id;
    }
    paintPrices();
    if(accepted) {
      $("wallet-key").textContent=result.driver_identity;
      if(!collectionState)status(spending() ? "Approval saved. Checking your session and settlement…" :
        "Old consent is saved. It grants no spending authority. Ask the operator for a new invitation to approve spending.");
    } else if(initial) status("Review the operator, current prices and budget, then select Approve once. Your wallet may ask for permission.");
  } catch(e) {
    live=null;paintPrices();status(e.message || "The approval link is unavailable.",true);
  }
  controls();
  if(accepted && spending())await checkCollection();
}
const collectionMessages={
  waiting_for_operator_binding:"Waiting for the operator to bind your approval to your charging session.",
  waiting_for_session_end:"Automatic collection is armed. Waiting for the bound session to end.",
  ready:"The session account is ready for automatic collection.",
  wallet_attempt_reserved:"A wallet attempt is reserved. Do not start another payment; reconcile with the operator if this page was closed.",
  submission_authorised:"A signing permit was issued. Keep this page open. If interrupted, reconcile with the operator.",
  broadcast_unknown:"Submission outcome is uncertain. No further broadcast will be attempted; checking the recorded transaction is safe.",
  submitted:"Payment submitted. Waiting for provider evidence.",
  provider_unconfirmed:"Payment seen by the chain provider, awaiting confirmation.",
  provider_confirmed:"Payment confirmed by the chain provider.",
  operator_credit_review_required:"This session has a net credit. The operator must review and pay the credit separately.",
  no_payment_due:"The final net account is zero. No payment is due.",
  collection_blocked:"Automatic collection is blocked. Ask the operator to review the account."
};
function paintCollection(result) {
  creditDirection=result.direction==="operator_to_driver";
  collectionState=result.state;
  latestTxid=result.txid||null;
  $("collection-section").hidden=false;
  $("collection-status").textContent=(collectionMessages[result.state] || result.state)+
    (result.error ? " "+result.error : "");
  if(result.quote) {
    const q=JSON.parse(result.quote.payload);
    $("collection-amount").textContent=`Session payment: ${q.amount_sats} sat${result.fee_sats!==undefined ? " + "+result.fee_sats+" sat fee" : ", fee cap "+q.max_fee_sats+" sat"}. Transaction: ${q.account.ocpp_transaction_id}.`;
  }
  $("collection-txid").textContent=result.txid ? `BSV transaction ID: ${result.txid}` : "";
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
  $("settlement-badge").textContent=result.state==="provider_confirmed"?"Confirmed":result.txid?"Submitted":"In progress";
  if(result.state==="provider_confirmed")status(creditDirection ?
    "Your session credit is confirmed by the chain provider. Reconnect your wallet here to import the receipt." :
    "Session payment confirmed by the chain provider. No further collection will be attempted.");
}
async function checkCollection() {
  if(!capability || framed || collectionBusy || busy)return;
  collectionBusy=true;controls();
  try {
    let result=await api("collection_status");
    if(result.direction!=="operator_to_driver" && ["submitted","broadcast_unknown","provider_unconfirmed"].includes(result.state)&&result.txid)
      result=await api("reconcile_collection");
    paintCollection(result);
    if(creditDirection && result.state==="provider_confirmed" && connectedWallet && !attemptedImports.has(result.txid)){
      attemptedImports.add(result.txid);
      try{
        await importCredit(connectedWallet,checked,await api("credit_receipt"));
        importedCredits.add(result.txid);
      }catch(e){halted=true;$("credit-status").textContent=`Credit sent, wallet import not complete: ${e.message}. Reconnect to retry importing the same payment.`;}
    }
    if(creditDirection && importedCredits.has(result.txid)){
      $("credit-status").textContent="Credit accepted by your wallet. No further payment is sent.";
      status("Your session credit is confirmed and its receipt is accepted by your wallet.");
    }
    if(result.state==="ready" && connectedWallet && !halted) {
      const paid=await collectOnce(connectedWallet,checked,binding,result.quote,api,
        msg=>{$("collection-status").textContent=msg;});
      paintCollection(paid);
    } else if(["ready","waiting_for_session_end"].includes(result.state)&&!connectedWallet) {
      $("collection-status").textContent+=" Select Reconnect wallet to resume.";
    }
  } catch(e) {
    // Never create a replacement transaction after ANY uncertain wallet interaction.
    halted=true;pendingReport=e.pendingReport || pendingReport;
    $("collection-section").hidden=false;
    $("collection-status").textContent=`Collection paused: ${e.message}. No new payment will be attempted automatically.`;
  } finally {collectionBusy=false;controls();}
}
async function connectWallet() {
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
  checked=null;receipt=null;accepted=false;live=null;connectedWallet=null;binding=null;halted=false;pendingReport=null;collectionState=null;
  $("terms").hidden=true;$("wallet-section").hidden=true;$("result").hidden=true;
  $("receipt").value="";$("wallet-key").textContent="Not connected";
  $("collection-section").hidden=true;$("collection-amount").textContent="";$("collection-txid").textContent="";
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
  status("Spending approval saved. Keep BSV Browser and this page open for automatic collection when the bound session ends.");
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
      if(!pricesValid())throw Error("Current Amber prices are unavailable. Try again later.");
    }
    status("Waiting for BSV Browser. This action connects your identity and signs your capped spending approval. No payment is made now.");
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
  } catch(e){status(e.message,true);}
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
let theme=matchMedia("(prefers-color-scheme:dark)").matches?"dark":"light";
function paintTheme(){document.documentElement.dataset.theme=theme;$("theme").textContent=theme==="dark"?"Light":"Dark";$("theme").setAttribute("aria-label",`Switch to ${theme==="dark"?"light":"dark"} mode`);}
$("theme").onclick=()=>{theme=theme==="dark"?"light":"dark";paintTheme();};paintTheme();
setInterval(()=>{paintPrices();controls();},1000);
setInterval(()=>{if(capability&&!framed&&!busy&&!collectionBusy)refresh();},30000);
controls();
