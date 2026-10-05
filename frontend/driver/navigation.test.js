import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {driverActions} from "./navigation.js";
test("both driver modes retain guarded actions without a six-button feature menu",()=>{
 assert.deepEqual(driverActions.map(([id])=>id),["connect","pair","approve","refresh","sync","signout","save","report","register"]);
 assert.equal(driverActions.find(([id])=>id==="approve")[1],"Authorise EV charging budget");
 const portal=readFileSync(new URL("./portal.js",import.meta.url),"utf8");
 const session=readFileSync(new URL("./app.js",import.meta.url),"utf8");
 assert.match(portal,/mountDriverToolbar\("portal"\)/);
 assert.match(session,/mountDriverToolbar\("session"\)/);
 assert.match(portal,/approve:\{enabled:false/);
 assert.match(session,/signout:\{enabled:false/);
});
test("QR value panels are wired for private, registration and pairing codes",()=>{
 const session=readFileSync(new URL("./app.js",import.meta.url),"utf8");
 for(const label of ["Private session URL","Registration URL","Pairing URI"])
   assert.ok(session.includes(`"${label}"`));
 const navigation=readFileSync(new URL("./navigation.js",import.meta.url),"utf8");
 assert.match(navigation,/writeText\(field.value\)/);
 assert.match(navigation,/panel.hidden=!value/);
 assert.match(navigation,/field.readOnly=true/);
});
