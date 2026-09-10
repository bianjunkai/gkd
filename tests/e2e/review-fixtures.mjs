import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { readFileSync } from 'node:fs';
import { resolve, join } from 'node:path';
import vm from 'node:vm';

export const root = resolve(import.meta.dirname, '../..');
export const input = (value, dataset = {}) => ({ detail: { value }, currentTarget: { dataset } });
export const tap = dataset => ({ currentTarget: { dataset } });
export function deferred() { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; }
export async function until(check, label = 'condition') {
  for (let i = 0; i < 400; i++) { if (check()) return; await new Promise(done => setTimeout(done, 10)); }
  assert.fail(`Timed out waiting for ${label}`);
}

export function fixture(groupCount = 1) {
  const workspace = { id: 'ws-test', name: '隔离回归工作空间', timezone: 'Asia/Shanghai', ai_enabled: false };
  const auth = { token: 'synthetic-test-token', user: { id: 'user-test', username: 'review', display_name: '回归测试' }, workspace };
  const task = { title: '发送合同', status: 'next', owner: null, scheduled: null, deadline: null,
    priority: 'medium', location: null, context: null, tags: [] };
  const groups = Array.from({ length: groupCount }, (_, index) => ({ id: `group-${index}`, title: `目标 ${String(index).padStart(3, '0')}`,
    path: `资料/目标 ${String(index).padStart(3, '0')}.md`, folder_id: null, revision: 1, content_hash: 'a'.repeat(64),
    status: 'active', tags: [], tasks: [], created_at: '2026-09-08T00:00:00Z', updated_at: '2026-09-08T00:00:00Z',
    body: '原有正文', markdown: '# 原有正文', deleted_at: null, progress: { done: 0, total: 0 } }));
  const proposal = { id: 'proposal-one', revision: 1, status: 'pending_confirmation', capture_id: 'capture-one', created_at: 1788825600,
    actions: [{ action_id: 'action-one', type: 'create', target_group_id: null, folder_id: null, group_title: '新任务组', file_name: null,
      tasks: [{ ...task }], body_append: '', evidence: { title: '发送合同' }, field_confidence: { title: 'high' } }],
    questions: [], previews: [{ group_id: 'group-new', title: '新任务组', path: '新任务组.md', diff: '+ 发送合同', before_revision: null, will_reopen: false }],
    classification: 'single_task', reference_time: '2026-09-08T00:00:00Z', timezone: 'Asia/Shanghai', provider: { provider: 'local-rules' }, candidates: [] };
  const captures = [{ id: 'capture-one', client_capture_id: 'client-one', raw_text: '发送合同的原文', source_type: 'text',
    status: 'needs_confirmation', created_at: '2026-09-08T00:00:00Z', deleted_at: null, reference_timezone: 'Asia/Shanghai',
    proposals: [{ id: proposal.id, revision: 1, status: proposal.status }], jobs: [{ id: 'job-one', status: 'succeeded', error: null }] }];
  const profile = { user: auth.user, workspace, ai: { configured: false, provider: 'local', model: '', consent_version: 'v1',
    disclosure: '仅发送用户选择的原文。', usage: { requests: 0, limit: 20, date: '2026-09-08', timezone: 'Asia/Shanghai' } }, storage: { bytes: 1000, limit: 10000000 } };
  const state = { auth, profile, groups, captures, proposal, requests: [], unexpected: [], previewFailures: 0,
    confirmFailures: 0, jobGate: null, proposalIdForJob: 'proposal-background' };
  const reply = (data, status = 200) => ({ status, data: structuredClone(data) });
  const failure = (code, message, status = 409) => reply({ error: { code, message } }, status);
  function page(items, url, fallback = 20) {
    const offset = Number(url.searchParams.get('offset') || 0), limit = Number(url.searchParams.get('limit') || fallback);
    return reply({ items: items.slice(offset, offset + limit), total: items.length, offset, limit });
  }
  state.handle = async (method, path, body) => {
    state.requests.push({ method, path, body: structuredClone(body) });
    const url = new URL(path, 'http://review.invalid'), name = url.pathname;
    if (method === 'GET' && name === '/api/health') return reply({ status: 'ok', version: '0.1.0', password_auth: true });
    if (method === 'GET' && name === '/api/me') return reply(profile);
    if (method === 'GET' && name === '/api/folders') return reply([]);
    if (method === 'GET' && name === '/api/jobs') return reply([]);
    if (method === 'GET' && name === '/api/tasks') return page([], url);
    if (name === '/api/captures' && method === 'GET') {
      const trash = url.searchParams.get('trash') === 'true', status = url.searchParams.get('status');
      return page(captures.filter(c => Boolean(c.deleted_at) === trash && (!status || c.status === status)), url);
    }
    if (name === '/api/captures' && method === 'POST') {
      const capture = { ...captures[0], ...body, id: 'capture-new', status: 'unprocessed', proposals: [], jobs: [] };
      captures.push(capture); return reply(capture, 201);
    }
    if (name.startsWith('/api/captures/')) {
      const capture = captures.find(c => c.id === name.split('/')[3]);
      if (!capture) return failure('NOT_FOUND', '记录不存在', 404);
      if (name.endsWith('/analyze') && method === 'POST') return reply({ id: 'job-one', status: 'running', kind: 'analysis', result: null }, 202);
      if (name.endsWith('/state') && method === 'POST') {
        if (body.action === 'trash') capture.deleted_at = 1;
        if (body.action === 'restore') capture.deleted_at = null;
        if (body.action === 'archive') capture.status = 'archived';
        if (body.action === 'unarchive') capture.status = 'unprocessed';
        return reply(capture);
      }
      if (method === 'GET') return capture.integrity_error ? failure(capture.integrity_error.code, capture.integrity_error.message, 503) : reply(capture);
    }
    if (name === '/api/jobs/job-one' && method === 'GET') {
      if (state.jobGate) await state.jobGate.promise;
      return reply({ id: 'job-one', status: 'succeeded', kind: 'analysis', attempts: 1, result: { proposal_id: state.proposalIdForJob }, error: null });
    }
    if (name === '/api/group-options' && method === 'GET') {
      const q = (url.searchParams.get('q') || '').trim().toLowerCase();
      return page(groups.filter(g => !q || (g.title + '\n' + g.path).toLowerCase().includes(q))
        .map(({ id, title, path, folder_id }) => ({ id, title, path, folder_id })), url);
    }
    if (name === '/api/groups' && method === 'GET') return page(url.searchParams.get('trash') === 'true' || url.searchParams.get('status') === 'archived' ? [] : groups, url);
    if (name.startsWith('/api/groups/') && method === 'GET') return reply(groups.find(g => g.id === name.split('/')[3]));
    if (name.startsWith('/api/proposals/')) {
      if (method === 'GET') return reply(proposal);
      if (method === 'PATCH') {
        if (body.proposal_revision !== proposal.revision) return failure('PROPOSAL_REVISION_CONFLICT', '提案修订已变化');
        Object.assign(proposal, { revision: proposal.revision + 1, status: 'pending_confirmation', actions: structuredClone(body.actions), questions: structuredClone(body.questions) });
        return reply(proposal);
      }
      if (name.endsWith('/preview')) {
        if (body.proposal_revision !== proposal.revision) return failure('PROPOSAL_REVISION_CONFLICT', '提案修订已变化');
        if (state.previewFailures-- > 0) return failure('PREVIEW_FAILED', '模拟预览请求失败，请重试', 503);
        if (proposal.status === 'stale') return failure('PROPOSAL_STALE', '目标已变化，请重新预览');
        return reply({ previews: proposal.previews, task_count: 1, file_count: proposal.previews.length });
      }
      if (name.endsWith('/confirm')) {
        if (state.confirmFailures-- > 0) { proposal.status = 'stale'; return failure('PROPOSAL_STALE', '目标已变化，请重新预览'); }
        proposal.status = 'applied'; return reply({ id: 'change-one', group_ids: ['group-0'], folder_ids: [], status: 'committed', undone_by: null });
      }
    }
    state.unexpected.push({ method, path });
    return failure('UNEXPECTED_TEST_REQUEST', `${method} ${path}`, 404);
  };
  return state;
}

// Every browser request is intercepted. These tests cannot reach the real local API.
export async function browserFixture(browser, t, state = fixture()) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, serviceWorkers: 'block' });
  const errors = [];
  await context.addInitScript(auth => sessionStorage.setItem('guike.session.v1', JSON.stringify(auth)), state.auth);
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url());
    if (url.origin !== 'http://review.invalid') { state.unexpected.push(request.url()); return route.abort(); }
    if (url.pathname.startsWith('/api/')) {
      const result = await state.handle(request.method(), url.pathname + url.search, request.postData() ? request.postDataJSON() : undefined);
      return route.fulfill({ status: result.status, json: result.data });
    }
    if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: await readFile(join(root, 'apps/web/dist/index.html')) });
    if (/^\/assets\/[\w.-]+\.(js|css)$/.test(url.pathname)) return route.fulfill({ contentType: url.pathname.endsWith('.js') ? 'text/javascript' : 'text/css', body: await readFile(join(root, 'apps/web/dist', url.pathname)) });
    if (url.pathname === '/favicon.ico') return route.fulfill({ status: 204 });
    state.unexpected.push(request.url()); return route.abort();
  });
  const page = await context.newPage(); page.on('pageerror', error => errors.push(error.message));
  page.setDefaultTimeout(6000);
  t.after(async () => { state.jobGate?.resolve(); await context.close(); assert.deepEqual(state.unexpected, []); assert.deepEqual(errors, []); });
  await page.goto('http://review.invalid/'); await page.getByLabel('记录内容').waitFor();
  return { page, state };
}

export async function openProposal(page) {
  await page.getByRole('button', { name: '打开记录', exact: true }).first().click();
  await page.getByRole('button', { name: /提案修订 1/ }).click();
  const dialog = page.getByRole('dialog', { name: '把建议，变成下一步。' });
  await dialog.getByLabel('任务标题', { exact: true }).waitFor(); return dialog;
}

export function nativeFixture(state = fixture()) {
  const app = { globalData: { session: structuredClone(state.auth), profile: null, lastChange: null } };
  const storage = new Map(), navigation = [], modalAnswers = [];
  const wx = {
    request(options) {
      const url = new URL(options.url);
      state.handle(options.method || 'GET', url.pathname + url.search, options.data)
        .then(result => options.success({ statusCode: result.status, data: result.data }))
        .catch(error => options.fail({ errMsg: error.message }));
      return { abort() {} };
    },
    getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, structuredClone(value)), removeStorageSync: key => storage.delete(key),
    showToast() {}, stopPullDownRefresh() {}, navigateBack: () => navigation.push('back'),
    navigateTo: ({ url }) => navigation.push(url), redirectTo: ({ url }) => navigation.push(url),
    reLaunch: ({ url }) => navigation.push(url), switchTab: ({ url }) => navigation.push(url),
    showModal: options => { const answer = modalAnswers.shift() ?? true; options.success({ confirm: answer, cancel: !answer }); },
  };
  function page(name) {
    let definition;
    vm.runInNewContext(readFileSync(join(root, `apps/miniprogram/dist/pages/${name}/index.js`), 'utf8'),
      { wx, getApp: () => app, Page: value => { definition = value; }, setTimeout, clearTimeout }, { timeout: 1000 });
    definition.setData = values => Object.assign(definition.data, structuredClone(values)); return definition;
  }
  async function settled(controller, allowError = false) {
    await until(() => !controller.data.busy, 'Mini Program page operation');
    if (!allowError) assert.equal(controller.data.error, '');
    assert.deepEqual(state.unexpected, []);
  }
  return { state, app, page, settled, navigation, modalAnswers };
}
