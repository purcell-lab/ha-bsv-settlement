// Explicit read-only diagnostic, not imported by the application entry point.
// Browser observations are NOT trusted server-side grant or ownership evidence.
async function observe(wallet, method, validate, timeoutMs) {
  let timer;
  try {
    if (typeof wallet?.[method] !== "function") return {state:"missing_method"};
    const value = await Promise.race([
      Promise.resolve().then(() => wallet[method]({})),
      new Promise((_, reject) => { timer=setTimeout(() => reject(Error("timeout")),timeoutMs); })
    ]);
    const result=validate(value);
    return result === undefined ? {state:"invalid_response"} : {state:"observed",value:result};
  } catch {
    // Never retain raw errors: wallets may include identifiers or tokens.
    return {state:"unavailable"};
  } finally { clearTimeout(timer); }
}

/** No unlock, identity key, signing, transaction, permission or receipt calls.
 * A timeout limits this probe; it cannot cancel an underlying wallet RPC.
 * This result cannot establish permission, identity, ownership or readiness.
 */
export async function probeWalletEnvironment(wallet,{origin,timeoutMs=1500}={}) {
  const url=new URL(origin);
  if(url.protocol!=="https:" || url.origin!==origin || url.username || url.password)
    throw Error("An exact HTTPS application origin is required");
  if(!Number.isInteger(timeoutMs) || timeoutMs<1 || timeoutMs>5000)
    throw Error("Invalid diagnostic timeout");
  const result={
    schema:"wallet-environment-observation-v1", origin,
    observed_at:new Date().toISOString(), evidence_class:"untrusted_browser_observation",
    authentication:{state:"not_checked"},version:{state:"not_checked"},network:{state:"not_checked"},
    monthly_grant:{state:"not_verified"}, receiving:{state:"not_verified"},
    automatic_collection_ready:false
  };
  result.authentication=await observe(wallet,"isAuthenticated",
    r => typeof r?.authenticated==="boolean" ? r.authenticated : undefined,timeoutMs);
  if(result.authentication.state!=="observed" || result.authentication.value!==true) return result;
  result.version=await observe(wallet,"getVersion",
    r => typeof r?.version==="string" && /^[\w .+/-]{1,80}$/.test(r.version) ? r.version : undefined,timeoutMs);
  result.network=await observe(wallet,"getNetwork",
    r => ["mainnet","testnet"].includes(r?.network) ? r.network : undefined,timeoutMs);
  return result;
}
