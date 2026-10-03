export const invitationDefaults={total:1000,fee:1000,minutes:720};
export function validateInvitationLimits({total,fee,minutes}){
  for(const [value,min,max] of [[total,1,100000],[fee,0,1000],[minutes,1,1440]]){
    if(value===""||!Number.isInteger(Number(value))||Number(value)<min||Number(value)>max)
      return "Enter whole-number limits within the displayed ranges.";
  }
  if(Number(fee)>Number(total))return "The fee cap cannot exceed the total spending limit.";
  return "";
}
export function sameInvitationScope(budget,scope,session){
  if(!budget||budget.binding||["revoked","expired"].includes(budget.state))return false;
  return scope==="current" ? budget.terms.session_mode==="existing_session"&&
    budget.terms.session_id===session?.session_id : budget.terms.session_mode==="next_session_reservation";
}
export async function pendingReplacement(budget){
  if(budget?.state!=="awaiting_driver_consent"||budget.receipt||budget.collection||budget.automatic_credit?.txid)
    throw Error("Only an unapproved invitation without a settlement can be replaced.");
  const bytes=new TextEncoder().encode(budget.invitation.payload);
  const digest=await crypto.subtle.digest("SHA-256",bytes);
  return {replace_pending_budget_id:budget.terms.budget_id,
    expected_invitation_hash:Array.from(new Uint8Array(digest),b=>b.toString(16).padStart(2,"0")).join(""),
    confirm_replace_pending:true};
}
