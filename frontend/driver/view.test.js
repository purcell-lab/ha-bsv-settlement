import test from "node:test";
import assert from "node:assert/strict";
import {driverView} from "./view.js";
test("confirmed credit is not wallet acceptance until import succeeds",()=>{
 const args={hasInvitation:true,accepted:true,registered:true,credit:true,state:"provider_confirmed"};
 assert.equal(driverView(args).reconnect,"Receive credit in wallet");
 assert.equal(driverView(args).stage,"approved");
 assert.equal(driverView({...args,imported:true}).stage,"settled");
});
test("wrong or incomplete registration remains visible",()=>{
 assert.equal(driverView({hasInvitation:true,accepted:true}).reconnect,"Register receiving wallet");
});
test("unknown broadcast never offers another payment",()=>{
 const v=driverView({hasInvitation:true,accepted:true,registered:true,state:"broadcast_unknown"});
 assert.match(v.subtitle,/Do not send another/);
});
test("blocked settlement does not imply payment readiness",()=>{
 assert.equal(driverView({hasInvitation:true,accepted:true,registered:true,state:"collection_blocked"}).title,"Settlement needs attention");
});
test("held attempts and reviewed recovery are not normal automatic collection",()=>{
 const base={hasInvitation:true,accepted:true,registered:true,connected:true};
 assert.equal(driverView({...base,state:"wallet_attempt_reserved"}).title,"Collection needs review");
 assert.equal(driverView({...base,state:"wallet_attempt_reserved"}).reconnect,undefined);
 assert.equal(driverView({...base,state:"recovery_ready"}).reconnect,"Review and resume collection");
});
