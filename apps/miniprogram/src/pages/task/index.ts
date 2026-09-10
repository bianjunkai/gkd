import { draftOf, emptyTask, statusLabels, versionOf, type Group, type Task, type TaskDraft, type TimeSpec } from '@gkd/client';
import { api, captureUrl, operate, requireSession, state, toast } from '../../runtime';

const statuses: TaskDraft['status'][] = ['inbox', 'next', 'active', 'waiting', 'scheduled', 'someday', 'blocked', 'done', 'cancelled', 'archived'];
Page({
  data: { busy: false, error: '', ready: false, isNew: false, draft: emptyTask(), tags: '', groupTitle: '',
    statusIndex: 1, statusLabels: statuses.map(s => statusLabels[s]), priorityIndex: 1, priorities: ['低', '普通', '高'],
    periodIndex: 0, periods: ['不指定时段', '上午', '下午', '晚上'], sources: [] as string[], timezone: 'Asia/Shanghai' },
  groupId: '', taskId: '', base: null as Group | null,
  onLoad(options: Record<string, string>) { this.groupId = options.group_id || ''; this.taskId = options.id || ''; this.setData({ isNew: !this.taskId }); if (requireSession()) void this.reload(); },
  async reload() { await operate(this, async () => {
    const group = await api.group(this.groupId); this.base = group;
    const task = this.taskId ? group.tasks.find(t => t.id === this.taskId) : null;
    if (this.taskId && !task) throw new Error('任务不存在或已经变化。');
    const draft = task ? draftOf(task) : emptyTask();
    this.setData({ ready: true, draft, tags: draft.tags.join(', '), statusIndex: statuses.indexOf(draft.status),
      priorityIndex: ['low', 'medium', 'high'].indexOf(draft.priority), groupTitle: group.title, sources: task?.source_refs || [],
      timezone: state().profile?.workspace.timezone || state().session!.workspace.timezone,
      periodIndex: [null, 'morning', 'afternoon', 'evening'].indexOf(draft.scheduled?.period || null) });
  }); },
  input(event: WxInputEvent) {
    const field = String(event.currentTarget.dataset.field) as 'title' | 'owner' | 'location' | 'context';
    this.setData({ draft: { ...this.data.draft, [field]: field === 'title' ? event.detail.value : event.detail.value || null } });
  },
  tags(event: WxInputEvent) { this.setData({ tags: event.detail.value }); },
  status(event: WxInputEvent) { const index = Number(event.detail.value); this.setData({ statusIndex: index, draft: { ...this.data.draft, status: statuses[index] } }); },
  priority(event: WxInputEvent) { const index = Number(event.detail.value); this.setData({ priorityIndex: index, draft: { ...this.data.draft, priority: ['low', 'medium', 'high'][index] as TaskDraft['priority'] } }); },
  time(event: WxInputEvent) {
    const field = String(event.currentTarget.dataset.field) as 'scheduled' | 'deadline';
    const part = String(event.currentTarget.dataset.part), old = this.data.draft[field];
    if (!old && part !== 'date') { this.setData({ error: '请先选择日期。' }); return; }
    let value: TimeSpec = old || { date: '', time: null, period: null, timezone: this.data.timezone };
    if (part === 'date') value = { ...value, date: event.detail.value };
    if (part === 'time') { value = { ...value, time: event.detail.value, period: null }; if (field === 'scheduled') this.setData({ periodIndex: 0 }); }
    if (part === 'period') { const index = Number(event.detail.value); value = { ...value, time: null, period: [null, 'morning', 'afternoon', 'evening'][index] as TimeSpec['period'] }; this.setData({ periodIndex: index }); }
    this.setData({ draft: { ...this.data.draft, [field]: value }, error: '' });
  },
  clear(event: WxTapEvent) { const field = String(event.currentTarget.dataset.field); this.setData({ draft: { ...this.data.draft, [field]: null }, ...(field === 'scheduled' ? { periodIndex: 0 } : {}) }); },
  save() { void operate(this, async () => {
    const draft = { ...this.data.draft, tags: this.data.tags.split(/[,，]/).map(t => t.trim()).filter(Boolean) };
    const change = this.taskId ? await api.updateTask(this.groupId, this.taskId, versionOf(this.base!), draft) : await api.addTask(this.base!, draft);
    toast('任务已保存', change); wx.navigateBack();
  }); },
  original(event: WxTapEvent) { wx.navigateTo({ url: captureUrl(String(event.currentTarget.dataset.id)) }); },
});
