import { build } from "esbuild";
import { copyFile, mkdir } from "node:fs/promises";
const output="../../preview/recovery";
await mkdir(output,{recursive:true});
await build({entryPoints:["recovery-preview.js"],bundle:true,format:"esm",minify:true,
  outfile:`${output}/app.bundle.js`,define:{
    "location.hash":JSON.stringify("#budget=11111111-2222-4333-8444-555555555555&token=fictional"),
    "window.top":"window",
  }});
for(const file of ["index.html","style.css"])await copyFile(file,`${output}/${file}`);
for(const file of ["dm-sans-latin-wght-normal.woff2","DM-SANS-LICENSE.txt"])
  await copyFile(`../../custom_components/bsv_settlement/frontend/driver/${file}`,`${output}/${file}`);
