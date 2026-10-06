// Staged native acceptance helper. NOT imported by entry.js or a shipped page.
// The caller supplies an already selected wallet; no discovery or connection.
import {probeWalletEnvironment} from "./wallet-evidence.js";
import {inspectOutgoingAction} from "./outgoing-evidence.js";

const busy = new WeakSet();
const methods = ["isAuthenticated", "getVersion", "getNetwork", "getPublicKey", "listActions"];
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const blank = reason => ({
  schema: "native-wallet-acceptance-observation-v1",
  evidence_class: "untrusted_browser_observation",
  reason, native_acceptance: "not_established", status_repaired: false,
  retry_authorised: false, receipt_acceptance: "not_assessed",
  automatic_collection_ready: false, calls: [],
});

/**
 * One explicit, approved read-only observation. Does not perform acceptance
 * of receipts or request native permissions. A timed-out RPC cannot be
 * cancelled: this wallet object stays locked until its outstanding RPC ends.
 * No production entry point, scheduler, uploader or persistence is added.
 */
export async function captureNativeObservation(wallet, options = {}) {
  const {origin, targetAlias, readOnlyApproved, timeoutMs = 1500} = options;
  // Copy the exact target before any await, preventing caller-side reassignment.
  const target = {...options.target};
  let url;
  try { url = new URL(origin); } catch { return blank("invalid_scope"); }
  if (url.protocol !== "https:" || url.origin !== origin || url.username || url.password ||
      !/^[a-z0-9][a-z0-9-]{0,39}$/.test(targetAlias || "") ||
      !/^0[23][0-9a-f]{64}$/.test(target.identity || "") ||
      !uuid.test(target.budgetId || "") || !/^[0-9a-f]{64}$/.test(target.txid || "") ||
      !Number.isInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 4000)
    return blank("invalid_scope");
  if (readOnlyApproved !== true) return blank("read_only_scope_not_approved");
  if (!wallet || !["object", "function"].includes(typeof wallet))
    return blank("wallet_unavailable");
  if (busy.has(wallet)) return blank("inspection_in_progress");
  busy.add(wallet);
  const result = {...blank("not_observed"), origin, target_alias: targetAlias,
    observed_at: new Date().toISOString()};
  let finished = false, pending = 0, timedOut = false;
  const release = () => { if (finished && pending === 0) busy.delete(wallet); };
  const view = {};
  try {
    for (const method of methods) {
      // Capability proxies can throw while reading a property.
      const fn = wallet[method];
      if (typeof fn !== "function") continue;
      view[method] = async args => {
        if (timedOut) throw Error("inspection_stopped");
        result.calls.push(method);
        pending++;
        let timer;
        const operation = Promise.resolve().then(() => fn.call(wallet, args));
        // A handler for both outcomes avoids unhandled late rejections.
        operation.then(() => { pending--; release(); }, () => { pending--; release(); });
        try {
          return await Promise.race([operation, new Promise((_, reject) => {
            timer = setTimeout(() => { timedOut = true; reject(Error("timeout")); }, timeoutMs);
          })]);
        } finally { clearTimeout(timer); }
      };
    }
    // This outer probe's deadline is longer so our wrapper owns timeout/latching.
    result.environment = await probeWalletEnvironment(view, {origin, timeoutMs: 5000});
    const env = result.environment;
    if (timedOut) result.reason = "rpc_timeout_no_retry";
    else if (env.authentication.value !== true || env.version.state !== "observed" ||
             env.network.value !== "mainnet") result.reason = "environment_not_verified";
    else {
      const outgoing = await inspectOutgoingAction(view, target);
      // Private target IDs and extra wallet metadata must not leave this helper.
      const {txid: _privateTxid, ...safe} = outgoing;
      result.outgoing = safe;
      result.reason = timedOut ? "rpc_timeout_no_retry" :
        outgoing.evidence === "wallet_reported" ? "observation_only" : outgoing.reason;
    }
    result.outstanding_rpc = pending > 0;
    return result;
  } catch {
    return {...result, reason: "inspection_unavailable", outstanding_rpc: pending > 0};
  } finally {
    finished = true;
    release();
  }
}
