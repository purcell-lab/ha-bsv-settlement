// Test-only: run the shipped S3 validator and signer on a Python-issued challenge.
import fs from "node:fs";
import {PrivateKey,ProtoWallet} from "@bsv/sdk";
import {checkMonthlyChallenge,signMonthly} from "./monthly-wallet.js";
const {challenge,hostname,stations,secret}=JSON.parse(fs.readFileSync(0,"utf8"));
const wallet=new ProtoWallet(PrivateKey.fromString(secret,"hex"));
const identity=(await wallet.getPublicKey({identityKey:true})).publicKey;
checkMonthlyChallenge(challenge,{hostname,identity,stations});
process.stdout.write(JSON.stringify(await signMonthly(wallet,{payload:challenge.payload,keyID:challenge.keyID,identity,description:"fixture"})));
