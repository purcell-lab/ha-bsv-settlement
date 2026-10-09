// Test-only fictional identity: official SDK request signature consumed by Python HTTP tests.
import {PrivateKey,ProtoWallet} from "@bsv/sdk";
import {signPortalRequest} from "./portal-model.js";
let input="";
for await(const chunk of process.stdin)input+=chunk;
const {grant,origin,request}=JSON.parse(input);
console.log(JSON.stringify(await signPortalRequest(new ProtoWallet(new PrivateKey(19)),grant,origin,request)));
