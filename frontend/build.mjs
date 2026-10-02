import { build } from "esbuild";
import { copyFile } from "node:fs/promises";
const target="../custom_components/bsv_settlement/frontend";
for(const [input,output] of [
  ["bsv-operator-card.js","operator-card.js"],
  ["bsv-budget-card.js","budget-card.js"],
  ["bsv-session-review-card.js","session-review-card.js"],
]){
  await build({entryPoints:[input],bundle:true,format:"esm",minify:true,legalComments:"eof",outfile:`${target}/${output}`});
  await copyFile(`${target}/${output}`,`../preview/${output}`);
}
await copyFile(`${target}/session-review-card.js`,"bsv-session-review-card.bundle.js");
await copyFile(`${target}/budget-card.js`,"bsv-budget-card.bundle.js");
for(const name of ["index.html","style.css"])await copyFile(`driver/${name}`,`${target}/driver/${name}`);
await copyFile("node_modules/@fontsource-variable/dm-sans/files/dm-sans-latin-wght-normal.woff2",`${target}/driver/dm-sans-latin-wght-normal.woff2`);
await copyFile("node_modules/@fontsource-variable/dm-sans/LICENSE",`${target}/driver/DM-SANS-LICENSE.txt`);
await copyFile(`${target}/driver/dm-sans-latin-wght-normal.woff2`,"../preview/dm-sans-latin-wght-normal.woff2");
await copyFile(`${target}/driver/DM-SANS-LICENSE.txt`,"../preview/DM-SANS-LICENSE.txt");
