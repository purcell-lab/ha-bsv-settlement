// S3 monthly setup orchestration. Not imported by the shipped page until S4.
// One app action coordinates the separate security checks; it never creates a
// payment, never asserts wallet spending and never reports readiness the server
// has not verified. Wallet prompts stay under the wallet's control.
import {KeyDeriver,Signature} from "@bsv/sdk";
import {bytes,hex,canonical} from "./model.js";

export const monthlyProtocol=[2,"ev monthly spending"];
export const monthlyLimitSats=30000;
const TERMS=["accept_before","authority_id","collection_policy","conversion_policy","credits_refill",
  "driver_identity","effective_at","included_session","issued_at","monthly_limit_sats","network","nonce",
  "operator_address","operator_identity","origin","period_policy","recurs_until_cancelled","scope",
  "station_ids","unused_carries_forward","version"];
const POLICY=["booking_event","evidence_ref","fee_basis","policy_id","timezone","wallet_version"];
const IDENTITY=/^(02|03)[0-9a-f]{64}$/;
const keys=o=>o&&typeof o==="object"&&!Array.isArray(o)?Object.keys(o).sort().join(","):"";
const time=v=>typeof v==="string"&&/(Z|[+-]\d\d:\d\d)$/.test(v)?Date.parse(v):NaN;

/** Verify server terms are exactly what the page described before any prompt. */
export function checkMonthlyChallenge(challenge,{hostname,identity,stations,now=Date.now()}){
  const t=challenge?.terms;
  const fail=()=>{throw Error("The monthly terms from the station did not match. Nothing was signed.");};
  if(keys(challenge)!=="authority_id,keyID,payload,protocolID,revision,terms"||keys(t)!==TERMS.join(",")||
    keys(t.period_policy)!==POLICY.join(","))fail();
  const issued=time(t.issued_at),before=time(t.accept_before);
  if(t.version!==4||t.scope!=="recurring_calendar_month_charging_including_driver_fees"||
    t.network!=="BSV mainnet"||t.monthly_limit_sats!==monthlyLimitSats||t.recurs_until_cancelled!==true||
    t.credits_refill!==false||t.unused_carries_forward!==false||
    t.collection_policy!=="one_final_net_payment_per_session_on_closure"||
    t.conversion_policy!=="freeze_configured_sat_per_aud_at_session_binding"||
    t.period_policy.fee_basis!=="all_driver_paid_wallet_debits"||
    // S3 never folds an existing session or debt into consent implicitly.
    t.included_session!==null||
    t.origin!==hostname||t.driver_identity!==identity||!IDENTITY.test(t.operator_identity)||
    canonical(t.station_ids)!==canonical([...stations].sort())||
    !Number.isFinite(issued)||!Number.isFinite(before)||time(t.effective_at)!==issued||
    before<=issued||before-issued>600000||issued>now+30000||before<=now||
    challenge.authority_id!==t.authority_id||challenge.keyID!==t.authority_id||
    canonical(challenge.protocolID)!==canonical(monthlyProtocol)||!Number.isSafeInteger(challenge.revision)||
    challenge.payload!==canonical({version:4,action:"authorise_monthly_charging",terms:t}))fail();
  return t;
}

export function checkCancelChallenge(request,{identity,authorityId}){
  let p;
  try{p=JSON.parse(request?.payload);}catch{p=null;}
  if(keys(request)!=="authority_id,keyID,payload,protocolID,revision"||
    keys(p)!=="action,authority_id,driver_identity,terms_hash,version"||p.version!==4||
    p.action!=="cancel_monthly_charging"||p.authority_id!==authorityId||p.driver_identity!==identity||
    !/^[0-9a-f]{64}$/.test(p.terms_hash)||request.authority_id!==authorityId||request.keyID!==authorityId||
    canonical(request.protocolID)!==canonical(monthlyProtocol)||canonical(p)!==request.payload||
    !Number.isSafeInteger(request.revision))
    throw Error("The cancellation request did not match your monthly authority. Nothing was signed.");
  return p;
}

export async function signMonthly(wallet,{payload,keyID,identity,description}){
  const {signature}=await wallet.createSignature({protocolID:monthlyProtocol,keyID,counterparty:"anyone",
    data:bytes(payload),description});
  const key=new KeyDeriver("anyone").derivePublicKey(monthlyProtocol,keyID,identity);
  if(!key.verify(bytes(payload),Signature.fromDER(signature)))throw Error("Wallet monthly signature did not verify.");
  return {payload,signature:hex(signature)};
}

/** Observe what this wallet session can do. Spending permission is never inferred here. */
export async function walletCapabilities(wallet,{supportedMethods=null,timeoutMs=20000,assertActive=()=>{}}={}){
  let timer;
  const can=m=>supportedMethods?supportedMethods.includes(m):typeof wallet?.[m]==="function";
  try{
    return await Promise.race([(async()=>{
      if(!wallet||!can("getPublicKey")||!can("createSignature"))throw Error("Wallet APIs are unavailable in this browser.");
      let locked=false;
      // Some substrates behind WalletClient lack the lock query: then the
      // identity request below is the wallet's own unlock gate.
      if(can("isAuthenticated"))try{locked=!(await wallet.isAuthenticated({}))?.authenticated;}catch{locked=false;}
      if(locked){
        assertActive();
        if(!can("waitForAuthentication"))throw Error("Unlock your wallet, then try again.");
        await wallet.waitForAuthentication({});
      }
      assertActive();
      const identity=(await wallet.getPublicKey({identityKey:true})).publicKey;
      assertActive();
      if(!IDENTITY.test(identity||""))throw Error("Invalid wallet identity.");
      const network=can("getNetwork")?(await wallet.getNetwork({})).network:null;
      assertActive();
      return {identity,network,canReceive:can("internalizeAction")&&can("getNetwork"),
        // Only server-side adapter evidence can establish a monthly grant.
        monthlyPermission:"server_evidence_required"};
    })(),new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error("Wallet connection timed out.")),timeoutMs);})]);
  }finally{clearTimeout(timer);}
}

const conflict=e=>e?.status===409&&e?.code==="revision_conflict";

/**
 * One "Authorise monthly charging" action. Each step runs only if still missing.
 * api(action,data) posts to the portal; signIn(wallet) performs the existing
 * proof-of-control login; syncReceipts(wallet,identity) imports existing credits.
 */
export class MonthlySetup{
  constructor({api,signIn,syncReceipts,setupAdapters={},onStep=()=>{}}){
    Object.assign(this,{api,signIn,syncReceipts,setupAdapters,onStep});
  }
  step(id,state,detail=""){this.steps.push({id,state,detail});this.onStep(id,state,detail);}
  async run({wallet,supportedMethods=null,identity=null,hostname,confirmTerms,assertActive=()=>{}}){
    this.steps=[];
    const missing=new Set();
    const result=(status,extra={})=>({ready:false,...extra,status,steps:this.steps,
      missing:[...new Set([...(status?.readiness?.missing||[]),...missing])]});
    const caps=await walletCapabilities(wallet,{supportedMethods,assertActive});
    if(caps.network!=="mainnet")throw Error("Use your BSV mainnet wallet for monthly charging.");
    this.step("wallet","done");
    if(identity!==caps.identity){
      const signed=await this.signIn(wallet);
      assertActive();
      if(signed!==caps.identity)throw Error("Sign-in identity changed. Nothing was approved.");
      identity=signed;this.step("sign_in","done");
    }else this.step("sign_in","skipped","Already signed in with this wallet.");
    let status=await this.api("monthly_status");
    assertActive();
    if(!status.enabled){this.step("authority","unavailable","Monthly charging is not enabled at this station.");return result(status);}
    const state=status.authority?.state;
    if(state==="active")this.step("authority","skipped","Monthly authority already active.");
    else if(state==="cancelled"){this.step("authority","blocked","Monthly authority was cancelled.");return result(status);}
    else{
      let challenge;
      try{challenge=await this.api("monthly_challenge",{revision:status.revision});}
      catch(e){if(conflict(e))this.step("authority","conflict","Changed in another window. Reload.");throw e;}
      assertActive();
      const terms=checkMonthlyChallenge(challenge,{hostname,identity,stations:status.station_ids});
      if(!(await confirmTerms(terms))){this.step("authority","declined");missing.add("monthly_authority");return result(status);}
      assertActive();
      const proof=await signMonthly(wallet,{payload:challenge.payload,keyID:challenge.keyID,identity,
        description:"Authorise monthly EV charging: at most 30,000 sat each calendar month including fees. One payment per session after it ends. Cancel any time."});
      assertActive();
      await this.api("monthly_accept",{authority_id:challenge.authority_id,proof,revision:challenge.revision});
      this.step("authority","done");
      status=await this.api("monthly_status");
      assertActive();
    }
    // Resume missing steps only. Adapters must be reviewed for the exact wallet
    // and origin. Their return values NEVER establish server-verified readiness.
    for(const [id,needed] of [
      ["receiving_registration",!status.receiving?.registered],
      ["wallet_monthly_permission",status.native_grant?.state!=="verified"],
    ]){
      if(!needed)continue;
      const adapter=this.setupAdapters[id];
      if(typeof adapter!=="function"){
        this.step(id,"unavailable","Setup support is not installed. Ask the operator; saved authority is unchanged.");
        continue;
      }
      assertActive();
      try{
        await adapter({wallet,identity,status,assertActive});
        assertActive();
        status=await this.api("monthly_status");
        assertActive();
      }catch(error){
        assertActive();
        this.step(id,"paused",error.message);
        missing.add(id);
      }
    }
    if(caps.canReceive){
      const sync=await this.syncReceipts(wallet,identity);
      assertActive();
      // A paused sync is reported, never shown as received.
      if(sync?.paused){missing.add("receipts_paused");this.step("receipts","paused",sync.reason||"Receiving credits paused.");}
      else this.step("receipts","done");
    }else{missing.add("wallet_receiving");this.step("receipts","missing","This wallet connection cannot receive credits.");}
    if(status.native_grant?.state!=="verified")this.step("wallet_permission","missing",
      "The wallet's monthly spending permission is not verified. Payments will ask in the wallet each time, or wait.");
    else this.step("wallet_permission","done");
    // Readiness is the server's verified answer, narrowed by local capability.
    const ready=status.readiness?.automatic_collection===true&&caps.canReceive&&!missing.size;
    return {...result(status),ready};
  }
}

export async function cancelMonthly({api,wallet,identity,authorityId}){
  const request=await api("monthly_cancel_challenge",{revision:0});
  checkCancelChallenge(request,{identity,authorityId});
  const proof=await signMonthly(wallet,{payload:request.payload,keyID:request.keyID,identity,
    description:"Cancel monthly EV charging. Stops new charges; existing payments and credits are unchanged."});
  const result=await api("monthly_cancel",{proof,revision:request.revision});
  // Native wallet permission revocation is reported separately and never assumed.
  return {cancelled:result.cancelled===true,walletPermissionRevoked:result.wallet_permission_revoked};
}
