// Presentation for monthly authority. Text only: never grants, infers or retries.
export const missingText={
  monthly_disabled:"Monthly charging is not enabled at this station yet.",
  monthly_authority:"Monthly charging is not authorised for this wallet.",
  allowance_review:"Your monthly allowance is held for operator review. No new charge will start.",
  wallet_monthly_permission:"Your wallet's monthly spending permission is not verified. Each payment may ask in your wallet, or wait until you approve it.",
  receiving_registration:"No receiving wallet is registered for operator credits.",
  wallet_receiving:"This wallet connection cannot receive credits. Open the page in a BSV wallet that can.",
  receipts_paused:"Receiving your confirmed credits paused. Use Retry receiving credits in Wallet.",
  session_ownership_unavailable:"The operator must enable verified session matching.",
  session_accounting_unavailable:"The operator must enable final session accounting.",
  collection_service_unavailable:"Automatic collection is not available at this station. The operator must complete setup.",
  allowance_exhausted:"Your monthly application allowance is used or reserved. No new charge can start.",
  wallet_allowance_exhausted:"Your wallet's monthly allowance has no remaining capacity.",
  authority_changed:"Your authority changed while checking. Refresh its status.",
  wallet_connection:"Reconnect your wallet to make the signer available. History sign-in alone cannot collect payments.",
};

export function readinessView(status,{walletConnected=false}={}){
  if(!status)return {ready:false,title:"Checking monthly charging",items:[]};
  if(!status.enabled)return {ready:false,title:"Monthly charging unavailable",items:[missingText.monthly_disabled]};
  const missing=[...(status.readiness?.missing||[])];
  if(!walletConnected)missing.push("wallet_connection");
  const ready=status.readiness?.automatic_collection===true&&!missing.length;
  return {ready,title:ready?"Monthly charging ready":"Action needed before automatic payment",
    items:missing.map(code=>missingText[code]||"Monthly charging needs operator review.")};
}

export function setupAction(status,{connected=false}={}){
  if(status?.authority?.state==="cancelled")return {enabled:false,label:"Monthly charging cancelled"};
  if(!status?.authority)return {enabled:true,label:"Authorise monthly charging"};
  if(!connected)return {enabled:true,label:"Reconnect wallet"};
  const gaps=status.readiness?.missing||[];
  if(gaps.some(k=>["receiving_registration","wallet_monthly_permission","wallet_receiving","receipts_paused"].includes(k)))
    return {enabled:true,label:"Finish wallet setup"};
  return {enabled:false,label:"Monthly authority saved"};
}

const sat=v=>Number.isSafeInteger(v)?`${v.toLocaleString("en-AU")} sat`:"Unavailable";
const MONTHS=["January","February","March","April","May","June","July","August","September","October","November","December"];

/** Limit, Spent, Reserved and Remaining exactly as the server reports them. */
export function allowanceRows(allowance){
  if(!allowance)return [];
  const m=allowance.month,period=m?`${MONTHS[m.month-1]} ${m.year} (${m.timezone})`:"Unavailable";
  return [["Month",period],["Limit (fees included)",sat(allowance.limit_sats)],["Spent",sat(allowance.spent_sats)],
    ["Reserved",sat(allowance.reserved_sats)],["Remaining",sat(allowance.remaining_sats)],
    ...(allowance.blocked?[["Status","Held for review"]]:[])];
}

/** The public terms shown before the action; S3 checks the signed terms match them. */
export function termsList({stationIds=[],operatorIdentity=""}={}){
  const operator=/^(02|03)[0-9a-f]{64}$/.test(operatorIdentity)?
    `${operatorIdentity.slice(0,8)}…${operatorIdentity.slice(-6)}`:"This station's operator";
  return [
    ["Operator",operator],
    ["Stations",stationIds.length?stationIds.join(", "):"Shown when monthly charging is enabled"],
    ["Monthly limit","30,000 sat each calendar month, including network fees you pay"],
    ["Recurrence","Renews each month until you cancel"],
    ["Charging","One final net payment per session, after it ends. No monthly bill."],
    ["Credits","Operator credits are paid to you separately and do not add to your limit. Unused limit does not carry over."],
    ["Cancellation","Cancel any time here. Stops new charges; payments already made or submitted are unchanged."],
  ];
}

/** Compare the exact signed terms with what was shown. Returns the rows to confirm. */
export function signedTermsRows(terms){
  return [["Stations",terms.station_ids.join(", ")],["Monthly limit",sat(terms.monthly_limit_sats)+" including fees"],
    ["Wallet month",`${terms.period_policy.timezone} calendar month (${terms.period_policy.wallet_version})`],
    ["Collection","One final net payment per session, after it ends"],
    ["Request expires",new Date(terms.accept_before).toLocaleTimeString([],{hour:"2-digit",minute:"2-digit"})]];
}

export function authorityText(status){
  const a=status?.authority;
  if(!status?.enabled)return "Unavailable";
  if(!a)return "Not authorised";
  if(a.state==="cancelled")return "Cancelled · wallet permission revocation not verified";
  return `Active since ${new Date(a.accepted_at).toLocaleDateString("en-AU",{day:"numeric",month:"short",year:"numeric"})}`;
}

export function grantText(status){
  return ({verified:"Verified for this month",unverified:"Not verified",not_applicable:"Not applicable"})
    [status?.native_grant?.state]||"Not checked";
}
