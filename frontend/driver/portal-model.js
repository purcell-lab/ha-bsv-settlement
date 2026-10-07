import {KeyDeriver,Signature} from "@bsv/sdk";
import {bytes,hex,canonical} from "./model.js";
import {provisionalSats} from "../ui.js";
export const loginProtocol=[2,"ev portal login"];
export const loginScope="read_own_sessions_sync_receipts_and_collect_signed_session_budgets";
export function compactIdentity(identity){
  return typeof identity==="string"&&/^(02|03)[0-9a-f]{64}$/.test(identity)?
    `${identity.slice(0,6)}…${identity.slice(-4)}`:"";
}
export async function signPortalLogin(wallet,challenge,origin,now=Date.now()){
  const p=JSON.parse(challenge.payload);
  if(Object.keys(p).sort().join(",")!=="action,browser_binding,expires_at,issued_at,nonce,origin,scope,version"||
    p.action!=="sign_in_driver_portal"||p.version!==1||p.origin!==origin||p.scope!==loginScope||
    !/^[A-Za-z0-9_-]{43}$/.test(p.nonce)||!/^[0-9a-f]{64}$/.test(p.browser_binding)||
    !Number.isSafeInteger(p.issued_at)||!Number.isSafeInteger(p.expires_at)||
    p.issued_at*1000>now+30000||p.expires_at*1000<=now||p.expires_at<=p.issued_at||p.expires_at-p.issued_at>120||
    canonical(challenge.protocolID)!==canonical(loginProtocol)||challenge.keyID!==p.nonce||
    canonical(p)!==challenge.payload)throw Error("Invalid portal sign-in challenge.");
  const identity=(await wallet.getPublicKey({identityKey:true})).publicKey;
  if(!/^(02|03)[0-9a-f]{64}$/.test(identity))throw Error("Invalid wallet identity.");
  const {signature}=await wallet.createSignature({protocolID:loginProtocol,keyID:p.nonce,counterparty:"anyone",
    data:bytes(challenge.payload),description:"Sign in for automatic per-session charging payments under your separately signed budget, private history and existing credit receipts. This signature does not create or increase a spending budget."});
  const key=new KeyDeriver("anyone").derivePublicKey(loginProtocol,p.nonce,identity);
  if(!key.verify(bytes(challenge.payload),Signature.fromDER(signature)))throw Error("Wallet login signature did not verify.");
  return {identity,payload:challenge.payload,signature:hex(signature)};
}
export function averageNet(session){
  const values=[session.import_kwh,session.export_kwh,session.net_amount_aud];
  if(values.some(v=>v===null||v===undefined||v===""||!Number.isFinite(Number(v))))return null;
  const [i,e,net]=values.map(Number);
  const result=i>=0&&e>=0&&i+e>0?net/(i+e):NaN;
  return Number.isFinite(result)?result:null;
}
export function averageRate(amount,kwh){
  if([amount,kwh].some(v=>v===null||v===undefined||v===""||!Number.isFinite(Number(v)))||Number(kwh)<=0)return null;
  const result=Number(amount)/Number(kwh);
  return Number.isFinite(result)?result:null;
}
export function transactionStatus(t){
  if(t.state==="provider_unconfirmed"&&t.direction==="operator_to_driver"&&
    t.wallet_receipt_status==="wallet_reported_accepted")
    return "Wallet received credit · awaiting block confirmation";
  if(t.state==="provider_confirmed")return t.direction==="operator_to_driver"?
    t.wallet_receipt_status==="wallet_reported_accepted"?"Confirmed · wallet acceptance recorded":"Confirmed · receipt sync needed":
    "Payment confirmed";
  return ({provider_unconfirmed:"Awaiting block confirmation",submitted:"Submitted · awaiting confirmation",
    awaiting_driver_payment:"Awaiting driver payment",
    ready:"Awaiting wallet approval",submission_authorised:"Wallet signing in progress",
    broadcast_unknown:"Submission uncertain · do not retry payment",waived:"Waived",
    wallet_attempt_reserved:"Held for review",waiting_for_session_end:"Session in progress",
    no_operator_credit:"No operator credit due",
    monthly_reserved:"Session amount reserved within allowance. No payment confirmed.",
    reservation_released:"Unused reservation released. Not a payment.",
    wallet_spend_recorded:"Wallet spending recorded. Provider confirmation is not recorded."})[t.state]||String(t.state||"Review required").replaceAll("_"," ");
}
export function provisionalSession(s,now=Date.now()){
  // A provisional account is not a payment. Never replace a submitted/final
  // transaction, or manufacture a number from missing pricing or a stale meter.
  if(s.ended_at!==null||s.closure||(s.transactions||[]).some(t=>
    t.txid||Number.isSafeInteger(t.amount_sats)||!["waiting_for_session_end","automatic_credit_pending"].includes(t.state)))return null;
  const checked=Date.parse(s.meter_updated_at),net=s.net_amount_aud;
  const fresh=Number.isFinite(checked)&&now-checked>=-5000&&now-checked<=120000;
  const valid=fresh&&
    ["number","string"].includes(typeof net)&&String(net).trim()!==""&&Number.isFinite(Number(net));
  const sats=valid?provisionalSats({net_cost_aud:net},{},{state:s.satoshis_per_aud}).sats:null;
  if(sats===null)return {payment:"Provisional amount unavailable",amount:"Provisional amount unavailable",
    note:!fresh?"Waiting for a fresh meter update. No payment requested.":
      "Waiting for valid session pricing and its conversion rate. No payment requested."};
  const direction=Number(net)<0?"credit":Number(net)>0?"charge":"balance";
  return {payment:`Provisional ${direction} ${sats} sat`,
    amount:`Provisional ${direction}${direction==="balance"?"":" to you"}: ${sats} sat`,
    note:`AUD ${Math.abs(Number(net)).toFixed(2)} · ${s.satoshis_per_aud} sat/AUD (session rate). Network fees excluded. Amount can change until the session closes. Not a payment request.`};
}
export function sessionSummary(s,now=Date.now()){
  const provisional=provisionalSession(s,now);
  if(provisional)return {payment:provisional.payment,status:"Active",warning:!!s.quality_flags?.length};
  const rows=s.transactions||[];
  let payment="No payment",status=s.ended_at?"Recorded":"Active";
  if(s.closure?.state?.includes("waiv"))status="Waived";
  if(rows.length>1){payment=`${rows.length} payments`;status="See details";}
  if(rows.length===1){
    const t=rows[0],amount=Number.isSafeInteger(t.amount_sats)?`${t.amount_sats} sat`:"amount unknown";
    payment=`${t.direction==="operator_to_driver"?"Credit":"Pay"} ${amount}`;
    status=t.state==="provider_confirmed"?
      t.direction==="operator_to_driver"?
        (t.wallet_receipt_status==="wallet_reported_accepted"&&Number.isFinite(Date.parse(t.wallet_imported_at))?"Received":"Receipt due"):
        "Confirmed":
      ({provider_unconfirmed:t.direction==="operator_to_driver"&&t.wallet_receipt_status==="wallet_reported_accepted"?
          "Received · unconfirmed":"Confirming",submitted:"Submitted",broadcast_unknown:"Review",
        awaiting_driver_payment:"Awaiting payment",ready:"Wallet approval",submission_authorised:"Signing",
        wallet_attempt_reserved:"Held",waived:"Waived",waiting_for_session_end:"Active",
        no_operator_credit:"No credit"})[t.state]||"Review";
  }
  return {payment,status,warning:!!s.quality_flags?.some(f=>f!=="manual_energy_adjustment")};
}
