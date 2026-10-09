/* ApplyPilot: dependency-free, same-origin client. All remote text uses textContent. */
const ROOT = document.querySelector('#app');
const DIALOG = document.querySelector('#confirmation');
const ROUTES = [
  ['overview', '总览', 'grid', '01'], ['profile', '个人档案', 'user', '02'],
  ['radar', '岗位雷达', 'radar', '03'], ['analysis', '匹配分析', 'target', '04'],
  ['resume', '简历工作台', 'file', '05'], ['tracker', '投递追踪', 'layers', '06'],
];
const STATUS = {saved:'已收藏',skipped:'已跳过',preparing:'准备材料',filling:'填写中',waiting_user:'等待确认',submission_unknown:'提交结果未知',submitted:'已投递（手动记录）',assessment:'笔试 / 测评',interview:'面试中',offer:'已获 Offer',rejected:'已结束'};
const MODEL_PROVIDERS = {openai:{label:'OpenAI',destination:'OpenAI API（api.openai.com）'},deepseek:{label:'DeepSeek',destination:'DeepSeek API（api.deepseek.com）'}};
const TYPE = {campus:'校园招聘',internship:'实习招聘',all:'所有招聘类型'};
const VERDICT = {pass:'已知硬性要求通过',fail:'存在不满足的硬性要求',unknown:'信息不足，需进一步确认'};
const PATHS = {
  grid:['M3 3h7v7H3z','M14 3h7v7h-7z','M3 14h7v7H3z','M14 14h7v7h-7z'],
  user:['M20 21v-2a7 7 0 0 0-14 0v2','M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8'],
  radar:['M21 12a9 9 0 1 1-9-9','M17 12a5 5 0 1 1-5-5','M12 12 21 3','M12 12h.01'],
  target:['M21 12a9 9 0 1 1-9-9','M17 12a5 5 0 1 1-5-5','M12 12 21 3','M17 3h4v4'],
  file:['M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z','M14 2v6h6','M8 13h8','M8 17h5'],
  layers:['m12 3 10 6-10 6L2 9z','m2 14 10 6 10-6','m2 19 10 6 10-6'],
  shield:['m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6z','m8 12 3 3 5-6'],
  arrow:['M5 12h14','m14 7 5 5-5 5'],
  external:['M14 3h7v7','M10 14 21 3','M21 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5'],
  plus:['M12 5v14','M5 12h14'],check:['m5 12 4 4L19 6'],close:['m6 6 12 12','M6 18 18 6'],
  clock:['M12 8v4l3 2','M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0'],
  search:['M21 21 16.65 16.65','M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0'],
  briefcase:['M8 6V4h8v2','M3 7h18v14H3z','M3 12c6 3 12 3 18 0','M12 11v5'],
  spark:['m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z'],
  plane:['m3 3 19 8-9 3-3 8z','M22 11 10 14 3 3'],
  info:['M12 11v6','M12 7h.01','M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0'],
  refresh:['M20 7v5h-5','M4 17v-5h5','M6 6a8 8 0 0 1 13 2l1 4','M4 12l1 4a8 8 0 0 0 13 2'],
  menu:['M4 6h16','M4 12h16','M4 18h16'],cap:['m2 9 10-5 10 5-10 5z','M6 11v6c4 3 8 3 12 0v-6','M22 9v7'],
  folder:['M3 5h7l2 3h9v13H3z'],download:['M12 3v12','m7 10 5 5 5-5','M4 16v5h16v-5'],
  question:['M9 9a3 3 0 1 1 5 2c-2 1-2 1-2 3','M12 17h.01','M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0'],
};
let state = null, route = validRoute(location.hash.slice(1)), selectedId = null, mobileOpen = false;
let profileDraft = null, editVersion = null, profileDirty = false, importedDraft = null, importText = '', importFile = null;
let searchDraft = {query:'', recruitment_type:'campus', cities:''};
let eventsCache = new Map(), eventsErrors = new Map(), lastSearchRun = null;
const trackDrafts = new Map();
const busy = new Set();

function validRoute(value) { return ROUTES.some(r => r[0] === value) ? value : 'overview'; }
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2).toLowerCase(), value);
    else if (key === 'checked' || key === 'disabled' || key === 'selected' || key === 'required') node[key] = !!value;
    else if (key === 'value') node.value = value;
    else node.setAttribute(key, String(value));
  }
  for (const child of children.flat(Infinity)) {
    if (child !== null && child !== undefined && child !== false) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}
function icon(name, size = 18) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg','svg');
  for (const [k,v] of Object.entries({viewBox:'0 0 24 24',width:size,height:size,fill:'none',stroke:'currentColor','stroke-width':1.6,'stroke-linecap':'round','stroke-linejoin':'round','aria-hidden':'true'})) svg.setAttribute(k,v);
  for (const d of PATHS[name] || PATHS.info) { const p = document.createElementNS(svg.namespaceURI,'path'); p.setAttribute('d',d); svg.append(p); }
  return svg;
}
const text = value => value === null || value === undefined ? '' : typeof value === 'string' ? value : Array.isArray(value) ? value.map(text).join(' · ') : typeof value === 'object' ? Object.entries(value).filter(([,v])=>v !== null && v !== '').map(([k,v])=>`${k}: ${text(v)}`).join('\n') : String(value);
const array = value => Array.isArray(value) ? value : value ? [value] : [];
const split = value => String(value || '').split(/[,，、\n]+/).map(s=>s.trim()).filter(Boolean);
const copy = value => JSON.parse(JSON.stringify(value));
const jobs = () => array(state?.jobs);
const selected = () => jobs().find(j=>String(j.id)===String(selectedId)) || null;
const jobPath = (j,suffix) => `/api/jobs/${encodeURIComponent(j.id)}/${suffix}`;
const statusName = status => STATUS[status] || status || '未记录';
const formatDate = value => { if (!value) return '时间未提供'; const d = new Date(value); return Number.isNaN(d.getTime()) ? text(value) : d.toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}); };
function safeURL(value) { try { const u = new URL(value); return ['https:','http:'].includes(u.protocol) ? u.href : null; } catch { return null; } }
function btn(label, handler, {style='', ico=null, disabled=false, key=null, type='button'}={}) {
  const pending = key && busy.has(key);
  return el('button',{class:`btn ${style}`,type,disabled:disabled || pending,onClick:handler},pending?el('span',{class:'spinner','aria-hidden':'true'}):ico?icon(ico,14):null,pending?'处理中…':label);
}
function tag(label, tone='') {return el('span',{class:`tag ${tone}`},label);}
function notice(content, tone='') {return el('div',{class:`notice ${tone}`,role:tone==='error'?'alert':'note'},content);}
function sectionTitle(title, action=null, subtitle='') {return el('div',{class:'section-title'},el('div',{},el('h2',{},title),subtitle?el('p',{},subtitle):null),action);}
function empty(title, description, action=null, name='radar') {return el('div',{class:'empty'},icon(name,27),el('h3',{},title),el('p',{},description),action);}
function list(items, fallback='暂无记录') {return array(items).length ? el('ul',{class:'bullet-list'},array(items).map(item=>el('li',{},text(item)))) : el('p',{class:'small-copy'},fallback);}
function toast(message, error=false) {
  const box=el('div',{class:`toast ${error?'error':''}`,role:error?'alert':'status'},el('span',{},message));
  box.append(el('button',{'aria-label':'关闭通知',onClick:()=>box.remove()},'×'));
  document.querySelector('#notifications').append(box);
  if (!error) setTimeout(()=>box.remove(),6000);
}
async function api(path, body) {
  const controller = new AbortController();
  const timer = setTimeout(()=>controller.abort(),65000);
  try {
    const options={method:body===undefined?'GET':'POST',credentials:'same-origin',headers:{Accept:'application/json'},cache:'no-store',signal:controller.signal};
    if (body!==undefined) {options.headers['Content-Type']='application/json'; options.headers['X-CSRF-Token']=state?.csrf || ''; options.body=JSON.stringify(body);}
    const response = await fetch(path,options);
    let payload; try {payload=await response.json();} catch {throw new Error('服务返回了无法读取的内容，请刷新核对状态。');}
    if (!response.ok) {const err=new Error(payload.error || `请求失败（${response.status}）`); err.code=payload.code; err.status=response.status; err.payload=payload; throw err;}
    return payload;
  } catch (error) {
    if (error.name==='AbortError') throw new Error('请求等待超时，结果尚未确认。请刷新核对，避免重复操作。');
    if (error instanceof TypeError) throw new Error('连接中断，操作结果未确认。请检查网络并刷新核对，不要重复提交。');
    throw error;
  } finally {clearTimeout(timer);}
}
async function refresh({resetProfile=false}={}) {
  state=await api('/api/state');
  if (!profileDraft || resetProfile) {profileDraft=normalizeProfile(state.profile); editVersion=state.profile_version; profileDirty=false;}
  if (!importedDraft && state.draft) importedDraft=state.draft.profile || state.draft;
  if (!selected() && jobs().length) selectedId=jobs()[0].id;
  if (!jobs().length) selectedId=null;
}
async function action(key, work, success) {
  if (busy.has(key)) return;
  busy.add(key); render();
  try {await work(); if (success) toast(success);}
  catch(error) {if(error.payload?.run)lastSearchRun=error.payload.run; try{await refresh();}catch{} toast(error.code==='version_conflict'||error.code==='stale_profile'?`${error.message}。编辑内容已保留，请查看最新档案版本后再保存。`:error.message,true);}
  finally {busy.delete(key); render();}
}
function go(next) {
  next=validRoute(next); mobileOpen=false;
  if (route!==next) {route=next; location.hash=next; window.scrollTo({top:0});}
  render();
  if(next==='tracker' && selected()) loadEvents(selected());
}
window.addEventListener('hashchange',()=>{route=validRoute(location.hash.slice(1));mobileOpen=false;render();if(route==='tracker'&&selected())loadEvents(selected());});
window.matchMedia?.('(max-width: 700px)').addEventListener('change',()=>{mobileOpen=false;render();});
window.addEventListener('keydown',event=>{if(event.key==='Escape'&&mobileOpen){mobileOpen=false;render();}});
window.addEventListener('beforeunload',event=>{if(profileDirty){event.preventDefault();event.returnValue='';}});
async function confirmDialog({title, description, check=null, confirm='确认', danger=false}) {
  if (DIALOG.open) return false;
  return new Promise(resolve=>{
    const checkbox=check?el('input',{type:'checkbox'}):null;
    const accept=btn(confirm,()=>finish(true),{style:danger?'danger':'primary',disabled:!!check});
    if(checkbox)checkbox.addEventListener('change',()=>accept.disabled=!checkbox.checked);
    const cancel=btn('取消',()=>finish(false));
    function finish(value){DIALOG.close();DIALOG.removeEventListener('cancel',onCancel);resolve(value);}
    function onCancel(event){event.preventDefault();finish(false);}
    DIALOG.replaceChildren(...[el('h2',{id:'dialog-title'},title),el('p',{},description),checkbox?el('label',{class:'checkbox-row'},checkbox,el('span',{},check)):null,el('div',{class:'button-row'},cancel,accept)].filter(Boolean));
    DIALOG.setAttribute('aria-labelledby','dialog-title');DIALOG.addEventListener('cancel',onCancel);DIALOG.showModal();
  });
}
function normalizeProfile(raw={}) {
  return {name:raw.name||'',education:array(raw.education).map(e=>({...e})),experience:array(raw.experience).map(e=>({...e})),projects:array(raw.projects).map(e=>({...e})),skills:array(raw.skills),preferences:{roles:array(raw.preferences?.roles),cities:array(raw.preferences?.cities),excluded_roles:array(raw.preferences?.excluded_roles),recruitment_type:raw.preferences?.recruitment_type||'campus',graduation_year:text(raw.preferences?.graduation_year)},resume_text:raw.resume_text||''};
}
function profileCompleteness() {const p=state.profile||{};const checks=[!!p.name,!!array(p.education).length,!!(array(p.experience).length||array(p.projects).length),!!array(p.skills).length,!!array(p.preferences?.roles).length,!!state.profile_confirmed];return {done:checks.filter(Boolean).length,total:checks.length};}
function modelProvider(provider){return Object.hasOwn(MODEL_PROVIDERS,provider)?MODEL_PROVIDERS[provider]:null;}
function modeLabel(result=null) {
  if(result){
    const provider=modelProvider(result.model_provider||result.provider);
    if(result.mode==='model')return provider?`${provider.label} 辅助 · 保留规则校验`:'模型辅助 · 提供方未记录';
    return text(result.model_status).startsWith('fallback_rules:')?'规则结果 · 模型回退':'规则分析 · 未使用模型';
  }
  const provider=modelProvider(state.model?.provider);
  if(state.model?.configured)return provider?`${provider.label} 已配置 · 逐次授权`:'提供方未知 · 已暂停';
  return provider?`规则模式 · ${provider.label} 未启用`:'规则模式 · 未配置模型';
}
function modelStatusText(value){
  const status=text(value);
  if(status==='not_configured')return '未配置模型。此结果由规则与已确认原文生成。';
  if(status==='used_grounded_model_output')return '已使用模型返回的受证据约束结果，资格结论仍由规则核验。';
  if(status==='no_reviewed_evidence')return '缺少可用的已确认经历，未发送模型请求。';
  if(status.startsWith('fallback_rules:'))return `模型请求未成功或未通过证据检查，已回退到规则结果。原因：${status.slice(15)}`;
  return status||'服务未返回进一步状态说明。';
}
function header(title, eyebrow, desc, right=null){return el('div',{class:'page-heading'},el('div',{},el('div',{class:'eyebrow'},eyebrow),el('h1',{},title),el('p',{},desc)),right);}
function render() {
  if(!state)return;
  const nav=el('nav',{class:'navigation','aria-label':'主要导航'},ROUTES.map(([id,label,ico,num])=>el('button',{class:`nav-item ${route===id?'active':''}`,type:'button','aria-current':route===id?'page':null,onClick:()=>go(id)},icon(ico,17),label,el('span',{class:'nav-number'},num))));
  const sidebar=el('aside',{class:`sidebar ${mobileOpen?'open':''}`,inert:window.matchMedia?.('(max-width: 700px)').matches&&!mobileOpen?'':null},el('div',{class:'brand'},el('div',{class:'brand-mark','aria-hidden':'true'},'A'),'ApplyPilot'),el('div',{class:'brand-tag'},'YOUR NEXT CHAPTER'),el('div',{class:'nav-label'},'求职工作台'),nav,el('div',{class:'sidebar-bottom'},el('div',{class:'privacy-note'},el('strong',{},icon('shield',14),'每一步，由你掌舵'),el('p',{},'信息可追溯，建议有依据。外部投递始终由你确认并完成。')),el('div',{class:'session-status'},el('i',{class:'status-dot'}),'独立演示会话'),el('p',{class:'session-version'},'APPLYPILOT / CAMPUS EDITION'),state.session_expires_at?el('p',{class:'session-expiry'},`会话到期：${formatDate(state.session_expires_at*1000)}`):null));
  const topbar=el('header',{class:'topbar'},el('div',{class:'breadcrumbs'},el('button',{class:'mobile-menu',type:'button','aria-label':mobileOpen?'关闭菜单':'打开菜单','aria-expanded':mobileOpen,onClick:()=>{mobileOpen=!mobileOpen;render();}},icon('menu',20)),el('span',{},'工作空间'),el('span',{class:'crumb-separator'},'/'),el('strong',{},ROUTES.find(r=>r[0]===route)[1])),el('div',{class:'topbar-right'},el('span',{class:'header-label'},'CHINA CAMPUS'),el('span',{class:'mode-pill'},el('i',{class:'status-dot'}),modeLabel()),el('button',{class:'topbar-refresh',type:'button','aria-label':'刷新会话状态',disabled:busy.has('refresh'),onClick:()=>action('refresh',()=>refresh(),'状态已刷新。')},icon('refresh',15)),el('div',{class:'avatar',title:'虚构演示档案'},(state.profile?.name||'演示').slice(0,1))));
  const content=el('main',{id:'main',class:'content',tabindex:'-1'},el('div',{class:'demo-notice'},el('span',{},icon('info',13),'公开演示环境，请仅使用虚构资料。会话默认保留 24 小时；系统不会向外部网站自动投递。'),el('b',{class:'demo-badge'},'PUBLIC DEMO')));
  const pages={overview:renderOverview,profile:renderProfile,radar:renderRadar,analysis:renderAnalysis,resume:renderResume,tracker:renderTracker};
  content.append(pages[route](),el('footer',{class:'footnote'},el('span',{},'ApplyPilot · 为下一程，做更充分的准备'),el('span',{},'可追溯的信息 / 有依据的建议 / 由你确认的行动')));
  ROOT.replaceChildren(el('div',{class:'app-shell'},sidebar,el('button',{class:`mobile-overlay ${mobileOpen?'open':''}`,type:'button','aria-label':'关闭导航',tabindex:'-1',onClick:()=>{mobileOpen=false;render();}}),el('div',{class:'main-shell'},topbar,content)));
}
function verificationTag(job) {
  if(job.record_type==='demo')return tag('虚构示例','amber');
  if(job.record_type==='announcement')return tag('官方招聘公告','blue');
  if(job.record_type==='lead')return tag('招聘线索 · 待核验','amber');
  const v=job.verification?.status;
  if(v==='verified'||v==='verified_open'||v==='live_verified'||v==='verified_live')return tag('已核验来源','teal');
  if(v==='cached'||v==='stale')return tag('缓存信息 · 请复核','amber');
  return tag(v?`核验状态：${v}`:'来源待核验','amber');
}
function availabilityTag(job) {const labels={open:'招聘中',active:'招聘中',closed:'已关闭',unknown:'招聘状态待确认',expired:'已过期',unverified:'招聘状态待核验'};return tag(labels[job.availability]||text(job.availability)||'招聘状态待确认',job.availability==='closed'||job.availability==='expired'?'red':'');}
function companyLogo(job,index=0){return el('div',{class:`company-logo tone-${index%3}`,'aria-hidden':'true'},(job.company||'?').slice(0,1));}
function jobTags(job){return el('div',{class:'tags'},verificationTag(job),tag(TYPE[job.recruitment_type]||'类型待确认'),availabilityTag(job));}
function compactJob(job,index=0){return el('div',{class:'job-compact'},companyLogo(job,index),el('div',{class:'job-compact-content'},el('h3',{},job.title||'未命名岗位'),el('p',{},`${job.company||'企业待确认'} · ${text(job.city)||'地点待确认'}`),el('div',{class:'tags'},verificationTag(job),tag(statusName(job.tracker_status)))),btn('查看',()=>{selectedId=job.id;go('radar');},{style:'small',ico:'arrow'}));}
function renderOverview(){
  const p=profileCompleteness();const analyzed=jobs().filter(j=>j.analysis);const submitted=jobs().filter(j=>['submitted','assessment','interview','offer','rejected'].includes(j.tracker_status));
  const hero=el('section',{class:'hero'},el('div',{class:'hero-copy'},el('div',{class:'hero-label'},icon('spark',13),'A THOUGHTFUL START'),el('h2',{},'机会很多，',el('br'),'找到真正适合你的方向。'),el('p',{},'从梳理经历到核验岗位，从有据可依的匹配到投递追踪。把下一步，走得更清晰。'),btn(state.profile_confirmed?'探索适合的岗位':'从确认个人档案开始',()=>go(state.profile_confirmed?'radar':'profile'),{style:'dark',ico:'arrow'})),el('div',{class:'hero-art','aria-hidden':'true'},el('div',{class:'orbit'}),el('div',{class:'orbit two'}),el('div',{class:'orbit three'}),el('div',{class:'journey-core'},icon('plane',40)),el('div',{class:'journey-tag t1'},icon('check',12),'真实经历'),el('div',{class:'journey-tag t2'},icon('shield',12),'来源可核验'),el('div',{class:'journey-tag t3'},icon('target',12),'更有方向'),el('span',{class:'small-spark'},'✧')));
  const stats=[['档案就绪项',`${p.done} / ${p.total}`,state.profile_confirmed?'已由你确认 · 可用于分析':'确认档案后，开启匹配分析','user'],['工作台岗位',String(jobs().length),`${jobs().filter(j=>j.record_type!=='demo').length} 条非示例记录 · 含待核验线索`,'radar'],['已分析岗位',String(analyzed.length),'基于已确认档案与已知要求','target'],['投递与后续',String(submitted.length),'手动跟进记录，不代表自动投递','layers']];
  const workflow=[['profile','梳理个人档案','让每一项经历都有依据',state.profile_confirmed],['radar','发现目标岗位','从官方来源寻找机会',jobs().some(j=>j.record_type!=='demo')],['analysis','理解匹配与差距','先核验，再决定下一步',analyzed.length>0],['tracker','准备并记录投递','你的确认，始终在场',submitted.length>0]];
  const current=workflow.findIndex(w=>!w[3]);
  const runs=array(state.runs).slice(-4).reverse();
  const activity=el('div',{},el('div',{class:'activity'},el('div',{class:'activity-icon'},icon('user',12)),el('div',{},el('h3',{},state.profile_confirmed?'个人档案已确认':'个人档案等待确认'),el('p',{},`版本 ${state.profile_version ?? 0} · ${state.profile_confirmed?'分析仅引用这份确认后的档案':'示例内容也需要先检查并确认'}`))),runs.map(run=>el('div',{class:'activity'},el('div',{class:'activity-icon'},icon('radar',12)),el('div',{},el('h3',{},runTitle(run)),el('p',{},runSummary(run)),el('time',{},formatDate(run.finished_at||run.created_at||run.started_at))))));
  return el('div',{},header('让下一程，更有方向。','YOUR CAREER, IN FOCUS','从这里，开始一次更从容的求职。',el('span',{class:'page-date'},new Date().toLocaleDateString('zh-CN',{year:'numeric',month:'long',day:'numeric',weekday:'long'}))),hero,el('section',{class:'stats','aria-label':'当前会话统计'},stats.map(([label,value,note,ico])=>el('div',{class:'stat'},el('div',{class:'stat-top'},label,el('span',{class:'stat-icon'},icon(ico,15))),el('div',{class:'stat-value'},value),el('p',{class:'stat-note'},note)))),sectionTitle('你的求职路径',null,'每一个阶段，都留下清楚的依据。'),el('section',{class:'workflow'},workflow.map(([id,label,desc,done],index)=>el('button',{class:`workflow-step ${done?'ready':index===current?'current':''}`,type:'button',onClick:()=>go(id)},el('span',{class:'step-number'},done?icon('check',12):`0${index+1}`),el('div',{},el('h3',{},label),el('p',{},desc))))),el('div',{class:'overview-grid'},el('section',{class:'card card-pad'},sectionTitle('工作台里的机会',btn('全部岗位',()=>go('radar'),{style:'ghost',ico:'arrow'})),jobs().length?jobs().slice(0,3).map(compactJob):empty('下一份机会，从搜索开始','搜索官方招聘来源，查看能够核验的岗位与线索。',btn('前往岗位雷达',()=>go('radar'),{style:'small'}))),el('section',{class:'card card-pad'},sectionTitle('最近的工作记录',tag('本次会话')),activity)));
}
function runTitle(run){const kind=run.kind||run.type||run.action;const name={search:'岗位搜索',analyze:'匹配分析',analysis:'匹配分析',resume:'简历建议',profile_import:'简历导入'}[kind]||'工作台执行记录';return `${name} · ${{success:'完成',succeeded:'完成',completed:'完成',failed:'失败',partial:'部分来源失败',running:'执行中'}[run.status]||run.status||'已记录'}`;}
function runSummary(run){if(run.error)return text(run.error);const result=run.result||{};if(result.message)return text(result.message);const count=run.job_count??run.result_count??result.job_count??result.found;const failures=array(run.failures||result.failures);return [(run.query||run.request?.query)?`搜索：${run.query||run.request?.query}`:'',count!==undefined?`${count} 条记录`:'',failures.length?`${failures.length} 个来源未成功`:''].filter(Boolean).join(' · ')||'查看岗位来源与结果，不以执行记录代表真实投递。';}
function inputField(label,value,onChange,{type='text',placeholder='',hint='',wide=false,rows=3,maxLength=4000}={}){
  const control=el(type==='textarea'?'textarea':'input',{type:type==='textarea'?null:type,value:value??'',placeholder,maxlength:maxLength,rows:type==='textarea'?rows:null,onInput:event=>{onChange(event.target.value);profileDirty=true;}});
  return el('label',{class:`field ${wide?'span-two':''}`},el('span',{},label),control,hint?el('small',{},hint):null);
}
function selectField(label,value,options,onChange){const select=el('select',{onChange:event=>{onChange(event.target.value);profileDirty=true;}},options.map(([id,label])=>el('option',{value:id,selected:value===id},label)));return el('label',{class:'field'},el('span',{},label),select);}
function formRows(key,title,ico,fields){
  const items=profileDraft[key];
  return el('section',{class:'card form-section'},el('h2',{},icon(ico,16),title),items.map((item,index)=>el('div',{class:'form-row'},el('div',{class:'form-row-title'},`${title} ${index+1}`,btn('移除',()=>{items.splice(index,1);profileDirty=true;render();},{style:'ghost small'})),el('div',{class:'form-grid'},fields.map(([field,label,options])=>inputField(label,item[field],value=>item[field]=value,options))))),btn(`添加${title}`,()=>{items.push(key==='education'?{}:{id:`${key}-${Date.now()}-${items.length}`});profileDirty=true;render();},{style:'small',ico:'plus'}));
}
function renderProfile(){
  const p=profileDraft;
  const save=async event=>{event.preventDefault();if(!event.target.reportValidity())return;await action('profile-save',async()=>{await api('/api/profile',{profile:p,confirmed:true,expected_version:editVersion});importedDraft=null;await refresh({resetProfile:true});eventsCache.clear();},'档案已确认保存。后续分析将引用这个版本。');};
  const form=el('form',{id:'profile-form',onSubmit:save},
    el('section',{class:'card form-section'},el('h2',{},icon('user',16),'基本信息与求职方向'),el('div',{class:'form-grid'},inputField('演示姓名',p.name,v=>p.name=v,{placeholder:'请使用虚构姓名',maxLength:80}),inputField('毕业年份',p.preferences.graduation_year,v=>p.preferences.graduation_year=v,{placeholder:'例如 2027',maxLength:10}),inputField('目标岗位',p.preferences.roles.join('，'),v=>p.preferences.roles=split(v),{placeholder:'产品经理，数据分析',hint:'使用逗号分隔'}),inputField('意向城市',p.preferences.cities.join('，'),v=>p.preferences.cities=split(v),{placeholder:'上海，北京',hint:'未填写时不限制城市'}),selectField('招聘类型',p.preferences.recruitment_type,Object.entries(TYPE),v=>p.preferences.recruitment_type=v),inputField('排除的岗位方向',p.preferences.excluded_roles.join('，'),v=>p.preferences.excluded_roles=split(v),{placeholder:'按需填写，使用逗号分隔'}))),
    formRows('education','教育经历','cap',[['institution','学校',{placeholder:'虚构大学名称'}],['degree','学历',{placeholder:'本科 / 硕士 / 博士'}],['major','专业',{placeholder:'专业名称'}],['graduation','毕业时间',{placeholder:'例如 2027-06'}]]),
    formRows('experience','实践经历','briefcase',[['organization','组织 / 公司',{placeholder:'虚构组织'}],['role','角色 / 职位',{}],['description','实际做过的工作',{type:'textarea',wide:true,rows:4,hint:'仅写已有事实；不编造职责、成果或量化指标。'}]]),
    formRows('projects','项目经历','folder',[['name','项目名称',{wide:true}],['description','项目内容与个人贡献',{type:'textarea',wide:true,rows:4}]]),
    el('section',{class:'card form-section'},el('h2',{},icon('spark',16),'技能与原始文本'),el('div',{class:'form-grid'},inputField('已有技能',p.skills.join('，'),v=>p.skills=split(v),{wide:true,placeholder:'Python，SQL，用户研究',hint:'使用逗号分隔；只记录真实掌握的技能。'}),inputField('简历原始文本',p.resume_text,v=>p.resume_text=v,{type:'textarea',wide:true,rows:6,maxLength:30000,hint:'演示资料会保存在当前服务端会话中，请勿填入真实联系方式或其他个人敏感信息。'}))),
    el('label',{class:'checkbox-row'},el('input',{type:'checkbox',required:true,checked:state.profile_confirmed&&!profileDirty}),el('span',{},'我已核对以上内容，确认这些是用于本次公开演示的虚构资料。')),
    el('div',{class:'form-actions'},el('p',{class:'small-copy'},`正在编辑版本 ${editVersion ?? 0}。保存将使旧版分析与建议需要重新核验。`),btn('确认并保存档案',null,{style:'primary',ico:'check',key:'profile-save',type:'submit'})));
  const importCard=el('section',{class:'card card-pad'},sectionTitle('从简历开始',null,'导入 → 人工核对 → 确认保存'),el('p',{class:'small-copy'},'粘贴文本或上传 TXT、PDF、DOCX。导入仅生成待核对草稿，不会自动确认档案。'),el('label',{class:'field gap-top'},el('span',{},'粘贴虚构简历文本'),el('textarea',{rows:8,maxlength:30000,placeholder:'例如：\n林晓｜2027 届\n示例大学，计算机科学，本科\n技能：Python、SQL\n项目：校园活动报名系统…',value:importText,onInput:event=>{importText=event.target.value;importFile=null;}})),el('label',{class:'field gap-top'},el('span',{},'或选择简历文件（不超过 2 MiB）'),el('input',{class:'file-input',type:'file',accept:'.txt,.pdf,.docx,text/plain,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document','aria-label':'选择 TXT、PDF 或 DOCX 简历',onChange:readImportFile})),btn('提取待核对草稿',()=>action('profile-import',async()=>{if(!importText.trim()&&!importFile)throw new Error('请先粘贴文本或选择简历文件。');const result=await api('/api/profile/import',importFile?{filename:importFile.name,content_base64:importFile.base64}:{text:importText});importedDraft=result.draft?.profile||result.draft;await refresh();},'草稿已提取。请检查并载入表单，核对后再确认保存。'),{style:'primary full-width gap-top',ico:'file',key:'profile-import'}),importFile?el('p',{class:'small-copy gap-top'},`已选择：${importFile.name}`):null,el('p',{class:'disabled-hint'},'PDF 最多 30 页，需可选中文本；不支持扫描件 OCR 或加密文档。解析失败会明确提示，不会假装读懂文件。'),importedDraft?renderImportReview():null);
  const help=el('section',{class:'card card-pad'},sectionTitle('关于信息与隐私'),list(['公开演示仅使用虚构资料。','导入草稿及确认档案保存在服务器上的独立会话中，默认 24 小时过期。','规则模式不调用外部模型。模型辅助分析会逐次请求授权。','简历建议只能引用你已确认的经历和技能。']));
  return el('div',{},header('先认识你，再寻找机会。','YOUR FOUNDATION','把经历整理清楚，让之后的每一个建议都有出处。',tag(state.profile_confirmed?'已确认档案':'等待确认',state.profile_confirmed?'teal':'amber')),editVersion!==state.profile_version?notice(`服务器已有版本 ${state.profile_version}，你仍在编辑版本 ${editVersion}。请先保留修改，再载入最新版本。`):null,editVersion!==state.profile_version?btn('载入最新档案（放弃当前编辑）',async()=>{if(await confirmDialog({title:'载入服务器最新档案？',description:'当前尚未保存的表单修改将被替换。导入文本不会被删除。',confirm:'载入最新版本'})){profileDraft=normalizeProfile(state.profile);editVersion=state.profile_version;profileDirty=false;render();}},{style:'small gap-top'}):null,el('div',{class:'profile-layout'},form,el('aside',{class:'profile-aside'},importCard,help)));
}
async function readImportFile(event){
  const file=event.target.files?.[0];if(!file)return;
  if(!/\.(txt|pdf|docx)$/i.test(file.name)){toast('支持 TXT、PDF 和 DOCX 文件；请勿上传真实个人资料。',true);event.target.value='';return;}
  if(file.size>2*1024*1024){toast('文件超过 2 MiB，请缩小后重新选择。',true);event.target.value='';return;}
  try{const bytes=new Uint8Array(await file.arrayBuffer());let binary='';for(let i=0;i<bytes.length;i+=8192)binary+=String.fromCharCode(...bytes.subarray(i,i+8192));importFile={name:file.name,base64:btoa(binary)};importText='';render();toast(`已选择 ${file.name}，点击提取生成待核对草稿。`);}catch{toast('无法读取文件，请尝试粘贴文本。',true);}
}
function renderImportReview(){
  const draft=normalizeProfile(importedDraft);const summary=[draft.name?`姓名：${draft.name}`:'姓名：未识别',...draft.education.map(e=>`教育：${[e.institution,e.degree,e.major,e.graduation].filter(Boolean).join(' · ')}`),...draft.experience.map(e=>`经历：${[e.organization,e.role,e.description].filter(Boolean).join(' · ')}`),...draft.projects.map(e=>`项目：${[e.name,e.description].filter(Boolean).join(' · ')}`),`技能：${draft.skills.join('、')||'未识别'}`];
  return el('div',{class:'import-review'},el('h3',{},'待核对的导入草稿'),tag('尚未确认','amber'),el('div',{class:'import-preview'},summary.join('\n')),el('p',{},'载入只替换左侧编辑表单。请检查缺失或误识别字段，再点击“确认并保存档案”。'),btn('载入表单，继续核对',async()=>{if(profileDirty&&!(await confirmDialog({title:'用导入草稿替换当前编辑？',description:'尚未保存的表单修改将被替换；已保存的档案不变，仍需你再次确认保存。',confirm:'载入草稿'})))return;profileDraft=draft;editVersion=state.profile_version;profileDirty=true;render();toast('已载入草稿。请逐项核对，然后确认保存。');},{style:'small full-width gap-top'}));
}
function renderRadar(){
  const form=el('form',{class:'search-form',onSubmit:event=>{event.preventDefault();searchJobs();}},el('div',{class:'search-field'},el('label',{for:'search-query'},'岗位关键词'),el('input',{id:'search-query',value:searchDraft.query,placeholder:'例如：产品、开发、数据分析',maxlength:120,onInput:event=>searchDraft.query=event.target.value})),el('div',{class:'search-field'},el('label',{for:'search-type'},'招聘类型'),el('select',{id:'search-type',onChange:event=>searchDraft.recruitment_type=event.target.value},Object.entries(TYPE).map(([id,label])=>el('option',{value:id,selected:searchDraft.recruitment_type===id},label)))),el('div',{class:'search-field'},el('label',{for:'search-city'},'意向城市'),el('input',{id:'search-city',value:searchDraft.cities,placeholder:'不限 / 多城市用逗号分隔',maxlength:100,onInput:event=>searchDraft.cities=event.target.value})),btn('搜索官方来源',null,{style:'primary',ico:'search',type:'submit',key:'search'}));
  const run=lastSearchRun || array(state.runs).filter(r=>(r.kind||r.type||r.action)==='search').at(-1);
  const failures=array(run?.failures||run?.result?.failures);
  const sourceSummary=array(run?.source_summary||run?.result?.source_summary);
  return el('div',{},header('下一份机会，值得认真发现。','OPPORTUNITY RADAR','优先中国校招与实习。来源、核验状态和未知信息，始终可见。'),el('section',{class:'card search-card'},form,el('p',{class:'search-help'},icon('shield',12),'默认探索字节跳动官方校招详情及相关岗位，范围有限，非全网搜索。其他来源须由服务端明确配置；搜索不发送你的档案。'),searchDraft.recruitment_type==='all'?el('p',{class:'search-help'},'已选择所有招聘类型；可能包含非校招岗位。不会因零结果自动扩大来源或地区范围。'):null),busy.has('search')?notice('正在向官方招聘来源发起请求，请稍候。完成前不代表已发现或核验任何岗位。','info'):null,failures.length?notice(el('div',{},el('strong',{},`${failures.length} 个来源未成功获取`),el('div',{class:'run-failures'},failures.map(f=>el('p',{},text(f)))))):null,sourceSummary.length?renderSourceSummary(sourceSummary,run):null,el('div',{class:'radar-toolbar'},el('strong',{},`工作台共 ${jobs().length} 条记录`),el('span',{},'包含示例与历史搜索；请查看每条记录的来源标签')),jobs().length?el('div',{class:'radar-grid'},el('section',{class:'jobs-list','aria-label':'岗位列表'},jobs().map((job,index)=>el('button',{class:`job-card ${String(job.id)===String(selectedId)?'selected':''}`,type:'button','aria-pressed':String(job.id)===String(selectedId),onClick:()=>{selectedId=job.id;render();}},el('div',{class:'job-card-top'},companyLogo(job,index),el('div',{},el('h3',{},job.title||'未命名岗位'),el('p',{},`${job.company||'企业待确认'} · ${text(job.city)||'地点待确认'}`))),jobTags(job),el('div',{class:'job-card-meta'},el('span',{},statusName(job.tracker_status)),el('span',{},job.verification?.verified_at?`核验于 ${formatDate(job.verification.verified_at)}`:'尚无核验时间'))))),selected()?renderJobDetail(selected()):empty('选择一个岗位','查看岗位描述、来源证据和下一步。')):empty('尚未发现符合条件的岗位','尝试调整关键词或城市。招聘公告与岗位详情会分别标记；来源不可用会明确显示。',null,'search'));
}
function renderSourceSummary(summaries,run){
  const status={ok:'获取完成',partial:'部分失败',failed:'获取失败',not_run_budget_exhausted:'预算用尽，未运行'};
  return el('details',{class:'source-results card',open:run?.status==='failed'},el('summary',{},`本次搜索来源记录 · ${summaries.length} 个来源 · ${formatDate(run?.finished_at||run?.started_at)}`),summaries.map(source=>el('div',{class:'source-result-row'},el('div',{class:'tags'},tag({bytedance:'字节跳动官方招聘',ashby:'已配置的 Ashby 招聘站'}[source.source]||source.source||'未命名来源'),tag(status[source.status]||source.status||'状态未知',source.status==='ok'?'teal':'amber')),el('p',{},`请求 ${source.requests??'未提供'} 次 · 核验 ${source.verified??'未提供'} 条 · 返回 ${source.returned??'未提供'} 条`),el('p',{},source.source==='bytedance'?'基于官方详情关联图的有界探索，不覆盖全部校招岗位。':source.source==='ashby'?'仅查询明确配置的全球招聘站点，不代表中国校招覆盖。':text(source.scope)||'来源范围未提供'))));
}
async function searchJobs(){await action('search',async()=>{const result=await api('/api/search',{query:searchDraft.query.trim(),recruitment_type:searchDraft.recruitment_type,cities:split(searchDraft.cities)});lastSearchRun=result.run||{failures:result.failures};await refresh();if(array(result.jobs).length)selectedId=result.jobs[0].id;const count=array(result.jobs).length;toast(count?`本次搜索返回 ${count} 条记录，请检查来源与招聘状态。`:'本次搜索未返回匹配岗位。请查看来源记录或调整条件。');});}
function renderSource(job){
  const v=job.verification||{};const url=job.record_type==='demo'?null:safeURL(job.canonical_url);
  return el('div',{class:'source-box'},el('div',{class:'detail-label'},'来源与核验记录'),el('p',{},`来源：${text(v.source)||'未提供'}`),el('p',{},`核验状态：${v.status||'unknown'} · ${formatDate(v.verified_at)}`),el('p',{},`证据：${text(v.evidence)||'暂无可核验证据；请打开原始页面核实。'}`),url?el('a',{href:url,target:'_blank',rel:'noopener noreferrer'},job.record_type==='demo'?'示例链接（不代表真实招聘）':'打开原始招聘页面',icon('external',11)):el('p',{},job.record_type==='demo'?'虚构示例，没有真实招聘页面或可投递入口。':'暂无有效原始链接'));
}
function renderJobDetail(job){return el('aside',{class:'card detail-panel'},el('div',{class:'detail-label'},'JOB DETAILS'),el('h2',{},job.title||'未命名岗位'),el('p',{class:'company-info'},`${job.company||'企业待确认'} · ${text(job.city)||'地点待确认'}`),jobTags(job),el('hr',{class:'divider'}),el('h3',{class:'detail-label'},'岗位描述'),el('p',{class:'detail-body'},text(job.description)||'尚无岗位描述，请打开原始页面查看。'),el('hr',{class:'divider'}),el('h3',{class:'detail-label'},'招聘要求'),Array.isArray(job.requirements)?el('ul',{class:'detail-body list'},job.requirements.map(r=>el('li',{},text(r)))):el('p',{class:'detail-body'},text(job.requirements)||'具体要求未知，不应视为已满足。'),renderSource(job),el('div',{class:'detail-footer'},btn('查看匹配分析',()=>go('analysis'),{style:'primary',ico:'target'}),btn('准备简历',()=>go('resume'),{ico:'file'}),btn('投递追踪',()=>go('tracker'),{ico:'layers'})));}
function jobPicker(actionButton){return el('div',{class:'analysis-header'},el('label',{class:'visually-hidden',for:'selected-job'},'选择目标岗位'),el('select',{class:'job-picker',id:'selected-job',onChange:event=>{selectedId=event.target.value;render();}},jobs().map(j=>el('option',{value:j.id,selected:String(j.id)===String(selectedId)},`${j.company||'未知企业'} · ${j.title||'未命名岗位'}${j.record_type==='demo'?'（虚构示例）':''}`))),actionButton);}
function selectedContext(job){return el('div',{class:'selected-context'},el('div',{},el('h3',{},job.title),el('p',{},`${job.company} · ${text(job.city)||'地点待确认'} · ${TYPE[job.recruitment_type]||'招聘类型待确认'}`)),verificationTag(job));}
function profileGate(){return !state.profile_confirmed?notice(el('div',{},'请先核对并确认个人档案。导入草稿不会直接用于分析。 ',btn('确认个人档案',()=>go('profile'),{style:'ghost small'}))):null;}
async function analyze(job,resume=false){
  const key=`${resume?'resume':'analyze'}:${job.id}`;
  if(busy.has(key))return;
  // Consent is bound to the displayed provider, even if a refresh changes state
  // while the dialog is open. The server independently rejects stale providers.
  const configured=!!state.model?.configured;
  const approvedProvider=configured?state.model.provider:null;
  if(configured){
    const provider=modelProvider(approvedProvider);
    if(!provider){toast('模型提供方未识别，已停止发送。请刷新并检查服务端配置。',true);return;}
    const allowed=await confirmDialog({title:resume?`允许 ${provider.label} 辅助简历建议？`:`允许 ${provider.label} 辅助匹配分析？`,description:`本次操作会将已确认的最少必要经历、项目、技能及目标岗位描述发送至 ${provider.destination}；姓名和联系方式不发送。结果仍受确定性资格校验约束。请仅使用虚构资料。`,check:`我同意本次将上述虚构资料发送至 ${provider.label}。`,confirm:'允许并继续'});
    if(!allowed)return;
  }
  await action(key,async()=>{await api(jobPath(job,resume?'resume':'analyze'),configured?{model_consent:approvedProvider}:{});await refresh();},resume?'简历建议已生成，请逐条核对原文、建议和证据。':'分析已完成。请同时关注未知项与硬性要求。');
}

function renderAnalysis(){
  const job=selected();const a=job?.analysis;
  const head=header('把匹配，讲得有理有据。','FIT, WITH EVIDENCE','先检查硬性要求，再理解优势、差距与需要进一步确认的问题。');
  if(!job)return el('div',{},head,empty('先选择一个目标岗位','从岗位雷达开始，找到想进一步了解的机会。',btn('打开岗位雷达',()=>go('radar'),{style:'primary'}),'target'));
  const top=el('div',{},head,jobPicker(btn(a?'重新分析':'开始匹配分析',()=>analyze(job),{style:'primary',ico:'target',key:`analyze:${job.id}`,disabled:!state.profile_confirmed})),profileGate(),selectedContext(job),a?.stale?notice('此分析已过期：个人档案或岗位信息已更新。请重新分析后再据此行动。'):null);
  if(!a){top.append(empty('了解你与这个岗位的距离','分析将逐项核验学历、毕业时间与招聘要求。缺少证据时会标记为“未知”，不会把它当作通过。',null,'target'));return top;}
  const eligibility=['pass','fail','unknown'].includes(a.eligibility)?a.eligibility:'unknown';
  top.append(el('section',{class:'analysis-summary'},el('div',{class:`verdict ${eligibility}`},el('div',{class:'verdict-label'},icon(eligibility==='pass'?'check':eligibility==='fail'?'close':'question',14),'ELIGIBILITY CHECK'),el('h2',{},VERDICT[eligibility]),el('p',{},eligibility==='pass'?'已知要求与当前档案相符，不代表所有资格已获招聘方确认，也不保证获得录用。':eligibility==='fail'?'至少一项已知硬性条件不满足。请查看下方依据，不建议忽略要求直接行动。':'缺失信息或无法判断的要求仍然存在。先补充材料或核对官方说明，再做决定。')),el('div',{class:'card card-pad'},sectionTitle('本次分析方式'),tag(modeLabel(a),a.mode==='model'?'blue':'teal'),el('p',{class:'model-status gap-top'},modelStatusText(a.model_status)),el('p',{class:'small-copy gap-top'},`分析使用档案版本 ${a.profile_version ?? '未提供'} · 生成于 ${formatDate(a.created_at)}。资格结论来自明确证据，模型不能覆盖失败或未知项。`))),el('div',{class:'analysis-grid'},el('section',{class:'card card-pad'},sectionTitle('硬性要求核验'),array(a.hard_requirements).length?array(a.hard_requirements).map(item=>{const status=['pass','fail','unknown'].includes(item.status)?item.status:'unknown';return el('div',{class:'criteria-row'},el('span',{class:`criteria-icon ${status}`},icon(status==='pass'?'check':status==='fail'?'close':'question',17)),el('div',{},el('h3',{},({verification:'来源核验',availability:'招聘状态',degree:'学历要求',cohort:'毕业届别',cities:'意向城市',roles:'岗位方向',major:'专业要求',recruitment_type:'招聘类型',other_requirements:'其他硬性条件'}[item.name]||item.name||'未命名要求'),tag({pass:'通过',fail:'未满足',unknown:'未知'}[status],{pass:'teal',fail:'red',unknown:'amber'}[status])),el('p',{},item.reason||'未提供判定原因'),el('p',{class:'evidence'},`依据：${text(item.evidence)||'暂无证据'}`)));}):el('p',{class:'small-copy'},'没有可供核验的明确要求，不应视为满足资格。')),el('div',{class:'analysis-side'},el('section',{class:'card card-pad'},sectionTitle('已有优势'),array(a.strengths).length?array(a.strengths).map(item=>el('div',{class:'strength'},el('p',{},typeof item==='string'?item:item.claim),el('p',{class:'evidence'},evidenceDescription(item.evidence_ids,a)))):el('p',{class:'small-copy'},'尚未识别到有证据支持的优势。')),el('section',{class:'card card-pad'},sectionTitle('差距与待确认项'),list(a.gaps,'暂无明确差距；这不代表不存在未知项。'),el('hr',{class:'divider'}),el('h3',{class:'detail-label'},'建议进一步确认'),list(a.questions,'暂无额外问题。')))),el('div',{class:'button-row gap-top'},btn('进入简历工作台',()=>go('resume'),{style:'primary',ico:'file'}),btn('查看岗位原文与来源',()=>go('radar'),{ico:'external'})));
  return top;
}
function evidenceDescription(ids,result=null){
  const values=array(ids);if(!values.length)return '证据引用：未提供，请人工核验。';
  return `证据引用：${values.map(id=>{
    if(result?.stale)return text(id);
    const p=state.profile||{},parts=String(id).split('/');
    if(['experience','projects','skills'].includes(parts[0])&&/^\d+$/.test(parts[1])){
      const item=array(p[parts[0]])[Number(parts[1])];
      if(item){const label=parts[0]==='skills'?`技能：${item}`:parts[0]==='experience'?`经历 ${Number(parts[1])+1}：${item.organization||item.role||'未命名'}`:`项目 ${Number(parts[1])+1}：${item.name||'未命名'}`;return `${label}（${id}）`;}
    }
    for(const key of ['experience','projects']){const item=array(p[key]).find(e=>String(e.id)===String(id));if(item)return `${id}（${item.name||item.organization||item.role||key}）`;}
    return text(id);
  }).join('、')}`;
}

function renderResume(){
  const job=selected(),r=job?.resume;
  const head=header('把已有经历，表达得更准确。','YOUR STORY, REFINED','保留事实，调整表达。每条建议都展示原文、修改原因与引用依据。');
  if(!job)return el('div',{},head,empty('先选择一个目标岗位','简历建议需要结合明确的岗位描述。',btn('打开岗位雷达',()=>go('radar'),{style:'primary'}),'file'));
  const top=el('div',{},head,jobPicker(btn(r?'重新生成建议':'生成有据可依的建议',()=>analyze(job,true),{style:'primary',ico:'spark',key:`resume:${job.id}`,disabled:!state.profile_confirmed})),profileGate(),selectedContext(job),r?.stale?notice('这些建议已过期：个人档案或岗位信息已变化。请重新生成并核对。'):null,notice('简历建议不会新增技能、职责、数字或成果。请自行核对与采用，系统不会覆盖你的档案或向招聘方发送内容。','info'));
  if(!r){top.append(empty('好的表达，从真实经历开始','生成后，可逐条查看建议前后的区别与依据。没有足够事实支撑时，不会凭空补充内容。',null,'file'));return top;}
  top.append(el('div',{class:'radar-toolbar'},el('strong',{},`${array(r.suggestions).length} 条可核对的建议`),tag(modeLabel(r),r.mode==='model'?'blue':'teal')),el('p',{class:'model-status subtitle-line'},modelStatusText(r.model_status)));
  if(!array(r.suggestions).length){top.append(empty('当前没有可安全生成的修改建议','可能缺少经确认的相关经历，或岗位要求不够明确。你可以补充事实后重新生成。',btn('检查个人档案',()=>go('profile'),{style:'small'}),'file'));return top;}
  top.append(...array(r.suggestions).map((suggestion,index)=>el('section',{class:'card resume-suggestion'},el('div',{class:'suggestion-title'},el('h2',{},`建议 ${String(index+1).padStart(2,'0')}`),tag(suggestion.before===suggestion.after?'保留原文 · 未改写':'需人工核对',suggestion.before===suggestion.after?'teal':'amber')),el('div',{class:'compare-grid'},el('div',{class:'compare-block'},el('div',{class:'detail-label'},'BEFORE / 原始表述'),el('p',{},suggestion.before||'未提供原文')),el('div',{class:'compare-block after'},el('div',{class:'detail-label'},'AFTER / 建议表述'),el('p',{},suggestion.after||'未提供建议'))),el('p',{class:'suggestion-reason'},`${suggestion.before===suggestion.after?'原文与建议一致，当前未做修改。建议依据':'修改原因'}：${suggestion.reason||'未提供'}`),el('p',{class:'evidence'},evidenceDescription(suggestion.evidence_ids,r)))));
  return top;
}
function boardGroup(status){if(['submitted','assessment','interview'].includes(status))return 2;if(['offer','rejected','skipped'].includes(status))return 3;if(['preparing','filling','waiting_user','submission_unknown'].includes(status))return 1;return 0;}
function renderTracker(){
  const job=selected();
  const result=el('div',{},header('让每一次行动，都有记录。','FOLLOW YOUR PROGRESS','跟进机会、整理材料、记录反馈。投递状态与来源事实分开保存。'),notice('当前工作台不向外部招聘系统提交申请。“已投递”仅能在你确认已自行完成外部投递后，作为手动记录保存。','info'),el('div',{class:'tracker-summary'},el('span',{},`${jobs().length} 条岗位记录`),el('span',{},`${jobs().filter(j=>j.tracker_status==='submission_unknown').length} 条结果未知`),el('span',{},'未知结果不会自动重试')));
  if(!jobs().length){result.append(empty('还没有需要跟进的岗位','在岗位雷达中找到目标后，即可记录材料准备、投递与后续反馈。',btn('寻找岗位',()=>go('radar'),{style:'primary'}),'layers'));return result;}
  const columns=['收藏与待决定','材料准备 / 待确认','已投递与流程中','结果与已结束'];
  result.append(el('div',{class:'board'},columns.map((title,index)=>{const items=jobs().filter(j=>boardGroup(j.tracker_status)===index);return el('section',{class:'board-column'},el('h2',{class:'board-heading'},title,el('span',{},items.length)),items.length?items.map(j=>el('button',{class:`board-card ${String(selectedId)===String(j.id)?'selected':''}`,type:'button',onClick:()=>{selectedId=j.id;render();loadEvents(j);}},el('h3',{},j.title),el('p',{},`${j.company} · ${text(j.city)||'地点待确认'}`),el('div',{class:'tags'},tag(statusName(j.tracker_status),j.tracker_status==='submission_unknown'?'amber':j.tracker_status==='offer'?'teal':''),j.record_type==='demo'?tag('虚构示例','amber'):null))):el('div',{class:'board-empty'},'这个阶段暂无记录'));})));
  if(job)result.append(renderTrackDetail(job));result.append(el('section',{class:'card card-pad gap-top'},sectionTitle('安全地体验填表与确认',el('a',{class:'btn small',href:'/demo-form'},'受控填表演示（非真实投递）',icon('arrow',13))),el('p',{class:'small-copy'},'只使用虚构信息，不访问招聘网站；填写、回读后需人工确认，仅保存本会话模拟回执。')));
  return result;
}
function renderTrackDetail(job){
  const draft=trackDrafts.get(String(job.id))||{status:job.tracker_status||'saved',note:''};
  const unresolved=job.submission_unresolved||job.tracker_status==='submission_unknown';
  if(unresolved&&!['submission_unknown','submitted'].includes(draft.status))draft.status='submission_unknown';
  trackDrafts.set(String(job.id),draft);
  let next=draft.status, note=draft.note;
  const select=el('select',{onChange:event=>{next=event.target.value;draft.status=next;}},Object.entries(STATUS).map(([id,label])=>el('option',{value:id,selected:next===id,disabled:unresolved&&!['submission_unknown','submitted'].includes(id)},label)));
  const form=el('form',{onSubmit:async event=>{event.preventDefault();await updateTrack(job,next,note);}},el('label',{class:'field'},el('span',{},'记录新的阶段'),select),el('label',{class:'field gap-top'},el('span',{},'备注（仅保存在当前演示会话）'),el('textarea',{rows:3,maxlength:1000,value:note,placeholder:'例如：已在官网完成投递，等待后续通知',onInput:event=>{note=event.target.value;draft.note=note;}})),btn('保存阶段记录',null,{style:'primary gap-top',ico:'check',type:'submit',key:`track:${job.id}`}));
  const history=el('div',{class:'card card-pad'},sectionTitle('历史事件',btn('刷新记录',()=>loadEvents(job,true),{style:'ghost small',ico:'refresh',key:`events:${job.id}`})));
  const events=eventsCache.get(String(job.id));
  if(busy.has(`events:${job.id}`))history.append(el('div',{class:'loading-inline'},el('span',{class:'spinner'}),'正在读取事件记录…'));
  else if(eventsErrors.has(String(job.id)))history.append(notice(eventsErrors.get(String(job.id)),'error'));
  else if(events?.length)history.append(el('div',{class:'event-list'},events.map(event=>{const from=event.from_status||event.previous_status;const to=event.to_status||event.status||event.new_status;return el('div',{class:'event-row'},el('h4',{},to?`${from?`${statusName(from)} → `:''}${statusName(to)}`:event.event_type||event.type||'状态更新'),el('p',{},event.note||event.reason||event.summary||''),event.attestation||event.manual_attestation||event.source==='manual_attestation'||event.kind==='manual_attestation'?el('p',{},'凭据类型：用户手动声明，不代表系统自动投递。'):null,el('time',{},formatDate(event.occurred_at||event.created_at||event.timestamp||event.at)));})));
  else history.append(el('p',{class:'small-copy'},events?'暂无历史事件。':'选择岗位或点击刷新，读取已保存的历史事件。'));
  return el('section',{class:'track-detail'},el('div',{class:'card card-pad'},sectionTitle('更新跟进状态',verificationTag(job)),el('h3',{class:'track-job-title'},job.title),el('p',{class:'company-info'},job.company),unresolved?notice('结果仍待核实：请先到原始招聘网站检查提交结果。在你明确确认已自行完成投递前，状态会保持未知；不能通过跳过、结束或切换阶段解除，也不会自动重试。'):null,form),history);
}
async function updateTrack(job,status,note){
  if(busy.has(`track:${job.id}`))return;
  const current=jobs().find(item=>String(item.id)===String(job.id))||job;
  if((current.submission_unresolved||current.tracker_status==='submission_unknown')&&!['submission_unknown','submitted'].includes(status)){toast('提交结果仍待核实。请先检查招聘网站；仅能保持未知，或明确确认已自行完成投递后记录为已投递。',true);return;}
  let attestation=false;
  if(status==='submitted'){
    attestation=await confirmDialog({title:'确认你已自行完成外部投递？',description:`岗位：${job.company} · ${job.title}。本操作只保存跟进记录，不会替你向招聘网站提交申请，也不能证明招聘方已收到。`,check:'我确认已自行在招聘方渠道完成此次投递，并希望标记为手动记录。',confirm:'确认并记录已投递'});
    if(!attestation)return;
  }else if(status==='submission_unknown'){
    if(!(await confirmDialog({title:'记录为“提交结果未知”？',description:'该状态用于提醒你先核对招聘网站或确认邮件。系统不会把它当作已成功投递，也不会自动重试。',confirm:'记录未知状态'})))return;
  }
  const idempotency_key=globalThis.crypto?.randomUUID?.()||`${Date.now()}-${Math.random().toString(36).slice(2)}`;
  await action(`track:${job.id}`,async()=>{const result=await api(jobPath(job,'track'),{status,note,attestation,idempotency_key});trackDrafts.delete(String(job.id));if(result.events)eventsCache.set(String(job.id),result.events);else eventsCache.delete(String(job.id));await refresh();},status==='submitted'?'已保存“手动声明已投递”，未进行外部自动投递。':'跟进阶段已保存。');
  await loadEvents(job,true);
}
async function loadEvents(job,force=false){
  const id=String(job.id),key=`events:${id}`;
  if(busy.has(key)||(!force&&eventsCache.has(id)))return;
  busy.add(key);eventsErrors.delete(id);if(route==='tracker')render();
  try{const result=await api(jobPath(job,'events'));eventsCache.set(id,array(result.events));}
  catch(error){eventsErrors.set(id,error.message);}
  finally{busy.delete(key);if(route==='tracker')render();}
}
async function boot(){
  try{await refresh();render();if(route==='tracker'&&selected())loadEvents(selected());}
  catch(error){ROOT.replaceChildren(el('div',{class:'boot error'},el('div',{class:'brand-mark'},'A'),el('strong',{},'工作台暂时未连接'),el('span',{},error.message),btn('重新连接',boot,{style:'primary',ico:'refresh'})));}
}
boot();
