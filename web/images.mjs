// Only raster bytes supplied privately by the local archive can become an image.
const validData=value=>/^data:image\/(png|jpeg|gif|webp);base64,[A-Za-z0-9+/]+={0,2}$/.test(value || "");
const reasons={missing:"原图已不可用，未能保存图片。",unsupported:"这个图片格式暂不支持。",too_large:"图片超过 8 MB，暂未保存。"};
const imageViews=new WeakMap();
const node=(tag,cls,text)=>{
  const result=document.createElement(tag);result.className=cls;
  if(text!==undefined)result.textContent=text;
  return result;
};
const button=(label,action,cls="")=>{
  const result=node("button",cls,label);result.type="button";
  result.addEventListener("click",action);return result;
};
export function disposeImageViews(root){
  for(const view of root.querySelectorAll(".image-attachment"))imageViews.get(view)?.();
}

function zoomView(image){
  const root=node("span","image-attachment image-zoom-view");
  const viewport=node("span","image-scroll");
  viewport.tabIndex=0;viewport.setAttribute("role","region");viewport.setAttribute("aria-label","图片查看区域");
  const stage=node("span","image-stage"),img=node("img","");
  img.alt=image.name || "图片";img.decoding="async";img.referrerPolicy="no-referrer";img.draggable=false;
  stage.append(img);viewport.append(stage);
  const tools=node("span","image-tools"),zoomRow=node("span","image-zoom-row"),actions=node("span","image-zoom-actions");
  tools.setAttribute("role","group");tools.setAttribute("aria-label","图片缩放控制");
  const less=button("−",()=>setScale(scale/1.25),"image-step"),more=button("＋",()=>setScale(scale*1.25),"image-step");
  less.setAttribute("aria-label","缩小图片");more.setAttribute("aria-label","放大图片");
  const slider=node("input","image-zoom-slider");slider.type="range";slider.min="5";slider.max="400";slider.step="0.1";
  slider.setAttribute("aria-label","图片缩放");
  const percent=node("output","image-percent","加载中");percent.setAttribute("aria-live","off");
  slider.addEventListener("input",()=>setScale(Number(slider.value)/100));
  const fitButton=button("适应页面",()=>fit()),original=button("原始大小",()=>setScale(1));
  zoomRow.append(less,slider,more,percent);actions.append(fitButton,original);
  tools.append(zoomRow,actions);
  const hint=node("span","image-zoom-hint","滚轮缩放 · 拖动查看细节");
  root.append(viewport,tools,hint);
  const controls=[less,more,slider,fitButton,original];controls.forEach(control=>control.disabled=true);
  let scale=1,minScale=.05,ready=false,fitMode=true,disposed=false,drag=null,suppressClick=false;
  let renderedWidth=0,renderedHeight=0,paddingX=0,paddingY=0;
  const fitScale=()=>Math.min(1,Math.max(1,viewport.clientWidth)/img.naturalWidth,
    Math.max(1,viewport.clientHeight)/img.naturalHeight);
  function layout(){
    renderedWidth=img.naturalWidth*scale;renderedHeight=img.naturalHeight*scale;
    img.style.width=renderedWidth+"px";
    img.style.height=renderedHeight+"px";
    const width=Math.max(viewport.clientWidth,renderedWidth),height=Math.max(viewport.clientHeight,renderedHeight);
    paddingX=(width-renderedWidth)/2;paddingY=(height-renderedHeight)/2;
    stage.style.width=width+"px";stage.style.height=height+"px";
    const display=Math.round(scale*1000)/10;
    slider.value=String(display);slider.setAttribute("aria-valuetext",display+"%");percent.textContent=display+"%";
    less.disabled=scale<=minScale+.00001;more.disabled=scale>=4-.00001;
    viewport.classList.toggle("can-pan",width>viewport.clientWidth+1 || height>viewport.clientHeight+1);
  }
  function setScale(next,anchor=null,followFit=false){
    if(!ready || disposed || !Number.isFinite(next))return;
    const point=anchor || {x:viewport.clientWidth/2,y:viewport.clientHeight/2};
    const imageX=(viewport.scrollLeft+point.x-paddingX)/scale,imageY=(viewport.scrollTop+point.y-paddingY)/scale;
    fitMode=followFit;minScale=Math.min(.05,fitScale());slider.min=String(minScale*100);
    scale=Math.max(minScale,Math.min(4,next));layout();
    // Preserve the point under the cursor (or viewport centre) while zooming.
    viewport.scrollLeft=Math.max(0,Math.min(renderedWidth+paddingX*2-viewport.clientWidth,imageX*scale+paddingX-point.x));
    viewport.scrollTop=Math.max(0,Math.min(renderedHeight+paddingY*2-viewport.clientHeight,imageY*scale+paddingY-point.y));
  }
  function fit(){setScale(fitScale(),null,true);viewport.scrollLeft=0;viewport.scrollTop=0}
  img.addEventListener("load",()=>{
    if(disposed || !img.naturalWidth || !img.naturalHeight)return;
    ready=true;controls.forEach(control=>control.disabled=false);fit();
  });
  img.addEventListener("error",()=>{
    dispose();root.replaceChildren();root.className="image-attachment image-missing";
    root.append(node("span","",image.name || "图片"),node("small","","图片数据不可用。"));
  },{once:true});
  viewport.addEventListener("wheel",event=>{
    if(!ready)return;
    event.preventDefault();
    const rect=viewport.getBoundingClientRect(),unit=event.deltaMode===1?16:event.deltaMode===2?viewport.clientHeight:1;
    setScale(scale*Math.exp(-event.deltaY*unit*.0018),{x:event.clientX-rect.left,y:event.clientY-rect.top});
  },{passive:false});
  viewport.addEventListener("pointerdown",event=>{
    if(event.button!==0 || !viewport.classList.contains("can-pan"))return;
    drag={id:event.pointerId,x:event.clientX,y:event.clientY,left:viewport.scrollLeft,top:viewport.scrollTop,moved:false};
    suppressClick=false;
  });
  viewport.addEventListener("pointermove",event=>{
    if(!drag || event.pointerId!==drag.id)return;
    const dx=event.clientX-drag.x,dy=event.clientY-drag.y;
    if(!drag.moved && Math.abs(dx)+Math.abs(dy)>3){
      drag.moved=true;viewport.setPointerCapture?.(event.pointerId);viewport.classList.add("is-panning");
    }
    if(drag.moved){event.preventDefault();viewport.scrollLeft=drag.left-dx;viewport.scrollTop=drag.top-dy}
  });
  const endDrag=()=>{
    if(!drag)return;suppressClick=drag.moved;const id=drag.id;drag=null;
    viewport.classList.remove("is-panning");
    if(viewport.hasPointerCapture?.(id))viewport.releasePointerCapture(id);
  };
  viewport.addEventListener("pointerup",endDrag);viewport.addEventListener("pointercancel",endDrag);
  viewport.addEventListener("lostpointercapture",endDrag);
  viewport.addEventListener("click",event=>{
    if(suppressClick){event.preventDefault();event.stopPropagation();suppressClick=false}
  },true);
  const ResizeObserverClass=document.defaultView?.ResizeObserver;
  const observer=ResizeObserverClass?new ResizeObserverClass(()=>{if(ready && !disposed){if(fitMode)fit();else layout()}}):null;
  observer?.observe(viewport);
  function dispose(){disposed=true;endDrag();observer?.disconnect();imageViews.delete(root)}
  imageViews.set(root,dispose);
  img.src=image.data_url;
  return {root,viewport,dispose};
}

export function renderImage(image,onOpen=null){
  if(validData(image.data_url)){
    const wrapper=node("span","image-attachment image-thumbnail");
    const frame=onOpen?button("",()=>onOpen(image),"image-thumbnail-frame"):node("span","image-thumbnail-frame");
    if(onOpen){
      frame.setAttribute("aria-label","查看图片："+(image.name || "图片"));frame.title="点击查看大图";
    }
    const img=node("img","");img.alt=image.name || "图片";img.decoding="async";img.referrerPolicy="no-referrer";img.draggable=false;
    frame.append(img);wrapper.append(frame);
    let disposed=false;
    const resize=()=>{
      if(disposed || !img.naturalWidth || !img.naturalHeight || !wrapper.clientWidth)return;
      const scale=Math.min(1,Math.max(1,wrapper.clientWidth-2)/img.naturalWidth,240/img.naturalWidth,220/img.naturalHeight);
      img.style.width=img.naturalWidth*scale+"px";img.style.height=img.naturalHeight*scale+"px";
    };
    img.addEventListener("load",resize);
    img.addEventListener("error",()=>{
      dispose();const missing=renderImage({...image,data_url:null,status:"invalid"});
      wrapper.className=missing.className;wrapper.replaceChildren(...missing.childNodes);
    },{once:true});
    const ResizeObserverClass=document.defaultView?.ResizeObserver;
    const observer=ResizeObserverClass?new ResizeObserverClass(resize):null;observer?.observe(wrapper);
    const dispose=()=>{disposed=true;observer?.disconnect();imageViews.delete(wrapper)};
    imageViews.set(wrapper,dispose);img.src=image.data_url;return wrapper;
  }
  const wrapper=node("span","image-attachment image-missing");
  wrapper.append(node("span","",image.name || "图片"),node("small","",reasons[image.status] || "图片数据不可用。"));
  return wrapper;
}

export function openImagePreview(image){
  if(!validData(image.data_url) || document.querySelector(".image-overlay"))return;
  const previous=document.activeElement,overlay=node("div","image-overlay");
  overlay.setAttribute("role","dialog");overlay.setAttribute("aria-modal","true");overlay.setAttribute("aria-label","图片预览");
  const panel=node("div","image-preview-panel"),head=node("div","image-preview-head");
  const close=button("关闭图片",()=>remove());
  const view=zoomView(image);
  const remove=()=>{
    document.removeEventListener("keydown",keydown);view.dispose();overlay.remove();
    if(previous?.isConnected)previous.focus();
  };
  const keydown=event=>{
    if(event.key==="Escape"){event.preventDefault();event.stopPropagation();remove()}
    if(event.key==="Tab"){
      const focusable=[...panel.querySelectorAll('button:not(:disabled),input:not(:disabled),[tabindex="0"]')];
      const index=focusable.indexOf(document.activeElement);
      if(event.shiftKey && index<=0){event.preventDefault();focusable.at(-1)?.focus()}
      else if(!event.shiftKey && (index===focusable.length-1 || index<0)){event.preventDefault();focusable[0]?.focus()}
    }
  };
  head.append(node("span","image-preview-title",image.name || "图片"),close);
  panel.append(head,view.root);overlay.append(panel);
  overlay.addEventListener("click",event=>{if(event.target===overlay)remove()});
  document.addEventListener("keydown",keydown);document.body.append(overlay);close.focus();
}
