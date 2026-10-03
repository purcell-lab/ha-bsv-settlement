// Test-only fictional identity: official SDK signature consumed by Python HTTP tests.
import {PrivateKey,ProtoWallet} from "@bsv/sdk";
import {signPortalLogin} from "./portal-model.js";
let input="";
for await(const chunk of process.stdin)input+=chunk;
const challenge=JSON.parse(input);
console.log(JSON.stringify(await signPortalLogin(
  new ProtoWallet(new PrivateKey(19)),challenge,JSON.parse(challenge.payload).origin)));
