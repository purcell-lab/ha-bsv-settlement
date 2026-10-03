// Receipt-only orchestration. Never grants spending authority or creates payments.
export class ReceiptSync {
  constructor({connect,importReceipt,onState=()=>{}}){
    Object.assign(this,{connect,importReceipt,onState});
    this.running=false;this.paused=false;this.wallet=null;
    this.completed=new Set();
  }
  async run({jobs,identity,available=false,manual=false,allowed=true}){
    if(!allowed||this.running||!identity)return;
    const pending=jobs.filter(j=>!this.completed.has(j.key));
    if(!pending.length||(!manual&&(this.paused||!available)))return;
    this.running=true;this.paused=false;this.onState("syncing");
    try{
      const connection=await this.connect({automatic:!manual});
      if(connection.identity!==identity)throw Error("Use the wallet that registered this receiving address.");
      this.wallet=connection.wallet;
      for(const job of pending){
        await this.importReceipt(this.wallet,job,identity);
        this.completed.add(job.key);
      }
      this.onState("synced");
    }catch(error){
      this.wallet=null;this.paused=true;this.onState("paused",error);
    }finally{this.running=false;}
  }
}
