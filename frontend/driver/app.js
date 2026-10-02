import { WalletClient } from "@bsv/sdk";
import { parseInvitation, signConsent } from "./model.js";

const $ = id => document.getElementById(id);
const fragment = new URLSearchParams(location.hash.slice(1));
const capability = fragment.has("budget") && fragment.has("token")
  ? {budget_id:fragment.get("budget"),token:fragment.get("token")} : null;
const framed = !!capability && window.top !== window;
let checked = null, receipt = null, busy = false, live = null, accepted = false;
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
  $("approve").disabled = framed || busy || !checked || expired || accepted || !!receipt ||
    (!!capability && !pricesValid());
  $("load").disabled = busy; $("invitation").disabled = busy;
  $("reset").disabled = busy; $("download").disabled = !receipt || busy;
  $("retry").disabled = busy || !receipt || !capability || accepted;
  if (expired && !accepted) status("This invitation has expired. Ask the operator for a new link.",true);
  $("approve").textContent = accepted ? "Consent saved in Home Assistant" :
    receipt ? "Consent signed" : `Approve ${checked ? checked.terms.max_total_sats.toLocaleString()+" sat " : ""}session budget`;
}
async function api(action, extra={}) {
  const response = await fetch("/api/bsv_settlement/driver",{
    method:"POST",credentials:"omit",cache:"no-store",referrerPolicy:"no-referrer",
    headers:{"Content-Type":"application/json"},
    body:JSON.stringify({...capability,action,...extra}),
    signal:AbortSignal.timeout(15000)
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
  $("rate").textContent=`${t.satoshis_per_aud} sat / AUD, fixed for this consent`;
  $("session").textContent=t.session_mode==="next_session_reservation" ? "One future charging session" : t.session_id;
  $("transaction").textContent=t.transaction_id;
  $("operator").textContent=t.operator_address;
  $("operator-name").textContent=t.operator_name || "Charging operator";
  $("contact").textContent=t.operator_contact || "Contact the operator who supplied this invitation.";
  $("operator-key").textContent=t.operator_identity;
  $("expiry").textContent=new Date(t.expires_at).toLocaleString();
  $("pricing").textContent=t.pricing_rule;
  $("tariffs").textContent=`Import tariff: ${t.import_price_entity}. Export tariff: ${t.export_price_entity}.`;
  $("scope").textContent=t.account_scope;
  $("terms").hidden=false; $("wallet-section").hidden=false;
  $("invitation").value=JSON.stringify(invitation,null,2);
  controls();
}
async function refresh(initial=false) {
  try {
    const result=await api("read");
    if (checked && result.invitation.payload!==checked.invitation.payload) throw Error("Invitation changed. Reopen the operator's link.");
    if (!checked) show(result.invitation);
    live=result.prices; accepted=result.state==="consent_verified_not_payment_authority";
    paintPrices();
    if(accepted) {
      $("wallet-key").textContent=result.driver_identity;
      status("Budget consent is saved in Home Assistant. No payment or charger control is enabled.");
    } else if(initial) status("Review the operator, current prices and budget, then select Approve once. Your wallet may ask for permission.");
  } catch(e) {
    live=null;paintPrices();status(e.message || "The approval link is unavailable.",true);
  }
  controls();
}
function clear() {
  checked=null;receipt=null;accepted=false;live=null;
  $("terms").hidden=true;$("wallet-section").hidden=true;$("result").hidden=true;
  $("receipt").value="";$("wallet-key").textContent="Not connected";
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
  accepted=result.state==="consent_verified_not_payment_authority";
  if (!accepted || result.driver_identity!==receipt.driver_identity) throw Error("Consent was not accepted for this wallet.");
  status("Budget consent saved in Home Assistant. You can close this page. No funds moved.");
  $("result-note").textContent="Your signed receipt is verified and saved in HA. Keep a copy if you wish.";
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
    status("Waiting for BSV Browser. This action connects your identity and signs budget consent only.");
    const wallet=new WalletClient("auto");
    let timer;
    const identity=(await Promise.race([
      wallet.getPublicKey({identityKey:true}),
      new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error("Wallet connection timed out. Open this page inside BSV Browser.")),30000);})
    ]).finally(()=>clearTimeout(timer))).publicKey;
    if(!/^(02|03)[0-9a-f]{64}$/.test(identity))throw Error("Invalid wallet identity.");
    $("wallet-key").textContent=identity;
    receipt=await signConsent(wallet,checked,identity);
    $("receipt").value=JSON.stringify(receipt,null,2);$("result").hidden=false;
    if(capability)await submitReceipt();
    else status("Consent signed. Return the receipt to the operator for verification. No funds moved.");
  } catch(e) {
    status(receipt ? `Signed, but saving is not confirmed: ${e.message}. Select Retry saving; do not sign again.` :
      `Approval not completed: ${e.message || e}`,true);
  } finally {busy=false;controls();}
};
$("retry").onclick=async()=>{
  busy=true;controls();
  try{await submitReceipt();}catch(e){status(`Saving is not confirmed: ${e.message}. The existing signature is retained.`,true);}
  finally{busy=false;controls();}
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
setInterval(()=>{if(capability&&!framed&&!busy)refresh();},30000);
controls();
