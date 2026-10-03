import test from "node:test";
import assert from "node:assert/strict";
import {privateSessionUrl} from "./private-link.js";
const origin="https://charger.example.test";
const path="/bsv_settlement/driver/index.html";
const fragment="#budget=11111111-2222-4333-8444-555555555555&token="+"a".repeat(43);
test("private QR preserves only the exact same-origin fragment capability",()=>{
 const url=origin+path+fragment;
 assert.equal(privateSessionUrl(url,origin),url);
 for(const bad of [origin+path,"https://other.example.test"+path+fragment,
   origin+"/other"+fragment,origin+path+"?token=secret"+fragment,
   origin+path+fragment+"&budget=duplicate",origin+path+fragment.slice(0,-1),
   "javascript:alert(1)","https://user:password@charger.example.test"+path+fragment])
   assert.equal(privateSessionUrl(bad,origin),null);
});
