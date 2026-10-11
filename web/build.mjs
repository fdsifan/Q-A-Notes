import * as esbuild from "esbuild";
import { readFileSync, mkdirSync, writeFileSync } from "node:fs";
const result=await esbuild.build({
  entryPoints:["panel.mjs"],bundle:true,write:false,format:"esm",target:"es2022",minify:true,
  legalComments:"eof",platform:"browser"
});
const script=result.outputFiles[0].text.replace(/<\/script/gi,"<\\/script");
// Embed math fonts so the installed panel has no CDN or network dependency.
let mathCss=readFileSync("node_modules/katex/dist/katex.min.css","utf8");
mathCss=mathCss.replace(/src:([^;]+);/g,(all,sources)=>{
  const font=sources.match(/url\(([^)]+\.woff2)\)format\("woff2"\)/);
  return font?`src:url(data:font/woff2;base64,${readFileSync("node_modules/katex/dist/"+font[1].replace(/["']/g,"")).toString("base64")}) format("woff2");`:all;
});
const html=readFileSync("panel.html","utf8").replace("/*APP_BUNDLE*/",()=>script)
  .replace("</style>",()=>mathCss+"</style>")
  .replace("<script>","<script type=\"module\">");
mkdirSync("dist",{recursive:true});writeFileSync("dist/panel.html",html);
console.log("Built panel:",Buffer.byteLength(html),"bytes");
