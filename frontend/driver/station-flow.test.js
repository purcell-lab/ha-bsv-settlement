import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {stationFlow} from "./station-flow.js";

test("weekly public copy describes defaults, never personal authority or recurring billing", () => {
  const result=stationFlow({station:{monthly_enabled:false},loaded:true});
  const text=JSON.stringify(result);
  assert.equal(result.mode,"weekly");
  for(const pattern of [/1,000 sat total/,/up to seven days/,/including network fees/,
    /per completed session/,/not a weekly bill/,/do not refill/,
    /No automatic renewal/,/fresh signature/,/Sign-in alone does not approve/,
    /exact limit/,/Keep the session page and wallet available/])assert.match(text,pattern);
  assert.doesNotMatch(text,/30,000|calendar month|Monthly|monthly|budget approved|funds reserved/);
  assert.equal(result.rows[0][0],"Default invitation");
});
test("loading, unavailable and malformed station replies never advertise monthly or weekly authority", () => {
  for(const result of [stationFlow(),stationFlow({unavailable:true}),
    stationFlow({station:{monthly_enabled:"true"},loaded:true}),
    stationFlow({station:{monthly_enabled:null},loaded:true})]){
    assert.ok(["loading","unavailable"].includes(result.mode));
    assert.equal(result.rows.length,0);
    assert.doesNotMatch(JSON.stringify(result),/30,000|1,000|Renew|authorise monthly/);
  }
});
test("explicitly enabled experiment keeps its existing distinct copy", () => {
  const result=stationFlow({station:{monthly_enabled:true},loaded:true});
  assert.equal(result.mode,"monthly");
  assert.match(result.intro,/authorise monthly/);
  assert.doesNotMatch(JSON.stringify(result),/seven days|1,000/);
});
test("personal limits and signatures are not read or rewritten by public wording", () => {
  const station={monthly_enabled:false,max_total_sats:321,signature:"PRIVATE"};
  const before=structuredClone(station);
  assert.equal(JSON.stringify(stationFlow({station,loaded:true})).includes("PRIVATE"),false);
  assert.deepEqual(station,before);
});
test("portal hides dormant monthly controls and gates default copy on public station status", () => {
  const source=readFileSync(new URL("./portal.js",import.meta.url),"utf8");
  const template=source.split('document.querySelector("main").innerHTML=`')[1].split('`;\nconst $')[0];
  assert.match(template,/<h2 id="monthly-offer-title">Charging approval<\/h2>/);
  assert.match(template,/id="monthly-authorise"[^>]*disabled hidden/);
  assert.doesNotMatch(template.split('id="portal-intro"')[1].split("</p>")[0],/monthly/);
  for(const guarded of [
    '$("monthly-authorise").hidden=!enabled',
    '$("allowance").hidden=!enabled||!status?.allowance',
    '$("monthly-cancel-area").hidden=!enabled||',
    '$("perm-authority-label").textContent=enabled?',
    'stationFlow({station,loaded:stationLoaded,unavailable:stationUnavailable})',
  ]) assert.ok(source.includes(guarded),guarded);
});
