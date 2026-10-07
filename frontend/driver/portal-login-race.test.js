import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";

// Exercise the actual portal function with a deferred history response, not a
// second copy of its implementation. No browser, server, wallet or funds.
const source=readFileSync(new URL("./portal.js",import.meta.url),"utf8");
const begin=source.indexOf("async function signIn(");
const end=source.indexOf("\nfunction monthlyActionEnabled(",begin);
assert.ok(begin>=0&&end>begin);
function fixture({ttl=900,previousExpiry=0,loginIdentity="driver"}={}){
  let release,connectionCalls=0;
  const history=new Promise(resolve=>{release=resolve;});
  const context=vm.createContext({
    generation:0,identity:null,wallet:null,identityVerifiedAt:null,expires:previousExpiry,
    Date:{now:()=>100000},document:{hidden:false},location:{origin:"https://charging.example.com"},
    imports:new Map(),receiptSync:{paused:false,completed:new Set()},
    message(){},signPortalLogin:async()=>({identity:"driver"}),
    api:async action=>action==="login"?{identity:loginIdentity,expires_in:ttl}:{},
    load:()=>history,
    connectReceivingWallet:async(candidate,active)=>{active();connectionCalls++;return candidate;},
  });
  vm.runInContext(source.slice(begin,end)+"\nglobalThis.testSignIn=signIn;",context);
  return {context,release,connections:()=>connectionCalls};
}

for(const previousExpiry of [0,50000]){
  test(`fresh login survives delayed history with prior expiry ${previousExpiry}`,async()=>{
    const f=fixture({previousExpiry}),pending=f.context.testSignIn({});
    await new Promise(resolve=>setImmediate(resolve)); // History remains pending.
    const wouldExpire=!!f.context.identity&&100000>=f.context.expires;
    // Reproduce the production expiry watchdog before history can finish.
    if(wouldExpire){f.context.generation++;f.context.identity=null;}
    f.release();
    await pending;
    assert.equal(wouldExpire,false,"fresh login must not be cleared by the expiry watchdog");
    assert.equal(f.context.identity,"driver");
    assert.equal(f.context.expires,1000000);
    assert.equal(f.connections(),1);
  });
}

for(const ttl of [0,-1,901,NaN,"900",null]){
  test(`invalid login lifetime ${String(ttl)} cannot establish identity`,async()=>{
    const f=fixture({ttl});f.release();
    await assert.rejects(f.context.testSignIn({}),/lifetime/);
    assert.equal(f.context.identity,null);
    assert.equal(f.connections(),0);
  });
}

test("identity mismatch does not install a login or deadline",async()=>{
  const f=fixture({loginIdentity:"other"});f.release();
  await assert.rejects(f.context.testSignIn({}),/identity mismatch/);
  assert.equal(f.context.identity,null);assert.equal(f.context.expires,0);
});

test("sign out during delayed history still blocks wallet verification",async()=>{
  const f=fixture(),pending=f.context.testSignIn({});
  await new Promise(resolve=>setImmediate(resolve));
  f.context.generation++;f.context.identity=null;
  f.release();
  await assert.rejects(pending,/Sign-in changed/);
  assert.equal(f.connections(),0);
});
