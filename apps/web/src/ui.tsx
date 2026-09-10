import { cloneElement, createContext, isValidElement, useContext, useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { ApiClient, ApiError, errorFromResponse, statusLabels, type AuthResult, type Change, type Job, type Profile, type TaskDraft, type TimeSpec, type Transport } from '@gkd/client';

export const browserTransport: Transport = async <T,>({ method, path, headers, body }: Parameters<Transport>[0]): Promise<T> => {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 25000);
  try {
    const response = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body), signal: controller.signal });
    const value = await response.json().catch(() => null);
    if (!response.ok) throw errorFromResponse(response.status, value);
    return value as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError('NETWORK_ERROR', '未能连接服务端。请检查网络或服务是否启动；本机草稿仍会保留。');
  } finally { clearTimeout(timeout); }
};

export type Nav = 'inbox' | 'today' | 'workspace' | 'search' | 'settings';
export interface UIContextValue {
  api: ApiClient; auth: AuthResult; profile: Profile | null; epoch: number;
  refresh: () => void; notify: (message: string, change?: Change) => void;
  openGroup: (id: string) => void; openCapture: (id: string) => void; openProposal: (id: string, options?: { onlyIfIdle?: boolean }) => void;
  go: (nav: Nav) => void;
}
export const UIContext = createContext<UIContextValue | null>(null);
export function useUI() { const value = useContext(UIContext); if (!value) throw new Error('UI provider missing'); return value; }

export function messageOf(error: unknown): string { return error instanceof Error ? error.message : '操作未完成，请重试。'; }
export function useMounted() {
  const mounted = useRef(false);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  return () => mounted.current;
}
export function useResource<T>(load: () => Promise<T>, key: string | number) {
  const loader = useRef(load); loader.current = load;
  const [state, setState] = useState<{ data: T | null; loading: boolean; error: unknown }>({ data: null, loading: true, error: null });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let active = true;
    setState(previous => ({ ...previous, loading: true, error: null }));
    loader.current().then(data => { if (active) setState({ data, loading: false, error: null }); }, error => { if (active) setState(previous => ({ ...previous, loading: false, error })); });
    return () => { active = false; };
  }, [key, attempt]);
  return { ...state, reload: () => setAttempt(v => v + 1) };
}
export function useOperation() {
  const lock = useRef(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  async function run(operation: () => Promise<void>) {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError(null);
    try { await operation(); } catch (e) { setError(e); } finally { lock.current = false; setBusy(false); }
  }
  return { busy, error, run, clearError: () => setError(null) };
}
export async function waitForJob(api: ApiClient, id: string, progress?: (job: Job) => void): Promise<Job> {
  const started = Date.now();
  while (Date.now() - started < 120000) {
    const job = await api.job(id); progress?.(job);
    if (job.status === 'succeeded') return job;
    if (job.status === 'failed') throw new ApiError(job.error?.code || 'JOB_FAILED', job.error?.message || '后台处理失败，请重试。');
    await new Promise(resolve => setTimeout(resolve, 900));
  }
  throw new ApiError('JOB_RUNNING', '任务仍在后台处理。你可以稍后从原文或「我的」查看结果。');
}
export function ErrorNote({ error, retry }: { error: unknown; retry?: () => void }) {
  if (!error) return null;
  const fields = error instanceof ApiError && Array.isArray(error.details.fields) ? error.details.fields as Array<{ message: string }> : [];
  return <div className="error-note" role="alert"><span>{messageOf(error)}</span>{fields.slice(0, 4).map((field, i) => <div key={i}>{field.message.replace(/^Value error, /, '')}</div>)}{retry && <button type="button" onClick={retry}>重试</button>}</div>;
}
export function Loading({ label = '正在读取…' }: { label?: string }) { return <div className="loading" role="status"><span className="loading-dot" />{label}</div>; }
export function Empty({ title, children, action }: { title: string; children: ReactNode; action?: ReactNode }) {
  return <div className="empty-state"><div className="empty-mark" aria-hidden="true"><span /><span /><span /></div><h3>{title}</h3><p>{children}</p>{action}</div>;
}
export function Badge({ status }: { status: string }) { return <span className={`badge status-${status}`}>{statusLabels[status] || status}</span>; }
export function Icon({ name, size = 20 }: { name: string; size?: number }) {
  const paths: Record<string, ReactNode> = {
    inbox: <><path d="M4 4h16v16H4z" /><path d="M4 13h5l2 3h2l2-3h5M8 8h8" /></>,
    today: <><path d="M5 5h14v15H5zM8 3v4m8-4v4M5 10h14" /><path d="m9 15 2 2 4-4" /></>,
    workspace: <><path d="M3 6h7l2 3h9v11H3z" /><path d="M3 6V4h6l2 2h7v3" /></>,
    search: <><circle cx="10" cy="10" r="6" /><path d="m15 15 6 6" /></>,
    settings: <><circle cx="12" cy="8" r="4" /><path d="M4 21c0-5 3-7 8-7s8 2 8 7" /></>,
    plus: <path d="M12 4v16M4 12h16" />, arrow: <path d="m9 5 7 7-7 7" />,
    close: <path d="m6 6 12 12M6 18 18 6" />, check: <path d="m5 12 4 4L19 6" />,
    undo: <><path d="m8 4-5 5 5 5M3 9h10a7 7 0 0 1 0 14" /></>,
    file: <><path d="M5 3h9l5 5v13H5zM14 3v6h5M8 13h8M8 17h6" /></>,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="square" strokeLinejoin="miter" aria-hidden="true">{paths[name] || paths.file}</svg>;
}
export function Brand() { return <span className="brand"><span className="brand-mark" aria-hidden="true"><i /><i /><i /></span><span>归刻<small>GUIKE</small></span></span>; }
export function Modal({ title, subtitle, children, onClose, wide = false }: { title: string; subtitle?: string; children: ReactNode; onClose: () => void; wide?: boolean }) {
  const container = useRef<HTMLDivElement>(null);
  const close = useRef(onClose); close.current = onClose;
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden'; container.current?.focus();
    function keyboard(event: KeyboardEvent) {
      if (event.key === 'Escape') { event.preventDefault(); close.current(); }
      if (event.key === 'Tab') {
        const elements = Array.from(container.current?.querySelectorAll<HTMLElement>('button,input,textarea,select,a[href],summary,[tabindex]') || [])
          .filter(e => e.tabIndex >= 0 && !e.matches(':disabled') && !e.closest('[inert]') && e.getClientRects().length > 0);
        const first = elements[0], last = elements.at(-1);
        if (!first) { event.preventDefault(); return; }
        if (event.shiftKey && (document.activeElement === first || document.activeElement === container.current)) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && (document.activeElement === last || document.activeElement === container.current)) { event.preventDefault(); first.focus(); }
      }
    }
    document.addEventListener('keydown', keyboard);
    return () => { document.removeEventListener('keydown', keyboard); document.body.style.overflow = overflow; previous?.focus(); };
  }, []);
  return <div className="modal-backdrop"><div className={`modal ${wide ? 'modal-wide' : ''}`} role="dialog" aria-modal="true" aria-label={title} tabIndex={-1} ref={container}>
    <header className="modal-header"><div><h2>{title}</h2>{subtitle && <p>{subtitle}</p>}</div><button type="button" className="icon-button" aria-label="关闭" onClick={onClose}><Icon name="close" /></button></header>
    <div className="modal-body">{children}</div></div></div>;
}
export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  const id = useId();
  const control = isValidElement<{ id?: string; 'aria-describedby'?: string }>(children)
    ? cloneElement(children, { id, ...(hint ? { 'aria-describedby': id + '-hint' } : {}) }) : children;
  return <div className="field"><label htmlFor={id}>{label}</label>{control}{hint && <small id={id + '-hint'}>{hint}</small>}</div>;
}

function TagsInput({ tags, onChange, id }: { tags: string[]; onChange: (tags: string[]) => void; id?: string }) {
  const [text, setText] = useState(tags.join(', '));
  const last = useRef(JSON.stringify(tags));
  useEffect(() => { const key = JSON.stringify(tags); if (last.current !== key) { last.current = key; setText(tags.join(', ')); } }, [tags]);
  return <input id={id} aria-label="标签（逗号分隔）" value={text} onChange={e => { setText(e.target.value); const values = e.target.value.split(/[,，]/).map(t => t.trim()).filter(Boolean); last.current = JSON.stringify(values); onChange(values); }} />;
}

function TimeFields({ label, value, timezone, deadline, onChange }: { label: string; value: TimeSpec | null; timezone: string; deadline?: boolean; onChange: (v: TimeSpec | null) => void }) {
  return <div className="time-fields"><Field label={`${label}日期`}><input type="date" value={value?.date || ''} onChange={e => onChange(e.target.value ? { date: e.target.value, time: value?.time || null, period: value?.period || null, timezone: value?.timezone || timezone } : null)} /></Field>
    <Field label={`${label}时间`}><input type="time" value={value?.time || ''} disabled={!value} onChange={e => value && onChange({ ...value, time: e.target.value || null, period: null })} /></Field>
    {!deadline && <Field label="计划时段"><select value={value?.period || ''} disabled={!value} onChange={e => value && onChange({ ...value, time: null, period: (e.target.value || null) as TimeSpec['period'] })}><option value="">不指定</option><option value="morning">上午</option><option value="afternoon">下午</option><option value="evening">晚上</option></select></Field>}
    {value && <small className="time-zone">{value.timezone}{deadline && !value.time ? ' · 当天结束后才算逾期' : ''}</small>}
  </div>;
}
export function TaskFields({ task, onChange, timezone }: { task: TaskDraft; onChange: (v: TaskDraft) => void; timezone: string }) {
  return <div className="task-fields"><Field label="任务标题"><input value={task.title} maxLength={200} onChange={e => onChange({ ...task, title: e.target.value })} required /></Field>
    <div className="form-grid three"><Field label="状态"><select value={task.status} onChange={e => onChange({ ...task, status: e.target.value as TaskDraft['status'] })}>{['inbox', 'next', 'active', 'waiting', 'scheduled', 'someday', 'blocked', 'done', 'cancelled', 'archived'].map(s => <option key={s} value={s}>{statusLabels[s]}</option>)}</select></Field>
      <Field label="负责人"><input value={task.owner || ''} maxLength={100} placeholder="未指定" onChange={e => onChange({ ...task, owner: e.target.value || null })} /></Field>
      <Field label="优先级"><select value={task.priority} onChange={e => onChange({ ...task, priority: e.target.value as TaskDraft['priority'] })}><option value="low">低</option><option value="medium">普通</option><option value="high">高</option></select></Field></div>
    <TimeFields label="计划" value={task.scheduled} timezone={timezone} onChange={scheduled => onChange({ ...task, scheduled })} />
    <TimeFields label="截止" value={task.deadline} timezone={timezone} deadline onChange={deadline => onChange({ ...task, deadline })} />
    <details className="more-fields"><summary>标签、地点与情境</summary><div className="form-grid three"><Field label="标签（逗号分隔）"><TagsInput tags={task.tags} onChange={tags => onChange({ ...task, tags })} /></Field><Field label="地点"><input value={task.location || ''} onChange={e => onChange({ ...task, location: e.target.value || null })} /></Field><Field label="情境"><input value={task.context || ''} onChange={e => onChange({ ...task, context: e.target.value || null })} /></Field></div></details>
  </div>;
}
export function dateLabel(value: string | number, timezone = 'Asia/Shanghai') {
  const date = new Date(typeof value === 'number' ? value * 1000 : value);
  return new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', timeZone: timezone }).format(date);
}
