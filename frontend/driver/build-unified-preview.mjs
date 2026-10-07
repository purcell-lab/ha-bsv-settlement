import {build} from "esbuild";
import {mkdir,copyFile} from "node:fs/promises";
const root="../../preview/unified-driver";
for(const [entry,folder,define] of [
  ["portal-preview.js",root,{"window.top":"window"}],
  ["recovery-preview.js",root+"/session",{
    "location.hash":JSON.stringify("#budget=11111111-2222-4333-8444-555555555555&token="+"x".repeat(43)),
    "location.origin":JSON.stringify("https://charging.example.com"),
    "location.href":JSON.stringify("https://charging.example.com/bsv_settlement/driver/index.html#budget=11111111-2222-4333-8444-555555555555&token="+"x".repeat(43)),
    "window.top":"window"
  }]
]){
  await mkdir(folder,{recursive:true});
  await build({entryPoints:[entry],bundle:true,format:"esm",minify:true,outfile:folder+"/app.bundle.js",define});
  for(const name of ["index.html","style.css"])await copyFile(name,folder+"/"+name);
  for(const name of ["dm-sans-latin-wght-normal.woff2","DM-SANS-LICENSE.txt"])
    await copyFile("../../custom_components/bsv_settlement/frontend/driver/"+name,folder+"/"+name);
}
