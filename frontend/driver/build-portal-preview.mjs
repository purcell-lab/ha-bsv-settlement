import {build} from "esbuild";
import {copyFile,mkdir} from "node:fs/promises";
const output="../../preview/portal";
await mkdir(output,{recursive:true});
await build({entryPoints:["portal-preview.js"],bundle:true,format:"esm",minify:true,
  outfile:`${output}/app.bundle.js`,define:{"window.top":"window"}});
for(const file of ["index.html","style.css"])await copyFile(file,`${output}/${file}`);
for(const file of ["dm-sans-latin-wght-normal.woff2","DM-SANS-LICENSE.txt"])
  await copyFile(`../../custom_components/bsv_settlement/frontend/driver/${file}`,`${output}/${file}`);
