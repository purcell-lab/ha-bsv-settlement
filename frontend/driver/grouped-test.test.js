import {test} from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {declaration,preflight,GroupedTest} from "./grouped-test.js";

const origin="https://charging.example";
function fixture({manifest={metanet:declaration},status={enabled:true,origin,monthly_limit_sats:30000}}={}) {
  const calls=[];
  const fetcher=async(path,options)=>{
    calls.push(path);
    assert.equal(options.credentials,"omit");
    assert.equal(options.redirect,"error");
    return {ok:true,json:async()=>path==="/manifest.json"?structuredClone(manifest):status};
  };
  const wallet=new Proxy({
    getNetwork:async()=>{calls.push("getNetwork");return {network:"mainnet"};},
    waitForAuthentication:async()=>{calls.push("waitForAuthentication");return {authenticated:true};}
  },{get(t,k){if(k in t)return t[k];throw Error("Forbidden wallet method "+String(k));}});
  return {fetcher,wallet,calls};
}
test("preflight reads public configuration only, no wallet calls",async()=>{
  const h=fixture();const r=await preflight(h.fetcher,origin);
  assert.equal(r.spending_permission,"not_verified");
  assert.deepEqual(h.calls,["/api/bsv_settlement/grouped-test","/manifest.json"]);
});
test("explicit test asks authenticated wallet too, but never claims a grant",async()=>{
  const h=fixture(),runner=new GroupedTest({...h,origin});
  const r=await runner.run();
  assert.deepEqual(h.calls,["/api/bsv_settlement/grouped-test","/manifest.json","getNetwork","waitForAuthentication"]);
  assert.equal(r.spending_permission,"not_verified");
  await assert.rejects(runner.run(),/already attempted/);
});
test("duplicate clicks cannot issue another request",async()=>{
  const h=fixture(),runner=new GroupedTest({...h,origin});
  const results=await Promise.allSettled([runner.run(),runner.run()]);
  assert.equal(results.filter(r=>r.status==="fulfilled").length,1);
  assert.equal(h.calls.filter(c=>c==="waitForAuthentication").length,1);
});
for(const [name,change] of [
  ["wrong cap",m=>{m.metanet.groupPermissions.spendingAuthorization.amount=30001;}],
  ["extra protocol",m=>{m.metanet.groupPermissions.protocolPermissions=[];}],
  ["legacy namespace",m=>{m.babbage={};}],
  ["wrong version",m=>{m.metanet.schemaVersion=2;}],
  ["no declaration",m=>{delete m.metanet;}],
])test("reject "+name,async()=>{
  const manifest={metanet:structuredClone(declaration)};change(manifest);
  const h=fixture({manifest});
  await assert.rejects(new GroupedTest({...h,origin}).run(),/manifest/);
  assert.ok(!h.calls.includes("getNetwork"));
});
test("disabled and wrong origin fail before wallet interaction",async()=>{
  for(const status of [{enabled:false,origin,monthly_limit_sats:30000},
    {enabled:true,origin:"https://other.example",monthly_limit_sats:30000}]){
    const h=fixture({status});
    await assert.rejects(new GroupedTest({...h,origin}).run(),/not enabled/);
    assert.equal(h.calls.length,1);
  }
});
test("wrong network, missing API and malformed auth never show approval",async()=>{
  for(const wallet of [
    {},{getNetwork:async()=>({network:"testnet"}),waitForAuthentication:()=>{throw Error("must not call");}},
    {getNetwork:async()=>({network:"mainnet"}),waitForAuthentication:async()=>({})}
  ]){
    await assert.rejects(new GroupedTest({...fixture(),wallet,origin}).run());
  }
});
test("timed-out native request is not retried",async()=>{
  let prompts=0;
  const wallet={getNetwork:async()=>({network:"mainnet"}),waitForAuthentication:()=>{
    prompts++;return new Promise(()=>{});
  }};
  const runner=new GroupedTest({...fixture(),wallet,origin,timeoutMs:5});
  await assert.rejects(runner.run(),/timed out/);
  await assert.rejects(runner.run(),/already attempted/);
  assert.equal(prompts,1);
});
test("wallet denial is propagated without fallback",async()=>{
  const wallet={getNetwork:async()=>({network:"mainnet"}),waitForAuthentication:async()=>{throw Error("declined");}};
  await assert.rejects(new GroupedTest({...fixture(),wallet,origin}).run(),/declined/);
});
test("diagnostic entry is isolated from all payment and receipt workers",()=>{
  const page=readFileSync(new URL("./grouped-page.js",import.meta.url),"utf8");
  assert.deepEqual([...page.matchAll(/from "([^"]+)"/g)].map(m=>m[1]),["./grouped-test.js"]);
  assert.ok(!/createAction|signAction|internalizeAction|getPublicKey|createSignature/.test(page));
});
