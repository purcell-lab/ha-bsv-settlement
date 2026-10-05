import test from "node:test";
import assert from "node:assert/strict";
import {pendingCreditJobs} from "./portal-credit-jobs.js";
import {ReceiptSync} from "./receipt-sync.js";
const credit=(id,patch={})=>({id,txid:"a".repeat(64),direction:"operator_to_driver",state:"provider_confirmed",...patch});
const session=(id,transactions=[])=>({session_id:id,transactions});
test("finds awaiting credits beyond visible history; excludes debits, unconfirmed and accepted receipts",async()=>{
 const rows=Array.from({length:29},(_,i)=>session(String(i)));
 rows[0].transactions=[credit("debit",{direction:"driver_to_operator"}),credit("pending",{state:"provider_unconfirmed"})];
 rows[1].transactions=[credit("accepted",{wallet_receipt_status:"wallet_reported_accepted",wallet_imported_at:"2026-10-05T00:00:00Z"})];
 rows[26].transactions=[credit("old-credit")];
 const offsets=[];
 const jobs=await pendingCreditJobs(async offset=>{
  offsets.push(offset);return {identity:"driver",total:rows.length,sessions:rows.slice(offset,offset+25)};
 },"driver");
 assert.deepEqual(offsets,[0,25]);assert.deepEqual(jobs.map(j=>j.row.id),["old-credit"]);
});
test("identical transaction rows do not import twice",async()=>{
 const jobs=await pendingCreditJobs(async()=>({identity:"driver",total:2,sessions:[
  session("one",[credit("credit")]),session("two",[credit("credit")])]}),"driver");
 assert.equal(jobs.length,1);
});
test("identity changes, expiry and malformed pagination fail before wallet use",async()=>{
 for(const page of [{identity:"other",total:0,sessions:[]},{identity:"driver",total:1,sessions:[]},
   {identity:"driver",total:NaN,sessions:[]}])
  await assert.rejects(pendingCreditJobs(async()=>page,"driver"));
 await assert.rejects(pendingCreditJobs(async()=>assert.fail("No fetch"),"driver",()=>{throw Error("Expired");}));
});
test("background batches receive newly confirmed credits exactly once, no session selection",async()=>{
 let rows=[session("first",[credit("first")])],imports=[];
 const sync=new ReceiptSync({connect:async()=>({wallet:{},identity:"driver"}),
  importReceipt:async(_,job)=>imports.push(job.row.id)});
 for(let i=0;i<3;i++){
  if(i===2)rows.push(session("older",[credit("older")]));
  const jobs=await pendingCreditJobs(async()=>({identity:"driver",total:rows.length,sessions:rows}),"driver");
  await sync.run({jobs,identity:"driver",available:true});
 }
 assert.deepEqual(imports,["first","older"]);
});
test("absent wallet does not probe transports or import, even with an authenticated history",async()=>{
 const jobs=await pendingCreditJobs(async()=>({identity:"driver",total:1,sessions:[session("one",[credit("one")])]}),"driver");
 const sync=new ReceiptSync({connect:()=>assert.fail("No discovery"),importReceipt:()=>assert.fail("No import")});
 await sync.run({jobs,identity:"driver",available:false});
});
