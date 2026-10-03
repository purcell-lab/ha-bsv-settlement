// Fictional, isolated UI preview. Never included in the HACS driver bundle.
import { PrivateKey, ProtoWallet, Utils } from "@bsv/sdk";
import { canonical, bytes, paymentAuthority, spendingScope } from "./model.js";
import { pairingProtocol, requiredMethods } from "./pairing.js";
const banner=document.createElement("section");
banner.id="preview-banner";
banner.innerHTML=`<h2>Fictional pairing preview</h2><p>No live wallet, driver record or payment. The QR connects only to this simulated preview, not a real service.</p><label for="preview-mode">Simulated phone response</label><select id="preview-mode"><option value="compatible">Compatible mainnet wallet</option><option value="incompatible">Current wallet missing getNetwork</option><option value="waiting">Wait for scan</option><option value="rejected">Connection lost</option></select>`;
document.querySelector("main").prepend(banner);
const origin="https://charging.example.com", operator=PrivateKey.fromRandom();
const driver=new ProtoWallet(PrivateKey.fromRandom());
const identity=(await driver.getPublicKey({identityKey:true})).publicKey;
const terms={
  version:2,budget_id:"11111111-2222-4333-8444-555555555555",session_id:"fictional-session",
  transaction_id:"fictional-proxy-id",network:"BSV mainnet",
  operator_identity:operator.toPublicKey().toString(),operator_address:operator.toPublicKey().toAddress(),
  operator_name:"Community charging demo",operator_contact:"Fictional operator",
  max_total_sats:1000,max_fee_sats:10,satoshis_per_aud:"100",
  pricing_rule:"Interval energy × dynamic rate",account_scope:"One charging session",
  import_price_entity:"sensor.demo_import",export_price_entity:"sensor.demo_export",
  created_at:new Date().toISOString(),expires_at:new Date(Date.now()+3600000).toISOString(),scope:spendingScope,
};
terms.payment_authority=paymentAuthority(terms);
const payload=canonical(terms), invitation={version:1,payload,signature:operator.sign(bytes(payload)).toDER("hex")};
let session;
const realFetch=window.fetch;
window.fetch=async(url,options)=>{
  if(url!=="/api/bsv_settlement/driver")return realFetch(url,options);
  const body=JSON.parse(options.body);
  if(body.action==="read"){
    const p={available:true,start:new Date(Date.now()-60000).toISOString(),
      end:new Date(Date.now()+3600000).toISOString(),estimate:false};
    return Response.json({invitation,state:"awaiting_driver_consent",driver_identity:null,
      automatic_credit_enabled:false,prices:{valid:true,checked_at:new Date().toISOString(),
        import:{...p,aud_per_kwh:"0.25"},export:{...p,aud_per_kwh:"0.12"}}});
  }
  if(body.action==="pairing_create"){
    session={topic:"fictional_pairing_topic_123",origin,relay:"wss://charging.example.com",
      backendIdentityKey:body.backend_identity,desktop_token:"d".repeat(43),
      expiry:String(Math.floor(Date.now()/1000)+120)};
    return Response.json(session);
  }
  if(body.action==="pairing_cancel")return Response.json({disconnected:true});
  return Response.json({error:"Preview only. No real approval or payment is sent."},{status:400});
};
const to64=a=>Utils.toBase64(a).replaceAll("+","-").replaceAll("/","_").replaceAll("=","");
const from64=s=>Utils.toArray(s.replaceAll("-","+").replaceAll("_","/"),"base64");
class DemoSocket{
  constructor(){
    setTimeout(async()=>{
      if(this.closed)return;
      this.onopen?.();
      const mode=document.getElementById("preview-mode").value;
      if(mode==="waiting")return;
      if(mode==="rejected"){this.onclose?.();return;}
      await new Promise(r=>setTimeout(r,1200));
      this.reply({id:"demo",seq:1,method:"pairing_approved",params:{
        mobileIdentityKey:identity,protocolID:JSON.stringify(pairingProtocol),
        permissions:mode==="incompatible"?requiredMethods.filter(m=>m!=="getNetwork"):requiredMethods,
      }});
    },100);
  }
  async reply(msg){
    if(this.closed)return;
    const {ciphertext}=await driver.encrypt({protocolID:pairingProtocol,keyID:session.topic,
      counterparty:session.backendIdentityKey,plaintext:bytes(JSON.stringify(msg))});
    this.onmessage?.({data:JSON.stringify({topic:session.topic,mobileIdentityKey:identity,ciphertext:to64(ciphertext)})});
  }
  async send(raw){
    const envelope=JSON.parse(raw);
    const {plaintext}=await driver.decrypt({protocolID:pairingProtocol,keyID:session.topic,
      counterparty:session.backendIdentityKey,ciphertext:from64(envelope.ciphertext)});
    const msg=JSON.parse(new TextDecoder().decode(new Uint8Array(plaintext)));
    if(msg.method==="pairing_ack")return;
    const result=msg.method==="getPublicKey"?{publicKey:identity}:msg.method==="getNetwork"?{network:"mainnet"}:null;
    await this.reply({id:msg.id,seq:msg.seq,...(result?{result}:{error:{code:4001,message:"Preview does not sign spending approval"}})});
  }
  close(){this.closed=true;}
}
window.WebSocket=DemoSocket;
await import("./app.js");
