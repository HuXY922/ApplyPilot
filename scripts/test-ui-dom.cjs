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
 get namespaceURI(){return 'http://www.w3.org/2000/svg'}
}
function all(n,p){return [...(p(n)?[n]:[]),...n.children.flatMap(c=>all(c,p))]};function find(n,p){return all(n,p)[0]}
const roots={app:new N('div'),confirmation:new N('dialog'),notifications:new N('div')};
const doc={querySelector(q){return roots[q.slice(1)]},createElement(t){return new N(t)},createElementNS(ns,t){return new N(t)},createTextNode(t){return new N('#text',String(t))}};
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
 for(const r of ['profile','radar','analysis','resume','tracker','overview']){run(`go('${r}')`);await tick();check(roots.app.textContent.length>100,'renders '+r)}
 run("go('profile')");let form=find(roots.app,n=>n.attrs.id==='profile-form');let required=find(form,n=>n.required);required.checked=true;await form.emit('submit');check(run('state.profile_confirmed')===true,'profile review confirmation persists');check(calls.filter(x=>x.path==='/api/profile').at(-1).body.expected_version===1,'profile sends expected version');
 run("selectedId=jobs().find(j=>j.title==='AI 产品经理').id;go('analysis')");await run('analyze(selected())');check(roots.app.textContent.includes('信息不足，需进一步确认'),'rule eligibility unknown rendered, not success');check(roots.app.textContent.includes('硬性要求核验'),'analysis criteria rendered');
 run("go('resume')");await run('analyze(selected(),true)');check(roots.app.textContent.includes('BEFORE / 原始表述'),'resume before evidence rendered');check(roots.app.textContent.includes('AFTER / 建议表述'),'resume after evidence rendered');
 run("go('tracker')");await tick();const count=calls.length;let pending=run("updateTrack(selected(),'submitted','manual test')");await tick();check(roots.confirmation.open,'manual submitted opens confirmation');check(!roots.confirmation.textContent.includes('null'),'dialog has no null text');let cancel=find(roots.confirmation,n=>n.tagName==='BUTTON'&&n.textContent==='取消');await cancel.emit('click');await pending;check(calls.length===count,'cancel does not send tracking request');
 pending=run("updateTrack(selected(),'submitted','manual test')");await tick();let cb=find(roots.confirmation,n=>n.tagName==='INPUT');check(!!cb,'manual attestation checkbox shown');cb.checked=true;await cb.emit('change');await find(roots.confirmation,n=>n.tagName==='BUTTON'&&n.textContent.includes('确认并记录')).emit('click');await pending;check(run('selected().tracker_status')==='submitted','manual submitted persisted');check(roots.app.textContent.includes('用户手动声明'),'event differentiates manual attestation');check(!roots.app.textContent.includes('时间未提供'),'event actual timestamp rendered');
 pending=run("updateTrack(selected(),'submission_unknown','unknown test')");await tick();await find(roots.confirmation,n=>n.tagName==='BUTTON'&&n.textContent==='记录未知状态').emit('click');await pending;check(run('selected().tracker_status')==='submission_unknown','unknown status persisted');check(all(roots.app,n=>n.tagName==='OPTION'&&n.disabled).length===9,'unknown disables every exit except stay unknown or attested submitted');
 const beforeBypass=calls.length;
 for(const status of ['skipped','rejected','assessment','interview','offer','saved','preparing','filling','waiting_user'])await run(`updateTrack(selected(),'${status}','blocked bypass')`);
 check(calls.length===beforeBypass,'indirect skip, result and preparation bypasses send no request');
 check(run('selected().submission_unresolved')===true&&run('selected().tracker_status')==='submission_unknown','blocked transitions preserve durable unknown hold');
 // A stale display state must not override the backend's durable safety flag.
 run("selected().tracker_status='skipped'");await run("updateTrack(selected(),'preparing','stale display bypass')");
 check(calls.length===beforeBypass,'durable unresolved flag blocks bypass even with stale skipped display');run("selected().tracker_status='submission_unknown'");
 run("go('profile');profileDraft.skills.push('新技能');profileDirty=true;render()");form=find(roots.app,n=>n.attrs.id==='profile-form');find(form,n=>n.required).checked=true;await form.emit('submit');run("go('analysis')");check(roots.app.textContent.includes('此分析已过期'),'changed profile makes old analysis visibly stale');run("go('resume')");check(roots.app.textContent.includes('这些建议已过期'),'changed profile makes resume visibly stale');
 run("go('radar');searchDraft.query='fixture'");await run('searchJobs()');check(run('state.jobs.length')===4,'mocked discovery adds backend source record');check(roots.app.textContent.includes('<img src=x onerror=alert(1)>'),'malicious job title remains literal text');check(all(roots.app,n=>n.tagName==='IMG').length===0,'malicious title creates no image nodes');check(roots.app.textContent.includes('本次搜索来源记录'),'search source scope visible');
 run("searchDraft.query='offline'");await run('searchJobs()');check(roots.app.textContent.includes('simulated_network_failure'),'failed search displays source failure');check(run('state.jobs.length')===4,'failed search does not insert fake fallback jobs');
 run("go('profile');importText='姓名：测试虚构人\\n技能：Python、SQL\\n项目：演示项目\\n毕业：2027-06';render()");const oldName=run('state.profile.name');await find(roots.app,n=>n.tagName==='BUTTON'&&n.textContent.includes('提取待核对草稿')).emit('click');check(roots.app.textContent.includes('待核对的导入草稿'),'import creates review UI');check(run('state.profile.name')===oldName,'import does not change confirmed profile');check(roots.app.textContent.includes('尚未确认'),'import remains unconfirmed');
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
