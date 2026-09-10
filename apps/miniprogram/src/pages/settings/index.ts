import { versionOf, type Capture, type Group, type Job, type Profile } from '@gkd/client';
import { API_BASE_URL } from '../../config';
import { api, confirm, dateLabel, operate, requireSession, setSession, state, toast, waitJob } from '../../runtime';

Page({
  data: { busy: false, error: '', profile: null as Profile | null, name: '', timezone: 'Asia/Shanghai',
    groups: [] as Group[], captures: [] as Capture[], archive: false, jobLabel: '', report: '',
    jobs: [] as Array<Job & { time_label: string; kind_label: string }>, apiBase: API_BASE_URL },
  onShow() { if (requireSession()) void this.reload(); },
  onPullDownRefresh() { void this.reload(); },
  async loadData() {
    const [profile, groups, captures, jobs] = await Promise.all([api.me(), api.groups({ trash: !this.data.archive, status: this.data.archive ? 'archived' : null, limit: 100 }),
      api.captures({ trash: !this.data.archive, status: this.data.archive ? 'archived' : null, limit: 100 }), api.jobs()]);
    state().profile = profile;
    this.setData({ profile, name: profile.workspace.name, timezone: profile.workspace.timezone, groups: groups.items, captures: captures.items,
      jobs: jobs.slice(0, 10).map(j => ({ ...j, time_label: dateLabel(j.created_at), kind_label: { analysis: '整理记录', reindex: '重建索引' }[j.kind] })) });
  },
  async reload() { await operate(this, async () => this.loadData()); },
  input(event: WxInputEvent) { this.setData({ [String(event.currentTarget.dataset.field)]: event.detail.value }); },
  saveSettings() { void operate(this, async () => { await api.updateWorkspace({ name: this.data.name, timezone: this.data.timezone }); toast('设置已保存'); await this.loadData(); }); },
  ai(event: WxSwitchEvent) { void operate(this, async () => {
    const enabled = event.detail.value, profile = this.data.profile!;
    if (enabled && !await confirm('启用外部 AI', `${profile.ai.provider} / ${profile.ai.model}\n${profile.ai.disclosure}`)) { await this.loadData(); return; }
    await api.updateWorkspace({ ai_enabled: enabled, ai_consent_version: profile.ai.consent_version }); toast(enabled ? '外部 AI 已启用' : '外部 AI 已关闭'); await this.loadData();
  }); },
  reindex() { void operate(this, async () => { const queued = await api.rebuildIndex(); this.setData({ jobLabel: '正在检查并重建索引…' }); const job = await waitJob(queued.id); this.setData({ jobLabel: `索引完成：${job.result?.groups} 个文件，${job.result?.tasks} 项任务。`, report: job.result?.errors?.map(e => e.path + '：' + e.message).join('\n') || '' }); await this.loadData(); }); },
  archive(event: WxSwitchEvent) { this.setData({ archive: event.detail.value }); void this.reload(); },
  restoreGroup(event: WxTapEvent) { void operate(this, async () => { const group = this.data.groups.find(g => g.id === event.currentTarget.dataset.id); if (!group) return;
    const change = this.data.archive ? await api.updateGroup(group.id, { ...versionOf(group), status: 'active' }) : await api.restoreGroup(group); toast('任务组已恢复', change); await this.loadData(); }); },
  restoreCapture(event: WxTapEvent) { void operate(this, async () => { const capture = await api.captureState(String(event.currentTarget.dataset.id), this.data.archive ? 'unarchive' : 'restore'); toast(capture.integrity_error ? '记录已恢复，原文仍不可用' : '原文已恢复'); await this.loadData(); }); },
  trashUnavailable(event: WxTapEvent) { void operate(this, async () => {
    if (!await confirm('移入回收站', '将不可用记录移入回收站？原始文件会保留，已经生成的任务不受影响。')) return;
    await api.captureState(String(event.currentTarget.dataset.id), 'trash'); toast('记录已移入回收站'); await this.loadData();
  }); },
  checkJob(event: WxTapEvent) { void operate(this, async () => { const job = await waitJob(String(event.currentTarget.dataset.id)); this.setData({ jobLabel: job.status === 'succeeded' ? '后台任务已完成。' : '后台任务已结束。' }); await this.loadData(); }); },
  logout() { void operate(this, async () => { try { await api.logout(); } catch { await confirm('未连接到服务端', '本机将退出，服务端会话会在到期后失效。'); } finally { setSession(null); wx.reLaunch({ url: '/pages/login/index' }); } }); },
});
