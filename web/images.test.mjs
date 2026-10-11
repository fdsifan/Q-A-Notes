import test from "node:test";
import assert from "node:assert/strict";
import { Window } from "happy-dom";
const window=new Window();
globalThis.document=window.document;
const {renderImage,openImagePreview,disposeImageViews}=await import("./images.mjs");
const saved={name:"截图.png",data_url:"data:image/png;base64,iVBORw0KGgo="};
const control=(root,label)=>[...root.querySelectorAll("button")].find(button=>button.textContent===label || button.getAttribute("aria-label")===label);
function loaded(root,{width=1000,height=800,viewWidth=300,viewHeight=400}={}){
  const img=root.querySelector("img"),area=root.querySelector(".image-scroll");
  Object.defineProperties(img,{naturalWidth:{value:width},naturalHeight:{value:height}});
  Object.defineProperties(area,{clientWidth:{value:viewWidth},clientHeight:{value:viewHeight}});
  img.dispatchEvent(new window.Event("load"));
  return {img,area,slider:root.querySelector("input")};
}
function cleanup(root){disposeImageViews(document.body);root.remove()}
function preview(){openImagePreview(saved);return document.querySelector('[role="dialog"]')}
function closePreview(root){control(root,"关闭图片").click()}

test("inline images have a fitted thumbnail and no zoom controls or intercepted wheel",()=>{
  let opens=0;const root=renderImage(saved,()=>opens++);document.body.append(root);
  const img=root.querySelector("img");
  Object.defineProperties(img,{naturalWidth:{value:1000},naturalHeight:{value:800}});
  Object.defineProperty(root,"clientWidth",{value:302});img.dispatchEvent(new window.Event("load"));
  assert.equal(img.style.width,"240px");assert.equal(img.style.height,"192px");
  assert.equal(root.querySelector("input"),null);assert.equal(root.querySelector(".image-tools"),null);
  const wheel=new window.WheelEvent("wheel",{deltaY:-50,cancelable:true});root.dispatchEvent(wheel);
  assert.equal(wheel.defaultPrevented,false);assert.equal(img.style.width,"240px");
  root.querySelector("button").click();assert.equal(opens,1);cleanup(root);
});

test("tall and wide thumbnails stay compact, fit narrow panels, and do not upscale small originals",()=>{
  for(const [width,height,available,expectedWidth,expectedHeight] of [
    [600,1800,800,220/3,220],[1800,600,800,240,80],
    [1800,600,122,120,40],[80,60,800,80,60]
  ]){
    const root=renderImage(saved);document.body.append(root);const img=root.querySelector("img");
    Object.defineProperties(img,{naturalWidth:{value:width},naturalHeight:{value:height}});
    Object.defineProperty(root,"clientWidth",{value:available});img.dispatchEvent(new window.Event("load"));
    assert.ok(Math.abs(parseFloat(img.style.width)-expectedWidth)<.00001);
    assert.ok(Math.abs(parseFloat(img.style.height)-expectedHeight)<.00001);
    cleanup(root);
  }
});

test("large-preview controls fit the image, zoom to original pixels, and restore fit",()=>{
  const root=preview(),{img,area,slider}=loaded(root);
  assert.equal(img.style.width,"300px");assert.equal(img.style.height,"240px");
  assert.equal(root.querySelector("output").textContent,"30%");
  control(root,"原始大小").click();assert.equal(img.style.width,"1000px");
  assert.ok(area.classList.contains("can-pan"));assert.equal(slider.value,"100");
  control(root,"放大图片").click();assert.equal(img.style.width,"1250px");
  control(root,"适应页面").click();assert.equal(img.style.width,"300px");
  assert.equal(area.scrollLeft,0);assert.equal(area.scrollTop,0);closePreview(root);
});

test("slider limits disable the corresponding step without blocking reset",()=>{
  const root=preview(),{img,slider}=loaded(root);
  slider.value="400";slider.dispatchEvent(new window.Event("input"));
  assert.equal(img.style.width,"4000px");assert.equal(control(root,"放大图片").disabled,true);
  slider.value="5";slider.dispatchEvent(new window.Event("input"));
  assert.equal(img.style.width,"50px");assert.equal(control(root,"缩小图片").disabled,true);
  control(root,"原始大小").click();assert.equal(img.style.width,"1000px");closePreview(root);
});

test("large-preview wheel zoom keeps the point under the cursor",()=>{
  const root=preview(),{img,area}=loaded(root);
  control(root,"原始大小").click();area.scrollLeft=150;area.scrollTop=200;
  const wheel=new window.WheelEvent("wheel",{deltaY:-50,clientX:100,clientY:120,cancelable:true});
  // Happy DOM's WheelEvent omits the browser's inherited MouseEvent fields.
  Object.defineProperties(wheel,{clientX:{value:100},clientY:{value:120}});
  area.dispatchEvent(wheel);assert.equal(wheel.defaultPrevented,true);
  const scale=parseFloat(img.style.width)/1000;
  assert.ok(scale>1);assert.ok(Math.abs((area.scrollLeft+100)/scale-250)<.00001);
  assert.ok(Math.abs((area.scrollTop+120)/scale-320)<.00001);closePreview(root);
});

test("drag pans an enlarged image inside the large preview",()=>{
  const root=preview(),{area}=loaded(root);control(root,"原始大小").click();area.scrollLeft=200;area.scrollTop=180;
  area.dispatchEvent(new window.PointerEvent("pointerdown",{pointerId:1,button:0,clientX:140,clientY:170}));
  area.dispatchEvent(new window.PointerEvent("pointermove",{pointerId:1,clientX:100,clientY:120,cancelable:true}));
  assert.equal(area.scrollLeft,240);assert.equal(area.scrollTop,230);
  area.dispatchEvent(new window.PointerEvent("pointerup",{pointerId:1}));
  assert.equal(area.classList.contains("is-panning"),false);closePreview(root);
});

test("same-panel viewer supports wheel zoom, full focus trap and Escape focus restoration",()=>{
  const launcher=document.createElement("button");document.body.append(launcher);launcher.focus();openImagePreview(saved);
  const dialog=document.querySelector('[role="dialog"]'),{img,area}=loaded(dialog);
  const wheel=new window.WheelEvent("wheel",{deltaY:-80,clientX:150,clientY:200,cancelable:true});area.dispatchEvent(wheel);
  assert.equal(wheel.defaultPrevented,true);assert.ok(parseFloat(img.style.width)>300);
  const close=control(dialog,"关闭图片"),last=control(dialog,"原始大小");
  close.focus();document.dispatchEvent(new window.KeyboardEvent("keydown",{key:"Tab",shiftKey:true,cancelable:true}));
  assert.equal(document.activeElement,last);
  document.dispatchEvent(new window.KeyboardEvent("keydown",{key:"Tab",cancelable:true}));assert.equal(document.activeElement,close);
  document.dispatchEvent(new window.KeyboardEvent("keydown",{key:"Escape",cancelable:true}));
  assert.equal(document.querySelector('[role="dialog"]'),null);assert.equal(document.activeElement,launcher);launcher.remove();
});

test("huge images can fit below five percent and corrupt images remove unusable controls",()=>{
  const root=preview(),{img,slider}=loaded(root,{width:20000,height:10000});
  assert.equal(img.style.width,"300px");assert.ok(Number(slider.min)<5);
  img.dispatchEvent(new window.Event("error"));assert.equal(root.querySelector("input"),null);
  assert.ok(root.textContent.includes("图片数据不可用"));closePreview(root);
});
