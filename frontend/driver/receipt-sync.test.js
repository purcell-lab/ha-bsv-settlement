import test from "node:test";
import assert from "node:assert/strict";
import {ReceiptSync} from "./receipt-sync.js";

const jobs=[{key:"confirmed-a"},{key:"confirmed-b"}];
const args={jobs,identity:"driver",available:true};
function fixture(overrides={}){
  const calls=[],states=[];
  const sync=new ReceiptSync({
    connect:async mode=>{calls.push(["connect",mode]);return {wallet:{},identity:"driver"};},
    importReceipt:async(_,job)=>calls.push(["import",job.key]),
    onState:(...state)=>states.push(state),...overrides,
  });
  return {sync,calls,states};
}
test("automatically imports each confirmed receipt once and does not create payments",async()=>{
  const {sync,calls,states}=fixture();
  await sync.run(args);await sync.run(args);
  assert.deepEqual(calls,[["connect",{automatic:true}],["import","confirmed-a"],["import","confirmed-b"]]);
  assert.deepEqual(states.map(s=>s[0]),["syncing","synced"]);
});
test("no wallet probing without an available substrate; manual fallback remains possible",async()=>{
  const {sync,calls}=fixture();
  await sync.run({...args,available:false});assert.equal(calls.length,0);
  await sync.run({...args,available:false,manual:true});
  assert.deepEqual(calls[0],["connect",{automatic:false}]);
});
test("hidden, embedded, public or busy page gate prevents even manual connection",async()=>{
  const {sync,calls}=fixture();
  await sync.run({...args,allowed:false,manual:true});assert.equal(calls.length,0);
  await sync.run({...args,identity:null});assert.equal(calls.length,0);
  await sync.run({...args,jobs:[]});assert.equal(calls.length,0);
});
test("wrong wallet pauses before importing; polling never repeats prompts",async()=>{
  let connections=0,imports=0;
  const {sync,states}=fixture({connect:async()=>{connections++;return {wallet:{},identity:"other"};},
    importReceipt:async()=>{imports++;}});
  await sync.run(args);await sync.run(args);
  assert.equal(connections,1);assert.equal(imports,0);
  assert.match(states.at(-1)[1].message,/registered/);
});
test("declined or failed import stops the batch and requires explicit retry",async()=>{
  let fail=true;const imported=[];
  const {sync}=fixture({importReceipt:async(_,job)=>{
    if(fail&&job.key==="confirmed-b")throw Error("Permission declined");
    imported.push(job.key);
  }});
  await sync.run(args);await sync.run(args);
  assert.deepEqual(imported,["confirmed-a"]);assert.equal(sync.paused,true);
  fail=false;await sync.run({...args,manual:true});
  assert.deepEqual(imported,["confirmed-a","confirmed-b"]);
  assert.equal(sync.paused,false);
});
test("automatic sync and manual double click cannot overlap",async()=>{
  let release;const gate=new Promise(r=>{release=r;});
  const {sync,calls}=fixture({connect:async()=>{await gate;return {wallet:{},identity:"driver"};}});
  const first=sync.run(args);
  await sync.run({...args,manual:true});
  release();await first;assert.equal(calls.length,2);
});
test("newly confirmed credit syncs without repeating old successful imports",async()=>{
  const {sync,calls}=fixture();await sync.run(args);
  await sync.run({...args,jobs:[...jobs,{key:"confirmed-c"}]});
  assert.deepEqual(calls.filter(c=>c[0]==="import").map(c=>c[1]),["confirmed-a","confirmed-b","confirmed-c"]);
});
test("reporting failure preserves accepted-import flag for accurate retry wording",async()=>{
  const error=Object.assign(Error("Reporting pending"),{walletAccepted:true});
  const {sync,states}=fixture({importReceipt:async()=>{throw error;}});
  await sync.run(args);
  assert.equal(states.at(-1)[0],"paused");assert.equal(states.at(-1)[1].walletAccepted,true);
});
