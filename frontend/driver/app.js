import { WalletClient } from "@bsv/sdk";
import { parseInvitation, signConsent } from "./model.js";

const $ = id => document.getElementById(id);
let checked = null, wallet = null, identity = null, receipt = null, busy = false;
const status = (message, error = false) => {
  $("status").textContent = message;
  $("status").className = error ? "notice error" : "notice";
};
function controls() {
  const expired = checked && Date.parse(checked.terms.expires_at) <= Date.now();
  $("connect").disabled = busy || !checked || expired || !!wallet;
  $("approve").disabled = busy || !wallet || !checked || !!receipt || expired ||
    !$("trusted").checked || !$("consent").checked;
  $("load").disabled = busy;
  $("invitation").disabled = busy;
  $("reset").disabled = busy;
  $("download").disabled = !receipt || busy;
  if (expired && !receipt) status("This invitation has expired. Ask the operator for a new one.", true);
}
function clearApproval() {
  checked = null; wallet = null; identity = null; receipt = null;
  $("terms").hidden = true; $("result").hidden = true;
  $("wallet-section").hidden = true;
  $("trusted").checked = false; $("consent").checked = false;
  $("wallet-key").textContent = "Not connected";
  $("receipt").value = "";
  controls();
}
$("load").onclick = () => {
  clearApproval();
  try {
    checked = parseInvitation($("invitation").value);
    const t = checked.terms;
    $("budget").textContent = `${t.max_total_sats.toLocaleString()} sat`;
    $("fee").textContent = `${t.max_fee_sats.toLocaleString()} sat maximum, included in total`;
    $("rate").textContent = `${t.satoshis_per_aud} sat / AUD, fixed for this consent`;
    $("session").textContent = t.session_id;
    $("transaction").textContent = t.transaction_id;
    $("operator").textContent = t.operator_address;
    $("operator-key").textContent = t.operator_identity;
    $("expiry").textContent = new Date(t.expires_at).toLocaleString();
    $("pricing").textContent = t.pricing_rule;
    $("tariffs").textContent = `Import tariff: ${t.import_price_entity}. Export tariff: ${t.export_price_entity}.`;
    $("scope").textContent = t.account_scope;
    $("terms").hidden = false;
    $("wallet-section").hidden = false;
    status("Operator signature checked. Confirm the operator address through a trusted channel.");
  } catch (e) { status(e.message || "Cannot read this invitation.", true); }
  controls();
};
$("invitation").addEventListener("input", () => {
  clearApproval(); status("Invitation changed. Load and review it again.");
});
$("connect").onclick = async () => {
  busy = true; controls(); status("Waiting for BSV Browser. Approve access to your public identity.");
  try {
    // This is the same auto transport used by the todriguez.com/cfb demonstration.
    const candidate = new WalletClient("auto");
    let timer;
    const response = await Promise.race([
      candidate.getPublicKey({ identityKey: true }),
      new Promise((_, reject) => { timer = setTimeout(() => reject(Error("Wallet connection timed out.")), 30000); })
    ]).finally(() => clearTimeout(timer));
    const key = response.publicKey;
    if (!/^(02|03)[0-9a-f]{64}$/.test(key)) throw Error("Wallet returned an invalid public identity.");
    parseInvitation(JSON.stringify(checked.invitation));
    wallet = candidate; identity = key;
    $("wallet-key").textContent = key;
    status("Wallet connected. Review the terms before signing your consent.");
  } catch (e) {
    wallet = null; identity = null;
    status(`Connection failed. Open this page inside BSV Browser, or use BSV Desktop. ${String(e.message || "").slice(0, 180)}`, true);
  } finally { busy = false; controls(); }
};
$("approve").onclick = async () => {
  if ($("approve").disabled) return;
  busy = true; controls(); status("Check the signature request in your wallet. No transaction will be created.");
  try {
    receipt = await signConsent(wallet, checked, identity);
    $("receipt").value = JSON.stringify(receipt, null, 2);
    $("result").hidden = false;
    status("Budget consent signed. Return the receipt to the operator for HA verification. No funds moved.");
    $("result").scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (e) { status(`Consent was not completed: ${String(e.message || e).slice(0, 200)}`, true); }
  finally { busy = false; controls(); }
};
$("trusted").onchange = controls; $("consent").onchange = controls;
$("reset").onclick = () => {
  clearApproval(); $("invitation").value = "";
  status("Page cleared. This does not revoke a receipt already returned to HA. Ask the operator to revoke it.");
};
$("download").onclick = () => {
  if (!receipt) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(receipt, null, 2)], {type:"application/json"}));
  const a = document.createElement("a");
  a.href = url; a.download = `session-budget-${receipt.budget_id}.json`; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};
setInterval(controls, 1000);
controls();
