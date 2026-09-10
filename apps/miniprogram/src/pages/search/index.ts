import { type SearchHit } from '@gkd/client';
import { api, captureUrl, groupUrl, operate, requireSession } from '../../runtime';

Page({
  data: { busy: false, error: '', text: '', query: '', kindIndex: 0, kinds: ['全部', '任务', '任务组', '原始记录'],
    items: [] as Array<SearchHit & { label: string }>, total: 0, offset: 0, searched: false },
  onShow() { requireSession(); },
  input(event: WxInputEvent) { this.setData({ text: event.detail.value }); },
  search() { this.setData({ query: this.data.text.trim(), offset: 0 }); void this.reload(); },
  kind(event: WxTapEvent) { this.setData({ kindIndex: Number(event.currentTarget.dataset.index), offset: 0 }); if (this.data.query) void this.reload(); },
  async reload() { await operate(this, async () => {
    if (!this.data.query) { this.setData({ items: [], total: 0, searched: false }); return; }
    const result = await api.search(this.data.query, { kind: ['', 'task', 'group', 'capture'][this.data.kindIndex], offset: this.data.offset, limit: 30 });
    this.setData({ items: result.items.map(item => ({ ...item, label: { task: '任务', group: '任务组', capture: '原始记录' }[item.type] })), total: result.total, searched: true });
  }); },
  open(event: WxTapEvent) { const item = this.data.items[Number(event.currentTarget.dataset.index)]; if (item) wx.navigateTo({ url: item.type === 'capture' ? captureUrl(item.id) : groupUrl(item.group_id || item.id) }); },
  next() { this.setData({ offset: this.data.offset + 30 }); void this.reload(); },
  previous() { this.setData({ offset: Math.max(0, this.data.offset - 30) }); void this.reload(); },
});
