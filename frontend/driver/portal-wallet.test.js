import test from "node:test";
import assert from "node:assert/strict";
import {walletStatus,verifyReceivingWallet} from "./portal-wallet.js";

const fixture=()=>({getPublicKey:async()=>({publicKey:"driver"}),getNetwork:async()=>({network:"mainnet"})});
test("restored history is not a verified wallet connection",()=>{
  assert.equal(walletStatus("unverified").title,"Wallet connection not verified");
  assert.equal(walletStatus("unverified").action,"Reconnect wallet");
  assert.equal(walletStatus("unavailable").title,"Wallet not connected");
});
test("checking disables duplicate reconnect; connected and receipt pause are distinct",()=>{
  assert.equal(walletStatus("checking").disabled,true);
  assert.equal(walletStatus("connected").title,"Wallet connected");
  assert.equal(walletStatus("connected",true).action,"Retry receiving credits");
});
test("reconnect verifies same identity and mainnet without signing or spending",async()=>{
  const wallet=fixture();let calls=0;
  assert.equal(await verifyReceivingWallet(wallet,"driver",()=>{calls++;}),wallet);
  assert.ok(calls>=3);
});
test("wrong identity stops before checking network",async()=>{
  const wallet=fixture();wallet.getNetwork=()=>{throw Error("must not be called");};
  await assert.rejects(verifyReceivingWallet(wallet,"other"),/same wallet/);
});
test("wrong network cannot establish receiving connection",async()=>{
  const wallet=fixture();wallet.getNetwork=async()=>({network:"testnet"});
  await assert.rejects(verifyReceivingWallet(wallet,"driver"),/mainnet/);
});
test("rejection does not become connected",async()=>{
  const wallet=fixture();wallet.getPublicKey=async()=>{throw Error("Permission declined");};
  await assert.rejects(verifyReceivingWallet(wallet,"driver"),/declined/);
});
test("expiry or identity change during connection aborts before further calls",async()=>{
  let active=true;const wallet=fixture();
  wallet.getPublicKey=async()=>{active=false;return {publicKey:"driver"};};
  await assert.rejects(verifyReceivingWallet(wallet,"driver",()=>{
    if(!active)throw Error("Private access changed");
  }),/Private access changed/);
});
test("unresponsive wallet has a bounded connection timeout",async()=>{
  const wallet=fixture();wallet.getPublicKey=()=>new Promise(()=>{});
  await assert.rejects(verifyReceivingWallet(wallet,"driver",()=>{},5),/timed out/);
});
test("late identity response after timeout cannot initiate another wallet call",async()=>{
  const wallet=fixture();let release,networkCalls=0;
  wallet.getPublicKey=()=>new Promise(resolve=>{release=resolve;});
  wallet.getNetwork=async()=>{networkCalls++;return {network:"mainnet"};};
  await assert.rejects(verifyReceivingWallet(wallet,"driver",()=>{},5),/timed out/);
  release({publicKey:"driver"});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(networkCalls,0);
});
