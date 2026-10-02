// Isolated browser QA only. Not included in the production bundle.
import {ProtoWallet,PrivateKey} from "@bsv/sdk";
const wallet=new ProtoWallet(new PrivateKey(2));
wallet.getVersion=async()=>({version:"qa-wallet-1.0.0"});
wallet.getNetwork=async()=>({network:"mainnet"});
wallet.isAuthenticated=async()=>({authenticated:true});
wallet.internalizeAction=async()=>({accepted:true});
window.CWI=wallet;
