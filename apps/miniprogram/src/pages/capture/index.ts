import { statusLabels, type Capture } from '@gkd/client';
import { api, confirm, dateLabel, operate, proposalUrl, requireSession, state, toast, waitJob } from '../../runtime';

Page({
  data: { busy: false, error: '', capture: null as Capture | null, timeLabel: '', statusLabel: '', reference: '', modeIndex: 0,
    modes: ['本地规则（不外发）'], proposals: [] as Array<{ id: string; revision: number; status: string; label: string }>, jobLabel: '' },
  id: '',
  visible: false,
  navigationEpoch: 0,
  onLoad(options: Record<string, string>) { this.id = options.id || ''; },
  onShow() { this.visible = true; if (requireSession()) void this.reload(); },
  onHide() { this.visible = false; this.navigationEpoch++; },
  onUnload() { this.visible = false; this.navigationEpoch++; },
  canNavigate(token: string, epoch: number) { return this.visible && this.navigationEpoch === epoch && state().session?.token === token; },
  onPullDownRefresh() { void this.reload(); },
  async reload() { await operate(this, async () => {
    const capture = await api.capture(this.id); const profile = await api.me(); state().profile = profile;
    this.setData({ capture, timeLabel: dateLabel(capture.created_at), statusLabel: statusLabels[capture.status],
      proposals: (capture.proposals || []).map(p => ({ ...p, label: statusLabels[p.status] })),
      modes: profile.workspace.ai_enabled ? ['本地规则（不外发）', '外部 AI（已授权）'] : ['本地规则（不外发）'], modeIndex: 0 });
  }); },
  reference(event: WxInputEvent) { this.setData({ reference: event.detail.value }); },
  mode(event: WxInputEvent) { this.setData({ modeIndex: Number(event.detail.value) }); },
  analyze() { void operate(this, async () => {
    const token = state().session!.token, epoch = this.navigationEpoch;
    const capture = this.data.capture!; const again = ['processed', 'needs_confirmation'].includes(capture.status);
    if (again && !await confirm('重新分析', '已有整理结果。再次分析可能得到重复任务，请在确认页核对。继续？')) return;
    if (!this.canNavigate(token, epoch)) return;
    const job = await api.analyze(this.id, { mode: this.data.modeIndex ? 'external' : 'local', reanalyze: again,
      ...(this.data.reference ? { reference_time: this.data.reference } : {}) });
    const result = await waitJob(job.id, j => { if (this.canNavigate(token, epoch)) this.setData({ jobLabel: statusLabels[j.status] }); });
    if (this.canNavigate(token, epoch)) wx.navigateTo({ url: proposalUrl(result.result!.proposal_id!) });
  }); },
  checkJob() { void operate(this, async () => {
    const token = state().session!.token, epoch = this.navigationEpoch;
    const id = this.data.capture?.jobs?.[0]?.id; if (!id) return;
    const result = await waitJob(id, j => { if (this.canNavigate(token, epoch)) this.setData({ jobLabel: statusLabels[j.status] }); });
    if (this.canNavigate(token, epoch)) wx.navigateTo({ url: proposalUrl(result.result!.proposal_id!) });
  }); },
  openProposal(event: WxTapEvent) { wx.navigateTo({ url: proposalUrl(String(event.currentTarget.dataset.id)) }); },
  archive() { void operate(this, async () => { await api.captureState(this.id, this.data.capture?.status === 'archived' ? 'unarchive' : 'archive'); toast('记录状态已更新'); wx.navigateBack(); }); },
  trash() { void operate(this, async () => { if (!await confirm('移入回收站', '仅删除原始记录，已经生成的任务不会连带删除。可在「我的」恢复。')) return; await api.captureState(this.id, 'trash'); toast('已移入回收站'); wx.navigateBack(); }); },
});
