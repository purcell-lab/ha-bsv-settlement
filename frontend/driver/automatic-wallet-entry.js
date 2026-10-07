// UI orchestration only. Wallet signatures and server-side budgets remain authority.
// A denial/timeout never becomes a polling retry; explicit pause is sticky in this page.
export async function boundedWalletCall(operation,timeoutMs=120000){
  let timer;
  try{return await Promise.race([Promise.resolve().then(operation),new Promise((_,reject)=>{
    timer=setTimeout(()=>reject(Error("Wallet request timed out. Check the wallet before retrying.")),timeoutMs);
  })]);}finally{clearTimeout(timer);}
}
export class AutomaticWalletEntry {
  constructor({attempt,allowed=()=>true,onState=()=>{},onUnavailable=async()=>{}}) {
    Object.assign(this,{attempt,allowed,onState,onUnavailable});
    this.state="idle";this.running=false;this.epoch=0;
  }
  set(state,detail=""){this.state=state;this.onState(state,detail);}
  pause(detail="Automatic wallet setup paused."){
    this.epoch++;this.set("paused",detail);
  }
  invalidate(){if(this.state==="ready")this.set("idle");}
  async start(candidate,{resume=false}={}){
    if(this.running||!this.allowed())return;
    if(resume)this.set("idle");
    if(this.state!=="idle")return;
    this.running=true;const epoch=++this.epoch;
    const active=()=>{
      if(epoch!==this.epoch||this.state!=="running")
        throw Error("Automatic wallet setup was stopped.");
    };
    this.set("running");
    try{
      await this.attempt(candidate,active);
      active();this.set("ready");
    }catch(error){
      if(epoch!==this.epoch)return;
      if(error.code==="WALLET_UNAVAILABLE"){
        this.set("waiting_wallet","No local wallet found. Connect BSV Browser with the pairing code.");
        try{await this.onUnavailable();}
        catch{if(epoch===this.epoch)this.pause("Wallet pairing could not start. Retry when ready.");}
      }else this.pause(error.message||"Wallet setup was not completed.");
    }finally{this.running=false;}
  }
}
