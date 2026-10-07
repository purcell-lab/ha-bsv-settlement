import {parseInvitation,derivedInvitation} from "./model.js";
import {collectOnce} from "./collection.js";

const actions={claim_collection:"debit_claim",authorise_collection:"debit_authorise",
  report_collection:"debit_report",report_collection_failure:"debit_failure"};
export function collectionOutcome(result){
  if(result?.state==="provider_confirmed")return "Session payment confirmed by the provider.";
  if(["provider_unconfirmed","submitted"].includes(result?.state))
    return "Session payment submitted. Awaiting block confirmation.";
  return "Payment submission is uncertain or needs review. Do not pay again; no replacement will be attempted automatically.";
}

// User-started, serial coordinator. Never resumes holds or replaces an attempt.
export class PortalCollections {
  constructor({api,assertActive,onState=()=>{},collect=collectOnce,derive=derivedInvitation}){
    Object.assign(this,{api,assertActive,onState,collect,derive});
    this.running=false;this.enabled=false;this.seen=new Set();this.paused=new Map();this.cursor=0;
  }
  start(){this.enabled=true;} // Only from the explicit sign-in journey, not cookie restore.
  stop(){this.enabled=false;}
  async run(wallet){
    if(!this.enabled||this.running)return;
    this.running=true;
    try{
      this.assertActive();
      const {jobs}=await this.api("debit_jobs");
      const start=this.cursor%Math.max(jobs.length,1);
      const ordered=[...jobs.slice(start),...jobs.slice(0,start)];
      let checkedCount=0;
      for(const job of ordered){
        const key=job.budget_id+"|"+job.session_id;
        if(this.seen.has(key)||this.paused.has(key))continue;
        if(checkedCount++>=2)break; // Bound API/wallet work; rotate fairly on the next poll.
        this.cursor=(jobs.indexOf(job)+1)%jobs.length;
        this.assertActive();
        const status=await this.api("debit_status",job);
        this.assertActive();
        if(status.collection.state!=="ready"){
          if(["broadcast_unknown","wallet_attempt_reserved","recovery_ready","collection_blocked"].includes(status.collection.state))
            this.onState("held",job,status.collection.state);
          continue;
        }
        const parent=parseInvitation(JSON.stringify(status.invitation));
        if(parent.terms.budget_id!==job.budget_id||
          (parent.terms.version!==3&&
           (parent.terms.session_mode==="next_session_reservation"?status.binding?.session_id:parent.terms.session_id)!==job.session_id))
          throw Error("The discovered session does not match its signed budget.");
        const checked=parent.terms.version===3?
          await this.derive(status.session_invitation,parent,job.session_id):parent;
        // Lock before any wallet interaction. Failed or ambiguous attempts
        // require operator reconciliation, not another automatic run.
        this.seen.add(key);
        const scoped=async(action,data={})=>{
          const mapped=actions[action];if(!mapped)throw Error("Unsupported collection operation");
          // Diagnostics can still be reported after a local pause, but never
          // allow a payment call to continue after sign-out or hidden context.
          if(action!=="report_collection_failure")this.assertActive();
          return this.api(mapped,{...data,...job});
        };
        const guarded=new Proxy(wallet,{get:(target,method)=>{
          const value=target[method];
          return typeof value==="function"?async(...args)=>{
            this.assertActive();const result=await value.apply(target,args);this.assertActive();return result;
          }:value;
        }});
        try{
          this.onState("collecting",job);
          const result=await this.collect(guarded,checked,status.binding,status.collection.quote,scoped,
            text=>this.onState("progress",job,text));
          this.onState("submitted",job,result);
        }catch(error){
          this.paused.set(key,error); // Includes exact pending report, in memory only.
          this.onState("paused",job,error);
        }
      }
    }finally{this.running=false;}
  }
}
