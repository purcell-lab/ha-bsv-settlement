import test from "node:test";
import assert from "node:assert/strict";
import {walletConnectionUnavailable,walletConnectionHelp} from "./wallet-connection.js";
import {nextAction} from "./journey.js";
test("missing wallet and web2 errors explain connection recovery, not an operator approval block",()=>{
 for(const message of ["No wallet available over any communication substrate. Install a BSV wallet today!",
   "Wallet APIs are unavailable in web2 mode","Wallet connection timed out."]){
  assert.equal(walletConnectionUnavailable(Error(message)),true);
 }
 assert.equal(walletConnectionUnavailable(Error("User rejected signature")),false);
 assert.match(walletConnectionHelp(),/budget is not approved yet/);
 assert.match(walletConnectionHelp(),/open it inside BSV Browser/);
 assert.doesNotMatch(walletConnectionHelp(true),/pair first/);
});
test("wallet recovery becomes primary without overriding pending signed receipt",()=>{
 assert.equal(nextAction({approve:{enabled:true,primary:false},connect:{enabled:true}}),"connect");
 assert.equal(nextAction({save:{enabled:true},connect:{enabled:true}}),"save");
});
