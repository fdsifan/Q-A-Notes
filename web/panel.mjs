import { App, applyHostStyleVariables } from "@modelcontextprotocol/ext-apps";
import { renderMarkdown, sourceSelection, markdownPreview } from "./markdown.mjs";
import { renderImage, openImagePreview, disposeImageViews } from "./images.mjs";

const app = new App({name:"问答笔记",version:"0.7.6"}, {}, {autoResize:true});
const state = {view:"notes",data:null,detail:null,search:"",excerpt:"",comment:"",matches:[],total:0,selected:null,
  answerId:null,preferredStart:null,sourceExcerpt:null,sessionId:null,sessionTitle:"",offset:0,limit:50,manage:null,latestPending:false};
let selectionCleanup=()=>{};
const content = document.querySelector("#content");
const viewControls = document.querySelector("#view-controls");
const message = document.querySelector("#message");
const element = (tag, cls, text) => {
  const node=document.createElement(tag);
  if(cls) node.className=cls;
  if(text!==undefined) node.textContent=text;
  return node;
};
const button = (label, action, cls="") => {
  const node=element("button",cls,label);
  node.addEventListener("click", () => perform(action));
  return node;
};
function notify(text="",kind="info",action=null){
  message.replaceChildren();message.dataset.kind=kind;
  if(text)message.append(element("span","",text));
  if(action)message.append(button(action.label,action.run,"quiet"));
}
async function perform(action){
  try{await action()}catch(error){notify(error.message || "操作暂未完成，请重试。","error")}
}
let menu=null,menuTrigger=null;
function closeMenu(){
  menu?.remove();menu=null;menuTrigger?.setAttribute("aria-expanded","false");menuTrigger=null;
}
function openMenu(event,items,trigger){
  event.preventDefault();closeMenu();menuTrigger=trigger;trigger.setAttribute("aria-expanded","true");
  menu=element("div","context-menu");menu.setAttribute("role","menu");menu.setAttribute("aria-label","操作菜单");
  for(const item of items){
    const entry=button(item.label,async()=>{closeMenu();await item.run()},"menu-item"+(item.danger?" danger":""));
    entry.setAttribute("role","menuitem");menu.append(entry);
  }
  document.body.append(menu);
  const rect=trigger.getBoundingClientRect(),bounds=menu.getBoundingClientRect();
  const contextual=event.type==="contextmenu";
  menu.style.left=Math.max(8,Math.min(contextual?event.clientX:rect.right-bounds.width,window.innerWidth-bounds.width-8))+"px";
  menu.style.top=Math.max(8,Math.min(contextual?event.clientY:rect.bottom+4,window.innerHeight-bounds.height-8))+"px";
  menu.querySelector("button")?.focus();
}
function menuButton(label,items){
  const more=element("button","row-more","⋯");more.setAttribute("aria-label",label);
  more.title=label;more.setAttribute("aria-haspopup","menu");more.setAttribute("aria-expanded","false");
  more.addEventListener("click",event=>openMenu(event,items,more));return more;
}
function withMenu(node,label,items){
  const wrap=element("div","row-wrap"),more=menuButton(label,items);wrap.append(node,more);
  wrap.addEventListener("contextmenu",event=>openMenu(event,items,more));
  node.addEventListener("keydown",event=>{
    if(event.key==="ContextMenu" || (event.shiftKey && event.key==="F10"))openMenu(event,items,more);
  });return wrap;
}
document.addEventListener("pointerdown",event=>{if(menu && !menu.contains(event.target) && event.target!==menuTrigger)closeMenu()});
content.addEventListener("scroll",closeMenu,{passive:true});
document.addEventListener("keydown",event=>{
  if(!menu)return;
  if(event.key==="Escape"){event.preventDefault();const trigger=menuTrigger;closeMenu();trigger?.focus()}
  else if(event.key==="ArrowDown" || event.key==="ArrowUp"){
    event.preventDefault();const items=[...menu.querySelectorAll("button")],index=items.indexOf(document.activeElement);
    items[(index+(event.key==="ArrowDown"?1:items.length-1))%items.length]?.focus();
  }else if(event.key==="Tab")closeMenu();
});
async function call(name,args={}){
  const result=await app.callServerTool({name,arguments:args});
  if(result.isError)throw new Error(result.content?.find(x=>x.type==="text")?.text || "操作暂未完成");
  return result._meta?.["chat-notes/result"] || result.structuredContent;
}
function updateCounts(){
  if(!state.data)return;
  const c=state.data.counts;
  document.querySelector("#counts").textContent=c.questions+" 条问题 · "+c.answers+" 份回答 · "+c.notes+" 条笔记 · 本地保存";
}
const lastPageOffset=total=>Math.floor(Math.max(0,(total || 0)-1)/state.limit)*state.limit;
const listTotal=data=>state.view==="notes"?data.notes_total:data.questions_total;
function prepareLatestList(session){
  state.latestPending=true;
  state.offset=lastPageOffset(state.view==="notes"?session?.note_count:session?.question_count);
  state.data=null;
}
function accept(data){
  if(data?.notes && data?.questions){
    if((data.session_id || null)!==state.sessionId || (data.offset || 0)!==state.offset || data.search!==state.search ||
      (data.collection || "active")!==(state.manage || "active"))return;
    if(state.latestPending && (data.offset || 0)!==lastPageOffset(listTotal(data)))return;
    state.data=data;updateCounts();
    if(!state.detail && state.view!=="capture")render();
  }
}
let refreshNumber=0;
async function refresh(){
  const order=++refreshNumber;
  const args={search:state.search,limit:state.limit,offset:state.offset,collection:state.manage || "active"};
  if(state.sessionId)args.session_id=state.sessionId;
  for(let attempt=0;attempt<3;attempt++){
    const data=await call("notes_panel_data",args);
    if(!Array.isArray(data?.notes) || !Array.isArray(data?.questions) || !data.counts)
      throw new Error("未收到完整的记录，请重试。");
    if(order!==refreshNumber)return;
    // Reconcile a cached conversation count with the current last page.
    if(state.latestPending){
      const offset=lastPageOffset(listTotal(data));
      if(offset!==args.offset){state.offset=offset;args.offset=offset;continue}
    }
    accept(data);return;
  }
  throw new Error("记录正在变化，请点击刷新重试。");
}
let refreshing=false;
async function manualRefresh(){
  if(refreshing)return;
  refreshing=true;const control=document.querySelector("#refresh");control.disabled=true;
  control.classList.add("is-refreshing");control.setAttribute("aria-busy","true");control.title="正在刷新…";
  notify("正在刷新…");
  try{await refresh();notify("刷新成功，已更新本地记录。","success")}
  catch(error){notify("刷新失败："+(error.message || "请稍后重试。"),"error")}
  finally{
    refreshing=false;control.disabled=false;control.classList.remove("is-refreshing");
    control.setAttribute("aria-busy","false");control.title="刷新";
  }
}
async function changeNote(id,deleted){
  await call("set_note_deleted",{note_id:id,deleted});
  if(state.detail?.id===id)state.detail=null;
  // Remove the changed card immediately, even if the following read fails.
  if(state.data){
    state.data.notes=state.data.notes.filter(n=>n.id!==id);state.data.trash=state.data.trash?.filter(n=>n.id!==id);
    const rows=state.manage==="trash"?state.data.trash:state.view==="notes"?state.data.notes:null;
    if(rows && !rows.length && state.offset)state.offset=Math.max(0,state.offset-state.limit);
  }
  render();
  try{await refresh()}
  catch(error){notify((deleted?"笔记已移入回收站。":"笔记已恢复。")+"列表刷新失败："+error.message,"error");return}
  notify(deleted?"笔记已移入回收站。":"笔记已恢复。","success",
    deleted?{label:"撤销",run:()=>changeNote(id,false)}:null);
}
async function changeSession(id,hidden){
  await call("set_session_hidden",{session_id:id,hidden});
  if(hidden && state.view==="questions" && state.sessionId===id){
    state.sessionId=null;state.sessionTitle="";state.detail=null;state.offset=0;state.latestPending=false;
  }
  if(state.data){
    for(const s of state.data.sessions)if(s.id===id)s.hidden=hidden;
    state.data.hidden_sessions=state.data.hidden_sessions?.filter(s=>s.id!==id);
    if(state.manage==="hidden" && !state.data.hidden_sessions?.length && state.offset)state.offset=Math.max(0,state.offset-state.limit);
  }
  render();
  try{await refresh()}
  catch(error){notify((hidden?"会话已隐藏。":"会话已恢复显示。")+"列表刷新失败："+error.message,"error");return}
  notify(hidden?"会话已从问题记录中隐藏。":"会话已恢复显示。","success",
    hidden?{label:"撤销",run:()=>changeSession(id,false)}:null);
}
function go(view){
  const management=state.manage;state.view=view;state.detail=null;state.manage=null;
  if(view==="questions" && state.sessionId && state.data?.sessions?.find(s=>s.id===state.sessionId)?.hidden){
    perform(backToSessions);return;
  }
  if(view!=="capture" && state.sessionId){
    prepareLatestList(state.data?.sessions?.find(s=>s.id===state.sessionId));perform(refresh);
  }else{
    state.latestPending=false;
    if(view!=="capture" && (state.offset || management)){state.offset=0;state.data=null;perform(refresh)}
  }
  render();notify();
}
function formatTime(value){
  const date=new Date(value);
  return Number.isNaN(date.valueOf())?value || "":new Intl.DateTimeFormat("zh-CN",{
    timeZone:"Asia/Shanghai",year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hour12:false
  }).format(date);
}
async function chooseSession(session){
  state.manage=null;state.sessionId=session.id;state.sessionTitle=session.title;state.offset=0;state.detail=null;state.data=null;
  prepareLatestList(session);
  state.search="";document.querySelector("#search").value="";render();notify();await refresh();
}
async function backToSessions(){
  state.manage=null;state.sessionId=null;state.sessionTitle="";state.offset=0;state.detail=null;state.search="";state.data=null;state.latestPending=false;
  document.querySelector("#search").value="";render();notify();await refresh();
}
let renderedViewKey=null;
function render(){
  const key=JSON.stringify([state.view,state.sessionId,state.manage,state.search,state.offset,state.detail?.id || null]);
  const scrollTop=key===renderedViewKey?content.scrollTop:0;renderedViewKey=key;
  try{return renderView()}finally{
    if(state.latestPending && state.data && !state.detail && state.sessionId){
      content.scrollTop=content.scrollHeight;state.latestPending=false;
    }else content.scrollTop=scrollTop;
  }
}
function renderView(){
  closeMenu();
  selectionCleanup();selectionCleanup=()=>{};
  disposeImageViews(content);content.replaceChildren();viewControls.replaceChildren();
  for(const node of document.querySelectorAll("nav button"))node.classList.toggle("active",node.dataset.view===state.view);
  document.querySelector("#searchbox").hidden=state.view==="capture" || !!state.detail;
  const searchInput=document.querySelector("#search");
  searchInput.placeholder=state.manage==="trash"?"搜索回收站中的笔记…":state.manage==="hidden"?"搜索已隐藏的会话…":
    state.sessionId?"搜索本会话的"+(state.view==="notes"?"笔记…":"问题…"):"搜索会话名称或内容…";
  if(state.detail)return renderDetail();
  if(state.view==="capture")return renderCapture();
  if(state.manage)return renderManagement();
  if(!state.sessionId)return renderSessions();
  const head=element("div","session-head");
  head.append(button("‹ 会话列表",backToSessions),element("strong","",state.sessionTitle));
  if(state.view==="questions")head.append(menuButton("会话操作",[
    {label:"隐藏会话",run:()=>changeSession(state.sessionId,true)}]));
  viewControls.append(head);
  const info=element("div","session-info");
  info.append(element("span","meta","按时间从早到晚 · 北京时间"),button("打开此会话",async()=>{
    await call("open_saved_conversation",{session_id:state.sessionId});notify("已打开会话。");
  },"quiet"));
  if(state.view==="notes")info.append(button("回收站",()=>openManagement("trash"),"quiet"));
  viewControls.append(info);
  if(!state.data){content.append(element("p","empty","正在加载…"));return}
  const isNotes=state.view==="notes", rows=isNotes?state.data.notes:state.data.questions;
  if(!rows.length){
    content.append(element("p","empty",state.search?"没有找到相关内容。":isNotes?"还没有笔记。点击「＋ 摘录」保存第一段有用的回答。":"暂无已保存的问题。"));
    return;
  }
  for(const row of rows){
    const node=button("",async()=>{
      state.detail=await call(isNotes?"get_note_source":"get_question_source",isNotes?{note_id:row.id}:{question_id:row.id});
      state.detail.isNote=isNotes;
      render();notify();
    },isNotes?"row":"question-bubble");
    if(isNotes){
      node.append(element("strong","",markdownPreview(row.title).slice(0,100)),
        element("div","preview",markdownPreview(row.excerpt)),
        element("div","meta",formatTime(row.created)+" · 可查看完整来源"));
      content.append(withMenu(node,"笔记操作："+row.title,[
        {label:"删除笔记",danger:true,run:()=>changeNote(row.id,true)}]));
    }else{
      const record=element("article","question-record");
      node.append(element("span","question-text",row.display_text ?? row.text));
      record.append(element("div","question-time",formatTime(row.timestamp)),node,
        element("div","meta question-status",(row.answer_id?"回答已保存":"等待完整回答")+(row.has_images?" · 含图片":"")));
      content.append(record);
    }
  }
  const total=isNotes?state.data.notes_total:state.data.questions_total;
  if(total>state.limit){
    const pager=element("div","pager");
    const move=async offset=>{state.latestPending=false;state.offset=offset;state.data=null;render();await refresh()};
    const previous=button("上一页",()=>move(Math.max(0,state.offset-state.limit)));
    previous.disabled=state.offset===0;
    const next=button("下一页",()=>move(state.offset+state.limit));next.disabled=state.offset+rows.length>=total;
    pager.append(previous,element("span","meta",(state.offset+1)+"–"+(state.offset+rows.length)+" / "+total),next);
    content.append(pager);
  }
}
function renderSessions(){
  if(!state.data){content.append(element("p","empty","正在加载会话…"));return}
  const isNotes=state.view==="notes";
  const groups=(state.data.sessions || []).filter(s=>(isNotes?s.note_count:s.question_count)>0 && (isNotes || !s.hidden) &&
    (!state.search || s.title_matches || (isNotes?s.notes_matches:s.questions_matches)>0));
  const intro=element("div","list-tools");
  intro.append(element("p","list-intro",isNotes?"选择会话，查看其中的笔记":"选择会话，按问答时间查看问题记录"),
    button(isNotes?"回收站（"+state.data.trash_count+"）":"已隐藏会话（"+state.data.hidden_count+"）",
      ()=>openManagement(isNotes?"trash":"hidden"),"quiet"));viewControls.append(intro);
  if(!groups.length){content.append(element("p","empty",state.search?"没有找到相关会话。":isNotes?
    "还没有笔记。点击「＋ 摘录」保存第一段回答。":"暂无已保存的问题。"));return}
  for(const session of groups){
    const node=button("",()=>chooseSession(session),"row session-row");
    node.append(element("strong","",session.title),element("div","preview",session.question_count+" 条问题 · "+session.note_count+" 条笔记"),
      element("div","meta",formatTime(session.updated)+" · "+session.id.slice(0,8)));
    if(session.title_source==="first_question")node.append(element("div","meta","会话名称暂不可用，显示首个问题"));
    content.append(isNotes?node:withMenu(node,"会话操作："+session.title,[
      {label:"隐藏会话",run:()=>changeSession(session.id,true)}]));
  }
}
async function openManagement(kind){
  state.manage=kind;state.sessionId=null;state.sessionTitle="";state.detail=null;state.offset=0;state.search="";state.data=null;state.latestPending=false;
  document.querySelector("#search").value="";render();notify();await refresh();
}
function renderManagement(){
  const trash=state.manage==="trash",head=element("div","session-head");
  head.append(button("‹ 会话列表",backToSessions),element("strong","",trash?"笔记回收站":"已隐藏会话"));viewControls.append(head);
  viewControls.append(element("p","list-intro",trash?"删除的笔记保留在这里，可随时恢复。":"恢复后，会话会重新出现在问题记录中。"));
  if(!state.data){content.append(element("p","empty","正在加载…"));return}
  const rows=trash?state.data.trash:state.data.hidden_sessions;
  if(!rows?.length){content.append(element("p","empty",state.search?"没有找到相关内容。":trash?"回收站为空。":"暂无隐藏的会话。"));return}
  for(const row of rows){
    const card=element("div","management-card");
    if(trash){
      const node=button("",async()=>{
        state.detail={...await call("get_note_source",{note_id:row.id}),isNote:true};state.sessionTitle=row.session_title;render();notify();
      },"row");
      node.append(element("strong","",markdownPreview(row.title)),element("div","preview",markdownPreview(row.excerpt)),
        element("div","meta",row.session_title+" · 删除于 "+formatTime(row.deleted_at)));
      card.append(withMenu(node,"回收站笔记操作："+row.title,[{label:"恢复笔记",run:()=>changeNote(row.id,false)}]),
        button("恢复笔记",()=>changeNote(row.id,false),"quiet"));
    }else{
      card.append(element("strong","",row.title),element("div","meta",row.question_count+" 条问题 · 隐藏于 "+formatTime(row.hidden_at)),
        button("恢复显示",()=>changeSession(row.id,false),"quiet"));
    }
    content.append(card);
  }
  const total=trash?state.data.trash_total:state.data.hidden_total;
  if(total>state.limit){
    const pager=element("div","pager"),move=async offset=>{state.offset=offset;state.data=null;render();await refresh()};
    const previous=button("上一页",()=>move(Math.max(0,state.offset-state.limit)));previous.disabled=state.offset===0;
    const next=button("下一页",()=>move(state.offset+state.limit));next.disabled=state.offset+rows.length>=total;
    pager.append(previous,element("span","meta",(state.offset+1)+"–"+(state.offset+rows.length)+" / "+total),next);content.append(pager);
  }
}
function formattedSection(title,raw,options={}){
  const node=element("section","section");
  node.append(element("h2","section-label",title),renderMarkdown(raw,{...options,onOpenImage:openImagePreview,onOpenLink:url=>perform(async()=>{
    const result=await app.openLink({url});if(result.isError)throw new Error("链接暂时无法打开。");
  })}));
  return node;
}
function renderDetail(){
  const d=state.detail;
  const head=element("div","detail-head");
  head.append(button("‹ 返回",()=>{state.detail=null;render();notify()}),element("strong","",d.isNote?d.title:"问答记录"));
  if(d.isNote)head.append(menuButton("笔记操作",[
    d.deleted_at?{label:"恢复笔记",run:()=>changeNote(d.id,false)}:{label:"删除笔记",danger:true,run:()=>changeNote(d.id,true)}]));
  viewControls.append(head);
  const sourceInfo=element("div","session-info");
  sourceInfo.append(element("p","meta","所属会话："+state.sessionTitle));
  if(d.isNote)sourceInfo.append(button("打开原会话",async()=>{
    await call("open_original_conversation",{note_id:d.id});notify("已打开原会话。");
  },"quiet"));
  viewControls.append(sourceInfo);
  if(d.isNote){
    const quote=formattedSection("摘录",d.answer_snapshot || d.excerpt,
      d.answer_snapshot?{clip:[d.raw_start,d.raw_end],images:d.answer_images || []}:{});
    quote.lastChild.classList.add("quote");content.append(quote);
  }
  if(d.comment)content.append(formattedSection("我的备注",d.comment));
  const question=formattedSection("原问题",d.question_display ?? d.question_snapshot);
  const questionBody=element("div","question-bubble");
  questionBody.append(question.lastChild);
  if(d.question_images?.length){
    const attachments=element("div","attachment-list");
    for(const image of d.question_images)attachments.append(renderImage(image,openImagePreview));
    questionBody.prepend(attachments);
  }
  question.append(questionBody);
  content.append(question);
  const raw=d.answer_snapshot || "";
  const answerSection=formattedSection("完整原回答",raw,
    {images:d.answer_images || [],...(d.raw_start!==undefined?{highlight:[d.raw_start,d.raw_end]}:{})}),answer=answerSection.lastChild;
  answer.classList.add("answer");
  content.append(answerSection);
  if(d.answer_id){
    let captured=null;
    const selectedRange=()=>{
      const selection=window.getSelection();
      if(!selection?.rangeCount || !answer.contains(selection.getRangeAt(0).commonAncestorContainer) || !selection.toString().trim())
        return null;
      return sourceSelection(answer,raw,selection.getRangeAt(0));
    };
    const extractSelection=()=>{
      const selected=selectedRange() || captured;
      if(!selected)throw new Error("先在上面的完整原回答中选中文字。");
      state.excerpt=selected.text;state.sourceExcerpt=selected.source;state.comment="";state.answerId=d.answer_id;state.preferredStart=selected.start;
      state.matches=[];state.selected=null;
      go("capture");return findMatches();
    };
    const floating=button("＋ 加入笔记",extractSelection,"selection-note");floating.hidden=true;
    floating.addEventListener("mousedown",event=>event.preventDefault());content.append(floating);
    const updateSelection=()=>{
      captured=selectedRange();floating.hidden=!captured;
      if(captured){
        const bounds=content.getBoundingClientRect();
        if(captured.rect.bottom<bounds.top || captured.rect.top>bounds.bottom){floating.hidden=true;return}
        floating.style.left=Math.max(8,Math.min(captured.rect.left,window.innerWidth-130))+"px";
        floating.style.top=Math.max(bounds.top+8,captured.rect.top-42)+"px";
      }
    };
    document.addEventListener("selectionchange",updateSelection);
    content.addEventListener("scroll",updateSelection,{passive:true});
    selectionCleanup=()=>{document.removeEventListener("selectionchange",updateSelection);content.removeEventListener("scroll",updateSelection)};
  }
  if(d.isNote)content.append(element("p","meta",(d.deleted_at?"已在回收站 · ":"")+"保存于 "+formatTime(d.created)+" · 此处展示保存时的完整原文副本"));
}
async function findMatches(){
  const args={excerpt:state.sourceExcerpt || state.excerpt};
  if(state.answerId)args.answer_id=state.answerId;
  if(state.preferredStart!==null)args.start=state.preferredStart;
  if(state.sessionId)args.session_id=state.sessionId;
  const result=await call("match_excerpt",args);
  state.matches=result.matches;state.total=result.total;
  state.selected=state.preferredStart!==null?state.matches.find(m=>m.start===state.preferredStart) || null:
    state.total===1?state.matches[0]:null;
  render();
  notify(state.total===0?"尚未找到来源。回答结束后会自动保存；也可以从「问题记录」里的原回答摘录。":
    state.selected?"已确认来源，可保存笔记。":
    state.total>50?"匹配位置较多，只显示前 50 处。请增加摘录上下文，再选择对应来源。":"找到 "+state.total+" 个位置，请选择对应回答。");
}
function renderCapture(){
  content.append(element("p","sub","在 ChatGPT 回答中复制文字，点击下面的按钮；也可以直接粘贴。"));
  if(state.sessionId)content.append(element("p","capture-scope","当前会话："+state.sessionTitle));
  const actions=element("div","actions");
  actions.append(button("摘录复制内容",async()=>{
    const data=await call("read_copied_excerpt");
    state.excerpt=data.excerpt;state.sourceExcerpt=null;state.answerId=null;state.preferredStart=null;state.matches=[];state.selected=null;state.comment="";
    render();await findMatches();
  }));
  content.append(actions,element("label","","摘录文字"));
  const quote=element("textarea");
  quote.setAttribute("aria-label","摘录文字");
  quote.placeholder="粘贴想保存的回答片段…";quote.value=state.excerpt;
  quote.addEventListener("input",()=>{
    state.excerpt=quote.value;state.sourceExcerpt=null;state.selected=null;state.matches=[];state.answerId=null;state.preferredStart=null;state.total=0;
    for(const node of content.querySelectorAll(".candidate"))node.remove();
    notify();
    save.disabled=true;
  });
  content.append(quote);
  const find=button("查找原回答",findMatches);
  find.style.marginTop="9px";
  content.append(find);
  if(state.matches.length){
    content.append(element("label","","对应来源"));
    for(const match of state.matches){
      const card=element("div","candidate"+(state.selected===match?" selected":""));
      card.tabIndex=0;card.setAttribute("role","radio");card.setAttribute("aria-checked",state.selected===match?"true":"false");
      const preview=renderMarkdown(match.answer_preview,{compact:true});preview.classList.add("preview");
      card.append(element("strong","",markdownPreview(match.question).slice(0,150)),preview,
        element("div","meta",formatTime(match.timestamp)+" · 原文位置 "+match.start));
      const select=()=>{state.selected=match;render()};
      card.addEventListener("click",select);
      card.addEventListener("keydown",event=>{if(event.key==="Enter" || event.key===" "){event.preventDefault();select()}});
      content.append(card);
    }
  }
  content.append(element("label","","我的备注（可选）"));
  const comment=element("textarea");
  comment.style.minHeight="65px";comment.value=state.comment;comment.setAttribute("aria-label","我的备注");
  comment.addEventListener("input",()=>{state.comment=comment.value});
  content.append(comment);
  const save=button("保存笔记",async()=>{
    if(!state.selected)return;
    save.disabled=true;
    try{
      const result=await call("save_excerpt",{excerpt:state.sourceExcerpt || state.excerpt,answer_id:state.selected.answer_id,
        start:state.selected.start,comment:state.comment});
      state.view="notes";state.sessionId=result.note.session_id;state.offset=0;
      state.sessionTitle=state.data?.sessions?.find(s=>s.id===state.sessionId)?.title || state.sessionTitle || "笔记所属会话";
      state.search="";document.querySelector("#search").value="";
      state.detail={...result.note,isNote:true};render();await refresh();notify("已保存，完整来源也已保留。");
    }finally{save.disabled=!state.selected}
  },"primary");
  save.disabled=!state.selected;save.style.marginTop="14px";content.append(save);
}
for(const node of document.querySelectorAll("nav button"))node.addEventListener("click",()=>go(node.dataset.view));
document.querySelector("#refresh").addEventListener("click",manualRefresh);
let searchTimer;
document.querySelector("#search").addEventListener("input",event=>{
  state.search=event.target.value;state.offset=0;state.latestPending=false;clearTimeout(searchTimer);
  searchTimer=setTimeout(()=>perform(refresh),250);
});
app.ontoolresult=result=>accept(result._meta?.["chat-notes/result"] || result.structuredContent);
app.onhostcontextchanged=context=>{
  if(context.theme)document.documentElement.dataset.theme=context.theme;
  if(context.styles?.variables)applyHostStyleVariables(context.styles.variables);
};
render();
try{
  await app.connect();
  const context=app.getHostContext();
  if(context?.theme)document.documentElement.dataset.theme=context.theme;
  if(context?.styles?.variables)applyHostStyleVariables(context.styles.variables);
  if(context?.displayMode==="inline" && context.availableDisplayModes?.includes("pip")){
    await app.requestDisplayMode({mode:"pip"}).catch(()=>{});
  }
  await refresh();
  notify();
  setInterval(()=>{if(!refreshing && !menu)perform(refresh)},10000);
}catch(error){
  notify("请在 ChatGPT 的「问答笔记」插件面板中打开。连接失败："+error.message);
}
