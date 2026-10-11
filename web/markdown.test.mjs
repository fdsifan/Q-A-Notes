import test from "node:test";
import assert from "node:assert/strict";
import { Window } from "happy-dom";
const window=new Window();
window.document.write("<!doctype html><html><body></body></html>");
// Happy DOM does not expose document.compatMode; browser documents with this
// doctype use standards mode, which KaTeX requires.
Object.defineProperty(window.document,"compatMode",{value:"CSS1Compat"});
Object.assign(globalThis,{document:window.document,NodeFilter:window.NodeFilter});
const {renderMarkdown,sourceSelection}=await import("./markdown.mjs");
const {renderImage,openImagePreview}=await import("./images.mjs");
function textNode(root,value,last=false){
  const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT),hits=[];
  for(let node;(node=walker.nextNode());)if(node.data.includes(value))hits.push(node);
  assert.ok(hits.length,"Visible text exists: "+value);return last?hits.at(-1):hits[0];
}
function select(root,raw,from,to=from,last=false){
  const first=textNode(root,from,last),end=textNode(root,to,last),range=document.createRange();
  range.setStart(first,first.data.indexOf(from));range.setEnd(end,end.data.indexOf(to)+to.length);
  return sourceSelection(root,raw,range);
}
test("the user's example has three paragraphs and semantic bold text",()=>{
  const raw="**目前确实无法把笔记标记直接放在 ChatGPT 对应回答的位置。**\n\n插件能保存来源关联。\n\n现有版本**还没有满足这个核心需求**。";
  const root=renderMarkdown(raw);
  assert.equal(root.querySelectorAll("p").length,3);
  assert.equal(root.querySelectorAll("strong").length,2);
  assert.ok(!root.textContent.includes("**"));
});
test("a repeated excerpt after an emoji maps to the selected occurrence",()=>{
  const raw="🙂 **重复片段**，还有 [重复片段](https://example.com)。";
  const root=renderMarkdown(raw),selection=select(root,raw,"重复片段","重复片段",true);
  assert.equal(selection.start,[...raw.slice(0,raw.lastIndexOf("重复片段"))].length);
  assert.equal(selection.source,"重复片段");
  const clip=renderMarkdown(raw,{clip:[selection.start,selection.end]});
  assert.equal(clip.textContent,"重复片段");assert.equal(clip.querySelector("a").textContent,"重复片段");
});
test("selection across formatting and paragraphs retains exact raw source",()=>{
  const raw="🙂 开始 **加粗文字**。\n\n第二段 *末尾文字*。";
  const selection=select(renderMarkdown(raw),raw,"加粗文字","末尾文字");
  assert.equal(selection.source,"加粗文字**。\n\n第二段 *末尾文字");
  const root=renderMarkdown(raw,{clip:[selection.start,selection.end],highlight:[selection.start,selection.end]});
  assert.equal(root.querySelector("strong").textContent,"加粗文字");
  assert.equal(root.querySelector("em").textContent,"末尾文字");
  assert.equal(root.querySelectorAll("p").length,2);
  assert.ok([...root.querySelectorAll("mark")].every(node=>!node.textContent.includes("**")));
});
test("decoded entities and escaped Markdown retain their original bounds",()=>{
  const raw="&amp; 与 \\*文字\\*，&#x1F600;。";
  const root=renderMarkdown(raw);
  assert.equal(root.textContent,"& 与 *文字*，😀。");
  assert.equal(select(root,raw,"&").source,"&amp;");
  assert.equal(select(root,raw,"😀").source,"&#x1F600;");
});
test("code keeps literal entities and syntax-highlighted selection maps correctly",()=>{
  const raw="```javascript\nconst literal = '&amp;';\nconsole.log(literal);\n```";
  const root=renderMarkdown(raw);
  assert.equal(root.querySelector("pre").textContent,"const literal = '&amp;';\nconsole.log(literal);");
  assert.ok(root.querySelector(".hljs-keyword"));
  const selection=select(root,raw,"&amp;");
  assert.equal(selection.source,"&amp;");
  assert.equal(raw.indexOf(selection.source),selection.start);
});
test("nested lists and table cells map after stripped structural markers",()=>{
  const raw="- 一级\n  - **二级内容**\n\n| 名称 | 内容 |\n| --- | --- |\n| 测试 | 精确位置 |";
  const root=renderMarkdown(raw);
  assert.equal(root.querySelectorAll("ul").length,2);assert.equal(root.querySelectorAll("td").length,2);
  assert.equal(select(root,raw,"二级内容").source,"二级内容");
  assert.equal(select(root,raw,"精确位置").start,[...raw.slice(0,raw.indexOf("精确位置"))].length);
});
test("raw HTML and dangerous links remain inactive",()=>{
  const root=renderMarkdown('<script>window.stolen=true</script>\n\n[危险](javascript:alert%281%29)\n\n[链接](https://example.com)');
  assert.equal(root.querySelectorAll("script").length,0);
  assert.equal(root.querySelectorAll("a").length,1);
  assert.equal(root.querySelector("a").getAttribute("rel"),"noopener noreferrer");
});
test("formulas render locally and a selected formula retains its source",()=>{
  const raw="公式 $x^2 + y^2$。\n\n$$\n\\frac{1}{2}\n$$";
  const root=renderMarkdown(raw);
  assert.equal(root.querySelectorAll(".katex").length,2);
  const node=root.querySelector(".formula"),range=document.createRange();range.selectNodeContents(node);
  assert.equal(sourceSelection(root,raw,range).source,"$x^2 + y^2$");
});

test("saved Markdown images render without affecting the following source position",()=>{
  const url="C:/Users/example/截图.png",data="data:image/png;base64,iVBORw0KGgo=",raw="![截图]("+url+")\n\n🙂 **后面的文字**";
  const root=renderMarkdown(raw,{images:[{source_url:url,name:"截图.png",status:"saved",data_url:data}]});
  assert.equal(root.querySelector("img").src,data);
  assert.equal(select(root,raw,"后面的文字").start,[...raw.slice(0,raw.indexOf("后面的文字"))].length);
});

test("image rendering rejects network requests and active image formats",()=>{
  for(const data_url of ["https://example.com/image.png","file:///C:/private.png","data:image/svg+xml;base64,PHN2Zz4=","javascript:alert(1)"]){
    assert.equal(renderImage({name:"图片",data_url}).querySelector("img"),null);
  }
  const missing=renderMarkdown("![已失效图片](https://example.com/image.png)");
  assert.equal(missing.querySelector("img"),null);assert.ok(missing.textContent.includes("原图已不可用"));
});

test("image preview opens in the same panel and closes with focus restored",()=>{
  const image={name:"截图.png",status:"saved",data_url:"data:image/png;base64,iVBORw0KGgo="};
  const root=renderImage(image,openImagePreview);document.body.append(root);
  const button=root.querySelector("button");button.focus();button.click();
  const preview=document.querySelector('[role="dialog"]');assert.ok(preview);assert.equal(preview.querySelector("img").src,image.data_url);
  preview.querySelector("button").click();assert.equal(document.querySelector('[role="dialog"]'),null);
  assert.equal(document.activeElement,button);root.remove();
});
