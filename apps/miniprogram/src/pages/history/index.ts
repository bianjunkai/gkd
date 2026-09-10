import { type Change, type VersionPreview } from '@gkd/client';
import { api, confirm, dateLabel, operate, requireSession, state, toast } from '../../runtime';

Page({
  data: { busy: false, error: '', entries: [] as Array<Change & { time_label: string; restore_hash: string | null }>,
    diff: '', preview: null as VersionPreview | null },
  groupId: '',
  onLoad(options: Record<string, string>) { this.groupId = options.group_id || ''; if (requireSession()) void this.reload(); },
  async loadHistory() {
    const changes = await api.history(this.groupId);
    this.setData({ entries: changes.map(c => ({ ...c, time_label: dateLabel(c.created_at), restore_hash: c.versions?.find(v => v.group_id === this.groupId)?.after_hash || null })) });
  },
  async reload() { await operate(this, async () => this.loadHistory()); },
  inspect(event: WxTapEvent) { void operate(this, async () => { const change = await api.change(String(event.currentTarget.dataset.id)); this.setData({ diff: change.differences?.map(d => d.diff).join('\n\n') || '此变更仅修改目录属性。', preview: null }); }); },
  undo(event: WxTapEvent) { void operate(this, async () => {
    const change = this.data.entries.find(e => e.id === event.currentTarget.dataset.id); if (!change) return;
    if (!await confirm('撤销整个变更批次', `影响 ${change.group_ids.length} 个文件。存在后续编辑时会拒绝覆盖，需要先预览恢复。`)) return;
    await api.undo(change.id); state().lastChange = null; toast('已撤销'); await this.loadHistory();
  }); },
  preview(event: WxTapEvent) { void operate(this, async () => { const preview = await api.version(this.groupId, String(event.currentTarget.dataset.hash)); this.setData({ preview, diff: preview.diff || '业务内容无差异，恢复仍会产生新的修订号。' }); }); },
  restore() { void operate(this, async () => {
    if (!this.data.preview) return;
    if (!await confirm('恢复为新版本', '将以预览中的历史内容替换当前任务组。当前版本仍保留在历史中。确认继续？')) return;
    const change = await api.restoreVersion(this.groupId, this.data.preview); toast('已恢复为新版本', change);
    this.setData({ preview: null, diff: '' }); await this.loadHistory();
  }); },
  cancel() { this.setData({ preview: null, diff: '' }); },
});
