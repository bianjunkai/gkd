import { ApiError, type Folder, type GroupOption, type Preview, type Proposal, type ProposalAction, type Question, type TimeSpec } from '@gkd/client';
import { api, confirm, groupUrl, operate, requireSession, state, toast } from '../../runtime';

Page({
  data: { busy: false, error: '', proposal: null as Proposal | null, actions: [] as ProposalAction[], questions: [] as Question[], selected: {} as Record<string, boolean>,
    dirty: false, hasPreview: false, readonly: false, raw: '', fileCount: 0, taskCount: 0, selectedCount: 0,
    previews: [] as Preview[], groups: [] as GroupOption[], targetGroups: [] as GroupOption[], folders: [] as Folder[], targetLabels: ['新建任务组（Markdown 文件）'], targetIndexes: {} as Record<string, number>,
    targetInput: '', targetQuery: '', targetOffset: 0, targetTotal: 0, folderLabels: ['根目录'], folderNames: {} as Record<string, string>,
    periods: ['不指定时段', '上午', '下午', '晚上'], providerLabel: '', hasRequired: false },
  id: '',
  onLoad(options: Record<string, string>) { this.id = options.id || ''; if (requireSession()) void this.reload(); },
  async reload() {
    if (this.data.dirty && !await confirm('重新读取提案', '重新读取会放弃尚未保存的本机修改，继续？')) return;
    await operate(this, async () => {
    const [proposal, groups, allFolders] = await Promise.all([api.proposal(this.id), api.groupOptions({ q: this.data.targetQuery, offset: this.data.targetOffset, limit: 20 }), api.folders()]);
    const folders = allFolders.filter(f => !f.effective_archived);
    let raw = '原始记录不可用或已删除。'; try { raw = (await api.capture(proposal.capture_id)).raw_text; } catch { /* Proposal remains readable when its source was removed. */ }
    const selected: Record<string, boolean> = {}; for (const action of proposal.actions) selected[action.action_id] = proposal.selected_action_ids ? proposal.selected_action_ids.includes(action.action_id) : true;
    this.setData({ proposal, actions: proposal.actions, questions: proposal.questions, selected, raw, groups: groups.items, folders,
      targetTotal: groups.total, targetOffset: groups.offset, folderLabels: ['根目录', ...folders.map(f => f.path)],
      folderNames: Object.fromEntries(folders.map(f => [f.id, f.path])), readonly: ['applied', 'rejected'].includes(proposal.status),
      providerLabel: proposal.provider.provider === 'local-rules' ? '本地规则解析 · 请核对语义' : '外部 AI · ' + (proposal.provider.model || ''), dirty: false, hasPreview: false, previews: proposal.previews });
    this.syncTargets(); this.summary();
  }); },
  syncTargets() {
    const groups = [...this.data.groups], ids = new Set(groups.map(g => g.id));
    for (const action of this.data.actions) {
      if (action.type !== 'append' || !action.target_group_id || ids.has(action.target_group_id)) continue;
      const saved = this.data.targetGroups.find(g => g.id === action.target_group_id);
      groups.push(saved || { id: action.target_group_id, title: action.group_title, folder_id: action.folder_id,
        path: this.data.proposal?.previews.find(p => p.group_id === action.target_group_id)?.path || action.group_title });
      ids.add(action.target_group_id);
    }
    this.setData({ targetGroups: groups, targetLabels: ['新建任务组（Markdown 文件）', ...groups.map(g => '追加：' + g.path)],
      targetIndexes: Object.fromEntries(this.data.actions.map(a => [a.action_id, a.type === 'append' ? groups.findIndex(g => g.id === a.target_group_id) + 1 : 0])) });
  },
  targetInput(event: WxInputEvent) { this.setData({ targetInput: event.detail.value }); },
  async loadTargets() { await operate(this, async () => {
    const result = await api.groupOptions({ q: this.data.targetQuery, offset: this.data.targetOffset, limit: 20 });
    this.setData({ groups: result.items, targetTotal: result.total, targetOffset: result.offset }); this.syncTargets();
  }); },
  searchTargets() { if (this.data.busy) return; this.setData({ targetQuery: this.data.targetInput.trim(), targetOffset: 0 }); void this.loadTargets(); },
  previousTargets() { if (this.data.busy || !this.data.targetOffset) return; this.setData({ targetOffset: Math.max(0, this.data.targetOffset - 20) }); void this.loadTargets(); },
  nextTargets() { if (this.data.busy || this.data.targetOffset + 20 >= this.data.targetTotal) return; this.setData({ targetOffset: this.data.targetOffset + 20 }); void this.loadTargets(); },
  summary() {
    const actions = this.data.actions.filter(a => this.data.selected[a.action_id]);
    this.setData({ selectedCount: actions.length, taskCount: actions.reduce((n, a) => n + a.tasks.length, 0), hasRequired: this.data.questions.some(q => q.required && !q.resolved) });
  },
  changed(actions: ProposalAction[]) { this.setData({ actions, dirty: true, hasPreview: false }); this.syncTargets(); this.summary(); },
  select(event: WxCheckboxEvent) { this.setData({ selected: { ...this.data.selected, [String(event.currentTarget.dataset.id)]: event.detail.value.includes('yes') }, hasPreview: false }); this.summary(); },
  taskInput(event: WxInputEvent) {
    const index = Number(event.currentTarget.dataset.index), taskIndex = Number(event.currentTarget.dataset.task);
    const field = String(event.currentTarget.dataset.field) as 'title' | 'owner';
    const actions = this.data.actions.map((a, i) => i === index ? { ...a, tasks: a.tasks.map((t, ti) => ti === taskIndex ? { ...t, [field]: field === 'owner' ? event.detail.value || null : event.detail.value } : t) } : a);
    this.changed(actions);
  },
  actionInput(event: WxInputEvent) {
    const index = Number(event.currentTarget.dataset.index), field = String(event.currentTarget.dataset.field) as 'group_title' | 'body_append';
    this.changed(this.data.actions.map((a, i) => i === index ? { ...a, [field]: event.detail.value } : a));
  },
  target(event: WxInputEvent) {
    const index = Number(event.currentTarget.dataset.index), choice = Number(event.detail.value);
    const group = choice ? this.data.targetGroups[choice - 1] : null;
    if (choice && !group) return;
    this.changed(this.data.actions.map((a, i) => i !== index ? a : group ? { ...a, type: 'append', target_group_id: group.id, group_title: group.title, folder_id: group.folder_id } : { ...a, type: 'create', target_group_id: null, folder_id: null }));
  },
  folder(event: WxInputEvent) {
    const index = Number(event.currentTarget.dataset.index), choice = Number(event.detail.value);
    this.changed(this.data.actions.map((a, i) => i === index ? { ...a, folder_id: choice ? this.data.folders[choice - 1].id : null } : a));
  },
  time(event: WxInputEvent) {
    const index = Number(event.currentTarget.dataset.index), taskIndex = Number(event.currentTarget.dataset.task);
    const field = String(event.currentTarget.dataset.field) as 'scheduled' | 'deadline';
    const part = String(event.currentTarget.dataset.part), value = event.detail.value;
    const actions = this.data.actions.map((a, i) => i === index ? { ...a, tasks: a.tasks.map((t, ti) => {
      if (ti !== taskIndex) return t;
      const old = t[field];
      if (part !== 'date' && !old) { this.setData({ error: '请先选择日期。' }); return t; }
      let spec: TimeSpec = old || { date: '', time: null, period: null, timezone: this.data.proposal?.timezone || state().session!.workspace.timezone };
      if (part === 'date') spec = { ...spec, date: value };
      if (part === 'time') spec = { ...spec, time: value, period: null };
      if (part === 'period') spec = { ...spec, time: null, period: [null, 'morning', 'afternoon', 'evening'][Number(value)] as TimeSpec['period'] };
      return { ...t, [field]: spec };
    }) } : a);
    this.changed(actions);
  },
  clearTime(event: WxTapEvent) {
    const index = Number(event.currentTarget.dataset.index), taskIndex = Number(event.currentTarget.dataset.task), field = String(event.currentTarget.dataset.field);
    this.changed(this.data.actions.map((a, i) => i === index ? { ...a, tasks: a.tasks.map((t, ti) => ti === taskIndex ? { ...t, [field]: null } : t) } : a));
  },
  question(event: WxCheckboxEvent) { const index = Number(event.currentTarget.dataset.index); this.setData({ questions: this.data.questions.map((q, i) => i === index ? { ...q, resolved: event.detail.value.includes('yes') } : q), dirty: true, hasPreview: false }); this.summary(); },
  preview() { void operate(this, async () => {
    this.setData({ hasPreview: false });
    const proposal = await api.refreshProposalForPreview(this.data.proposal!, this.data.actions, this.data.questions, this.data.dirty);
    this.setData({ proposal, actions: proposal.actions, questions: proposal.questions, dirty: false });
    this.syncTargets(); this.summary();
    const selected = this.data.actions.filter(a => this.data.selected[a.action_id]).map(a => a.action_id);
    const preview = await api.previewProposal(proposal, selected);
    this.setData({ previews: preview.previews, hasPreview: true, fileCount: preview.file_count, taskCount: preview.task_count });
  }); },
  apply() { void operate(this, async () => {
    if (!this.data.hasPreview || this.data.dirty) throw new Error('请先更新预览。');
    const ids = this.data.actions.filter(a => this.data.selected[a.action_id]).map(a => a.action_id);
    let change;
    try { change = await api.confirmProposal(this.data.proposal!, ids); }
    catch (error) {
      if (error instanceof ApiError && error.code === 'PROPOSAL_STALE') this.setData({ hasPreview: false, proposal: { ...this.data.proposal!, status: 'stale' } });
      throw error;
    }
    toast('已写入 Markdown', change);
    if (change.group_ids.length) wx.redirectTo({ url: groupUrl(change.group_ids[0]) });
  }); },
  reject() { void operate(this, async () => { if (!await confirm('取消这份提案', '不会写入任务，原始记录仍然保留。')) return; await api.rejectProposal(this.data.proposal!); toast('提案已取消'); wx.navigateBack(); }); },
});
