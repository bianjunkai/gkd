import assert from 'node:assert/strict';
import { test } from 'node:test';
import { deferred, fixture, input, nativeFixture, tap, until } from './review-fixtures.mjs';

for (const action of ['inbox', 'analyze', 'check']) {
  for (const leave of ['hide', 'unload', 'return', 'account', 'stay']) {
    test(`review native: ${action} completion respects ${leave} lifecycle`, async () => {
      const state = fixture(); state.jobGate = deferred(); state.captures[0].status = action === 'check' ? 'processing' : 'unprocessed';
      const native = nativeFixture(state), page = native.page(action === 'inbox' ? 'inbox' : 'capture');
      if (action === 'inbox') page.onLoad(); else page.onLoad({ id: 'capture-one' });
      page.onShow(); await native.settled(page);
      if (action === 'inbox') { page.input(input('后台整理记录')); page.save(tap({ organize: 'yes' })); }
      else if (action === 'analyze') page.analyze();
      else page.checkJob();
      await until(() => state.requests.some(r => r.path === '/api/jobs/job-one'), 'native analysis poll');
      if (leave === 'hide') page.onHide();
      if (leave === 'unload') page.onUnload();
      if (leave === 'return') { page.onHide(); page.onShow(); }
      if (leave === 'account') native.app.globalData.session = { ...state.auth, token: 'different-account-token' };
      state.jobGate.resolve(); await native.settled(page);
      assert.deepEqual(native.navigation, leave === 'stay' ? ['/pages/proposal/index?id=proposal-background'] : []);
    });
  }
}

test('review native: stale confirmation rebinds before the next preview', async () => {
  const state = fixture(); state.confirmFailures = 1;
  const native = nativeFixture(state), page = native.page('proposal');
  page.onLoad({ id: 'proposal-one' }); await native.settled(page);
  page.preview(); await native.settled(page); assert.equal(page.data.hasPreview, true);
  page.apply(); await native.settled(page, true);
  assert.match(page.data.error, /目标已变化/); assert.equal(page.data.hasPreview, false); assert.equal(page.data.proposal.status, 'stale');
  page.preview(); await native.settled(page);
  assert.equal(page.data.hasPreview, true); assert.equal(page.data.proposal.revision, 2);
  assert.deepEqual(state.requests.filter(r => r.path.endsWith('/preview')).map(r => r.body.proposal_revision), [1, 2]);
  page.apply(); await native.settled(page);
  assert.deepEqual(native.navigation, ['/pages/group/index?id=group-0']);
});

test('review native: remote revision conflicts preserve local edits until reload is approved', async () => {
  const native = nativeFixture(), page = native.page('proposal');
  page.onLoad({ id: 'proposal-one' }); await native.settled(page);
  page.taskInput(input('尚未提交的本机修改', { index: 0, task: 0, field: 'title' }));
  native.state.proposal.revision = 2; native.state.proposal.actions[0].tasks[0].title = '另一客户端的修改';
  page.preview(); await native.settled(page, true);
  assert.match(page.data.error, /本机编辑尚未提交/);
  assert.equal(page.data.actions[0].tasks[0].title, '尚未提交的本机修改');
  assert.equal(native.state.requests.filter(r => r.method === 'PATCH').length, 0);
  native.modalAnswers.push(false); await page.reload();
  assert.equal(page.data.actions[0].tasks[0].title, '尚未提交的本机修改');
  native.modalAnswers.push(true); await page.reload(); await native.settled(page);
  assert.equal(page.data.actions[0].tasks[0].title, '另一客户端的修改'); assert.equal(page.data.dirty, false);
});

test('review native: successful revision save survives preview failure', async () => {
  const state = fixture(); state.previewFailures = 1;
  const native = nativeFixture(state), page = native.page('proposal');
  page.onLoad({ id: 'proposal-one' }); await native.settled(page);
  page.taskInput(input('保留已保存修订', { index: 0, task: 0, field: 'title' }));
  page.preview(); await native.settled(page, true);
  assert.equal(page.data.proposal.revision, 2); assert.equal(page.data.hasPreview, false); assert.equal(page.data.dirty, false);
  page.preview(); await native.settled(page);
  assert.equal(page.data.hasPreview, true); assert.equal(page.data.actions[0].tasks[0].title, '保留已保存修订');
  assert.equal(state.requests.filter(r => r.method === 'PATCH').length, 1);
  assert.deepEqual(state.requests.filter(r => r.path.endsWith('/preview')).map(r => r.body.proposal_revision), [2, 2]);
});

test('review native: target 101 remains selected when searching and changing pages', async () => {
  const state = fixture(101), native = nativeFixture(state), page = native.page('proposal');
  page.onLoad({ id: 'proposal-one' }); await native.settled(page);
  assert.equal(page.data.targetTotal, 101); assert.equal(page.data.groups.length, 20);
  for (let index = 1; index <= 5; index++) { page.nextTargets(); await native.settled(page); assert.equal(page.data.targetOffset, index * 20); }
  page.target(input('1', { index: 0 }));
  assert.equal(page.data.actions[0].target_group_id, 'group-100');
  page.targetInput(input('目标 000')); page.searchTargets(); await native.settled(page);
  assert.equal(page.data.targetTotal, 1); assert.equal(page.data.targetOffset, 0);
  assert.equal(page.data.targetGroups[page.data.targetIndexes['action-one'] - 1].id, 'group-100');
  page.targetInput(input('目标 100')); page.searchTargets(); await native.settled(page);
  assert.equal(page.data.groups[0].id, 'group-100');
  page.targetInput(input('')); page.searchTargets(); await native.settled(page);
  page.nextTargets(); await native.settled(page);
  assert.equal(page.data.actions[0].target_group_id, 'group-100');
  assert.equal(page.data.targetGroups[page.data.targetIndexes['action-one'] - 1].id, 'group-100');
  page.preview(); await native.settled(page);
  const patch = state.requests.find(r => r.method === 'PATCH');
  assert.equal(patch.body.actions[0].target_group_id, 'group-100'); assert.equal(patch.body.actions[0].type, 'append');
});

test('review native: unavailable Captures remain isolated and have confirmed recovery actions', async () => {
  const state = fixture(); state.captures.push({ ...state.captures[0], id: 'capture-broken', client_capture_id: 'client-broken', raw_text: '',
    integrity_error: { code: 'CAPTURE_UNAVAILABLE', message: '原文文件不可用' } });
  const native = nativeFixture(state), inbox = native.page('inbox');
  inbox.onLoad(); inbox.onShow(); await native.settled(inbox);
  assert.equal(inbox.data.captures.length, 2); assert.equal(inbox.data.captures[0].raw_text, '发送合同的原文');
  inbox.open(tap({ id: 'capture-broken' })); assert.deepEqual(native.navigation, []);
  native.modalAnswers.push(false); inbox.trashUnavailable(tap({ id: 'capture-broken' })); await native.settled(inbox);
  assert.equal(state.requests.filter(r => r.path.endsWith('/state')).length, 0);
  inbox.trashUnavailable(tap({ id: 'capture-broken' })); await native.settled(inbox);
  assert.equal(inbox.data.captures.length, 1);
  const settings = native.page('settings'); settings.onShow(); await native.settled(settings);
  assert.equal(settings.data.captures[0].integrity_error.code, 'CAPTURE_UNAVAILABLE');
  settings.restoreCapture(tap({ id: 'capture-broken' })); await native.settled(settings);
  assert.equal(state.captures[1].deleted_at, null); assert.equal(state.captures[1].raw_text, '');
  state.captures[1].status = 'archived'; settings.archive({ detail: { value: true } }); await native.settled(settings);
  settings.trashUnavailable(tap({ id: 'capture-broken' })); await native.settled(settings);
  assert.equal(state.captures[1].deleted_at, 1);
});
