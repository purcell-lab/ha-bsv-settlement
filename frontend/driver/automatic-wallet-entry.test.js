import test from "node:test";
import assert from "node:assert/strict";
import {AutomaticWalletEntry,boundedWalletCall} from "./automatic-wallet-entry.js";

test("starts without a click, then does not repeat successful setup",async()=>{
  let calls=0;const flow=new AutomaticWalletEntry({attempt:async()=>{calls++;}});
  await flow.start();await flow.start();assert.equal(calls,1);assert.equal(flow.state,"ready");
});
test("serialises load and visibility events",async()=>{
  let release,calls=0;
  const flow=new AutomaticWalletEntry({attempt:async()=>{calls++;await new Promise(r=>{release=r;});}});
  const first=flow.start();await flow.start();release();await first;assert.equal(calls,1);
});
test("refusal remains paused through repeated automatic checks",async()=>{
  let calls=0;const flow=new AutomaticWalletEntry({attempt:async()=>{calls++;throw Error("Permission denied");}});
  await flow.start();await flow.start();flow.invalidate();await flow.start();
  assert.equal(calls,1);assert.equal(flow.state,"paused");
  await flow.start(undefined,{resume:true});assert.equal(calls,2);
});
test("signed-out or cancelled pending proof cannot complete",async()=>{
  let release,completed=false;
  const flow=new AutomaticWalletEntry({attempt:async(_,active)=>{
    await new Promise(r=>{release=r;});active();completed=true;
  }});
  const pending=flow.start();flow.pause("Signed out");release();await pending;
  assert.equal(completed,false);assert.equal(flow.state,"paused");
});
test("hidden, framed or busy context starts no request",async()=>{
  let allowed=false,calls=0;
  const flow=new AutomaticWalletEntry({allowed:()=>allowed,attempt:async()=>{calls++;}});
  await flow.start();assert.equal(calls,0);
  allowed=true;await flow.start();assert.equal(calls,1);
});
test("only definite missing transport offers QR; rejection does not",async()=>{
  for(const code of ["WALLET_UNAVAILABLE","USER_DENIED",undefined]){
    let pairs=0;
    const flow=new AutomaticWalletEntry({attempt:async()=>{throw Object.assign(Error("Failed"),{code});},
      onUnavailable:async()=>{pairs++;}});
    await flow.start();await flow.start();
    assert.equal(pairs,code==="WALLET_UNAVAILABLE"?1:0);
  }
});
test("paired wallet continues without another sign-in button",async()=>{
  const wallet={paired:true};let received=null;
  const flow=new AutomaticWalletEntry({attempt:async candidate=>{
    if(!candidate)throw Object.assign(Error("Missing"),{code:"WALLET_UNAVAILABLE"});
    received=candidate;
  }});
  await flow.start();await flow.start(wallet,{resume:true});
  assert.equal(received,wallet);assert.equal(flow.state,"ready");
});
test("expiry permits fresh wallet proof, but never revives explicit pause",async()=>{
  let calls=0;const flow=new AutomaticWalletEntry({attempt:async()=>{calls++;}});
  await flow.start();flow.invalidate();await flow.start();assert.equal(calls,2);
  flow.pause();flow.invalidate();await flow.start();assert.equal(calls,2);
});
test("unresponsive native wallet is bounded and late return cannot continue",async()=>{
  let release,continued=false;
  await assert.rejects((async()=>{
    await boundedWalletCall(()=>new Promise(r=>{release=r;}),5);continued=true;
  })(),/timed out/);
  release();await new Promise(r=>setImmediate(r));assert.equal(continued,false);
});
