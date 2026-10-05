import {build} from "esbuild";
import {mkdir,copyFile,readFile,writeFile} from "node:fs/promises";
const root="../../preview/simple-driver";
for(const [entry,dir,define] of [
  ["portal-preview.js",root,{"window.top":"window"}],
  ["recovery-preview.js",`${root}/session`,{
    "location.hash":JSON.stringify("#budget=11111111-2222-4333-8444-555555555555&token="+"x".repeat(43)),
    "location.origin":JSON.stringify("https://charging.example.com"),
    "location.href":JSON.stringify("https://charging.example.com/bsv_settlement/driver/index.html#budget=11111111-2222-4333-8444-555555555555&token="+"x".repeat(43)),
    "window.top":"window"
  }]
]){
  await mkdir(dir,{recursive:true});
  await build({entryPoints:[entry],bundle:true,format:"esm",minify:true,outfile:`${dir}/app.bundle.js`,define});
  await copyFile("style.css",`${dir}/style.css`);
  const html=(await readFile("index.html","utf8")).replace("<body>",
    '<body><div role="note" style="padding:8px 16px;text-align:center;font-size:13px;background:var(--soft);color:var(--ink)">Design preview · Fictional data · No real payments</div>');
  await writeFile(`${dir}/index.html`,html);
  for(const name of ["dm-sans-latin-wght-normal.woff2","DM-SANS-LICENSE.txt"])
    await copyFile(`../../custom_components/bsv_settlement/frontend/driver/${name}`,`${dir}/${name}`);
}
const operator=`${root}/operator`;
await mkdir(operator,{recursive:true});
for(const name of ["index.html","preview.js","operator-card.js","budget-card.js","session-review-card.js",
  "dm-sans-latin-wght-normal.woff2","DM-SANS-LICENSE.txt"])
  await copyFile(`../../preview/${name}`,`${operator}/${name}`);
await build({entryPoints:["../bsv-operator-card.js"],bundle:true,format:"esm",minify:true,
  outfile:`${operator}/operator-card.js`,define:{localStorage:"window.previewAdjustmentStorage"}});
const operatorHtml=await readFile(`${operator}/index.html`,"utf8");
await writeFile(`${operator}/index.html`,operatorHtml.replace("./driver-preview.html","../session/index.html?scenario=active"));
