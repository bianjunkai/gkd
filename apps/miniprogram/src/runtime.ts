import { ApiClient, ApiError, errorFromResponse, formatTime, statusLabels, type AuthResult, type Change, type Job, type Profile, type Task, type TaskItem, type Transport } from '@gkd/client';
import { API_BASE_URL } from './config';

export interface GuikeApp { globalData: { session: AuthResult | null; profile: Profile | null; lastChange: Change | null } }
export const SESSION_KEY = 'guike-session-v1';
export const state = () => getApp<GuikeApp>().globalData;
export function setSession(value: AuthResult | null) {
  state().session = value; state().profile = null; state().lastChange = null;
  if (value) wx.setStorageSync(SESSION_KEY, value); else wx.removeStorageSync(SESSION_KEY);
}
export function requireSession() {
  if (!state().session) { wx.reLaunch({ url: '/pages/login/index' }); return false; }
  return true;
}
const transport: Transport = <T,>(request: Parameters<Transport>[0]) => new Promise<T>((resolve, reject) => {
  wx.request<T>({ url: API_BASE_URL + request.path, method: request.method, header: request.headers, data: request.body, timeout: 25000,
    success(value) {
      if (value.statusCode >= 200 && value.statusCode < 300) resolve(value.data);
      else {
        const error = errorFromResponse(value.statusCode, value.data);
        if (error.code === 'SESSION_EXPIRED') { setSession(null); wx.reLaunch({ url: '/pages/login/index' }); }
        reject(error);
      }
    }, fail() { reject(new ApiError('NETWORK_ERROR', '未连接到服务端，请检查网络或服务地址。本机草稿仍保留。')); },
  });
});
export const api = new ApiClient(transport, () => state().session?.token || null);
export type Controller = { data: { busy: boolean }; setData(value: Record<string, unknown>): void };
export async function operate(page: Controller, work: () => Promise<void>) {
  if (page.data.busy) return;
  page.setData({ busy: true, error: '' });
  try { await work(); } catch (error) { page.setData({ error: error instanceof Error ? error.message : '操作失败，请重试。' }); }
  finally { page.setData({ busy: false }); wx.stopPullDownRefresh(); }
}
export function toast(message: string, change?: Change) { if (change) state().lastChange = change; wx.showToast({ title: message, icon: 'none', duration: 2500 }); }
export async function confirm(title: string, content: string): Promise<boolean> {
  return new Promise(resolve => wx.showModal({ title, content, confirmText: '确认', success: result => resolve(result.confirm) }));
}
export async function prompt(title: string, placeholder: string, content = ''): Promise<string | null> {
  return new Promise(resolve => wx.showModal({ title, content, editable: true, placeholderText: placeholder, success: result => resolve(result.confirm ? result.content || '' : null) }));
}
export async function waitJob(id: string, progress?: (job: Job) => void): Promise<Job> {
  const start = Date.now();
  while (Date.now() - start < 90000) {
    const job = await api.job(id); progress?.(job);
    if (job.status === 'succeeded') return job;
    if (job.status === 'failed') throw new ApiError(job.error?.code || 'JOB_FAILED', job.error?.message || '后台任务失败，原文仍保留。');
    await new Promise<void>(resolve => setTimeout(resolve, 1000));
  }
  throw new ApiError('JOB_RUNNING', '任务仍在后台运行，请稍后从原文页或「我的」查看。');
}
export function dateLabel(value: string | number) {
  const date = new Date(typeof value === 'number' ? value * 1000 : value);
  const pad = (v: number) => String(v).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}
export function taskView<T extends Task | TaskItem>(task: T) {
  return { ...task, status_label: statusLabels[task.status], scheduled_label: task.scheduled ? formatTime(task.scheduled) : '', deadline_label: task.deadline ? formatTime(task.deadline) : '' };
}
export function captureUrl(id: string) { return '/pages/capture/index?id=' + encodeURIComponent(id); }
export function groupUrl(id: string) { return '/pages/group/index?id=' + encodeURIComponent(id); }
export function proposalUrl(id: string) { return '/pages/proposal/index?id=' + encodeURIComponent(id); }
