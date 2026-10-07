// One user-started journey; identity proof is never treated as spending consent.
import {parseInvitation,signConsent} from "./model.js";
import {registerCredit} from "./credit.js";
import {privateSessionUrl} from "./private-link.js";

export function authorisationRows({identity,connected,paused,collectionEnabled=false,approvals=[],supported=[]}) {
  const active=approvals.filter(a=>a.spending_active===true);
  const paymentApi=supported.includes("createAction")&&supported.includes("signAction");
  const ready=!!identity&&connected&&active.length>0&&paymentApi&&collectionEnabled;
  return [
    {label:"Wallet identity",ok:!!identity,text:identity?"Verified for private history":"Sign in required"},
    {label:"Wallet connection",ok:connected,text:connected?"Connected on BSV mainnet":"Not verified in this page"},
    {label:"Charging budget",ok:active.length>0,text:active.length?
      active.map(a=>`${a.scope==="weekly"?"Weekly":"Session"}: ${a.limit_sats.toLocaleString()} sat total · expires ${new Date(a.expires_at).toLocaleString()}`).join("; "):
      "No current signed spending approval"},
    {label:"Receive credits",ok:approvals.some(a=>a.receiving_registered),text:
      approvals.some(a=>a.receiving_registered)?"Receiving destination registered":"Receiving registration not verified"},
    {label:"Credit receipts",ok:connected&&!paused,text:paused?"Paused; use Retry receiving credits":
      connected?"Automatic receipt checks enabled; individual acceptance is in History":"Connect to check existing receipts"},
    {label:"Session payments",ok:ready,text:ready?
      "Automatic collection enabled for covered sessions. Wallet approval may still be required.":
      !active.length?"No current signed budget covers new payments":
      !connected?"Connect your wallet to collect covered sessions":
      !paymentApi?"This connection does not provide the payment APIs":
      "Budget approved; automatic collection is paused"},
  ];
}

export async function approvePublicBudget({candidate,identity,checked,read,approve,driverApi,origin,assertActive,onSigned=()=>{}}) {
  assertActive();
  const fresh=await read();
  assertActive();
  if(fresh.invitation?.payload!==checked.invitation.payload||fresh.state!=="awaiting_driver_consent")
    throw Error("The invitation changed. Review the fresh terms before signing.");
  // Require both fresh tariffs, not simply a stale server `valid` bit.
  const current=Date.now();
  if(!fresh.prices?.valid||!["import","export"].every(k=>{
    const p=fresh.prices[k];
    return p?.available===true&&Date.parse(p.start)<=current&&Date.parse(p.end)+90000>=current;
  }))
    throw Error("Current buy and sell rates are unavailable. No budget was signed.");
  const verified=parseInvitation(JSON.stringify(fresh.invitation));
  if((await candidate.getPublicKey({identityKey:true})).publicKey!==identity)
    throw Error("Wallet identity changed. Sign in again.");
  assertActive();
  const receipt=await signConsent(candidate,verified,identity);
  onSigned();
  assertActive();
  // Never retry a signature or approval submission automatically after ambiguity.
  let result;
  try{result=await approve(receipt);}
  catch{throw Error("Budget signature created, but saving is not confirmed. Ask the operator to check it before trying again.");}
  if(result.driver_identity!==identity||result.state!=="spending_authorised_wallet_permission_required")
    throw Error("Budget saving was not confirmed for this wallet. Do not sign again.");
  const url=privateSessionUrl(origin+"/bsv_settlement/driver/index.html"+(result.private_link_fragment||""),origin);
  if(!url)throw Error("Budget saved, but the private charging link is unavailable. Ask the operator to recover it; do not sign again.");
  const p=new URLSearchParams(new URL(url).hash.slice(1));
  const capability={budget_id:p.get("budget"),token:p.get("token")};
  let receivingError=null;
  if(result.automatic_credit_enabled&&!result.credit_destination_registered){
    try{
      assertActive();
      await registerCredit(candidate,verified,identity,(action,data)=>driverApi(action,{...capability,...data}));
    }catch{receivingError="Budget saved. Receiving registration still needs completion on your charging page.";}
  }
  return {url,receivingError};
}
