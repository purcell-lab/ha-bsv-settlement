import test from "node:test";
import assert from "node:assert/strict";
import {probeWalletEnvironment} from "./wallet-evidence.js";

const options={origin:"https://charging.example",timeoutMs:20};
function fixture(){
  const calls=[];
  const wallet={
    async isAuthenticated(){calls.push("isAuthenticated");return {authenticated:true};},
    async getVersion(){calls.push("getVersion");return {version:"fixture-wallet-1"};},
    async getNetwork(){calls.push("getNetwork");return {network:"mainnet"};},
  };
  for(const name of ["waitForAuthentication","getPublicKey","createSignature","createAction",
    "signAction","internalizeAction","ensureSpendingAuthorization","listSpendingAuthorizations",
    "revokePermission"]) wallet[name]=()=>{calls.push(name);throw Error("Forbidden operation");};
  return {wallet,calls};
}

test("environment observation is bounded and never a grant or readiness assertion",async()=>{
  const {wallet,calls}=fixture();
  const r=await probeWalletEnvironment(wallet,options);
  assert.deepEqual(calls,["isAuthenticated","getVersion","getNetwork"]);
  assert.equal(r.authentication.value,true);
  assert.equal(r.version.value,"fixture-wallet-1");
  assert.equal(r.network.value,"mainnet");
  assert.equal(r.monthly_grant.state,"not_verified");
  assert.equal(r.receiving.state,"not_verified");
  assert.equal(r.automatic_collection_ready,false);
  assert.equal(r.evidence_class,"untrusted_browser_observation");
  assert.equal("identity" in r,false);
});

test("locked wallet is not unlocked or prompted",async()=>{
  const {wallet,calls}=fixture();
  wallet.isAuthenticated=async()=>{calls.push("isAuthenticated");return {authenticated:false};};
  const r=await probeWalletEnvironment(wallet,options);
  assert.deepEqual(calls,["isAuthenticated"]);
  assert.equal(r.version.state,"not_checked");
});

test("missing wallet is explicit",async()=>{
  const r=await probeWalletEnvironment(undefined,options);
  assert.equal(r.authentication.state,"missing_method");
  assert.equal(r.network.state,"not_checked");
});

test("RPC proxies advertising arbitrary methods do not prove capabilities",async()=>{
  const wallet=new Proxy({}, {get:()=>async()=>{throw Error("unsupported SECRET_TOKEN");}});
  const r=await probeWalletEnvironment(wallet,options);
  assert.equal(r.authentication.state,"unavailable");
  assert.equal(r.automatic_collection_ready,false);
  assert.equal(JSON.stringify(r).includes("SECRET"),false);
});

test("a hanging observation times out without repeating the wallet call",async()=>{
  let calls=0;
  const r=await probeWalletEnvironment({isAuthenticated(){calls++;return new Promise(()=>{});}},options);
  assert.equal(r.authentication.state,"unavailable");
  assert.equal(calls,1);
});

test("malformed authentication is not truthy authentication",async()=>{
  const r=await probeWalletEnvironment({isAuthenticated:async()=>({authenticated:"true"})},options);
  assert.equal(r.authentication.state,"invalid_response");
});

test("testnet is recorded without a mainnet readiness claim",async()=>{
  const {wallet}=fixture();wallet.getNetwork=async()=>({network:"testnet"});
  const r=await probeWalletEnvironment(wallet,options);
  assert.equal(r.network.value,"testnet");
  assert.equal(r.automatic_collection_ready,false);
});

test("unexpected fields and invalid version/network payloads are discarded",async()=>{
  const {wallet}=fixture();
  wallet.getVersion=async()=>({version:"<script>secret</script>",secret:"secret"});
  wallet.getNetwork=async()=>({network:"other",token:"secret"});
  const r=await probeWalletEnvironment(wallet,options);
  assert.equal(r.version.state,"invalid_response");
  assert.equal(r.network.state,"invalid_response");
  assert.equal(JSON.stringify(r).includes("secret"),false);
});

test("a failed version read does not fabricate a version",async()=>{
  const {wallet}=fixture();wallet.getVersion=async()=>{throw Error("details");};
  const r=await probeWalletEnvironment(wallet,options);
  assert.equal(r.version.state,"unavailable");
  assert.equal(r.network.value,"mainnet");
});

test("invalid origin/timeout fails before any wallet method",async()=>{
  const {wallet,calls}=fixture();
  for(const origin of ["http://charging.example","https://charging.example/path",
    "https://name:password@charging.example","https://charging.example/#secret"])
    await assert.rejects(probeWalletEnvironment(wallet,{...options,origin}));
  for(const timeoutMs of [0,5001,NaN,"20"])
    await assert.rejects(probeWalletEnvironment(wallet,{...options,timeoutMs}));
  assert.deepEqual(calls,[]);
});
