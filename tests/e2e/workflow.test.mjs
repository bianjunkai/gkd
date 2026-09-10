import assert from 'node:assert/strict';
import { before, after, test } from 'node:test';
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdir, readFile } from 'node:fs/promises';
import { existsSync, readFileSync } from 'node:fs';
import vm from 'node:vm';
import { resolve } from 'node:path';
import { createServer } from 'node:net';
import { chromium } from 'playwright';

const root = resolve(import.meta.dirname, '../..');
const runRoot = resolve(root, '.cache', `e2e-${Date.now()}`);
const artifacts = resolve(root, '.cache/qa');
const python = process.env.GKD_PYTHON || resolve(root, 'services/api/.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const port = Number(process.env.GKD_E2E_PORT || 18640);
const baseURL = `http://127.0.0.1:${port}`;
let server, browser;
let serverOutput = '';

before(async () => {
  // Do not accidentally run mutation tests against somebody else's server on this port.
  await new Promise((resolve, reject) => {
    const probe = createServer();
    probe.once('error', reject);
    probe.listen(port, '127.0.0.1', () => probe.close(resolve));
  });
  await mkdir(runRoot, { recursive: true }); await mkdir(artifacts, { recursive: true });
  server = spawn(python, ['-X', 'utf8', '-m', 'uvicorn', 'app.main:create_app', '--factory', '--host', '127.0.0.1', '--port', String(port), '--no-access-log'], {
    cwd: resolve(root, 'services/api'), windowsHide: true,
    env: { ...process.env, DATA_ROOT: runRoot, DATABASE_URL: '', APP_ENV: 'test', ENABLE_PASSWORD_AUTH: 'true', RUN_WORKER: 'true', AI_PROVIDER: 'local' },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  server.stdout.on('data', value => { serverOutput += value; }); server.stderr.on('data', value => { serverOutput += value; });
  server.on('error', error => { serverOutput += error.message; });
  let healthy = false;
  for (let i = 0; i < 100; i++) {
    try { if ((await fetch(baseURL + '/api/health')).ok) { healthy = true; break; } } catch { /* Startup is asynchronous. */ }
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  assert.ok(healthy, 'API failed to start: ' + serverOutput.slice(-3000));
  const chrome = process.env.GKD_BROWSER || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
  browser = await chromium.launch({ headless: true, ...(existsSync(chrome) ? { executablePath: chrome } : {}) });
}, { timeout: 40000 });

after(async () => { await browser?.close(); server?.kill(); });

async function register(page, username) {
  await page.goto(baseURL);
  await page.getByRole('button', { name: '第一次使用？创建本地账号' }).click();
  await page.getByLabel('如何称呼你').fill('测试同学');
  await page.getByLabel('账号', { exact: true }).fill(username);
  await page.getByLabel('密码', { exact: true }).fill('test-password-2026');
  await page.getByRole('button', { name: '创建工作空间', exact: true }).click();
  await page.getByLabel('记录内容').waitFor();
}
async function session(page) { return page.evaluate(() => JSON.parse(sessionStorage.getItem('guike.session.v1'))); }
async function api(page, path) {
  const auth = await session(page);
  const response = await fetch(baseURL + '/api' + path, { headers: { Authorization: `Bearer ${auth.token}` } });
  assert.equal(response.status, 200, path);
  return response.json();
}

test('desktop: capture, edit preview, confirm, task completion, undo, export and account isolation', { timeout: 120000 }, async () => {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true });
  const page = await context.newPage();
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  page.on('dialog', dialog => dialog.accept());
  await register(page, 'browser-primary');
  await page.screenshot({ path: resolve(artifacts, 'desktop-inbox-empty.png'), fullPage: true });
  await page.getByLabel('记录内容').fill('明天下午给李总发合同\n周五18点前提交方案');
  await page.getByRole('button', { name: '保存并整理', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '把建议，变成下一步。' });
  await dialog.waitFor();
  assert.equal((await api(page, '/groups')).total, 0, 'No product data may be written before confirmation');
  await dialog.getByLabel('任务标题', { exact: true }).first().fill('给李总发送合同（已核对）');
  await dialog.getByRole('button', { name: '保存修订并更新预览', exact: true }).click();
  const confirm = dialog.getByRole('button', { name: '确认写入', exact: true });
  await confirm.waitFor();
  await page.waitForFunction(() => !Array.from(document.querySelectorAll('button')).find(b => b.textContent.trim() === '确认写入')?.disabled);
  await page.screenshot({ path: resolve(artifacts, 'desktop-proposal.png'), fullPage: true });
  await confirm.click(); await dialog.waitFor({ state: 'hidden' });
  const groups = await api(page, '/groups');
  assert.equal(groups.total, 1); assert.equal(groups.items[0].tasks.length, 2);
  assert.equal(groups.items[0].title, '日常任务'); assert.equal(groups.items[0].path, '日常任务.md');
  assert.equal(groups.items[0].folder_id, null);
  await page.getByRole('navigation').getByRole('button', { name: '今日', exact: true }).click();
  await page.getByRole('button', { name: '全部待办', exact: true }).click();
  await page.getByRole('button', { name: '完成：给李总发送合同（已核对）', exact: true }).click();
  await page.getByRole('button', { name: '完成：给李总发送合同（已核对）', exact: true }).waitFor({ state: 'hidden' });
  const g = await api(page, '/groups/' + groups.items[0].id);
  assert.equal(g.tasks[0].status, 'done'); assert.ok(g.markdown.includes('status: done'));
  assert.ok(g.markdown.includes('### 给李总发送合同'));
  await page.locator('.toast').getByRole('button', { name: '撤销', exact: true }).click();
  await page.getByRole('button', { name: '完成：给李总发送合同（已核对）', exact: true }).waitFor();
  assert.equal((await api(page, '/groups/' + g.id)).tasks[0].status, 'next');
  await page.screenshot({ path: resolve(artifacts, 'desktop-tasks.png'), fullPage: true });
  await page.getByRole('button', { name: '给李总发送合同（已核对）', exact: true }).click();
  const groupDialog = page.getByRole('dialog'); await groupDialog.waitFor();
  await groupDialog.getByRole('button', { name: 'Markdown 源码', exact: true }).click();
  assert.ok((await groupDialog.locator('.source-code').innerText()).includes('source_refs:'));
  await groupDialog.getByRole('button', { name: '版本记录', exact: true }).click();
  await groupDialog.getByRole('button', { name: '查看差异', exact: true }).first().click();
  await groupDialog.locator('.diff-code').waitFor();
  await groupDialog.getByRole('button', { name: '关闭', exact: true }).click();
  await page.getByRole('navigation').getByRole('button', { name: '搜索', exact: true }).click();
  await page.getByLabel('搜索关键词').fill('合同');
  await page.locator('.search-form').getByRole('button', { name: '搜索', exact: true }).click();
  await page.locator('.search-hit').first().waitFor();
  assert.ok(await page.locator('.search-hit').count() >= 3);
  await page.getByRole('navigation').getByRole('button', { name: '我的', exact: true }).click();
  const downloadEvent = page.waitForEvent('download');
  await page.getByRole('button', { name: '导出我的内容', exact: true }).click();
  const download = await downloadEvent; const downloadPath = resolve(runRoot, 'browser-export.zip');
  assert.equal(await download.failure(), null, 'The browser must finish the download without an error');
  assert.equal(download.suggestedFilename(), 'guike-export.zip');
  await download.saveAs(downloadPath);
  const downloaded = await readFile(downloadPath);
  assert.ok(downloaded.length > 100, `Downloaded export is incomplete: ${downloaded.length} bytes`);
  assert.equal(downloaded.subarray(0, 4).toString('hex'), '504b0304', 'Export must be a ZIP archive');
  const exportJob = (await api(page, '/jobs')).find(job => job.kind === 'export' && job.status === 'succeeded');
  assert.ok(exportJob?.result?.sha256, 'The export job must publish a checksum');
  assert.equal(createHash('sha256').update(downloaded).digest('hex'), exportJob.result.sha256, 'Downloaded bytes must match the server manifest');
  await page.getByRole('navigation').getByRole('button', { name: '收集箱', exact: true }).click();
  await page.getByLabel('记录内容').fill('账号一的待上传草稿，不应出现在账号二。');
  await page.reload(); await page.getByLabel('记录内容').waitFor();
  assert.equal(await page.getByLabel('记录内容').inputValue(), '账号一的待上传草稿，不应出现在账号二。');
  const former = await session(page);
  await page.getByRole('navigation').getByRole('button', { name: '我的', exact: true }).click();
  await page.getByRole('button', { name: '退出登录', exact: true }).click();
  await page.getByRole('button', { name: '第一次使用？创建本地账号' }).waitFor();
  assert.equal((await fetch(baseURL + '/api/me', { headers: { Authorization: 'Bearer ' + former.token } })).status, 401);
  await register(page, 'browser-secondary');
  assert.equal(await page.getByLabel('记录内容').inputValue(), '');
  assert.equal((await api(page, '/captures')).total, 0);
  assert.deepEqual(errors, []);
  await context.close();
});

test('task grouping: independently named containers are reused after confirmation', { timeout: 90000 }, async () => {
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await context.newPage();
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  try {
    await register(page, 'browser-grouping');
    await page.getByLabel('记录内容').fill('明天发送合同');
    await page.getByRole('button', { name: '保存并整理', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '把建议，变成下一步。' });
    await dialog.waitFor();
    assert.equal(await dialog.getByLabel('任务标题', { exact: true }).inputValue(), '明天发送合同');
    assert.equal(await dialog.getByLabel('任务组名称', { exact: true }).inputValue(), '日常任务');
    assert.equal(await dialog.getByLabel('归属位置', { exact: true }).inputValue(), '');
    assert.equal((await api(page, '/groups')).total, 0);
    await page.screenshot({ path: resolve(artifacts, 'desktop-task-grouping.png'), fullPage: true });
    await dialog.getByRole('button', { name: '更新预览', exact: true }).click();
    await dialog.getByRole('button', { name: '确认写入', exact: true }).click();
    await dialog.waitFor({ state: 'hidden' });
    const first = (await api(page, '/groups')).items[0];
    await page.getByLabel('记录内容').fill('周五提交方案');
    await page.getByRole('button', { name: '保存并整理', exact: true }).click();
    await dialog.waitFor();
    const destination = dialog.getByLabel('写入方式', { exact: true });
    await destination.locator(`option[value="${first.id}"]`).waitFor({ state: 'attached' });
    assert.equal(await destination.inputValue(), first.id);
    assert.equal(await dialog.getByLabel('任务组名称', { exact: true }).count(), 0);
    assert.equal((await api(page, '/groups/' + first.id)).tasks.length, 1);
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    assert.equal(await dialog.evaluate(node => node.scrollWidth > node.clientWidth), false);
    await page.screenshot({ path: resolve(artifacts, 'mobile-task-grouping.png'), fullPage: true });
    await dialog.getByRole('button', { name: '更新预览', exact: true }).click();
    await dialog.getByRole('button', { name: '确认写入', exact: true }).click();
    await dialog.waitFor({ state: 'hidden' });
    const groups = await api(page, '/groups');
    assert.equal(groups.total, 1); assert.equal(groups.items[0].id, first.id);
    assert.equal(groups.items[0].path, '日常任务.md'); assert.equal(groups.items[0].tasks.length, 2);
    assert.deepEqual(errors, []);
  } finally { await context.close(); }
});

test('mobile viewport: readable capture, offline draft and safe raw text', { timeout: 90000 }, async () => {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, deviceScaleFactor: 1, hasTouch: true });
  const page = await context.newPage();
  await register(page, 'browser-mobile');
  await context.setOffline(true);
  await page.getByLabel('记录内容').fill('离线时也要留下这段测试原文。');
  await page.getByText('当前离线。收集箱文字保留在本机，恢复连接后请再次提交。').waitFor();
  await context.setOffline(false);
  await page.reload(); await page.getByLabel('记录内容').waitFor();
  assert.equal(await page.getByLabel('记录内容').inputValue(), '离线时也要留下这段测试原文。');
  await page.getByLabel('记录内容').fill('资料：<script>window.injected = true</script> [链接](javascript:alert(1))');
  await page.getByRole('button', { name: '只保存', exact: true }).click();
  await page.locator('.capture-row').waitFor();
  assert.equal(await page.evaluate(() => window.injected), undefined);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.screenshot({ path: resolve(artifacts, 'mobile-inbox.png'), fullPage: true });
  await page.locator('.capture-copy').first().click();
  await page.getByRole('dialog').waitFor();
  assert.ok((await page.locator('.raw-capture').innerText()).includes('<script>'));
  assert.equal(await page.evaluate(() => window.injected), undefined);
  await page.screenshot({ path: resolve(artifacts, 'mobile-capture.png'), fullPage: true });
  await context.close();
});

test('Mini Program page logic: real API capture, confirmation, task and undo via a simulated wx transport', { timeout: 90000 }, async () => {
  const app = { globalData: { session: null, profile: null, lastChange: null } };
  const storage = new Map(); const navigation = []; const requests = [];
  const wx = {
    request(options) {
      const path = new URL(options.url).pathname + new URL(options.url).search;
      requests.push({ path, method: options.method, data: options.data });
      fetch(baseURL + path, { method: options.method, headers: options.header,
        body: options.data === undefined ? undefined : JSON.stringify(options.data) })
        .then(async response => options.success({ statusCode: response.status, data: await response.json() }))
        .catch(error => options.fail({ errMsg: error.message }));
      return { abort() {} };
    },
    getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, structuredClone(value)), removeStorageSync: key => storage.delete(key),
    showToast() {}, stopPullDownRefresh() {}, navigateBack() { navigation.push('back'); },
    navigateTo: value => navigation.push(value.url), redirectTo: value => navigation.push(value.url),
    reLaunch: value => navigation.push(value.url), switchTab: value => navigation.push(value.url),
    showModal: options => options.success({ confirm: true, cancel: false }),
  };
  function page(name) {
    let definition;
    vm.runInNewContext(readFileSync(resolve(root, `apps/miniprogram/dist/pages/${name}/index.js`), 'utf8'),
      { wx, getApp: () => app, Page: value => { definition = value; }, setTimeout, clearTimeout }, { timeout: 1000 });
    definition.setData = values => Object.assign(definition.data, structuredClone(values));
    return definition;
  }
  async function settled(page) {
    for (let i = 0; i < 500 && page.data.busy; i++) await new Promise(resolve => setTimeout(resolve, 25));
    assert.equal(page.data.busy, false, 'Mini Program page did not settle');
    assert.equal(page.data.error, '', page.data.error);
  }
  const input = (field, value, extra = {}) => ({ detail: { value }, currentTarget: { dataset: { field, ...extra } } });
  const tap = dataset => ({ currentTarget: { dataset } });
  const login = page('login'); login.onLoad(); await settled(login); login.toggleRegister();
  login.input(input('username', 'miniprogram-user')); login.input(input('password', 'test-password-2026')); login.input(input('display_name', '小程序测试'));
  login.submit(); await settled(login);
  assert.ok(app.globalData.session?.token);
  const nativeAuth = app.globalData.session;
  async function read(path) {
    const response = await fetch(baseURL + '/api' + path, { headers: { Authorization: 'Bearer ' + nativeAuth.token } });
    assert.equal(response.status, 200, path); return response.json();
  }
  const inbox = page('inbox'); inbox.onLoad(); inbox.onShow(); await settled(inbox);
  inbox.input(input('raw_text', '明天下午提交验收报告'));
  inbox.save(tap({ organize: 'yes' })); inbox.save(tap({ organize: 'yes' })); await settled(inbox);
  assert.equal((await read('/captures')).total, 1, 'Repeated taps must not duplicate Capture');
  assert.equal((await read('/groups')).total, 0);
  const proposalId = new URL(navigation.at(-1), 'http://local').searchParams.get('id');
  const proposal = page('proposal'); proposal.onLoad({ id: proposalId }); await settled(proposal);
  proposal.taskInput(input('title', '提交验收报告（小程序已核对）', { index: 0, task: 0 }));
  proposal.preview(); await settled(proposal); assert.equal(proposal.data.hasPreview, true);
  proposal.apply(); await settled(proposal);
  const groups = await read('/groups'); assert.equal(groups.total, 1); assert.equal(groups.items[0].tasks.length, 1);
  const group = groups.items[0];
  const task = page('task'); task.onLoad({ group_id: group.id, id: group.tasks[0].id }); await settled(task);
  task.status(input('', '7')); task.save(); await settled(task);
  assert.equal((await read('/groups/' + group.id)).tasks[0].status, 'done');
  const history = page('history'); history.onLoad({ group_id: group.id }); await settled(history);
  const latest = history.data.entries[0]; history.inspect(tap({ id: latest.id })); await settled(history);
  assert.ok(history.data.diff.includes('done'));
  history.undo(tap({ id: latest.id })); await settled(history);
  assert.equal((await read('/groups/' + group.id)).tasks[0].status, 'next');
  const settings = page('settings'); settings.onShow(); await settled(settings); settings.logout(); await settled(settings);
  assert.equal(app.globalData.session, null);
  assert.ok(!storage.has('guike-session-v1'));
  assert.equal((await fetch(baseURL + '/api/me', { headers: { Authorization: 'Bearer ' + nativeAuth.token } })).status, 401);
  assert.equal(requests.filter(r => r.path === '/api/captures' && r.method === 'POST').length, 1);
});
