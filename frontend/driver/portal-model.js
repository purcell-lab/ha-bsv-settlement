import {KeyDeriver,Signature} from "@bsv/sdk";
import {bytes,hex,canonical} from "./model.js";
export const loginProtocol=[2,"ev portal login"];
export const loginScope="read_own_charging_sessions_and_sync_existing_credit_receipts";
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
    data:bytes(challenge.payload),description:"Sign in to view your EV sessions and sync existing credit receipts. No spending approval or payment."});
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
  if(t.state==="provider_confirmed")return t.direction==="operator_to_driver"?
    t.wallet_receipt_status==="wallet_reported_accepted"?"Confirmed · wallet acceptance recorded":"Confirmed · receipt sync needed":
    "Payment confirmed";
  return ({provider_unconfirmed:"Awaiting block confirmation",submitted:"Submitted · awaiting confirmation",
    broadcast_unknown:"Submission uncertain · do not retry payment",waived:"Waived",
    wallet_attempt_reserved:"Held for review",waiting_for_session_end:"Session in progress",
    no_operator_credit:"No operator credit due"})[t.state]||String(t.state||"Review required").replaceAll("_"," ");
}
