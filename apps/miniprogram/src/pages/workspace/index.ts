import { type Folder, type Group } from '@gkd/client';
import { api, groupUrl, operate, prompt, requireSession, toast } from '../../runtime';

Page({
  data: { busy: false, error: '', folders: [] as Folder[], children: [] as Folder[], groups: [] as Group[], currentFolder: null as Folder | null,
    total: 0, offset: 0, archive: false, managing: null as Folder | null, folderName: '', parentIndex: 0,
    parentLabels: ['根目录'], parents: [] as Folder[] },
  folderId: null as string | null,
  onShow() { if (requireSession()) void this.reload(); },
  onPullDownRefresh() { void this.reload(); },
  async loadContent() {
    const [folders, groups] = await Promise.all([api.folders(), api.groups({ folder_id: this.folderId || 'root', status: this.data.archive ? 'archived' : null, offset: this.data.offset, limit: 30 })]);
    this.setData({ folders, children: folders.filter(f => f.parent_id === this.folderId && !f.effective_archived),
      currentFolder: folders.find(f => f.id === this.folderId) || null, groups: groups.items, total: groups.total });
  },
  async reload() { await operate(this, async () => this.loadContent()); },
  enter(event: WxTapEvent) { this.folderId = String(event.currentTarget.dataset.id); this.setData({ offset: 0 }); void this.reload(); },
  up() { this.folderId = this.data.currentFolder?.parent_id || null; this.setData({ offset: 0 }); void this.reload(); },
  root() { this.folderId = null; this.setData({ offset: 0 }); void this.reload(); },
  open(event: WxTapEvent) { wx.navigateTo({ url: groupUrl(String(event.currentTarget.dataset.id)) }); },
  newGroup() { wx.navigateTo({ url: '/pages/group/index?folder_id=' + encodeURIComponent(this.folderId || '') }); },
  newFolder() { void operate(this, async () => { const name = await prompt('新建文件夹', '文件夹名称'); if (name === null) return; const change = await api.createFolder(name, this.folderId); toast('文件夹已创建', change); await this.loadContent(); }); },
  manage(event: WxTapEvent) {
    const folder = this.data.folders.find(f => f.id === event.currentTarget.dataset.id); if (!folder) return;
    const parents = this.data.folders.filter(f => !f.effective_archived && f.id !== folder.id && !f.path.startsWith(folder.path + '/'));
    this.setData({ managing: folder, folderName: folder.name, parents, parentLabels: ['根目录', ...parents.map(p => p.path)], parentIndex: parents.findIndex(p => p.id === folder.parent_id) + 1 });
  },
  name(event: WxInputEvent) { this.setData({ folderName: event.detail.value }); },
  parent(event: WxInputEvent) { this.setData({ parentIndex: Number(event.detail.value) }); },
  cancelManage() { this.setData({ managing: null }); },
  saveFolder() { void operate(this, async () => {
    const folder = this.data.managing!; const change = await api.updateFolder(folder.id, { expected_revision: folder.revision, name: this.data.folderName, parent_id: this.data.parentIndex ? this.data.parents[this.data.parentIndex - 1].id : null });
    toast('文件夹已保存', change); this.setData({ managing: null }); await this.loadContent();
  }); },
  archive(event: WxSwitchEvent) { this.setData({ archive: event.detail.value, offset: 0 }); void this.reload(); },
  next() { this.setData({ offset: this.data.offset + 30 }); void this.reload(); },
  previous() { this.setData({ offset: Math.max(0, this.data.offset - 30) }); void this.reload(); },
});
