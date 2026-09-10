import { emptyTask, versionOf, type Folder, type Group, type Task } from '@gkd/client';
import { api, captureUrl, confirm, groupUrl, operate, requireSession, state, taskView, toast } from '../../runtime';

Page({
  data: { busy: false, error: '', group: null as Group | null, tasks: [] as ReturnType<typeof taskView<Task>>[],
    editing: false, isNew: false, title: '', body: '', tags: '', fileName: '', firstTask: '', statusIndex: 0,
    statuses: ['进行中', '已完成', '已归档'], folders: [] as Folder[], folderLabels: ['根目录'], folderIndex: 0,
    showSource: false, canUndo: false, sources: [] as string[] },
  id: '', folderId: null as string | null, base: null as Group | null,
  onLoad(options: Record<string, string>) { this.id = options.id || ''; this.folderId = options.folder_id || null; this.setData({ isNew: !this.id, editing: !this.id }); },
  onShow() { if (requireSession() && (!this.data.editing || this.data.isNew)) void this.reload(); },
  onPullDownRefresh() { if (!this.data.editing) void this.reload(); else wx.stopPullDownRefresh(); },
  async loadGroup() {
    const folders = (await api.folders()).filter(f => !f.effective_archived);
    this.setData({ folders, folderLabels: ['根目录', ...folders.map(f => f.path)] });
    if (!this.id) { this.setData({ folderIndex: folders.findIndex(f => f.id === this.folderId) + 1 }); return; }
    const group = await api.group(this.id); this.base = group;
    this.setData({ group, tasks: group.tasks.map(t => taskView(t)), sources: [...new Set(group.tasks.flatMap(t => t.source_refs))],
      canUndo: Boolean(state().lastChange?.group_ids.includes(this.id)) });
  },
  async reload() { await operate(this, async () => this.loadGroup()); },
  edit() { const group = this.data.group!; this.base = group; this.setData({ editing: true, title: group.title, body: group.body, tags: group.tags.join(', '),
    fileName: group.path.split('/').slice(-1)[0], folderIndex: this.data.folders.findIndex(f => f.id === group.folder_id) + 1,
    statusIndex: ['active', 'done', 'archived'].indexOf(group.status) }); },
  input(event: WxInputEvent) { this.setData({ [String(event.currentTarget.dataset.field)]: event.detail.value }); },
  folder(event: WxInputEvent) { this.setData({ folderIndex: Number(event.detail.value) }); },
  status(event: WxInputEvent) { this.setData({ statusIndex: Number(event.detail.value) }); },
  cancelEdit() { if (this.data.isNew) wx.navigateBack(); else this.setData({ editing: false }); },
  save() { void operate(this, async () => {
    const folder_id = this.data.folderIndex ? this.data.folders[this.data.folderIndex - 1].id : null;
    const data = { title: this.data.title, body: this.data.body, folder_id, tags: this.data.tags.split(/[,，]/).map(t => t.trim()).filter(Boolean) };
    const change = this.data.isNew ? await api.createGroup({ ...data, tasks: this.data.firstTask.trim() ? [emptyTask(this.data.firstTask)] : [] }) :
      await api.updateGroup(this.id, { ...versionOf(this.base!), ...data, file_name: this.data.fileName, status: ['active', 'done', 'archived'][this.data.statusIndex] as Group['status'] });
    toast('任务组已保存', change);
    if (this.data.isNew) { wx.redirectTo({ url: groupUrl(change.group_ids[0]) }); return; }
    this.setData({ editing: false }); await this.loadGroup();
  }); },
  task(event: WxTapEvent) { wx.navigateTo({ url: '/pages/task/index?group_id=' + encodeURIComponent(this.id) + '&id=' + encodeURIComponent(String(event.currentTarget.dataset.id || '')) }); },
  complete(event: WxTapEvent) { void operate(this, async () => {
    const group = this.data.group!; const task = group.tasks.find(t => t.id === event.currentTarget.dataset.id); if (!task) return;
    const change = await api.updateTask(group.id, task.id, versionOf(group), { status: task.status === 'done' ? 'next' : 'done' }); toast('任务状态已保存', change); await this.loadGroup();
  }); },
  source() { this.setData({ showSource: !this.data.showSource }); },
  original(event: WxTapEvent) { wx.navigateTo({ url: captureUrl(String(event.currentTarget.dataset.id)) }); },
  history() { wx.navigateTo({ url: '/pages/history/index?group_id=' + encodeURIComponent(this.id) }); },
  undo() { void operate(this, async () => { const change = state().lastChange; if (!change) return; if (change.group_ids.length > 1 && !await confirm('撤销整个批次', '这次变更涉及 ' + change.group_ids.length + ' 个文件，确定撤销？')) return; await api.undo(change.id); state().lastChange = null; toast('已撤销'); wx.navigateBack(); }); },
  trash() { void operate(this, async () => { if (!await confirm('移入回收站', '将这个任务组及其中的任务一起移入回收站，可在「我的」恢复。')) return; const change = await api.trashGroup(this.data.group!); toast('已移入回收站', change); wx.navigateBack(); }); },
});
