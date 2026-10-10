/* Automated DOM/API contract simulation.
 * Run: node scripts/test-ui-dom.cjs
 * Real: WSGI, SQLite, profile parser, rule analysis, tracker, CSRF/version controls.
 * Mocked: DOM implementation, network transport, source discovery, model config.
 * This is NOT a browser end-to-end, layout, live-network or live-model test.
 * Optional AP_UI_LAYOUT_PREVIEW=1 emits a clearly marked static visual preview.
 */
const fs=require('fs'),vm=require('vm'),cp=require('child_process'),assert=require('assert'),path=require('path'),os=require('os');
const REPO=path.resolve(__dirname,'..');
const DATA=fs.mkdtempSync(path.join(os.tmpdir(),'applypilot-ui-dom-'));
process.env.AP_UI_TEST_DATA=DATA;
process.on('exit',()=>fs.rmSync(DATA,{recursive:true,force:true}));
class N {
 constructor(tag='#text',content=''){this.tagName=tag.toUpperCase();this.children=[];this.attrs={};this.listeners={};this.className='';this._text=content;this.value='';this.checked=false;this.disabled=false;this.required=false;this.open=false;this.parent=null;}
 append(...nodes){for(let n of nodes.flat(Infinity)){if(!(n instanceof N))n=new N('#text',String(n));n.parent=this;this.children.push(n)}}
 replaceChildren(...nodes){this.children=[];this._text='';this.append(...nodes)}
 set textContent(t){this.children=[];this._text=String(t)} get textContent(){return this._text+this.children.map(c=>c.textContent).join('')}
 setAttribute(k,v){this.attrs[k]=String(v);if(k==='class')this.className=String(v);if(k==='open')this.open=true;}
 getAttribute(k){return this.attrs[k]}
 addEventListener(k,v){(this.listeners[k]??=[]).push(v)}
 removeEventListener(k,v){this.listeners[k]=(this.listeners[k]||[]).filter(f=>f!==v)}
 async emit(k,event={}){if(this.disabled)return;for(const f of this.listeners[k]||[])await f({target:this,preventDefault(){},...event})}
 remove(){if(this.parent)this.parent.children=this.parent.children.filter(x=>x!==this)}
 reportValidity(){return !find(this,n=>n.required&&!n.checked)}
 showModal(){this.open=true}close(){this.open=false}
 querySelector(selector){return find(this,n=>n.tagName===selector.toUpperCase())}
 scrollIntoView(options){this.lastScroll=options}
 focus(options){doc.activeElement=this;this.lastFocus=options}
 get namespaceURI(){return 'http://www.w3.org/2000/svg'}
}
function all(n,p){return [...(p(n)?[n]:[]),...n.children.flatMap(c=>all(c,p))]};function find(n,p){return all(n,p)[0]}
const roots={app:new N('div'),confirmation:new N('dialog'),notifications:new N('div')};
const doc={querySelector(q){return roots[q.slice(1)]||Object.values(roots).map(root=>find(root,n=>n.attrs.id===q.slice(1))).find(Boolean)},createElement(t){return new N(t)},createElementNS(ns,t){return new N(t)},createTextNode(t){return new N('#text',String(t))}};
let cookie='',calls=[],modelConfigured=false,modelProvider='openai';
async function fetchMock(url,options={}){
 const req={path:url,cookie,model_configured:modelConfigured,model_provider:modelProvider,csrf:options.headers?.['X-CSRF-Token']||''};if(options.method==='POST')req.body=JSON.parse(options.body);
 calls.push(req);const run=cp.spawnSync(process.env.PYTHON||'python',[path.join(REPO,'tests_applypilot/ui_wsgi_bridge.py')],{input:JSON.stringify(req),encoding:'utf8'});
 if(run.status!==0)throw new Error(run.stderr);const res=JSON.parse(run.stdout);req.response_status=res.status;req.response_code=res.body.code;if(res.headers['Set-Cookie'])cookie=res.headers['Set-Cookie'].split(';')[0];
 return {ok:res.status<400,status:res.status,json:async()=>res.body};
}
// Minimal DOM simulation intentionally does not implement browser layout/CSP.
function escapeHTML(value){return String(value).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function serialize(node){
 if(node.tagName==='#TEXT')return escapeHTML(node._text);
 const attrs={...node.attrs};if(node.className)attrs.class=node.className;if(node.value&&node.tagName!=='TEXTAREA')attrs.value=node.value;
 for(const key of ['checked','disabled','required','open'])if(node[key])attrs[key]='';
 const tag=node.tagName.toLowerCase();const start=`<${tag}${Object.entries(attrs).map(([k,v])=>` ${k}="${escapeHTML(v)}"`).join('')}>`;
 if(['input','br','hr','meta','link','img'].includes(tag))return start;
 return start+escapeHTML(node.tagName==='TEXTAREA'?node.value:node._text)+node.children.map(serialize).join('')+`</${tag}>`;
}
function writePreview(){
 const css=fs.readFileSync(path.join(REPO,'applypilot_web/style.css'),'utf8');
 const html='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ApplyPilot · Layout preview</title><style>'+css+'</style><body><div style="position:fixed;bottom:0;left:0;right:0;z-index:999;background:#f7e9bb;color:#584829;padding:8px;text-align:center;font:11px sans-serif">布局预览 · 使用虚构测试数据 · 非浏览器端到端验收</div>'+serialize(roots.app)+'</body></html>';
 fs.writeFileSync('/tmp/applypilot-layout-preview.html',html);fs.writeFileSync('/tmp/applypilot-layout-preview-url.txt','data:text/html;base64,'+Buffer.from(html).toString('base64'));
}
const ctx=vm.createContext({console,Node:N,document:doc,location:{hash:''},window:{addEventListener(){},scrollTo(){}},fetch:fetchMock,AbortController,URL,Date,JSON,Map,Set,Math,String,Array,Object,Uint8Array,Error,TypeError,Number,Promise,crypto:require('crypto').webcrypto,btoa:s=>Buffer.from(s,'binary').toString('base64'),setTimeout:(fn,t)=>{const id=setTimeout(fn,t);id.unref();return id},clearTimeout});
vm.runInContext(fs.readFileSync(path.join(REPO,'applypilot_web/app.js'),'utf8'),ctx);
const run=code=>vm.runInContext(code,ctx),tick=()=>new Promise(r=>setImmediate(r));
let checks=0;function check(cond,msg){assert(cond,msg);checks++;console.log('PASS',msg)}
(async()=>{
 await tick();check(roots.app.textContent.includes('让下一程，更有方向'),'bootstrap renders overview');check(run('state.jobs.length')===3,'3 real backend fictional seed jobs');if(process.env.AP_UI_LAYOUT_PREVIEW)writePreview();
 const disclosure='虚构演示；匿名会话约24小时后过期，过期数据会清理，不用于真实长期求职记录。';
 function checkDisclosure(context){
  const main=find(roots.app,n=>n.tagName==='MAIN'&&n.attrs.id==='main');
  const banner=main?.children[0];
  check(banner?.className==='demo-notice'&&banner.attrs.role==='note'&&banner.textContent.includes(disclosure),'demo retention disclosure leads main content '+context);
  check(banner.textContent.includes('请仅使用虚构资料')&&banner.textContent.includes('系统不会向外部网站自动投递'),'demo privacy and submission limits remain visible '+context);
 }
 checkDisclosure('on bootstrap');
 for(const r of ['profile','radar','analysis','resume','tracker','overview']){run(`go('${r}')`);await tick();check(roots.app.textContent.length>100,'renders '+r);checkDisclosure('on '+r)}
 await run('refresh()');run('render()');checkDisclosure('after refresh');
 run("go('profile')");let form=find(roots.app,n=>n.attrs.id==='profile-form');let required=find(form,n=>n.required);required.checked=true;await form.emit('submit');check(run('state.profile_confirmed')===true,'profile review confirmation persists');check(calls.filter(x=>x.path==='/api/profile').at(-1).body.expected_version===1,'profile sends expected version');
 run("selectedId=jobs().find(j=>j.title==='AI 产品经理').id;go('analysis')");await run('analyze(selected())');check(roots.app.textContent.includes('信息不足，需进一步确认'),'rule eligibility unknown rendered, not success');check(roots.app.textContent.includes('硬性要求核验'),'analysis criteria rendered');
 run("go('resume')");await run('analyze(selected(),true)');check(roots.app.textContent.includes('BEFORE / 原始表述'),'resume before evidence rendered');check(roots.app.textContent.includes('AFTER / 建议表述'),'resume after evidence rendered');
 check(roots.notifications.children.at(-1).textContent.includes('简历建议已生成'),'nonempty resume result reports generated suggestions');
 // Real empty rule output must not produce a misleading generated-suggestions toast.
 ctx.resumeToastProfile=run('JSON.parse(JSON.stringify(state.profile))');
 await run("api('/api/profile',{profile:{...resumeToastProfile,experience:[],projects:[]},confirmed:true,expected_version:state.profile_version})");await run('refresh()');
 roots.notifications.replaceChildren();await run('analyze(selected(),true)');
 check(run('selected().resume.suggestions.length')===0&&roots.app.textContent.includes('当前没有可安全生成的修改建议'),'empty resume result renders the no-suggestions state');
 check(roots.notifications.children.length===1&&roots.notifications.textContent.includes('未生成可核对的简历建议')&&!roots.notifications.textContent.includes('简历建议已生成'),'empty resume result reports completion without claiming suggestions exist');
 await run("api('/api/profile',{profile:resumeToastProfile,confirmed:true,expected_version:state.profile_version})");await run('refresh({resetProfile:true})');await run('analyze(selected(),true)');delete ctx.resumeToastProfile;
 run("go('tracker')");await tick();
 const unchangedCalls=calls.length,unchangedRevision=run('selected().tracker_revision'),unchangedEvents=run('eventsCache.get(String(selected().id)).length');
 await run("updateTrack(selected(),'saved','')");await run("updateTrack(selected(),'saved',' \\t\\n　')");
 check(calls.length===unchangedCalls,'repeated blank or whitespace same-stage saves send no requests');
 check(run('selected().tracker_revision')===unchangedRevision&&run('eventsCache.get(String(selected().id)).length')===unchangedEvents,'unchanged save preserves revision and history');
 check(roots.notifications.textContent.includes('阶段和备注未变化，无需保存。'),'unchanged save explains why no record was added');
 await run("updateTrack(selected(),'saved','Fictional QA note-only update.')");
 check(run('eventsCache.get(String(selected().id)).length')===unchangedEvents+1&&run('eventsCache.get(String(selected().id)).at(-1).note')==='Fictional QA note-only update.','meaningful same-stage note still creates an event');
 await run("updateTrack(selected(),'preparing','')");
 check(run('selected().tracker_status')==='preparing'&&run('eventsCache.get(String(selected().id)).length')===unchangedEvents+2,'genuine stage change with empty note still creates an event');
 const repeatedCalls=calls.length;await run("updateTrack(selected(),'preparing','')");
 check(calls.length===repeatedCalls,'empty repeated save after a real transition sends no requests');
 const count=calls.length;let pending=run("updateTrack(selected(),'submitted','manual test')");await tick();check(roots.confirmation.open,'manual submitted opens confirmation');check(!roots.confirmation.textContent.includes('null'),'dialog has no null text');let cancel=find(roots.confirmation,n=>n.tagName==='BUTTON'&&n.textContent==='取消');await cancel.emit('click');await pending;check(calls.length===count,'cancel does not send tracking request');
 pending=run("updateTrack(selected(),'submitted','manual test')");await tick();let cb=find(roots.confirmation,n=>n.tagName==='INPUT');check(!!cb,'manual attestation checkbox shown');cb.checked=true;await cb.emit('change');await find(roots.confirmation,n=>n.tagName==='BUTTON'&&n.textContent.includes('确认并记录')).emit('click');await pending;check(run('selected().tracker_status')==='submitted','manual submitted persisted');check(roots.app.textContent.includes('用户手动声明'),'event differentiates manual attestation');check(!roots.app.textContent.includes('时间未提供'),'event actual timestamp rendered');
 const submittedCalls=calls.length;await run("updateTrack(selected(),'submitted','')");
 check(calls.length===submittedCalls&&!roots.confirmation.open,'unchanged submitted save sends no request or new attestation');
 pending=run("updateTrack(selected(),'submission_unknown','unknown test')");await tick();await find(roots.confirmation,n=>n.tagName==='BUTTON'&&n.textContent==='记录未知状态').emit('click');await pending;check(run('selected().tracker_status')==='submission_unknown','unknown status persisted');check(all(roots.app,n=>n.tagName==='OPTION'&&n.disabled).length===9,'unknown disables every exit except stay unknown or attested submitted');
 const beforeBypass=calls.length;
 for(const status of ['skipped','rejected','assessment','interview','offer','saved','preparing','filling','waiting_user'])await run(`updateTrack(selected(),'${status}','blocked bypass')`);
 check(calls.length===beforeBypass,'indirect skip, result and preparation bypasses send no request');
 check(run('selected().submission_unresolved')===true&&run('selected().tracker_status')==='submission_unknown','blocked transitions preserve durable unknown hold');
 // A stale display state must not override the backend's durable safety flag.
 run("selected().tracker_status='skipped'");await run("updateTrack(selected(),'preparing','stale display bypass')");
 check(calls.length===beforeBypass,'durable unresolved flag blocks bypass even with stale skipped display');run("selected().tracker_status='submission_unknown'");
 run("selected().tracker_status='submitted'");await run("updateTrack(selected(),'submission_unknown','')");
 check(calls.length===beforeBypass&&!roots.confirmation.open,'no-op guard compares effective unknown status despite stale submitted display');
 pending=run("updateTrack(selected(),'submitted','')");await tick();
 check(roots.confirmation.open,'genuine unknown-hold release still requires fresh attestation');
 cb=find(roots.confirmation,n=>n.tagName==='INPUT');cb.checked=true;await cb.emit('change');await find(roots.confirmation,n=>n.tagName==='BUTTON'&&n.textContent.includes('确认并记录')).emit('click');await pending;
 check(run('selected().tracker_status')==='submitted'&&!run('selected().submission_unresolved'),'confirmed genuine attestation reaches backend and clears unknown hold');
 run("go('profile');profileDraft.skills.push('新技能');profileDirty=true;render()");form=find(roots.app,n=>n.attrs.id==='profile-form');find(form,n=>n.required).checked=true;await form.emit('submit');run("go('analysis')");check(roots.app.textContent.includes('此分析已过期'),'changed profile makes old analysis visibly stale');run("go('resume')");check(roots.app.textContent.includes('这些建议已过期'),'changed profile makes resume visibly stale');
 run("go('radar')");check(roots.app.textContent.includes('本轮最多约45秒，部分来源可能失败'),'search displays bounded wait and possible source failure');
 const sourceView=run(`renderSourceSummary([{source:'cxmt',status:'ok',requests:3,verified:20,returned:20,scope:'长鑫校园招聘',pages:2,page_size:10,total_available:45,truncated:true,verification_mode:'official_complete_jd_listing',stop_reason:'page_limit_reached'}],{status:'completed'})`);
 check(sourceView.textContent.includes('长鑫官方招聘'),'China source company label is visible');
 check(sourceView.textContent.includes('实验性')&&roots.app.textContent.includes('长鑫来源为实验性，线上验收未通过'),'CXMT experimental blocked acceptance is disclosed without concealing run status');
 check(sourceView.textContent.includes('本轮读取 2 页')&&sourceView.textContent.includes('每页最多 10 条')&&sourceView.textContent.includes('来源报告共 45 条（非本轮核验数）'),'upstream total separated from fetched page counts');
 check(sourceView.textContent.includes('未逐条打开详情页'),'complete-JD listing method disclosed without individual detail-fetch claim');
 check(sourceView.textContent.includes('仍有未查岗位'),'bounded pagination remainder disclosed');
 check(sourceView.open===true,'truncated source coverage automatically expanded');
 check(run(`renderSourceSummary([{source:'cxmt',status:'failed',requests:1,verified:0,returned:0,stop_reason:'source_paused_http_429'}],{status:'failed'})`).textContent.includes('冷却中，本次未请求'),'source cooldown reason visible');
 run("go('radar');searchDraft.query='fixture'");await run('searchJobs()');check(run('state.jobs.length')===4,'mocked discovery adds backend source record');check(roots.app.textContent.includes('<img src=x onerror=alert(1)>'),'malicious job title remains literal text');check(all(roots.app,n=>n.tagName==='IMG').length===0,'malicious title creates no image nodes');check(roots.app.textContent.includes('本次搜索来源记录'),'search source scope visible');
 run("searchDraft.query='offline'");await run('searchJobs()');check(roots.app.textContent.includes('simulated_network_failure'),'failed search displays source failure');check(run('state.jobs.length')===4,'failed search does not insert fake fallback jobs');
 // Failed searches stay in run history, but obsolete search error toasts must not
 // contradict a later successful or partial run. Keep unrelated action errors.
 const oldSearchError=run('searchErrorToast');
 check(roots.notifications.children.includes(oldSearchError),'failed search creates a scoped persistent error toast');
 const unrelatedError=run("toast('unrelated profile error fixture',true)");
 let releaseSearch,heldSearches=0;
 ctx.fetch=(url,options)=>url==='/api/search'?new Promise(resolve=>{heldSearches++;releaseSearch=()=>resolve(fetchMock(url,options));}):fetchMock(url,options);
 run("searchDraft.query='partial'");const pendingSearch=run('searchJobs()');
 check(!roots.notifications.children.includes(oldSearchError)&&run('searchErrorToast')===null,'new search removes old failure toast before response arrives');
 check(roots.notifications.children.includes(unrelatedError),'new search preserves unrelated action errors');
 const staleDuringSearch=run("searchErrorToast=toast('stale search fixture',true)");
 await run('searchJobs()');
 check(heldSearches===1&&roots.notifications.children.includes(staleDuringSearch),'duplicate in-flight search does not create a request or clear active notices');
 releaseSearch();await pendingSearch;ctx.fetch=fetchMock;
 check(run('lastSearchRun.status')==='partial','partial fixture passes through real WSGI and persisted run');
 check(!roots.notifications.children.includes(staleDuringSearch)&&run('searchErrorToast')===null,'partial completion clears stale search failure toast');
 check(roots.app.textContent.includes('simulated_partial_failure'),'partial completion retains current source failure details');
 check(roots.notifications.children.includes(unrelatedError),'partial completion preserves unrelated action error');
 for(const nextQuery of ['fixture','empty']){
  run("searchDraft.query='offline'");await run('searchJobs()');
  const failedToast=run('searchErrorToast');
  run(`searchDraft.query='${nextQuery}'`);await run('searchJobs()');
  check(run('lastSearchRun.status')==='completed'&&!roots.notifications.children.includes(failedToast)&&run('searchErrorToast')===null,nextQuery+' completed search clears previous failure toast');
 }
 run("searchDraft.query='offline'");await run('searchJobs()');const previousFailure=run('searchErrorToast');await run('searchJobs()');
 check(!roots.notifications.children.includes(previousFailure)&&roots.notifications.children.includes(run('searchErrorToast')),'repeated failed search replaces rather than stacks search failure toast');
 check(run('state.jobs.length')===4,'repeated partial, failed and empty runs preserve existing jobs');
 unrelatedError.remove();
 run("go('profile');importText='姓名：测试虚构人\\n技能：Python、SQL\\n项目：演示项目\\n毕业：2027-06';render()");const oldName=run('state.profile.name');await find(roots.app,n=>n.tagName==='BUTTON'&&n.textContent.includes('提取待核对草稿')).emit('click');check(roots.app.textContent.includes('待核对的导入草稿'),'import creates review UI');check(run('state.profile.name')===oldName,'import does not change confirmed profile');check(roots.app.textContent.includes('尚未确认'),'import remains unconfirmed');
 // Actual fictional TXT, selectable-text PDF, and DOCX through the UI's reader
 // and the real WSGI parser. The DOM is simulated; native chooser/layout is not.
 const fixtureResult=cp.spawnSync(process.env.PYTHON||'python',[path.join(REPO,'tests_applypilot/resume_fixtures.py')],{encoding:'utf8'});
 if(fixtureResult.status!==0)throw Error(fixtureResult.stderr);const fixtures=JSON.parse(fixtureResult.stdout);
 for(const ext of ['txt','pdf','docx']){
  const data=Buffer.from(fixtures[ext],'base64');
  const fileNode=find(roots.app,n=>n.attrs.type==='file');
  fileNode.files=[{name:`fictional-resume.${ext}`,size:data.length,arrayBuffer:async()=>Uint8Array.from(data).buffer}];
  await fileNode.emit('change');
  check(find(roots.app,n=>n.attrs.type==='file')===fileNode,ext+' native file input is retained across rerenders');
  check(run('importFile.name')===`fictional-resume.${ext}`&&roots.app.textContent.includes(`已选择：fictional-resume.${ext}`),ext+' selection stays visible');
  const oldVersion=run('state.profile_version'),savedName=run('state.profile.name');
  await find(roots.app,n=>n.attrs.id==='import-extract').emit('click');
  check(run('state.profile_version')===oldVersion&&run('state.profile.name')===savedName,ext+' extraction does not overwrite the saved profile');
  check(run('importedDraft.education[0].major')==='信息管理'&&run('importedDraft.preferences.graduation_year')==='2027',ext+' structured fields arrive from the real parser');
  run('profileDirty=true');const editBefore=run('profileDraft.name');
  let load=find(roots.app,n=>n.tagName==='BUTTON'&&n.textContent==='载入表单，继续核对').emit('click');await tick();
  await find(roots.confirmation,n=>n.tagName==='BUTTON'&&n.textContent==='取消').emit('click');await load;
  check(run('profileDraft.name')===editBefore,ext+' cancel preserves current edits');
  load=find(roots.app,n=>n.tagName==='BUTTON'&&n.textContent==='载入表单，继续核对').emit('click');await tick();
  await find(roots.confirmation,n=>n.tagName==='BUTTON'&&n.textContent==='载入草稿').emit('click');await load;
  form=find(roots.app,n=>n.attrs.id==='profile-form');required=find(form,n=>n.required);
  check(run('profileDraft.name')==='陈星河（虚构文件测试）'&&!required.checked,ext+' load updates form and requires fresh human confirmation');
  check(form.lastScroll?.block==='start'&&doc.activeElement===find(form,n=>n.tagName==='INPUT'),ext+' load scrolls to form and focuses first editable input');
  await form.emit('submit');check(run('state.profile_version')===oldVersion,ext+' unconfirmed form cannot save');
  const nameInput=all(form,n=>n.tagName==='INPUT')[0];nameInput.value=`陈星河（虚构${ext.toUpperCase()}测试）`;await nameInput.emit('input');
  required.checked=true;await form.emit('submit');
  check(run('state.profile_version')===oldVersion+1&&run('state.profile.name')===nameInput.value,ext+' manual edit and confirmation save a new version');
 }
 // Changing a source invalidates the previous extracted review immediately.
 function importTextarea(){return find(roots.app,n=>n.tagName==='TEXTAREA'&&n.attrs.placeholder?.startsWith('例如：'))}
 let sourceText=importTextarea();sourceText.value='姓名：虚构来源甲';await sourceText.emit('input');
 await find(roots.app,n=>n.attrs.id==='import-extract').emit('click');
 check(run('importedDraft.name')==='虚构来源甲','first source creates its own review');
 sourceText=importTextarea();sourceText.value='姓名：虚构来源乙';await sourceText.emit('input');
 check(run('importedDraft')===null&&!find(roots.app,n=>n.attrs.id==='import-review'),'new pasted source hides the older review immediately');
 await run('refresh()');run('render()');
 check(run('importedDraft')===null&&!find(roots.app,n=>n.attrs.id==='import-review'),'refresh cannot resurrect server draft for an older source');
 await find(roots.app,n=>n.attrs.id==='import-extract').emit('click');run('profileDirty=true');
 const nameBeforeStaleLoad=run('profileDraft.name');
 let staleLoad=find(roots.app,n=>n.tagName==='BUTTON'&&n.textContent==='载入表单，继续核对').emit('click');await tick();
 sourceText=importTextarea();sourceText.value='姓名：虚构来源丙';await sourceText.emit('input');
 await find(roots.confirmation,n=>n.tagName==='BUTTON'&&n.textContent==='载入草稿').emit('click');await staleLoad;
 check(run('profileDraft.name')===nameBeforeStaleLoad&&run('importedDraft')===null,'pending load confirmation cannot apply a superseded source');
 await find(roots.app,n=>n.attrs.id==='import-extract').emit('click');
 let releaseImport;const delayedImport=new Promise(resolve=>{releaseImport=resolve});
 ctx.fetch=async(url,options)=>{if(url==='/api/profile/import')await delayedImport;return fetchMock(url,options)};
 const pendingImport=find(roots.app,n=>n.attrs.id==='import-extract').emit('click');await tick();
 const blockedLoad=find(roots.app,n=>n.tagName==='BUTTON'&&n.textContent==='载入表单，继续核对');
 check(blockedLoad?.disabled,'review load is disabled while a newer extraction is pending');
 await blockedLoad.emit('click');check(!roots.confirmation.open&&run('profileDraft.name')===nameBeforeStaleLoad,'pending extraction cannot load an older review');
 releaseImport();await pendingImport;ctx.fetch=fetchMock;
 // A pending older read cannot replace newer input or send a stale file.
 let resolveOld;ctx.slowFile={name:'slow.txt',size:5,arrayBuffer:()=>new Promise(resolve=>{resolveOld=resolve})};
 let read=run('readImportFile({target:{files:[slowFile],value:"slow.txt"}})');await tick();
 check(run('importReading')&&run('importFile')===null&&find(roots.app,n=>n.attrs.id==='import-extract').disabled,'pending read clears prior selection and disables extraction');
 check(run('importedDraft')===null&&!find(roots.app,n=>n.attrs.id==='import-review'),'selecting a new file removes the previous extracted review');
 run("go('overview');go('profile')");
 check(run('importReading')&&find(roots.app,n=>n.attrs.id==='import-extract').disabled,'navigation during a slow read retains pending state');
 let paste=find(roots.app,n=>n.tagName==='TEXTAREA'&&n.attrs.placeholder?.startsWith('例如：'));paste.value='姓名：新文本';await paste.emit('input');
 resolveOld(Uint8Array.from(Buffer.from('older')).buffer);await read;
 check(run('importText')==='姓名：新文本'&&run('importFile')===null&&!find(roots.app,n=>n.attrs.id==='import-extract').disabled,'typing cancels older file reads without overwriting text');
 read=run('readImportFile({target:{files:[slowFile],value:"slow.txt"}})');await tick();
 const newer=Buffer.from('姓名：新文件');ctx.newerFile={name:'newer.txt',size:newer.length,arrayBuffer:async()=>Uint8Array.from(newer).buffer};
 await run('readImportFile({target:{files:[newerFile],value:"newer.txt"}})');resolveOld(Uint8Array.from(Buffer.from('older')).buffer);await read;
 check(run('importFile.name')==='newer.txt','slower previous selection cannot overwrite a newer file');
 const retainedChooser=find(roots.app,n=>n.attrs.type==='file');run("go('radar');go('profile');render();render()");
 check(find(roots.app,n=>n.attrs.type==='file')===retainedChooser&&run('importFile.name')==='newer.txt','navigation and repeated renders retain the same native chooser and file');
 for(const invalid of [{name:'bad.doc',size:3},{name:'large.txt',size:2097153},{name:'empty.txt',size:0},{name:'broken.txt',size:3,arrayBuffer:async()=>{throw Error('fixture read failure')}}]){
  ctx.invalidFile=invalid;await run('readImportFile({target:{files:[invalidFile],value:"invalid"}})');
  check(run('importFile')===null&&!run('importReading'),'invalid or unreadable '+invalid.name+' clears stale selection and recovers');
 }
 delete ctx.slowFile;delete ctx.newerFile;delete ctx.invalidFile;
 const version=run('state.profile_version');await fetchMock('/api/profile',{method:'POST',headers:{'X-CSRF-Token':run('state.csrf')},body:JSON.stringify({profile:run('state.profile'),confirmed:true,expected_version:version})});form=find(roots.app,n=>n.attrs.id==='profile-form');find(form,n=>n.required).checked=true;await form.emit('submit');check(roots.app.textContent.includes('服务器已有版本'),'concurrent version error refreshes latest version');check(roots.notifications.textContent.includes('编辑内容已保留'),'stale conflict preserves edits');
 // Simulate a provider being configured after bootstrap: first action is rejected,
 // then fresh state reveals the provider and the next action requires explicit consent.
 modelConfigured=true;run("go('analysis')");await run('analyze(selected())');
 check(calls.filter(x=>x.path.endsWith('/analyze')).at(-1).body.model_consent===undefined&&run('state.model.configured')===true,'configuration change rejects silent model action and refreshes state');
 check(run('state.model.configured')===true,'consent error refreshes configured provider state');
 let consentPending=run('analyze(selected())');await tick();check(roots.confirmation.open,'configured model opens transmission consent');
 let beforeCancel=calls.length;await find(roots.confirmation,n=>n.tagName==='BUTTON'&&n.textContent==='取消').emit('click');await consentPending;check(calls.length===beforeCancel,'canceling model consent sends no request');
 consentPending=run('analyze(selected())');await tick();let consentBox=find(roots.confirmation,n=>n.tagName==='INPUT');consentBox.checked=true;await consentBox.emit('change');await find(roots.confirmation,n=>n.tagName==='BUTTON'&&n.textContent==='允许并继续').emit('click');await consentPending;
 check(calls.filter(x=>x.path.endsWith('/analyze')).at(-1).body.model_consent==='openai','approved model request includes explicit provider consent');
 check(!roots.confirmation.open,'consent dialog closes after approval');
 // DeepSeek is a distinct consent destination; never infer OpenAI consent.
 modelProvider='deepseek';await run('refresh()');run("go('analysis')");
 check(roots.app.textContent.includes('DeepSeek 已配置'),'configured provider label identifies DeepSeek');
 consentPending=run('analyze(selected())');await tick();
 check(roots.confirmation.textContent.includes('DeepSeek API（api.deepseek.com）')&&!roots.confirmation.textContent.includes('OpenAI'),'DeepSeek modal names exact provider without OpenAI fallback');
 beforeCancel=calls.length;await find(roots.confirmation,n=>n.tagName==='BUTTON'&&n.textContent==='取消').emit('click');await consentPending;
 check(calls.length===beforeCancel,'canceling DeepSeek consent sends no request');
 consentPending=run('analyze(selected(),true)');await tick();consentBox=find(roots.confirmation,n=>n.tagName==='INPUT');consentBox.checked=true;await consentBox.emit('change');await find(roots.confirmation,n=>n.tagName==='BUTTON'&&n.textContent==='允许并继续').emit('click');await consentPending;
 let providerRequest=calls.filter(x=>x.path.endsWith('/resume')).at(-1);
 check(providerRequest.body.model_consent==='deepseek'&&providerRequest.response_status===200,'DeepSeek approval sends exact token and backend accepts');
 // Server configuration changes while the page still displays DeepSeek.
 modelProvider='openai';consentPending=run('analyze(selected())');await tick();consentBox=find(roots.confirmation,n=>n.tagName==='INPUT');consentBox.checked=true;await consentBox.emit('change');await find(roots.confirmation,n=>n.tagName==='BUTTON'&&n.textContent==='允许并继续').emit('click');await consentPending;
 providerRequest=calls.filter(x=>x.path.endsWith('/analyze')).at(-1);
 check(providerRequest.body.model_consent==='deepseek'&&providerRequest.response_code==='model_consent_required','stale DeepSeek consent is rejected after server switches provider');
 check(run('state.model.provider')==='openai','stale-provider rejection refreshes current provider');
 // A state refresh while a dialog is open must not rebind the user's approval.
 consentPending=run('analyze(selected())');await tick();modelProvider='deepseek';await run('refresh()');
 consentBox=find(roots.confirmation,n=>n.tagName==='INPUT');consentBox.checked=true;await consentBox.emit('change');await find(roots.confirmation,n=>n.tagName==='BUTTON'&&n.textContent==='允许并继续').emit('click');await consentPending;
 providerRequest=calls.filter(x=>x.path.endsWith('/analyze')).at(-1);
 check(providerRequest.body.model_consent==='openai'&&providerRequest.response_code==='model_consent_required','open dialog approval remains bound to originally displayed provider');
 modelProvider='unrecognized';await run('refresh()');run("go('analysis')");const unknownRequests=calls.length;await run('analyze(selected())');
 check(calls.length===unknownRequests&&!roots.confirmation.open,'unknown provider stops before dialog or request');
 check(run('modeLabel()').includes('提供方未知')&&!run('modeLabel()').includes('OpenAI'),'unknown provider never receives an OpenAI label');
 check(run("modeLabel({mode:'model',model_provider:'deepseek'})").includes('DeepSeek'),'historical result label uses recorded provider');
 check(!run("modeLabel({mode:'model',model_provider:'unknown'})").includes('OpenAI'),'unidentified historical result does not fall back to OpenAI');
 // File decoding uses the same real import endpoint; generated document extraction is
 // separately covered by backend tests. Here validate TXT selection and review only.
 modelConfigured=false;await run('refresh()');run("go('profile')");
 const content=Buffer.from('姓名：文件示例\n技能：SQL\n毕业：2027-06','utf8');
 const file={name:'fictional.txt',size:content.length,arrayBuffer:async()=>content.buffer.slice(content.byteOffset,content.byteOffset+content.byteLength)};
 ctx.testFile=file;await run('readImportFile({target:{files:[testFile]}})');
 check(roots.app.textContent.includes('已选择：fictional.txt'),'selected TXT file is visible');
 await find(roots.app,n=>n.tagName==='BUTTON'&&n.textContent.includes('提取待核对草稿')).emit('click');
 check(calls.filter(x=>x.path==='/api/profile/import').at(-1).body.filename==='fictional.txt','file import sends base64 contract');
 check(run('importedDraft.resume_text').includes('文件示例'),'file content extracted into unconfirmed draft');
 check(calls.filter(x=>x.body).every(x=>!!x.csrf),'all client mutations send CSRF header');
 console.log(JSON.stringify({checks,requests:calls.length,classification:'DOM simulation + real WSGI/SQLite/rule-runtime; source discovery mocked; not a browser E2E test'},null,2));
})().catch(e=>{console.error(e);process.exitCode=1});
