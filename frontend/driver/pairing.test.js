import test from "node:test";
import assert from "node:assert/strict";
import { PrivateKey, ProtoWallet, Utils } from "@bsv/sdk";
import { PrivateKey as MobileKey, ProtoWallet as MobileWallet } from "@bsv/sdk-mobile";
import { BrowserPairing, pairingUri, signatureMessage, pairingProtocol, requiredMethods, decodeEnvelope, normalizeWalletResult } from "./pairing.js";

const bytes = s => Array.from(new TextEncoder().encode(s));
const b64 = a => Utils.toBase64(a).replaceAll("+","-").replaceAll("/","_").replaceAll("=","");
const from64 = s => Utils.toArray(s.replaceAll("-","+").replaceAll("_","/"),"base64");
const origin = "https://charging.example.com";
class Socket {
  sent=[];
  constructor(url, protocols){this.url=url;this.protocols=protocols;}
  send(data){this.sent.push(data);}
  close(){this.closed=true;}
}
async function fixture(options={}) {
  const desktop = new ProtoWallet(PrivateKey.fromRandom());
  // Actual newer mobile SDK verifies the older bundled driver's wire format.
  const mobile = new MobileWallet(MobileKey.fromRandom());
  const apiCalls=[];
  const pairing = new BrowserPairing({
    origin, cryptoWallet:desktop, WebSocketClass:Socket, ...options,
    api:async(action, params)=>{
      apiCalls.push({action,params});
      return {topic:"fictional_pairing_topic_123",desktop_token:"d".repeat(43),
        origin,backendIdentityKey:params.backend_identity,expiry:String(Math.floor(Date.now()/1000)+120),
        relay:"wss://charging.example.com"};
    },
  });
  await pairing.start();pairing.socket.onopen();
  const identity=(await mobile.getPublicKey({identityKey:true})).publicKey;
  const envelope=async msg=>{
    const {ciphertext}=await mobile.encrypt({protocolID:pairingProtocol,keyID:pairing.session.topic,
      counterparty:pairing.session.backendIdentityKey,plaintext:bytes(JSON.stringify(msg))});
    return JSON.stringify({topic:pairing.session.topic,ciphertext:b64(ciphertext),mobileIdentityKey:identity});
  };
  const approve=async(permissions=requiredMethods)=>pairing.receive(await envelope({
    id:"approve",seq:1,method:"pairing_approved",params:{mobileIdentityKey:identity,
      protocolID:JSON.stringify(pairingProtocol),permissions},
  }));
  const read=async()=>{
    const wire=JSON.parse(pairing.socket.sent.at(-1));
    const {plaintext}=await mobile.decrypt({protocolID:pairingProtocol,keyID:pairing.session.topic,
      counterparty:pairing.session.backendIdentityKey,ciphertext:from64(wire.ciphertext)});
    return JSON.parse(new TextDecoder().decode(new Uint8Array(plaintext)));
  };
  return {pairing,mobile,desktop,envelope,approve,read,identity,apiCalls};
}
const tick=()=>new Promise(r=>setTimeout(r,0));

test("QR matches upstream signature transcript and anyone verification",async()=>{
  const f=await fixture();
  try{
    const uri=await pairingUri(f.desktop,f.pairing.session);
    const p=new URL(uri).searchParams;
    assert.equal(new URL(uri).protocol,"bsv-wallet:");
    assert.equal(p.has("desktop_token"),false);
    const anyone=new MobileWallet(new MobileKey(1));
    assert.equal((await anyone.verifySignature({protocolID:[0,"qr pairing"],
      keyID:p.get("topic"),counterparty:p.get("backendIdentityKey"),
      data:bytes(signatureMessage(f.pairing.session)),signature:from64(p.get("sig"))})).valid,true);
    assert.equal(f.pairing.socket.url.includes("token"),false);
    assert.equal(f.pairing.socket.protocols[1],"bsv-wallet-relay-token."+"d".repeat(43));
  }finally{await f.pairing.disconnect();}
});
test("encrypted pairing sends ack, not consent or payment, then one RPC",async()=>{
  const f=await fixture();
  try{
    await f.approve();
    assert.equal((await f.read()).method,"pairing_ack");
    assert.equal(f.pairing.state,"paired");
    assert.deepEqual(f.apiCalls.map(x=>x.action),["pairing_create"]);
    const response=f.pairing.wallet.getPublicKey({identityKey:true});await tick();
    const req=await f.read();
    assert.equal(req.method,"getPublicKey");
    await f.pairing.receive(await f.envelope({id:req.id,seq:req.seq,result:{publicKey:f.identity}}));
    assert.equal((await response).publicKey,f.identity);
    await assert.rejects(f.pairing.request("broadcast",{}),/not ready/);
  }finally{await f.pairing.disconnect();}
});
test("current mobile method list lacking getNetwork blocks settlement",async()=>{
  const f=await fixture();
  try{
    await f.approve(requiredMethods.filter(m=>m!=="getNetwork"));
    assert.equal(f.pairing.state,"incompatible");
    await assert.rejects(f.pairing.wallet.createSignature({}),/not ready/);
    assert.equal(f.pairing.socket.sent.length,1); // handshake only
  }finally{await f.pairing.disconnect();}
});
test("reject replay, wrong topic, changed identity and unknown fields",async()=>{
  const f=await fixture();
  try{
    await f.approve();
    await assert.rejects(f.approve(),/Replayed/);
    assert.throws(()=>decodeEnvelope(JSON.stringify({topic:"wrong",ciphertext:"YWJj"}),f.pairing.session.topic));
    assert.throws(()=>decodeEnvelope(JSON.stringify({topic:f.pairing.session.topic,ciphertext:"YWJj",secret:"bad"}),f.pairing.session.topic));
    const data=JSON.parse(await f.envelope({id:"reply",seq:3,result:{}}));
    data.mobileIdentityKey=PrivateKey.fromRandom().toPublicKey().toString();
    await assert.rejects(f.pairing.receive(JSON.stringify(data)),/identity changed/);
  }finally{await f.pairing.disconnect();}
});
test("timeout ends connection and never resends a wallet action",async()=>{
  const f=await fixture({rpcTimeout:15});
  try{
    await f.approve();
    await assert.rejects(f.pairing.wallet.createAction({description:"mock only"}),/timed out/);
    assert.equal(f.pairing.state,"disconnected");
    assert.equal(f.pairing.pending.size,0);
    assert.equal(f.apiCalls.length,1);
  }finally{await f.pairing.disconnect();}
});
test("concurrent RPC rejected, wallet rejection propagated without retry",async()=>{
  const f=await fixture();
  try{
    await f.approve();
    const response=f.pairing.wallet.createSignature({});await tick();
    await assert.rejects(f.pairing.wallet.createSignature({}),/pending/);
    const req=await f.read();
    const rejection=assert.rejects(response,/Wallet rejected/);
    await f.pairing.receive(await f.envelope({id:req.id,seq:req.seq,error:{code:4001,message:"Denied"}}));
    await rejection;
    assert.equal(f.pairing.socket.sent.length,2);
  }finally{await f.pairing.disconnect();}
});
test("mainnet and registered identity are checked before using paired wallet",async()=>{
  const f=await fixture();
  try{
    await f.approve();
    f.pairing.wallet.getPublicKey=async()=>({publicKey:f.identity});
    f.pairing.wallet.getNetwork=async()=>({network:"testnet"});
    await assert.rejects(f.pairing.verifiedWallet(),/mainnet/);
    f.pairing.wallet.getNetwork=async()=>({network:"mainnet"});
    await assert.rejects(f.pairing.verifiedWallet("wrong"),/registered/);
    assert.equal((await f.pairing.verifiedWallet(f.identity)).identity,f.identity);
  }finally{await f.pairing.disconnect();}
});
test("tampered ciphertext cannot establish paired identity",async()=>{
  const f=await fixture();
  try{
    const wire=await f.envelope({seq:1,method:"pairing_approved"});
    const e=JSON.parse(wire);e.ciphertext="AAAA"+e.ciphertext.slice(4);
    await assert.rejects(f.pairing.receive(JSON.stringify(e)));
    assert.equal(f.pairing.mobileIdentity,undefined);
  }finally{await f.pairing.disconnect();}
});
test("a late decrypted handshake cannot resurrect a disconnected transport",async()=>{
  const f=await fixture();
  try{
    let release;
    const original=f.desktop.decrypt.bind(f.desktop);
    f.desktop.decrypt=async args=>{
      const result=await original(args);
      await new Promise(r=>{release=r;});
      return result;
    };
    const pending=f.approve();await tick();
    f.pairing.fail("User disconnected");
    release();await pending;
    assert.equal(f.pairing.state,"disconnected");
    assert.equal(f.pairing.mobileIdentity,undefined);
  }finally{await f.pairing.disconnect();}
});
test("a response cannot satisfy the wrong request sequence",async()=>{
  const f=await fixture();
  try{
    await f.approve();
    const result=f.pairing.wallet.getPublicKey({identityKey:true});
    const rejected=assert.rejects(result,/Disconnected/);
    await tick();
    const req=await f.read();
    await assert.rejects(f.pairing.receive(await f.envelope({
      id:req.id,seq:req.seq+1,result:{publicKey:f.identity},
    })),/Unexpected/);
    await f.pairing.disconnect();await rejected;
  }finally{await f.pairing.disconnect();}
});
test("known wallet bytes normalize from arrays and JSON Uint8Array objects",()=>{
  assert.deepEqual(normalizeWalletResult({signature:{"0":48,"1":2}}),{signature:[48,2]});
  assert.deepEqual(normalizeWalletResult({tx:[1,2],signableTransaction:{reference:"a",tx:{"0":3}}}),
    {tx:[1,2],signableTransaction:{reference:"a",tx:[3]}});
  assert.deepEqual(normalizeWalletResult({metadata:{"0":"untouched"}}),{metadata:{"0":"untouched"}});
  for(const signature of [{"1":2}, {"0":256}, {"0":1.5}, {"0":true}, "00ff"])
    assert.throws(()=>normalizeWalletResult({signature}),/byte field/);
});
