/* Offline university-notice UI/WSGI regression.
 * Run: node scripts/test-notice-ui-dom.cjs
 * Real: app.js, WSGI API, CSRF/session controls, parsers, persisted SQLite.
 * Mocked: minimal DOM, hash navigation, fetch transport, source HTML, clock.
 * Network sockets/model calls are forbidden. This is NOT browser/layout E2E
 * or live-source verification. Existing scripts/test-ui-dom.cjs stays separate.
 */
const fs = require('fs'), vm = require('vm'), cp = require('child_process');
const assert = require('assert'), path = require('path'), os = require('os');
const REPO = path.resolve(__dirname, '..');
const DATA = fs.mkdtempSync(path.join(os.tmpdir(), 'applypilot-notice-ui-'));
process.on('exit', () => fs.rmSync(DATA, {recursive: true, force: true}));
let epoch = Date.parse('2026-10-11T12:00:00Z') / 1000;
let variant = 'normal', cookie = '', calls = [], sourceCalls = [], checks = 0, alerts = 0;

// Based on the existing dependency-free DOM harness. innerHTML throws rather
// than silently accepting dangerous HTML that this small DOM cannot execute.
class N {
  constructor(tag = '#text', content = '') {
    this.tagName = tag.toUpperCase(); this.children = []; this.attrs = {}; this.listeners = {};
    this.className = ''; this._text = content; this.value = ''; this.checked = false;
    this.disabled = false; this.required = false; this.open = false; this.parent = null;
  }
  append(...nodes) {
    for (let n of nodes.flat(Infinity)) {
      if (!(n instanceof N)) n = new N('#text', String(n));
      n.parent = this; this.children.push(n);
    }
  }
  replaceChildren(...nodes) { this.children = []; this._text = ''; this.append(...nodes); }
  set textContent(value) { this.children = []; this._text = String(value); }
  get textContent() { return this._text + this.children.map(n => n.textContent).join(''); }
  set innerHTML(value) { throw new Error('Unsafe HTML sink used in notice DOM test'); }
  setAttribute(key, value) { this.attrs[key] = String(value); if (key === 'open') this.open = true; }
  getAttribute(key) { return this.attrs[key]; }
  addEventListener(key, fn) { (this.listeners[key] ??= []).push(fn); }
  removeEventListener(key, fn) { this.listeners[key] = (this.listeners[key] || []).filter(f => f !== fn); }
  async emit(key, event = {}) {
    if (this.disabled) return;
    for (const fn of this.listeners[key] || []) await fn({target: this, preventDefault() {}, ...event});
  }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(n => n !== this); }
  reportValidity() { return !find(this, n => n.required && !n.checked); }
  showModal() { this.open = true; }
  close() { this.open = false; }
  querySelector(selector) { return find(this, n => n.tagName === selector.toUpperCase()); }
  focus() { doc.activeElement = this; }
  scrollIntoView() {}
  get namespaceURI() { return 'http://www.w3.org/2000/svg'; }
}
function all(node, predicate) {
  return [...(predicate(node) ? [node] : []), ...node.children.flatMap(n => all(n, predicate))];
}
function find(node, predicate) { return all(node, predicate)[0]; }
const roots = {app: new N('div'), confirmation: new N('dialog'), notifications: new N('div')};
const doc = {
  querySelector(selector) {
    return roots[selector.slice(1)] || Object.values(roots).map(root => find(root, n => n.attrs.id === selector.slice(1))).find(Boolean);
  },
  createElement: tag => new N(tag), createElementNS: (ns, tag) => new N(tag),
  createTextNode: value => new N('#text', String(value)),
};
const windowListeners = {};
const browserWindow = {
  addEventListener(name, fn) { (windowListeners[name] ??= []).push(fn); }, scrollTo() {},
};
class FixtureDate extends Date {
  constructor(...args) { super(...(args.length ? args : [epoch * 1000])); }
  static now() { return epoch * 1000; }
}
function bridge(req) {
  const result = cp.spawnSync(process.env.PYTHON || 'python', [path.join(REPO, 'tests_applypilot/ui_notices_bridge.py')], {
    cwd: REPO, env: {...process.env, AP_NOTICE_UI_TEST_DATA: DATA},
    input: JSON.stringify({test_epoch: epoch, test_variant: variant, ...req}), encoding: 'utf8', timeout: 15000,
  });
  if (result.status !== 0) throw new Error(result.error?.message || result.stderr || `Bridge exited ${result.status}`);
  const response = JSON.parse(result.stdout);
  sourceCalls.push(...response.test_source_calls);
  return response;
}
async function fetchMock(url, options = {}) {
  const req = {path: url, cookie, csrf: options.headers?.['X-CSRF-Token'] || ''};
  if (options.method === 'POST') req.body = JSON.parse(options.body);
  calls.push(req);
  const response = bridge(req);
  req.status = response.status; req.response = response.body;
  req.sourceCalls = response.test_source_calls; req.fingerprints = response.test_session_fingerprints;
  if (response.headers['Set-Cookie']) cookie = response.headers['Set-Cookie'].split(';')[0];
  return {ok: response.status < 400, status: response.status, json: async () => response.body};
}
const ctx = vm.createContext({
  console, Node: N, document: doc, location: {hash: '#notices'}, window: browserWindow,
  fetch: fetchMock, AbortController, URL, Date: FixtureDate, JSON, Map, Set, Math, String, Array,
  Object, Uint8Array, Error, TypeError, Number, Promise, crypto: require('crypto').webcrypto,
  alert() { alerts++; }, btoa: value => Buffer.from(value, 'binary').toString('base64'),
  setTimeout(fn, delay) { const timer = setTimeout(fn, delay); timer.unref(); return timer; }, clearTimeout,
});
const run = code => vm.runInContext(code, ctx);
const tick = () => new Promise(resolve => setImmediate(resolve));
const clone = value => JSON.parse(JSON.stringify(value));
function check(condition, description) { assert(condition, description); checks++; console.log('PASS', description); }
function button(label, root = roots.app) { return find(root, n => n.tagName === 'BUTTON' && n.textContent.includes(label)); }
function cards() { return all(roots.app, n => n.tagName === 'ARTICLE' && n.className.includes('notice-card')); }
function card(id) { const title = run(`noticeData.notices.find(n => n.id === ${JSON.stringify(id)}).title`); return cards().find(n => find(n, child => child.tagName === 'H2')?.textContent === title); }
function refreshButton() { return find(roots.app, n => n.attrs.id === 'notices-refresh'); }
async function selectSource(value) {
  const select = find(roots.app, n => n.tagName === 'SELECT'); select.value = value; await select.emit('change');
}
async function filterTitle(value) {
  const field = find(roots.app, n => n.attrs.type === 'search'); field.value = value; await field.emit('change');
}
async function hashNavigate(value) {
  ctx.location.hash = '#' + value;
  for (const fn of windowListeners.hashchange || []) await fn({});
  await tick();
}
function holdRequest(target) {
  let release, held = 0;
  ctx.fetch = (url, options) => url === target ? new Promise(resolve => {
    held++; release = () => resolve(fetchMock(url, options));
  }) : fetchMock(url, options);
  return {release() { assert(release, 'Expected held request'); release(); }, count() { return held; }, restore() { ctx.fetch = fetchMock; }};
}
vm.runInContext(fs.readFileSync(path.join(REPO, 'applypilot_web/app.js'), 'utf8'), ctx);

(async () => {
  await tick();
  check(run('route') === 'notices' && roots.app.textContent.includes('CAMPUS NOTICE RADAR'), 'cold #notices deep link renders the notice route');
  check(calls.length === 2 && calls.every(c => !c.body) && calls[1].path === '/api/notices', 'initial route only reads session and saved notices');
  check(sourceCalls.length === 0 && run('noticeData.total') === 0 && cards().length === 0, 'empty state never fetches a source or fabricates notice records');
  check(roots.app.textContent.includes('点击手动刷新') && roots.app.textContent.includes('尚未刷新'), 'empty state explains manual refresh and untouched source status');
  check(roots.app.textContent.includes('15 分钟') && roots.app.textContent.includes('无自动定时任务'), 'bounded scope, shared cooldown, and manual-only behavior are disclosed');
  check(run('state.jobs.length') === 3, 'old fictional jobs still bootstrap separately');

  // A pre-existing meaningful tracker record must survive all notice actions.
  run("go('tracker')"); await tick();
  await run("updateTrack(selected(),'preparing','Offline notice isolation fixture')");
  const oldJobs = JSON.stringify(run('state.jobs')), oldProfile = JSON.stringify(run('state.profile'));
  const jobId = run('selected().id');
  const trackerBefore = bridge({path: `/api/jobs/${jobId}/events`, cookie});
  const sessionBefore = trackerBefore.test_session_fingerprints;
  check(trackerBefore.body.events.some(e => e.note === 'Offline notice isolation fixture'), 'existing tracker update succeeds before notice work');
  run("go('notices')"); await tick();
  const readCount = calls.length;
  run("go('overview');go('notices')"); await tick();
  check(calls.length === readCount && sourceCalls.length === 0, 'ordinary route revisits reuse saved cache without fetching sources');

  const held = holdRequest('/api/notices/refresh');
  const pending = refreshButton().emit('click');
  check(run('noticeLoading') && refreshButton().disabled, 'manual refresh immediately marks loading and disables repeat clicks');
  await refreshButton().emit('click'); await run('loadNotices(true)');
  check(held.count() === 1, 'rapid repeated clicks and in-flight direct action create one request');
  run("go('overview')");
  check(roots.app.textContent.includes('让下一程，更有方向'), 'navigation remains available while notice refresh is pending');
  held.release(); await pending; held.restore();
  check(run('route') === 'overview' && roots.app.textContent.includes('让下一程，更有方向'), 'late refresh completion never navigates away from the newer page');
  await hashNavigate('notices');
  check(cards().length === 4 && run('noticeData.total') === 4, 'simulated Back navigation shows four persisted fixture notices');
  const refreshCall = calls.find(c => c.path === '/api/notices/refresh');
  check(refreshCall.status === 200 && JSON.stringify(refreshCall.body) === JSON.stringify({sources: ['nankai', 'dlut_panjin']}), 'manual refresh sends only the fixed source identifiers through real WSGI');
  check(!!refreshCall.csrf && refreshCall.sourceCalls.length === 8, 'manual refresh includes session CSRF and obeys fixture request budget');
  check(run('noticeData.notices.every(n => n.record_type === "university_notice")'), 'all persisted results keep notice identity instead of becoming jobs');
  check(JSON.stringify(refreshCall.fingerprints) === JSON.stringify(sessionBefore), 'notice refresh leaves pre-existing session SQLite bytes unchanged');
  check(roots.app.textContent.includes('后续页面未读取') && roots.app.textContent.includes('读取 1 / 1 页'), 'source coverage explicitly excludes unread later pages');

  const malicious = card('nankai:119691');
  check(malicious.textContent.includes('<img src=x onerror=alert(1)>'), 'malicious source title remains literal visible text');
  check(malicious.textContent.includes('<script>alert(2)</script>') && malicious.textContent.includes('<img src=x onerror=alert(3)>'), 'encoded malicious summary remains literal visible text');
  check(alerts === 0 && all(roots.app, n => ['IMG', 'SCRIPT', 'IFRAME'].includes(n.tagName)).length === 0, 'remote markup creates no executable DOM nodes or alert side effects');
  check(all(roots.app, n => Object.keys(n.attrs).some(key => /^on/i.test(key))).length === 0, 'remote text cannot inject inline event-handler attributes');
  check(all(roots.app, n => n.tagName === 'A' && n.attrs.target === '_blank').every(n => n.attrs.rel === 'noopener noreferrer' && n.attrs.href.startsWith('https://')), 'source links use HTTPS with isolated new tabs');
  check(malicious.textContent.includes('高校发布 / 转载：2026-10-10') && malicious.textContent.includes('首次发现：' + run("formatDate('2026-10-11T12:00:00Z')")), 'older university publication date is distinct from first discovery');
  check(malicious.textContent.includes('详情页另显示高校日期 2026-10-09'), 'conflicting list/detail university dates remain explicitly disclosed');
  check(run("noticeData.notices.every(n => n.employer_published_on === null)"), 'university metadata never invents employer publication dates');
  check(malicious.textContent.includes('截止日期：未知') && malicious.textContent.includes('10月31日'), 'deadline without a year remains unknown while preserving its original quote');
  check(card('dlut_panjin:48012').textContent.includes('截止日期原文明示：2026-11-01'), 'explicit full-year deadline renders with its evidence-based label');
  check(card('nankai:102975').textContent.includes('届别未知') && card('nankai:102975').textContent.includes('校园限制未知'), 'single-role announcement is not inferred to have a known cohort or campus eligibility');
  check(malicious.textContent.includes('招聘状态未知') && roots.app.textContent.includes('公告不等于可投递岗位'), 'notice cards avoid unsupported recruiting or eligibility claims');

  const afterRefreshCalls = calls.length, afterSourceCalls = sourceCalls.length;
  check(refreshButton().disabled && roots.app.textContent.includes('可再次请求时间'), 'cooldown disables the button and displays the next permitted time');
  await refreshButton().emit('click'); await refreshButton().emit('click');
  check(calls.length === afterRefreshCalls && sourceCalls.length === afterSourceCalls, 'repeated clicks during cooldown send no browser request');
  await run('loadNotices(true)');
  check(calls.at(-1).response.run.sources.every(s => s.status === 'cooldown') && calls.at(-1).sourceCalls.length === 0, 'backend independently enforces cooldown even if client button is bypassed');
  check(run('noticeData.total') === 4, 'cooldown preserves saved notices');
  await button('读取已有记录').emit('click');
  check(calls.at(-1).path === '/api/notices' && !calls.at(-1).body && calls.at(-1).sourceCalls.length === 0, 'read-existing action stays read-only after refresh');

  const filterCalls = calls.length;
  await selectSource('nankai'); check(cards().length === 2, 'source filter shows only the selected university');
  await filterTitle('示例公司'); check(cards().length === 1 && cards()[0] === card('nankai:119691'), 'saved-title/company filter matches existing records');
  await filterTitle('does-not-exist');
  check(cards().length === 0 && roots.app.textContent.includes('清空筛选或选择其他来源'), 'zero-result filter shows a recoverable empty state');
  check(calls.length === filterCalls, 'local source/text filters transmit no keywords or API calls');
  await filterTitle(''); await selectSource('');
  check(cards().length === 4, 'clearing filters restores all saved notices');

  const historyId = 'dlut_panjin:48012';
  await button('查看观察记录', card(historyId)).emit('click');
  check(calls.at(-1).path === `/api/notices/${historyId}/events` && calls.at(-1).status === 200, 'history opens the exact independent notice events endpoint');
  check(card(historyId).textContent.includes('首次发现') && run(`noticeHistory.get('${historyId}').events.length`) === 1, 'discovery event renders from persisted SQLite history');
  const historyCalls = calls.length;
  await button('收起记录', card(historyId)).emit('click');
  check(!card(historyId).textContent.includes('仅展示最近 12 条'), 'closing history removes the expanded event panel');
  await button('查看观察记录', card(historyId)).emit('click');
  check(calls.length === historyCalls, 'reopening unchanged history reuses its read-only cache');
  await selectSource('dlut_panjin'); await filterTitle('重庆银行');
  await hashNavigate('radar');
  check(run('route') === 'radar' && roots.app.textContent.includes('下一份机会，值得认真发现'), 'simulated Forward navigation reaches the existing jobs radar');
  await hashNavigate('notices');
  check(cards().length === 1 && card(historyId).textContent.includes('观察记录') && run('noticeQuery') === '重庆银行', 'Back navigation retains local filters and the selected notice history');

  // Advance the fixed clock past the shared cooldown, then change one parsed
  // deadline through the same real refresh endpoint. No database test seeding.
  epoch += 901; variant = 'changed'; run('render()');
  check(!refreshButton().disabled, 'elapsed cooldown permits a new refresh after normal rerender');
  await refreshButton().emit('click');
  check(JSON.stringify(calls.filter(c => c.path === '/api/notices/refresh').at(-1).body.sources) === JSON.stringify(['dlut_panjin']), 'manual refresh respects the selected fixed source');
  check(run(`noticeData.notices.find(n => n.id === '${historyId}').deadline_on`) === '2026-11-02', 'changed fixture deadline is parsed and persisted by the real backend');
  check(run(`noticeData.notices.find(n => n.id === '${historyId}').first_seen_at`) === '2026-10-11T12:00:00Z', 'changed content never rewrites original discovery time');
  await tick();
  check(!card(historyId).textContent.includes('正在读取观察记录'), 'completed source refresh never strands expanded history in an unfetched loading state');
  check(run('selectedNoticeId') === null && !find(card(historyId), n => n.className === 'notice-history'), 'successful source refresh collapses the old history panel before it can become stale');
  // Close/open is a supported user gesture whether refresh clears or reloads
  // expanded history. Assert only the fresh response, not a private cache form.
  if (run(`selectedNoticeId === '${historyId}'`)) await button('收起记录', card(historyId)).emit('click');
  await button('查看观察记录', card(historyId)).emit('click');
  check(run(`noticeHistory.get('${historyId}').events[0].event_type`) === 'content_changed', 'history refresh shows the newest content-change event');
  check(card(historyId).textContent.includes('原文或元数据有变化') && card(historyId).textContent.includes('截止日期'), 'history labels the changed deadline without embedding the entire page');

  epoch += 901; variant = 'details_unavailable'; run('render()');
  await refreshButton().emit('click');
  check(run('noticeData.run.status') === 'partial' && card(historyId).textContent.includes('最近详情读取：来源请求超时'), 'partial detail failure displays the current error separately from saved facts');
  check(card(historyId).textContent.includes('截止日期原文明示：2026-11-02') && card(historyId).textContent.includes('保留此前读取结果'), 'partial refresh retains the earlier explicit deadline with its stale-detail disclosure');

  await filterTitle(''); await selectSource('');
  epoch += 901; variant = 'unavailable'; run('render()');
  const noticesBeforeFailure = JSON.stringify(run('noticeData.notices'));
  await refreshButton().emit('click');
  check(run('noticeData.run.status') === 'unavailable' && roots.app.textContent.includes('来源请求超时'), 'source failures are explicit and never presented as successful verification');
  check(JSON.stringify(run('noticeData.notices')) === noticesBeforeFailure && cards().length === 4, 'failed refresh preserves every old notice and parsed detail');
  variant = 'normal';

  // A lost UI-to-WSGI connection must retain cache and recover using GET only.
  const readFailureCache = JSON.stringify(run('noticeData.notices'));
  const requestsBeforeLostRead = calls.length;
  ctx.fetch = async (url, options) => {
    if (url === '/api/notices') throw new TypeError('Offline synthetic disconnected transport');
    return fetchMock(url, options);
  };
  await button('读取已有记录').emit('click'); ctx.fetch = fetchMock;
  check(!run('noticeLoading') && roots.app.textContent.includes('连接中断') && roots.app.textContent.includes('已保存记录保持不变'), 'interrupted read exposes a recoverable error and releases loading');
  check(JSON.stringify(run('noticeData.notices')) === readFailureCache && calls.length === requestsBeforeLostRead, 'interrupted read preserves cached records without fallback mutation');
  await button('读取已有记录').emit('click');
  check(run('noticeError') === '' && calls.at(-1).path === '/api/notices' && calls.at(-1).sourceCalls.length === 0, 'read-only retry clears the connection error without refreshing sources');

  // Interrupted read/history responses may fill cache but cannot hijack a newer
  // route. This models event ordering, not actual browser Back/Forward layout.
  const heldRead = holdRequest('/api/notices');
  const pendingRead = button('读取已有记录').emit('click');
  run("go('tracker')"); await tick(); heldRead.release(); await pendingRead; heldRead.restore();
  check(run('route') === 'tracker' && roots.app.textContent.includes('让每一次行动，都有记录'), 'late read-only notice response preserves newer tracker navigation');
  await hashNavigate('notices');
  run('noticeHistory.clear();selectedNoticeId=null;render()');
  const heldHistory = holdRequest(`/api/notices/${historyId}/events`);
  const pendingHistory = button('查看观察记录', card(historyId)).emit('click');
  run("go('profile')"); heldHistory.release(); await pendingHistory; heldHistory.restore();
  check(run('route') === 'profile' && roots.app.textContent.includes('先认识你，再寻找机会'), 'late history response preserves newer profile navigation');
  await hashNavigate('notices');
  check(card(historyId).textContent.includes('原文或元数据有变化'), 'returning after interrupted history read uses the completed event cache');

  const missing = bridge({path: '/api/notices/nankai:99999/events', cookie});
  check(missing.status === 404 && missing.test_source_calls.length === 0, 'unknown notice history returns 404 without probing a remote URL');
  const noSession = bridge({path: '/api/notices'});
  check(noSession.status === 401, 'notice reads retain the original session requirement');
  const noCsrf = bridge({path: '/api/notices/refresh', cookie, body: {sources: ['nankai']}});
  check(noCsrf.status === 403 && noCsrf.test_source_calls.length === 0, 'notice refresh rejects missing CSRF before source access');
  const crossSite = bridge({path: '/api/notices', cookie, test_cross_site: true});
  check(crossSite.status === 403 && crossSite.test_source_calls.length === 0, 'notice reads retain the existing cross-site guard');
  const arbitrary = bridge({path: '/api/notices/refresh', cookie, csrf: run('state.csrf'), body: {url: 'https://evil.invalid'}});
  check(arbitrary.status === 400 && arbitrary.test_source_calls.length === 0, 'refresh rejects arbitrary URLs rather than expanding fixed-source scope');

  await run('refresh()');
  const trackerAfter = bridge({path: `/api/jobs/${jobId}/events`, cookie});
  check(JSON.stringify(run('state.jobs')) === oldJobs && JSON.stringify(run('state.profile')) === oldProfile, 'all notice workflows leave old jobs, tracker revisions, and profile unchanged');
  check(JSON.stringify(trackerAfter.body) === JSON.stringify(trackerBefore.body), 'all notice workflows leave old tracker event history unchanged');
  check(JSON.stringify(trackerAfter.test_session_fingerprints) === JSON.stringify(sessionBefore), 'notice API/history/filter/failure workflows never alter session SQLite bytes');
  run("go('radar')");
  check(!roots.app.textContent.includes('示例公司2027届校园招聘') && run('jobs().length') === 3, 'university announcements never appear in the existing jobs radar');
  run("go('tracker')"); await tick();
  check(roots.app.textContent.includes('Offline notice isolation fixture'), 'existing tracker still renders its original note and event after notice work');
  check(calls.filter(c => c.body).every(c => !!c.csrf), 'all UI mutations continue to send the session CSRF token');
  console.log(JSON.stringify({checks, uiRequests: calls.length, fixtureSourceRequests: sourceCalls.length,
    classification: 'Offline DOM/API simulation; real WSGI/parsers/SQLite; fixed fixture transport; no live network/model; not browser E2E'}, null, 2));
})().catch(error => { console.error(error); process.exitCode = 1; });
