'use strict';
(() => {
  const DESTINATION = '/api/demo/submit';
  const FICTIONAL = Object.freeze({
    name: '林小禾（虚构示例）', email: 'lin.xiaohe@example.invalid',
    role: 'AI 产品经理（虚构岗位）', education: '示例大学（虚构） · 信息管理硕士 · 2027届',
    experience: '示例实验室（虚构）：产品研究实习，原型设计、SQL 分析、用户反馈整理。',
  });
  const LABELS = {name:'姓名', email:'邮箱', role:'岗位', education:'教育经历', experience:'实践经历'};
  const $ = id => document.getElementById(id);
  const read = () => Object.fromEntries(Object.keys(FICTIONAL).map(key => [key, $(key).value]));
  const snapshot = fields => JSON.stringify(Object.keys(FICTIONAL).map(key => fields[key]));
  let csrf = '', review = null, busy = false, locked = false, version = 0, pending = null;
  try { pending = JSON.parse(sessionStorage.getItem('applypilot-demo-pending') || 'null'); } catch (_) { /* Optional storage. */ }
  if (pending) locked = true;
  function remember(value) {
    pending = value;
    try { if (value) sessionStorage.setItem('applypilot-demo-pending', JSON.stringify(value));
      else sessionStorage.removeItem('applypilot-demo-pending'); } catch (_) { /* In-memory protection remains. */ }
  }
  function status(text) { $('status').textContent = text; }
  function controls() {
    $('fields').disabled = !csrf || busy || locked;
    $('fill-fictional').disabled = !csrf || busy || locked;
    $('prepare').disabled = !csrf || busy || locked;
    $('cancel').disabled = busy || locked;
    $('confirm-simulation').disabled = busy || locked;
    $('submit-simulation').disabled = !review || !$('confirm-simulation').checked || busy || locked;
    $('reconcile').hidden = !locked;
    $('reconcile').disabled = busy;
  }
  async function api(path, data) {
    const response = await fetch(path, {
      method: data === undefined ? 'GET' : 'POST', credentials: 'same-origin',
      headers: data === undefined ? {} : {'Content-Type':'application/json', 'X-CSRF-Token':csrf},
      ...(data === undefined ? {} : {body:JSON.stringify(data)}),
      signal: AbortSignal.timeout(15000),
    });
    let body;
    try { body = await response.json(); } catch (_) { throw new Error('无法确定服务响应。'); }
    if (!response.ok) { const error = new Error(body.error || '操作未完成。'); error.definitive = response.status < 500; throw error; }
    return body;
  }
  function clearReview() { review = null; $('confirm-simulation').checked = false; $('review').hidden = true; controls(); }
  async function invalidate(message) {
    version += 1;
    const previous = review;
    clearReview();
    if (previous) {
      status(message);
      try { await api('/api/demo/cancel', {confirmation_token:previous.confirmation_token}); }
      catch (_) { status('本页确认已清除；服务端取消响应不明。重新核对会生成新确认并废止旧确认。'); }
    }
  }
  $('fields').addEventListener('input', () => { void invalidate('字段已修改，请重新核对并确认。'); });
  $('fill-fictional').addEventListener('click', async () => {
    await invalidate('样例已重填，请重新核对。');
    for (const [key,value] of Object.entries(FICTIONAL)) $(key).value = value;
    status('已填入虚构样例。核对后再进入最终人工确认。');
  });
  $('cancel').addEventListener('click', () => { void invalidate('已取消。未记录模拟回执。'); });
  $('confirm-simulation').addEventListener('change', controls);
  $('demo-form').addEventListener('submit', async event => {
    event.preventDefault();
    if (busy || locked || !csrf || !$('demo-form').reportValidity()) return;
    clearReview();
    busy = true; controls();
    const generation = ++version;
    const fields = read();
    try {
      const response = await api('/api/demo/prepare', {fields,destination:DESTINATION});
      if (generation !== version || snapshot(fields) !== snapshot(read())) {
        await api('/api/demo/cancel', {confirmation_token:response.confirmation_token});
        status('内容已改变，请重新核对。'); return;
      }
      review = response;
      $('review-fields').replaceChildren();
      for (const [key,value] of Object.entries(response.fields)) {
        const dt = document.createElement('dt'); dt.textContent = LABELS[key];
        const dd = document.createElement('dd'); dd.textContent = value;
        $('review-fields').append(dt,dd);
      }
      $('review-destination').textContent = location.origin + DESTINATION;
      $('review-hash').textContent = response.content_hash;
      $('review').hidden = false;
      $('result').hidden = true;
      status('等待人工确认。尚未记录模拟回执。');
    } catch (error) { status(error.message); }
    finally { busy = false; controls(); }
  });
  function showReceipt(receipt) {
    $('result').hidden = false;
    $('receipt').textContent = `${receipt.label}\n回执编号：${receipt.id}\n时间：${receipt.created_at}\n内容指纹：${receipt.content_hash}\n幂等操作号：${receipt.idempotency_key}\n只记录于当前体验会话，没有联系任何招聘方。`;
    $('result').dataset.receiptId = receipt.id;
    status('模拟回执已保存。非真实投递。');
    remember(null); locked = false; clearReview();
  }
  $('submit-simulation').addEventListener('click', async () => {
    if (busy || locked || !review || !$('confirm-simulation').checked) return;
    if (snapshot(read()) !== snapshot(review.fields)) { await invalidate('字段已变化，确认已失效。'); return; }
    busy = true; controls();
    remember({idempotency_key:review.idempotency_key,content_hash:review.content_hash});
    const payload = {...review, fields:read(),confirm_simulation:true};
    try {
      const receipt = await api(DESTINATION, payload);
      if (receipt.simulation !== true || receipt.status !== 'simulation_recorded' ||
          receipt.idempotency_key !== pending.idempotency_key || receipt.content_hash !== pending.content_hash) {
        throw new Error('回执与本次确认不匹配。');
      }
      showReceipt(receipt);
    } catch (error) {
      if (error.definitive) { remember(null); clearReview(); status(error.message + ' 请重新核对。'); }
      else { locked = true; clearReview(); status('提交结果不明，已停止，不会自动重试。请只读核查本会话回执。'); }
    } finally { busy = false; controls(); }
  });
  $('reconcile').addEventListener('click', async () => {
    if (busy || !pending) return;
    busy = true; controls();
    try {
      const response = await api('/api/demo/receipts');
      const receipt = response.receipts.find(item => item.idempotency_key === pending.idempotency_key && item.content_hash === pending.content_hash);
      if (receipt) showReceipt(receipt);
      else status('暂未找到该操作的回执，结果仍不明。保持停止，不自动重试；稍后可再次只读核查。');
    } catch (error) { status('核查失败：' + error.message + ' 未重新提交。'); }
    finally { busy = false; controls(); }
  });
  async function init() {
    try {
      const state = await api('/api/state'); csrf = state.csrf;
      status(locked ? '发现上次结果不明的操作。请只读核查回执，不会自动重试。' : '隔离体验会话已就绪。只使用虚构数据。');
    } catch (error) { status('无法建立体验会话：' + error.message); }
    controls();
  }
  void init();
})();
