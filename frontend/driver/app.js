import { WalletClient } from "@bsv/sdk";
import { parseInvitation, signConsent } from "./model.js";
import { collectOnce } from "./collection.js";

const $ = id => document.getElementById(id);
const fragment = new URLSearchParams(location.hash.slice(1));
const capability = fragment.has("budget") && fragment.has("token")
  ? {budget_id:fragment.get("budget"),token:fragment.get("token")} : null;
const framed = !!capability && window.top !== window;
let checked = null, receipt = null, busy = false, live = null, accepted = false;
let connectedWallet=null, binding=null, collectionBusy=false, halted=false, pendingReport=null, collectionState=null;
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
    (collectionState && !["waiting_for_operator_binding","waiting_for_session_end","ready"].includes(collectionState));
  $("retry-collection").disabled=busy || collectionBusy || !pendingReport;
  if (expired && !accepted) status("This invitation has expired. Ask the operator for a new link.",true);
  $("approve").textContent = accepted ? (spending() ? "Spending approval saved" : "Old consent saved, no spending authority") :
    receipt ? "Spending approval signed" : !spending() && checked ? "New invitation required to approve spending" :
    `Approve spending up to ${checked ? checked.terms.max_total_sats.toLocaleString() : "…"} sat`;
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
  checked=parseInvitation(JSON.stringify(invitation));
  const t=checked.terms;
  $("budget").textContent=`${t.max_total_sats.toLocaleString()} sat`;
  $("aud").textContent=`AUD ${(t.max_total_sats/Number(t.satoshis_per_aud)).toFixed(2)} at the displayed conversion rate`;
  $("fee").textContent=`${t.max_fee_sats} sat maximum, included in total`;
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
    if (checked && result.invitation.payload!==checked.invitation.payload) throw Error("Invitation changed. Reopen the operator's link.");
    if (!checked) show(result.invitation);
    live=result.prices; accepted=acceptedStates.includes(result.state); binding=result.binding;
    if(binding) {
      $("session").textContent=binding.session_id;
      $("transaction").textContent=binding.transaction_id;
    }
    paintPrices();
    if(accepted) {
      $("wallet-key").textContent=result.driver_identity;
      status(spending() ? "Spending approval saved. Keep this page open for automatic session-end collection; your wallet may request permission." :
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
  collectionState=result.state;
  $("collection-section").hidden=false;
  $("collection-status").textContent=(collectionMessages[result.state] || result.state)+
    (result.error ? " "+result.error : "");
  if(result.quote) {
    const q=JSON.parse(result.quote.payload);
    $("collection-amount").textContent=`Session payment: ${q.amount_sats} sat${result.fee_sats!==undefined ? " + "+result.fee_sats+" sat fee" : ", fee cap "+q.max_fee_sats+" sat"}. Transaction: ${q.account.ocpp_transaction_id}.`;
  }
  $("collection-txid").textContent=result.txid ? `BSV transaction ID: ${result.txid}` : "";
  if(result.state==="provider_confirmed")status("Session payment confirmed by the chain provider. No further collection will be attempted.");
}
async function checkCollection() {
  if(!capability || framed || collectionBusy || busy)return;
  collectionBusy=true;controls();
  try {
    let result=await api("collection_status");
    if(["submitted","broadcast_unknown","provider_unconfirmed"].includes(result.state)&&result.txid)
      result=await api("reconcile_collection");
    paintCollection(result);
    if(result.state==="ready" && connectedWallet && !halted) {
      const paid=await collectOnce(connectedWallet,checked,binding,result.quote,api,
        msg=>{$("collection-status").textContent=msg;});
      paintCollection(paid);
    } else if(["ready","waiting_for_session_end"].includes(result.state)&&!connectedWallet) {
      $("collection-status").textContent+=" Select Resume automatic collection to reconnect this wallet.";
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
setInterval(()=>{paintPrices();controls();},1000);
setInterval(()=>{if(capability&&!framed&&!busy&&!collectionBusy)refresh();},30000);
controls();
