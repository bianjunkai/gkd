import assert from 'node:assert/strict';
import { before, after, test } from 'node:test';
import { existsSync } from 'node:fs';
import { chromium } from 'playwright';
import { browserFixture, deferred, fixture, openProposal, until } from './review-fixtures.mjs';

let browser;
before(async () => {
  const chrome = process.env.GKD_BROWSER || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
  browser = await chromium.launch({ headless: true, ...(existsSync(chrome) ? { executablePath: chrome } : {}) });
});
after(async () => browser?.close());

async function editGroup(page) {
  await page.getByRole('navigation').getByRole('button', { name: '工作空间', exact: true }).click();
  await page.locator('.group-row').first().click();
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('button', { name: '编辑任务组', exact: true }).click();
  await dialog.getByLabel('任务组名称', { exact: true }).fill('尚未保存的任务组修改');
  return dialog;
}
async function finishInBackground(page, state) {
  state.jobGate.resolve();
  await page.locator('.toast').getByText('整理已完成，可从收集箱的原文页查看提案。', { exact: true }).waitFor();
}
test('review: late Inbox analysis preserves a workspace editor after navigation', async t => {
  const state = fixture(); state.jobGate = deferred();
  const { page } = await browserFixture(browser, t, state);
  await page.getByLabel('记录内容').fill('后台整理记录');
  await page.getByRole('button', { name: '保存并整理', exact: true }).click();
  await until(() => state.requests.some(r => r.path === '/api/jobs/job-one'), 'analysis poll');
  const editor = await editGroup(page);
  await finishInBackground(page, state);
  assert.equal(await editor.getByLabel('任务组名称', { exact: true }).inputValue(), '尚未保存的任务组修改');
  assert.equal(state.requests.filter(r => r.path.includes('proposal-background')).length, 0);
  assert.equal(state.requests.filter(r => r.method === 'PATCH' && r.path.startsWith('/api/groups')).length, 0);
});

test('review: late Inbox analysis does not replace another unsaved proposal modal', async t => {
  const state = fixture(); state.jobGate = deferred();
  const { page } = await browserFixture(browser, t, state);
  await page.getByLabel('记录内容').fill('另一个后台记录');
  await page.getByRole('button', { name: '保存并整理', exact: true }).click();
  await until(() => state.requests.some(r => r.path === '/api/jobs/job-one'));
  const editor = await openProposal(page);
  await editor.getByLabel('任务标题', { exact: true }).fill('当前提案未保存的修改');
  await finishInBackground(page, state);
  assert.equal(await editor.getByLabel('任务标题', { exact: true }).inputValue(), '当前提案未保存的修改');
  assert.equal(state.requests.filter(r => r.path.includes('proposal-background')).length, 0);
});

for (const mode of ['analyze', 'check']) test(`review: late Capture ${mode} does not reopen a dismissed modal`, async t => {
  const state = fixture(); state.jobGate = deferred(); state.captures[0].status = mode === 'check' ? 'processing' : 'unprocessed';
  const { page } = await browserFixture(browser, t, state);
  await page.getByRole('button', { name: '打开记录', exact: true }).first().click();
  const capture = page.getByRole('dialog', { name: '原始记录', exact: true });
  await capture.getByRole('button', { name: mode === 'check' ? '查看后台结果' : '整理这条记录', exact: true }).click();
  await until(() => state.requests.some(r => r.path === '/api/jobs/job-one'));
  await capture.getByRole('button', { name: '关闭', exact: true }).click();
  const editor = await editGroup(page);
  await finishInBackground(page, state);
  assert.equal(await editor.getByLabel('任务组名称', { exact: true }).inputValue(), '尚未保存的任务组修改');
  assert.equal(state.requests.filter(r => r.path.includes('proposal-background')).length, 0);
});

test('review: stale confirmation can be rebound, previewed and confirmed', async t => {
  const state = fixture(); state.confirmFailures = 1;
  const { page } = await browserFixture(browser, t, state), editor = await openProposal(page);
  const confirm = editor.getByRole('button', { name: '确认写入', exact: true });
  await editor.getByRole('button', { name: '更新预览', exact: true }).click();
  await confirm.click();
  await editor.getByRole('alert').getByText('目标已变化，请重新预览', { exact: true }).waitFor();
  assert.equal(await confirm.isDisabled(), true);
  await editor.getByRole('button', { name: '保存修订并更新预览', exact: true }).click();
  await confirm.click(); await editor.waitFor({ state: 'hidden' });
  const patches = state.requests.filter(r => r.method === 'PATCH');
  assert.equal(patches.length, 1); assert.equal(patches[0].body.proposal_revision, 1);
  assert.deepEqual(state.requests.filter(r => r.path.endsWith('/preview')).map(r => r.body.proposal_revision), [1, 2]);
  assert.equal(state.proposal.status, 'applied');
});

test('review: concurrent proposal edits stay local until an explicit reload', async t => {
  const { page, state } = await browserFixture(browser, t), editor = await openProposal(page);
  const title = editor.getByLabel('任务标题', { exact: true });
  await title.fill('必须保留的本机修改');
  state.proposal.revision = 2; state.proposal.actions[0].tasks[0].title = '另一客户端的修改';
  await editor.getByRole('button', { name: '保存修订并更新预览', exact: true }).click();
  await editor.getByRole('alert').getByText(/本机编辑尚未提交/).waitFor();
  assert.equal(await title.inputValue(), '必须保留的本机修改');
  assert.equal(state.requests.filter(r => r.method === 'PATCH').length, 0);
  page.once('dialog', dialog => dialog.dismiss());
  await editor.getByRole('button', { name: '重新读取提案', exact: true }).click();
  assert.equal(await title.inputValue(), '必须保留的本机修改');
  page.once('dialog', dialog => dialog.accept());
  await editor.getByRole('button', { name: '重新读取提案', exact: true }).click();
  await page.waitForFunction(() => [...document.querySelectorAll('input')].some(i => i.value === '另一客户端的修改'));
  assert.equal(await title.inputValue(), '另一客户端的修改');
});

test('review: a saved revision survives a subsequent preview request failure', async t => {
  const state = fixture(); state.previewFailures = 1;
  const { page } = await browserFixture(browser, t, state), editor = await openProposal(page);
  await editor.getByLabel('任务标题', { exact: true }).fill('已保存的修订内容');
  await editor.getByRole('button', { name: '保存修订并更新预览', exact: true }).click();
  await editor.getByRole('alert').getByText('模拟预览请求失败，请重试', { exact: true }).waitFor();
  await editor.getByRole('button', { name: '更新预览', exact: true }).click();
  await page.waitForFunction(() => [...document.querySelectorAll('button')].some(b => b.textContent === '确认写入' && !b.disabled));
  assert.equal(state.requests.filter(r => r.method === 'PATCH').length, 1);
  assert.deepEqual(state.requests.filter(r => r.path.endsWith('/preview')).map(r => r.body.proposal_revision), [2, 2]);
  assert.equal(await editor.getByLabel('任务标题', { exact: true }).inputValue(), '已保存的修订内容');
});

test('review: target 101 is selectable and remains selected through search and pagination', async t => {
  const state = fixture(101), { page } = await browserFixture(browser, t, state), editor = await openProposal(page);
  for (let index = 1; index <= 5; index++) {
    const loaded = page.waitForResponse(r => r.url().includes('/api/group-options?') && new URL(r.url()).searchParams.get('offset') === String(index * 20));
    await editor.getByRole('button', { name: '下一页目标', exact: true }).click(); await loaded;
  }
  const target = editor.getByLabel('写入方式', { exact: true });
  await target.selectOption('group-100');
  const query = editor.getByLabel('查找追加目标', { exact: true });
  await query.fill('目标 000');
  await editor.getByText('共 1 个目标 · 第 1 页', { exact: true }).waitFor();
  assert.equal(await target.inputValue(), 'group-100');
  assert.ok((await target.locator('option:checked').innerText()).includes('已选追加'));
  await query.fill('目标 100');
  await until(() => state.requests.some(r => r.path.includes(encodeURIComponent('目标 100'))));
  await target.selectOption('group-100');
  await query.fill('');
  await editor.getByText('共 101 个目标 · 第 1 页', { exact: true }).waitFor();
  await editor.getByRole('button', { name: '下一页目标', exact: true }).click();
  await editor.getByText('共 101 个目标 · 第 2 页', { exact: true }).waitFor();
  assert.equal(await target.inputValue(), 'group-100');
  await editor.getByRole('button', { name: '保存修订并更新预览', exact: true }).click();
  await page.waitForFunction(() => [...document.querySelectorAll('button')].some(b => b.textContent === '确认写入' && !b.disabled));
  const patch = state.requests.find(r => r.method === 'PATCH');
  assert.equal(patch.body.actions[0].target_group_id, 'group-100');
  assert.equal(patch.body.actions[0].type, 'append');
});

for (const readonly of [false, true]) test(`review: keyboard reaches all final diffs in a ${readonly ? 'readonly' : 'pending'} proposal`, async t => {
  const state = fixture();
  if (readonly) state.proposal.status = 'applied';
  state.proposal.previews.push({ ...state.proposal.previews[0], group_id: 'group-other', path: '第二份差异.md' });
  const { page } = await browserFixture(browser, t, state), editor = await openProposal(page);
  const summaries = editor.locator('.diff-item > summary');
  const beforeDiff = readonly ? editor.locator('.evidence > summary').last() : editor.getByRole('button', { name: '取消整个提案', exact: true });
  await beforeDiff.focus(); await page.keyboard.press('Tab');
  assert.equal(await summaries.first().evaluate(e => e === document.activeElement), true);
  await page.keyboard.press('Enter');
  assert.equal(await editor.locator('.diff-item').first().getAttribute('open'), '');
  await page.keyboard.press('Tab');
  assert.equal(await summaries.last().evaluate(e => e === document.activeElement), true);
  await page.keyboard.press('Tab');
  const close = editor.getByRole('button', { name: '关闭', exact: true });
  assert.equal(await close.evaluate(e => e === document.activeElement), true);
  await page.keyboard.press('Shift+Tab');
  assert.equal(await summaries.last().evaluate(e => e === document.activeElement), true);
});

test('review: an unavailable Capture does not block Inbox or recovery controls', async t => {
  const state = fixture();
  state.captures.push({ ...state.captures[0], id: 'capture-broken', client_capture_id: 'client-broken', raw_text: '',
    integrity_error: { code: 'CAPTURE_UNAVAILABLE', message: '原文文件不可用' } });
  const { page } = await browserFixture(browser, t, state);
  await page.getByText('发送合同的原文', { exact: true }).waitFor();
  await page.getByText('原文损坏或不可用，文件未被改写。', { exact: true }).waitFor();
  page.once('dialog', dialog => dialog.dismiss());
  await page.getByRole('button', { name: '移入回收站', exact: true }).click();
  assert.equal(state.requests.filter(r => r.path.endsWith('/state')).length, 0);
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('button', { name: '移入回收站', exact: true }).click();
  await page.getByRole('button', { name: '移入回收站', exact: true }).waitFor({ state: 'hidden' });
  await page.getByRole('navigation').getByRole('button', { name: '我的', exact: true }).click();
  const restore = page.getByRole('button', { name: '恢复记录（原文仍不可用）', exact: true });
  await restore.click();
  await page.locator('.toast').getByText('记录已恢复，原文仍不可用；请从备份检查原始文件。', { exact: true }).waitFor();
  state.captures[1].status = 'archived';
  await page.getByRole('button', { name: '已归档', exact: true }).click();
  await page.getByRole('button', { name: '移入回收站', exact: true }).waitFor();
  assert.equal(await page.getByRole('button', { name: '查看', exact: true }).count(), 0);
});
