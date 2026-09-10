import { ApiError, newDraft, statusLabels, type Capture, type CaptureDraft } from '@gkd/client';
import { api, captureUrl, confirm, dateLabel, operate, proposalUrl, requireSession, state, toast, waitJob } from '../../runtime';

type CaptureView = Capture & { time_label: string; status_label: string };
Page({
  data: { busy: false, error: '', draft: newDraft(), uncertain: false, storageError: false, count: 0,
    captures: [] as CaptureView[], total: 0, offset: 0, filterIndex: 0, filters: ['全部', '未整理', '待确认', '已整理'],
    sourceIndex: 0, sources: ['文字记录', '微信粘贴'], modeIndex: 0, modes: ['本地规则（不外发）'], jobStatus: '' },
  draftKey: '',
  visible: false,
  navigationEpoch: 0,
  onLoad() {
    if (!requireSession()) return;
    this.draftKey = 'guike-draft-v1-' + state().session!.user.id;
    try { const value = wx.getStorageSync<{ draft: CaptureDraft; uncertain: boolean }>(this.draftKey); if (value?.draft?.client_capture_id && typeof value.draft.raw_text === 'string') this.setData({ draft: value.draft, uncertain: value.uncertain, count: Array.from(value.draft.raw_text).length, sourceIndex: value.draft.source_type === 'wechat' ? 1 : 0 }); } catch { this.setData({ storageError: true }); }
  },
  onShow() { this.visible = true; if (requireSession()) void this.reload(); },
  onHide() { this.visible = false; this.navigationEpoch++; },
  onUnload() { this.visible = false; this.navigationEpoch++; },
  canNavigate(token: string, epoch: number) { return this.visible && this.navigationEpoch === epoch && state().session?.token === token; },
  onPullDownRefresh() { void this.reload(); },
  async fetchRecords() {
    const filters = ['', 'unprocessed', 'needs_confirmation', 'processed'];
    const result = await api.captures({ status: filters[this.data.filterIndex], offset: this.data.offset, limit: 20 });
    this.setData({ captures: result.items.map(c => ({ ...c, time_label: dateLabel(c.created_at), status_label: statusLabels[c.status] })), total: result.total });
  },
  async reload() { await operate(this, async () => { await this.fetchRecords(); const profile = await api.me(); state().profile = profile; this.setData({ modes: profile.workspace.ai_enabled ? ['本地规则（不外发）', '外部 AI（已授权）'] : ['本地规则（不外发）'], modeIndex: profile.workspace.ai_enabled ? this.data.modeIndex : 0 }); }); },
  persist(draft: CaptureDraft, uncertain = false) {
    this.setData({ draft, uncertain, count: Array.from(draft.raw_text).length });
    try { wx.setStorageSync(this.draftKey, { draft, uncertain }); this.setData({ storageError: false }); } catch { this.setData({ storageError: true }); }
  },
  input(event: WxInputEvent) { this.persist({ ...this.data.draft, raw_text: event.detail.value }); },
  source(event: WxInputEvent) { const index = Number(event.detail.value); this.setData({ sourceIndex: index }); this.persist({ ...this.data.draft, source_type: index ? 'wechat' : 'text' }); },
  mode(event: WxInputEvent) { this.setData({ modeIndex: Number(event.detail.value) }); },
  filter(event: WxInputEvent) { this.setData({ filterIndex: Number(event.detail.value), offset: 0 }); void this.reload(); },
  save(event: WxTapEvent) { void operate(this, async () => {
    const token = state().session!.token, epoch = this.navigationEpoch;
    const draft = this.data.draft;
    if (!draft.raw_text.trim()) throw new Error('请先填写内容。');
    this.persist(draft, true);
    let capture;
    try { capture = await api.saveCapture(draft); } catch (error) { if (error instanceof ApiError && error.status >= 400 && error.status < 500) this.persist(draft, false); throw error; }
    if (state().session?.token !== token) return;
    this.persist(newDraft()); this.setData({ sourceIndex: 0 }); toast('原文已保存'); await this.fetchRecords();
    if (state().session?.token !== token) return;
    if (event.currentTarget.dataset.organize === 'yes') {
      const job = await api.analyze(capture.id, { mode: this.data.modeIndex ? 'external' : 'local' });
      this.setData({ jobStatus: '排队中' });
      const result = await waitJob(job.id, j => { if (this.canNavigate(token, epoch)) this.setData({ jobStatus: statusLabels[j.status] }); });
      if (this.canNavigate(token, epoch)) {
        await this.fetchRecords();
        if (this.canNavigate(token, epoch)) wx.navigateTo({ url: proposalUrl(result.result!.proposal_id!) });
      }
    }
  }); },
  open(event: WxTapEvent) { const id = String(event.currentTarget.dataset.id); if (!this.data.captures.find(c => c.id === id)?.integrity_error) wx.navigateTo({ url: captureUrl(id) }); },
  trashUnavailable(event: WxTapEvent) { void operate(this, async () => {
    if (!await confirm('移入回收站', '将不可用记录移入回收站？原始文件会保留，已经生成的任务不受影响。')) return;
    await api.captureState(String(event.currentTarget.dataset.id), 'trash'); toast('记录已移入回收站'); await this.fetchRecords();
  }); },
  next() { this.setData({ offset: this.data.offset + 20 }); void this.reload(); },
  previous() { this.setData({ offset: Math.max(0, this.data.offset - 20) }); void this.reload(); },
});
