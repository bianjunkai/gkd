import { type TaskItem } from '@gkd/client';
import { api, operate, requireSession, state, taskView, toast } from '../../runtime';

type Item = ReturnType<typeof taskView<TaskItem>>;
Page({
  data: { busy: false, error: '', filters: ['今日', '全部待办', '下一步', '等待', '将来', '已完成'], filterIndex: 0,
    tasks: [] as Item[], overdue: [] as Item[], total: 0, overdueTotal: 0, offset: 0, canUndo: false },
  onShow() { if (requireSession()) void this.reload(); },
  onPullDownRefresh() { void this.reload(); },
  async loadTasks() {
    const views = ['today', 'all', 'next', 'waiting', 'someday', 'done'];
    const [tasks, overdue] = await Promise.all([api.tasks({ view: views[this.data.filterIndex], include_completed: this.data.filterIndex === 5, offset: this.data.offset, limit: 30 }), api.tasks({ view: 'overdue', limit: 100 })]);
    this.setData({ tasks: tasks.items.map(t => taskView(t)), total: tasks.total, overdue: overdue.items.map(t => taskView(t)), overdueTotal: overdue.total, canUndo: Boolean(state().lastChange) });
  },
  async reload() { await operate(this, async () => this.loadTasks()); },
  filter(event: WxTapEvent) { this.setData({ filterIndex: Number(event.currentTarget.dataset.index), offset: 0 }); void this.reload(); },
  open(event: WxTapEvent) { wx.navigateTo({ url: '/pages/task/index?group_id=' + encodeURIComponent(String(event.currentTarget.dataset.group)) + '&id=' + encodeURIComponent(String(event.currentTarget.dataset.id)) }); },
  complete(event: WxTapEvent) { void operate(this, async () => {
    const task = [...this.data.tasks, ...this.data.overdue].find(t => t.id === event.currentTarget.dataset.id); if (!task) return;
    const change = await api.updateTask(task.group_id, task.id, { expected_revision: task.group_revision, expected_hash: task.group_hash }, { status: task.status === 'done' ? 'next' : 'done' });
    toast(task.status === 'done' ? '任务已重新打开' : '这一步，完成了', change); await this.loadTasks();
  }); },
  undo() { void operate(this, async () => { if (!state().lastChange) return; await api.undo(state().lastChange!.id); state().lastChange = null; toast('已撤销'); await this.loadTasks(); }); },
  next() { this.setData({ offset: this.data.offset + 30 }); void this.reload(); },
  previous() { this.setData({ offset: Math.max(0, this.data.offset - 30) }); void this.reload(); },
  capture() { wx.switchTab({ url: '/pages/inbox/index' }); },
});
