import { build } from "esbuild";
import { readFile, writeFile, copyFile, mkdir } from "node:fs/promises";
const output="../../preview/pairing";
await mkdir(output,{recursive:true});
await build({entryPoints:["pairing-preview.js"],bundle:true,format:"esm",minify:true,
  outfile:`${output}/app.bundle.js`,define:{
    "location.origin":JSON.stringify("https://charging.example.com"),
    "location.hash":JSON.stringify("#budget=11111111-2222-4333-8444-555555555555&token=fictional"),
    "window.top":"window",
  }});
await copyFile("index.html",`${output}/index.html`);
await copyFile("style.css",`${output}/style.css`);
await copyFile("../../custom_components/bsv_settlement/frontend/driver/dm-sans-latin-wght-normal.woff2",
  `${output}/dm-sans-latin-wght-normal.woff2`);
await copyFile("../../custom_components/bsv_settlement/frontend/driver/DM-SANS-LICENSE.txt",
  `${output}/DM-SANS-LICENSE.txt`);
const css=await readFile(`${output}/style.css`,"utf8");
await writeFile(`${output}/style.css`,css+
  "\n#preview-banner{border:2px solid var(--accent)}#preview-mode{font:inherit;color:var(--ink);background:var(--paper);width:100%;padding:10px;border:1px solid var(--line);border-radius:6px}\n");
