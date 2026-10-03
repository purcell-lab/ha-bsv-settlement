export const stageLabels={
  check_quote:"Check the signed session account",wallet_identity:"Check driver wallet identity",
  wallet_network:"Check driver wallet network",sign_claim:"Sign the collection claim",
  claim_collection:"Reserve the collection attempt",create_draft:"Create the unsigned wallet draft",
  inspect_draft:"Validate the unsigned wallet draft",authorise_draft:"Request the one-use signing permit",
  recheck_wallet:"Recheck wallet before signing",sign_payment:"Sign the authorised transaction",
  inspect_signed:"Check the signed transaction",submit_payment:"Submit the exact signed transaction",
};
export const failureMessages={
  network_request_failed:"A network request failed; the response or wallet outcome may be unknown.",
  request_timeout:"A request timed out; the response or wallet outcome may be unknown.",
  wallet_rejected:"The wallet rejected or could not complete the requested operation.",
  validation_failed:"The quote, wallet draft or signing-permit checks did not pass.",
  unexpected_error:"Collection stopped unexpectedly; inspect the wallet before recovery.",
};
export function failureDiagnostic(stage,error){
  const message=String(error?.message||"");
  const code=/failed to fetch|network|load failed/i.test(message)?"network_request_failed":
    /timeout|timed out|abort/i.test(message)?"request_timeout":
    /denied|rejected|permission/i.test(message)?"wallet_rejected":
    /quote|draft|fee|recipient|funding|permit|signature|transaction|mainnet/i.test(message)?"validation_failed":"unexpected_error";
  // Do not send arbitrary wallet errors, URLs, signed bytes or secrets to HA.
  return {event_id:crypto.randomUUID(),stage:stageLabels[stage]?stage:"check_quote",code};
}
export function describeFailure(d){
  return d && stageLabels[d.stage] && failureMessages[d.code] ?
    `Collection paused at “${stageLabels[d.stage]}”. ${failureMessages[d.code]} No new payment will be attempted automatically.` : "";
}
