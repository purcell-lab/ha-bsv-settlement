// Offline wallet capability fixtures for tests. NOT compatibility evidence:
// no fixture names or reproduces a real Metanet or BSV Browser version.
// Real wallet/version behaviour must be recorded separately (S5 native evidence).
import {PrivateKey,ProtoWallet} from "@bsv/sdk";

export const fixtureEvidence="offline_fixture";

/** A ProtoWallet with configurable gaps; counts prompts that would reach a user. */
export function fixtureWallet(profile="full",{secret=7,unlockAfter=0}={}){
  const proto=new ProtoWallet(new PrivateKey(secret));
  const calls={getPublicKey:0,createSignature:0,internalizeAction:0,waitForAuthentication:0};
  let unlocked=unlockAfter===0;
  const wallet={
    profile,evidence:fixtureEvidence,calls,
    async isAuthenticated(){return {authenticated:unlocked};},
    async waitForAuthentication(){
      calls.waitForAuthentication++;
      if(profile==="locked_forever")return new Promise(()=>{});
      await new Promise(r=>setTimeout(r,unlockAfter));unlocked=true;return {authenticated:true};
    },
    async getPublicKey(args){calls.getPublicKey++;return proto.getPublicKey(args);},
    async getNetwork(){return {network:profile==="testnet"?"testnet":"mainnet"};},
    async createSignature(args){
      calls.createSignature++;
      if(profile==="declines")throw Error("User denied the signature request");
      return proto.createSignature(args);
    },
    async internalizeAction(){calls.internalizeAction++;return {accepted:true};},
  };
  if(profile==="missing_api")return {profile,evidence:fixtureEvidence,calls};
  if(profile==="sign_only"){delete wallet.internalizeAction;}
  return wallet;
}

/** Supported-method lists as a paired (relay) wallet would advertise them. */
export const pairedMethods={
  full:["getPublicKey","createSignature","getNetwork","internalizeAction","isAuthenticated","waitForAuthentication"],
  sign_only:["getPublicKey","createSignature","getNetwork"],
};
