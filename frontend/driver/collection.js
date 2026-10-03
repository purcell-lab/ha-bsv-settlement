import { Transaction, P2PKH, PublicKey, Signature } from "@bsv/sdk";
import { canonical, bytes, hash, hex, parseInvitation, spendingProtocol } from "./model.js";
import { failureDiagnostic } from "./diagnostics.js";

// Exact decimal arithmetic. Reject unsupported magnitudes instead of using float money.
function decimalParts(value) {
  const m=String(value).match(/^(-?)(\d+)(?:\.(\d+))?(?:[eE]([+-]?\d+))?$/);
  if(!m || String(value).length>80)throw Error("Invalid account decimal.");
  const scale=(m[3]||"").length-Number(m[4]||0);
  if(Math.abs(scale)>30)throw Error("Account decimal scale exceeds limits.");
  let n=BigInt(m[2]+(m[3]||""))*(m[1] ? -1n : 1n);
  return scale<0 ? [n*10n**BigInt(-scale),1n] : [n,10n**BigInt(scale)];
}
const roundPositive=(n,d)=>(n+d/2n)/d;
export async function checkQuote(envelope,checked,binding) {
  parseInvitation(JSON.stringify(checked.invitation));
  const t=checked.terms;
  if(t.version!==2 || !envelope || typeof envelope.payload!=="string" || envelope.payload.length>12000)
    throw Error("Invalid collection quote.");
  const q=JSON.parse(envelope.payload);
  if(await hash(envelope.payload)!==envelope.hash ||
      !PublicKey.fromString(t.operator_identity).verify(bytes(envelope.payload),Signature.fromDER(envelope.signature,"hex")))
    throw Error("The collection quote signature did not verify.");
  const sid=t.session_mode==="next_session_reservation" ? binding?.session_id : t.session_id;
  if(q.version!==1 || q.network!=="BSV mainnet" || q.budget_id!==t.budget_id ||
      q.invitation_hash!==await hash(checked.invitation.payload) ||
      q.recipient_address!==t.operator_address || q.operator_identity!==t.operator_identity ||
      q.max_total_sats!==t.max_total_sats || q.max_fee_sats!==t.max_fee_sats ||
      q.satoshis_per_aud!==t.satoshis_per_aud || q.expires_at!==t.expires_at ||
      !sid || q.account?.session_id!==sid || !q.account.ended_at ||
      Date.parse(q.account.ended_at)>Date.now() || !Number.isFinite(Date.parse(q.account.ended_at)) ||
      (binding && q.account.ocpp_transaction_id!==binding.transaction_id) ||
      !Number.isSafeInteger(q.amount_sats) || q.amount_sats<1 ||
      q.amount_sats+q.max_fee_sats>q.max_total_sats)
    throw Error("The quote does not match the approved session and limits.");
  const [net,nd]=decimalParts(q.account.net_cost_aud_unrounded);
  const [aud,ad]=decimalParts(q.account.net_amount_aud);
  const [rate,rd]=decimalParts(q.satoshis_per_aud);
  if(net<=0n || aud<=0n || rate<=0n || roundPositive(net*100n,nd)*ad!==aud*100n ||
      roundPositive(aud*rate,ad*rd)!==BigInt(q.amount_sats))
    throw Error("The quote amount does not match the fixed-rate session account.");
  return q;
}

export const shape = tx => ({
  version:tx.version,locktime:tx.lockTime,
  inputs:tx.inputs.map(i=>({txid:i.sourceTXID || i.sourceTransaction?.id("hex"),
    index:i.sourceOutputIndex,sequence:i.sequence ?? 0xffffffff})),
  outputs:tx.outputs.map(o=>({script:o.lockingScript.toHex(),satoshis:o.satoshis}))
});

export function inspectDraft(beef,q) {
  if(!Array.isArray(beef) || beef.length>4000000)throw Error("Wallet must return a bounded, complete Atomic BEEF transaction.");
  const tx=Transaction.fromAtomicBEEF(beef);
  const draft=shape(tx);
  if(draft.version!==1 || draft.locktime!==0 || !draft.inputs.length || draft.inputs.length>4 ||
      !draft.outputs.length || draft.outputs.length>2 ||
      new Set(draft.inputs.map(i=>i.txid+":"+i.index)).size!==draft.inputs.length)
    throw Error("Wallet transaction is outside demonstration limits.");
  for(const i of tx.inputs) {
    if(!i.sourceTransaction || (i.sourceTXID && i.sourceTransaction.id("hex")!==i.sourceTXID) ||
        !i.sourceTransaction.outputs[i.sourceOutputIndex] || (i.sequence ?? 0xffffffff)!==0xffffffff)
      throw Error("Complete funding evidence is required before signing.");
  }
  const script=new P2PKH().lock(q.recipient_address).toHex();
  const matches=draft.outputs.filter(o=>o.script===script);
  if(matches.length!==1 || matches[0].satoshis!==q.amount_sats ||
      draft.outputs.some(o=>!/^76a914[0-9a-f]{40}88ac$/.test(o.script) ||
        !Number.isSafeInteger(o.satoshis) || o.satoshis<=0))
    throw Error("The wallet draft does not match the exact operator payment.");
  const fee=tx.getFee();
  if(!Number.isSafeInteger(fee) || fee<0 || fee>q.max_fee_sats || q.amount_sats+fee>q.max_total_sats)
    throw Error("The wallet fee or total debit exceeds your signed limit.");
  return {tx,draft,fee};
}

export async function collectOnce(wallet,checked,binding,envelope,api,notify=()=>{},recoveryConfirmed=false) {
  let stage="check_quote",token=null;
  try {
  const q=await checkQuote(envelope,checked,binding);
  stage="wallet_identity";
  const identity=(await wallet.getPublicKey({identityKey:true})).publicKey;
  if(identity!==q.driver_identity)throw Error("Connect the approved driver's wallet.");
  stage="wallet_network";
  if((await wallet.getNetwork()).network!=="mainnet")
    throw Error("Connect the approved driver's mainnet wallet.");
  token=btoa(String.fromCharCode(...crypto.getRandomValues(new Uint8Array(32))))
    .replaceAll("+","-").replaceAll("/","_").replaceAll("=","");
  const payload=canonical({version:1,action:"claim_session_collection",budget_id:q.budget_id,
    quote_hash:envelope.hash,attempt_token_hash:await hash(token),driver_identity:identity});
  stage="sign_claim";
  const proof=await wallet.createSignature({protocolID:spendingProtocol,keyID:q.budget_id,
    counterparty:"anyone",data:bytes(payload),description:"Collect the approved EV session payment"});
  parseInvitation(JSON.stringify(checked.invitation));
  stage="claim_collection";
  const claim=await api("claim_collection",{attempt_token:token,proof:{payload,signature:hex(proof.signature)},
    confirm_recovered_attempt:recoveryConfirmed});
  if(claim.claimed!==true)throw Error("Collection attempt was not reserved. Do not retry payment.");
  notify("Preparing the session payment. Keep this page open.");
  // Never let the wallet sign or broadcast before the fee/recipient checks.
  stage="create_draft";
  const created=await wallet.createAction({
    description:"EV charging session settlement",
    outputs:[{lockingScript:new P2PKH().lock(q.recipient_address).toHex(),
      satoshis:q.amount_sats,outputDescription:"Final EV charging session account"}],
    labels:["ev-session:"+q.budget_id],version:1,lockTime:0,
    options:{signAndProcess:false,noSend:true,acceptDelayedBroadcast:false,
      returnTXIDOnly:false,randomizeOutputs:false}
  });
  stage="inspect_draft";
  if(!created.signableTransaction || created.txid || created.tx)
    throw Error("Wallet did not return an unsigned draft. Operator reconciliation is required.");
  const {draft,fee}=inspectDraft(created.signableTransaction.tx,q);
  stage="authorise_draft";
  const permit=await api("authorise_collection",{attempt_token:token,draft});
  if(permit.submit_once!==true || permit.draft_hash!==await hash(canonical(draft)) || permit.fee_sats!==fee)
    throw Error("The one-use signing permit does not match the wallet draft.");
  parseInvitation(JSON.stringify(checked.invitation));
  stage="recheck_wallet";
  if((await wallet.getPublicKey({identityKey:true})).publicKey!==identity ||
      (await wallet.getNetwork()).network!=="mainnet")throw Error("Wallet identity or network changed.");
  notify("Wallet signing may require permission. No second payment attempt will be made.");
  // noSend ensures the server can recheck revocation and expiry after wallet prompts.
  stage="sign_payment";
  const signed=await wallet.signAction({reference:created.signableTransaction.reference,spends:{},
    options:{noSend:true,acceptDelayedBroadcast:false,returnTXIDOnly:false}});
  stage="inspect_signed";
  const result=inspectDraft(signed.tx,q);
  if(canonical(shape(result.tx))!==canonical(draft))throw Error("Signed transaction differs from the reviewed draft.");
  const raw=result.tx.toHex();
  if(raw.length>16000)throw Error("Signed transaction exceeds the endpoint limit.");
  const report={attempt_token:token,raw_tx:raw};
  try {
    stage="submit_payment";
    notify("Submitting the approved payment once. Waiting for chain evidence.");
    return await api("report_collection",report);
  } catch(e) {
    e.pendingReport=report; // Memory only. Resending exact bytes cannot create a second payment.
    throw e;
  }
  } catch(e) {
    e.diagnostic=failureDiagnostic(stage,e);
    if(token){
      const report={attempt_token:token,diagnostic:e.diagnostic};
      try{await api("report_collection_failure",report);}
      catch{e.pendingDiagnostic=report;} // Retry only metadata when connectivity returns.
    }
    throw e;
  }
}
