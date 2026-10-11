import { fromMarkdown } from "mdast-util-from-markdown";
import { gfm } from "micromark-extension-gfm";
import { gfmFromMarkdown } from "mdast-util-gfm";
import { math } from "micromark-extension-math";
import { mathFromMarkdown } from "mdast-util-math";
import { decodeString } from "micromark-util-decode-string";
import katex from "katex";
import hljs from "highlight.js/lib/common";
import {renderImage} from "./images.mjs";

const locations=new WeakMap();
const parse=text=>fromMarkdown(text,{extensions:[gfm(),math()],mdastExtensions:[gfmFromMarkdown(),mathFromMarkdown()]});
const offset=(raw,point)=>[...raw].slice(0,point).join("").length;
const safeUrl=url=>/^(https?:|mailto:|codex:)/i.test(url || "")?url:null;

// MDAST positions refer to UTF-16 source offsets. Keep a position per visible
// character so formatting, entities and emoji cannot shift an excerpt's source.
function positions(raw,value,start,end,literal=false){
  const starts=[],ends=[];
  let cursor=start,index=0;
  while(index<value.length){
    let decoded=null,size=0;
    if(!literal && raw[cursor]==="&"){
      const entity=raw.slice(cursor,end).match(/^&(?:#\d+|#x[\da-f]+|[a-z][\da-z]+);/i)?.[0];
      if(entity){const text=decodeString(entity);if(value.startsWith(text,index)){decoded=text;size=entity.length}}
    }
    if(!literal && decoded===null && raw[cursor]==="\\" && /[!"#$%&'()*+,\-./:;<=>?@[\]\\^_`{|}~]/.test(raw[cursor+1] || "")){
      if(value[index]===raw[cursor+1]){decoded=value[index];size=2}
    }
    if(decoded===null && /\s/.test(value[index]) && /\s/.test(raw[cursor] || "")){
      decoded=value[index];size=raw[cursor]==="\r" && raw[cursor+1]==="\n"?2:1;
    }
    if(decoded!==null){
      for(let n=0;n<decoded.length;n++){starts.push(cursor);ends.push(cursor+size)}
      cursor+=size;index+=decoded.length;continue;
    }
    const found=raw.indexOf(value[index],cursor);
    if(found<0 || found>=end)throw new Error("无法确认排版文字的原文位置。");
    starts.push(found);ends.push(found+1);cursor=found+1;index++;
  }
  return {starts,ends};
}

export function renderMarkdown(raw,{highlight=null,clip=null,onOpenLink=null,onOpenImage=null,images=[],compact=false}={}){
  raw=raw || "";
  const root=document.createElement("div");root.className="markdown";
  const tree=parse(raw),definitions=new Map();
  for(const node of tree.children)if(node.type==="definition")definitions.set(node.identifier,node);
  const bounds=range=>range && [offset(raw,range[0]),offset(raw,range[1])];
  const highlighted=bounds(highlight),clipped=bounds(clip);
  const appendText=(parent,value,start,end,literal=false)=>{
    if(!value)return;
    const map=positions(raw,value,start,end,literal);
    let first=0,last=value.length;
    if(clipped){
      while(first<last && map.ends[first]<=clipped[0])first++;
      while(last>first && map.starts[last-1]>=clipped[1])last--;
    }
    let begin=first;
    while(begin<last){
      const marked=highlighted && map.starts[begin]<highlighted[1] && map.ends[begin]>highlighted[0];
      let finish=begin+1;
      while(finish<last && !!(highlighted && map.starts[finish]<highlighted[1] && map.ends[finish]>highlighted[0])===!!marked)finish++;
      const text=document.createTextNode(value.slice(begin,finish));
      locations.set(text,{starts:map.starts.slice(begin,finish),ends:map.ends.slice(begin,finish)});
      if(marked){const mark=document.createElement("mark");mark.append(text);parent.append(mark)}else parent.append(text);
      begin=finish;
    }
  };
  const appendChildren=(parent,node)=>{for(const child of node.children || [])append(parent,child)};
  const append=(parent,node)=>{
    const start=node.position?.start.offset ?? 0,end=node.position?.end.offset ?? raw.length;
    if(clipped && (end<=clipped[0] || start>=clipped[1]))return;
    const tags={paragraph:"p",strong:"strong",emphasis:"em",delete:"del",blockquote:"blockquote",listItem:"li",tableRow:"tr"};
    if(node.type==="text"){appendText(parent,node.value,start,end);return}
    if(node.type==="definition")return;
    if(node.type==="html"){
      if(/^<br\s*\/?\s*>$/i.test(node.value))parent.append(document.createElement("br"));
      else appendText(parent,node.value,start,end,true);
      return;
    }
    if(node.type==="break" || node.type==="thematicBreak"){
      parent.append(document.createElement(node.type==="break"?"br":"hr"));return;
    }
    if(node.type==="link" || node.type==="linkReference"){
      const definition=node.type==="linkReference"?definitions.get(node.identifier):node;
      const url=safeUrl(definition?.url),link=document.createElement(url&&!compact?"a":"span");
      if(url&&!compact){
        link.href=url;link.target="_blank";link.rel="noopener noreferrer";
        if(onOpenLink)link.addEventListener("click",event=>{event.preventDefault();onOpenLink(url)});
      }
      if(definition?.title)link.title=definition.title;
      appendChildren(link,node);parent.append(link);return;
    }
    if(node.type==="image" || node.type==="imageReference"){
      const definition=node.type==="imageReference"?definitions.get(node.identifier):node;
      const saved=images.find(image=>image.source_url===definition?.url);
      if(compact){const label=document.createElement("span");label.textContent=node.alt || "图片";parent.append(label)}
      else parent.append(renderImage(saved || {name:node.alt || "图片",status:"missing"},onOpenImage));
      return;
    }
    if(node.type==="inlineMath" || node.type==="math"){
      const formula=document.createElement(node.type==="math"?"div":"span");formula.className="formula";
      try{katex.render(node.value,formula,{displayMode:node.type==="math",throwOnError:false,trust:false,output:"html"})}
      catch{formula.textContent=node.value}
      formula.title=node.value;
      const walker=document.createTreeWalker(formula,NodeFilter.SHOW_TEXT);
      for(let text; (text=walker.nextNode());)locations.set(text,{starts:Array(text.length).fill(start),ends:Array(text.length).fill(end)});
      if(highlighted && start<highlighted[1] && end>highlighted[0])formula.classList.add("formula-mark");
      parent.append(formula);return;
    }
    if(node.type==="inlineCode" || node.type==="code"){
      const code=document.createElement("code");
      let contentStart=start;
      const fragment=raw.slice(start,end);
      if(node.type==="inlineCode")contentStart+=fragment.match(/^`+/)?.[0].length || 0;
      else if(/^\s*(`{3,}|~{3,})/.test(fragment))contentStart+=fragment.indexOf("\n")+1;
      if(node.type==="inlineCode"){appendText(code,node.value,contentStart,end,true);parent.append(code);return}
      const block=document.createElement("div");block.className="code-block";
      const header=document.createElement("div");header.className="code-head";
      const language=document.createElement("span");language.textContent=node.lang || "代码";header.append(language);
      if(!compact){
        const copy=document.createElement("button");copy.textContent="复制代码";copy.type="button";
        copy.addEventListener("click",async()=>{
          try{await navigator.clipboard.writeText(node.value);copy.textContent="已复制"}catch{copy.textContent="请选择代码后复制"}
        });header.append(copy);
      }
      const pre=document.createElement("pre");pre.append(code);
      if(node.lang && hljs.getLanguage(node.lang)){
        code.innerHTML=hljs.highlight(node.value,{language:node.lang,ignoreIllegals:true}).value;
        const map=positions(raw,node.value,contentStart,end,true),walker=document.createTreeWalker(code,NodeFilter.SHOW_TEXT);
        const nodes=[];for(let text;(text=walker.nextNode());)nodes.push(text);
        let index=0;
        for(const text of nodes){
          const temporary=document.createElement("span"),length=text.length;
          let n=0;
          while(n<length){
            const visible=!clipped || (map.ends[index+n]>clipped[0] && map.starts[index+n]<clipped[1]);
            const marked=highlighted && map.starts[index+n]<highlighted[1] && map.ends[index+n]>highlighted[0];
            let next=n+1;
            while(next<length && !!(!clipped || (map.ends[index+next]>clipped[0] && map.starts[index+next]<clipped[1]))===visible &&
              !!(highlighted && map.starts[index+next]<highlighted[1] && map.ends[index+next]>highlighted[0])===!!marked)next++;
            if(visible){
              const part=document.createTextNode(text.data.slice(n,next));locations.set(part,{starts:map.starts.slice(index+n,index+next),ends:map.ends.slice(index+n,index+next)});
              if(marked){const mark=document.createElement("mark");mark.append(part);temporary.append(mark)}else temporary.append(part);
            }
            n=next;
          }
          text.replaceWith(...temporary.childNodes);index+=length;
        }
      }else appendText(code,node.value,contentStart,end,true);
      block.append(header,pre);parent.append(block);return;
    }
    if(node.type==="table"){
      const wrapper=document.createElement("div");wrapper.className="table-scroll";
      const table=document.createElement("table"),thead=document.createElement("thead"),tbody=document.createElement("tbody");
      for(const [index,row] of node.children.entries()){
        if(clipped && (row.position.end.offset<=clipped[0] || row.position.start.offset>=clipped[1]))continue;
        const tr=document.createElement("tr");
        for(const [column,cell] of row.children.entries()){
          const td=document.createElement(index===0?"th":"td");if(node.align[column])td.style.textAlign=node.align[column];
          appendChildren(td,cell);tr.append(td);
        }
        (index===0?thead:tbody).append(tr);
      }
      table.append(thead,tbody);wrapper.append(table);parent.append(wrapper);return;
    }
    const tag=node.type==="heading"?"h"+node.depth:node.type==="list"?(node.ordered?"ol":"ul"):tags[node.type];
    if(!tag){appendChildren(parent,node);return}
    const result=document.createElement(tag);
    if(node.type==="list" && node.ordered && node.start!==1)result.start=node.start;
    if(node.type==="listItem" && node.checked!==null && node.checked!==undefined){
      const checkbox=document.createElement("input");checkbox.type="checkbox";checkbox.checked=node.checked;checkbox.disabled=true;
      checkbox.className="task-check";result.append(checkbox);result.classList.add("task-item");
    }
    appendChildren(result,node);
    if(result.textContent || result.querySelector("hr,br,input,img,.formula"))parent.append(result);
  };
  appendChildren(root,tree);
  return root;
}

export function sourceSelection(container,raw,range){
  if(!container.contains(range.commonAncestorContainer))return null;
  const walker=document.createTreeWalker(container,NodeFilter.SHOW_TEXT),selected=[];
  for(let node;(node=walker.nextNode());){
    const map=locations.get(node);
    if(!map || !range.intersectsNode(node))continue;
    const start=range.startContainer===node?range.startOffset:0,end=range.endContainer===node?range.endOffset:node.length;
    for(let n=start;n<end;n++)selected.push({value:node.data[n],start:map.starts[n],end:map.ends[n]});
  }
  while(selected.length && /\s/.test(selected[0].value))selected.shift();
  while(selected.length && /\s/.test(selected.at(-1).value))selected.pop();
  if(!selected.length)return null;
  const start=selected[0].start,end=selected.at(-1).end;
  return {text:range.toString().trim(),source:raw.slice(start,end),start:[...raw.slice(0,start)].length,end:[...raw.slice(0,end)].length,rect:range.getBoundingClientRect()};
}

export function markdownPreview(raw){
  const visit=node=>{
    if(node.type==="definition" || node.type==="footnoteDefinition")return "";
    if(typeof node.value==="string")return node.value;
    if(node.type==="image" || node.type==="imageReference")return node.alt || "图片";
    return (node.children || []).map(visit).join(node.type==="root" || node.type==="list"?" ":"");
  };
  return visit(parse(raw || ""));
}
