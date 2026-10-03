import {pendingReplacement} from "./invitation-form.js";

// A fresh create response is public-shaped. Read admin metadata separately;
// never create a second invitation when this read fails.
export async function registrationOpenRequest(created, readStatus) {
  const id=created?.terms?.budget_id;
  if(!id)throw Error("No invitation ID returned. Check approval before trying again.");
  const current=await readStatus({budget_id:id});
  if(current?.terms?.budget_id!==id)
    throw Error("Could not verify this invitation. Check approval before trying again.");
  if(current.terms.version!==3||current.terms.session_mode!=="multi_session"||current.binding)
    throw Error("Public registration requires an unbound multi-session invitation.");
  const hash=current.public_registration?.context_hash;
  if(typeof hash!=="string"||!hash)
    throw Error("Registration context is unavailable. Check approval before trying again.");
  const pending=await pendingReplacement(current);
  return {budget_id:id,expected_invitation_hash:pending.expected_invitation_hash,
    expected_context_hash:hash,confirm_public_registration:true};
}
