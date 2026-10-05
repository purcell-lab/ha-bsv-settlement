// History cookies never establish a live wallet connection.
export function walletStatus(state,paused=false){
  if(state==="checking")return {title:"Checking wallet connection",note:"Approve any wallet connection prompt.",action:"Connecting…",disabled:true};
  if(state==="connected")return {title:"Wallet connected",note:paused
    ?"Receiving credits is paused. Retry when you are ready."
    :"Confirmed credits are received automatically. No spending permission is granted.",
    action:paused?"Retry receiving credits":"Reconnect wallet",disabled:false};
  return {title:state==="unavailable"?"Wallet not connected":"Wallet connection not verified",
    note:"Your history is signed in, but receiving credits needs a connection to the same wallet.",
    action:"Reconnect wallet",disabled:false};
}

export async function verifyReceivingWallet(wallet,identity,assertActive=()=>{},timeoutMs=20000){
  let timer,cancelled=false;
  const active=()=>{
    if(cancelled)throw Error("Wallet connection check ended. Reconnect when ready.");
    assertActive();
  };
  try{
    await Promise.race([(async()=>{
      active();
      const actual=(await wallet.getPublicKey({identityKey:true})).publicKey;
      active();
      if(actual!==identity)throw Error("Reconnect the same wallet used for this charging history.");
      const result=await wallet.getNetwork();
      active();
      if(result.network!=="mainnet")throw Error("Use your BSV mainnet wallet to receive these credits.");
    })(),new Promise((_,reject)=>{timer=setTimeout(()=>{
      cancelled=true;
      reject(Error("Wallet connection timed out. Open this page in wallet-enabled BSV Browser or use Connect BSV Browser."));
    },timeoutMs);})]);
    active();
    return wallet;
  }finally{cancelled=true;clearTimeout(timer);}
}
