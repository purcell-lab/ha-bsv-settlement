import test from "node:test";
import assert from "node:assert/strict";
import {inspectOutgoingAction} from "./outgoing-evidence.js";

const target={identity:"02"+"ab".repeat(32),budgetId:"11111111-2222-4333-8444-555555555555",txid:"cd".repeat(32)};
const label="ev-session:"+target.budgetId;
function fixture(status="nosend"){
  const calls=[];
  const wallet={
    isAuthenticated:async()=>({authenticated:true}),
    getPublicKey:async args=>{assert.equal(args.seekPermission,false);return {publicKey:target.identity};},
    getNetwork:async()=>({network:"mainnet"}),
    listActions:async args=>{calls.push(args);return {totalActions:1,actions:[
      {txid:target.txid,status,isOutgoing:true,labels:[label],description:"private description",
        inputs:[{unlockingScript:"private script"}]}]};}
  };
  for(const name of ["createAction","signAction","abortAction","internalizeAction","createSignature"])
    wallet[name]=()=>assert.fail("No wallet mutation: "+name);
  return {wallet,calls};
}
for(const status of ["completed","unprocessed","sending","unproven","unsigned","nosend","nonfinal","failed"])
  test("observes "+status+" without implying provider proof, repair or receipt acceptance",async()=>{
    const {wallet,calls}=fixture(status),result=await inspectOutgoingAction(wallet,target);
    assert.equal(result.wallet_status,status);
    assert.equal(result.evidence,"wallet_reported");
    assert.equal(result.status_repaired,false);
    assert.equal(result.provider_checked,false);
    assert.equal(result.retry_authorised,false);
    assert.equal(result.receipt_acceptance,"not_assessed");
    assert.equal(calls.length,1);
    assert.deepEqual(calls[0].labels,[label]);
    assert.equal(calls[0].seekPermission,false);
    assert.equal(calls[0].limit,50);
    assert.doesNotMatch(JSON.stringify(result),/private description|private script/);
  });
test("wrong or changing wallet fails closed",async()=>{
  let f=fixture();f.wallet.getPublicKey=async()=>({publicKey:"03"+"bb".repeat(32)});
  assert.equal((await inspectOutgoingAction(f.wallet,target)).wallet_status,"unknown");
  assert.equal(f.calls.length,0);
  f=fixture();let n=0;f.wallet.getNetwork=async()=>({network:++n===1?"mainnet":"testnet"});
  assert.equal((await inspectOutgoingAction(f.wallet,target)).reason,"wallet_changed_during_inspection");
});
test("missing authentication or capability never triggers a connection prompt",async()=>{
  let f=fixture();f.wallet.isAuthenticated=async()=>({authenticated:false});
  assert.equal((await inspectOutgoingAction(f.wallet,target)).wallet_status,"unknown");
  assert.equal(f.calls.length,0);
  delete f.wallet.listActions;
  assert.equal((await inspectOutgoingAction(f.wallet,target)).reason,"inspection_unsupported");
});
test("no query match or denied access is not proof of no payment",async()=>{
  const f=fixture();f.wallet.listActions=async()=>({totalActions:100,actions:[]});
  assert.equal((await inspectOutgoingAction(f.wallet,target)).reason,"not_observed_in_bounded_query");
  f.wallet.listActions=async()=>{throw Error("secret-wallet-data");};
  const result=await inspectOutgoingAction(f.wallet,target);
  assert.equal(result.reason,"wallet_inspection_unavailable");
  assert.equal(result.retry_authorised,false);
  assert.doesNotMatch(JSON.stringify(result),/secret/);
});
test("duplicate, wrong-direction, missing-label and malformed results are not accepted",async()=>{
  const row={txid:target.txid,status:"completed",isOutgoing:true,labels:[label]};
  for(const response of [
    {totalActions:2,actions:[row,row]},
    {totalActions:1,actions:[{...row,isOutgoing:false}]},
    {totalActions:1,actions:[{...row,labels:[]}]},
    {totalActions:1,actions:[{...row,status:"invented"}]},
    {totalActions:0,actions:[row]},
    {totalActions:51,actions:Array(51).fill(row)},
    {totalActions:"1",actions:[row]}
  ]){
    const {wallet}=fixture();wallet.listActions=async()=>response;
    assert.equal((await inspectOutgoingAction(wallet,target)).wallet_status,"unknown");
  }
});
test("invalid target has no wallet calls",async()=>{
  for(const patch of [{identity:"bad"},{budgetId:"https://private"},{txid:"bad"}]){
    const {wallet,calls}=fixture();
    assert.equal((await inspectOutgoingAction(wallet,{...target,...patch})).reason,"invalid_target");
    assert.equal(calls.length,0);
  }
});
