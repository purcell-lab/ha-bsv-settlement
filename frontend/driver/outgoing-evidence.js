// Read-only capability foundation, deliberately not wired into automatic UI.
// Provider confirmation, wallet-local status and incoming receipt acceptance
// are independent. This adapter NEVER repairs, broadcasts or creates actions.
const statuses=new Set(["completed","unprocessed","sending","unproven","unsigned","nosend","nonfinal","failed"]);
const base=()=>({wallet_status:"unknown",status_repaired:false,retry_authorised:false,
  provider_checked:false,receipt_acceptance:"not_assessed"});
const unknown=reason=>({...base(),reason});
const valid=s=>typeof s==="string";

export async function inspectOutgoingAction(wallet,{identity,budgetId,txid}={}){
  if(!valid(identity)||!/^0[23][0-9a-f]{64}$/.test(identity)||
      !valid(budgetId)||!/^[0-9a-f-]{36}$/.test(budgetId)||
      !valid(txid)||!/^[0-9a-f]{64}$/.test(txid))return unknown("invalid_target");
  if(!wallet||["isAuthenticated","getPublicKey","getNetwork","listActions"].some(
    method=>typeof wallet[method]!=="function"))return unknown("inspection_unsupported");
  const label="ev-session:"+budgetId;
  const matchesWallet=async()=>{
    if((await wallet.isAuthenticated({})).authenticated!==true)return false;
    const key=await wallet.getPublicKey({identityKey:true,seekPermission:false});
    const network=await wallet.getNetwork({});
    return key.publicKey===identity&&network.network==="mainnet";
  };
  try{
    if(!await matchesWallet())return unknown("wallet_identity_or_network_mismatch");
    // No permission acquisition, scripts, raw transactions or global history.
    const response=await wallet.listActions({labels:[label],labelQueryMode:"all",
      includeLabels:true,includeInputs:false,includeOutputs:false,
      includeInputSourceLockingScripts:false,includeInputUnlockingScripts:false,
      includeOutputLockingScripts:false,limit:50,offset:0,seekPermission:false});
    if(!await matchesWallet())return unknown("wallet_changed_during_inspection");
    if(!response||!Array.isArray(response.actions)||response.actions.length>50||
        !Number.isSafeInteger(response.totalActions)||response.totalActions<response.actions.length)
      return unknown("invalid_wallet_response");
    const rows=response.actions.filter(r=>r?.txid===txid);
    if(rows.length!==1)return unknown(rows.length?"ambiguous_wallet_action":"not_observed_in_bounded_query");
    const row=rows[0];
    if(row.isOutgoing!==true||!Array.isArray(row.labels)||!row.labels.includes(label)||
        !statuses.has(row.status))return unknown("wallet_action_mismatch");
    return {...base(),txid,wallet_status:row.status,evidence:"wallet_reported",
      reason:row.status==="nosend"?"wallet_status_repair_not_available":"observed_only"};
  }catch{
    // Wallet errors may contain private metadata; never return the raw error.
    return unknown("wallet_inspection_unavailable");
  }
}
