import { useEffect, useState } from 'react';
import { ApiError, newDraft, type CaptureDraft, type Job } from '@gkd/client';
import { Badge, dateLabel, Empty, ErrorNote, Field, Icon, Loading, Modal, useMounted, useOperation, useResource, useUI, waitForJob } from './ui';

interface DraftState { draft: CaptureDraft; uncertain: boolean }
function readDraft(key: string): DraftState {
  try { const value = JSON.parse(localStorage.getItem(key) || 'null'); if (typeof value?.draft?.raw_text === 'string' && value.draft.client_capture_id) return value; } catch { /* Keep the editor usable when storage is unavailable. */ }
  return { draft: newDraft(), uncertain: false };
}

export function Inbox() {
  const { api, auth, profile, epoch, refresh, notify, openCapture, openProposal, go } = useUI();
  const storageKey = 'guike.draft.v1.' + auth.user.id;
  const [draftState, setDraftState] = useState(() => readDraft(storageKey));
  const [storageError, setStorageError] = useState(false);
  const [mode, setMode] = useState<'local' | 'external'>('local');
  const [filter, setFilter] = useState('');
  const [offset, setOffset] = useState(0);
  const [job, setJob] = useState<Job | null>(null);
  const operation = useOperation();
  const mounted = useMounted();
  function analysisReady(result: Job) {
    refresh(); notify('整理已完成，可从收集箱的原文页查看提案。');
    if (mounted()) openProposal(result.result!.proposal_id!, { onlyIfIdle: true });
  }
  const feed = useResource(() => api.captures({ status: filter || null, offset, limit: 20 }), `${epoch}:${filter}:${offset}`);
  const draft = draftState.draft;
  function persist(value: DraftState) {
    setDraftState(value);
    try { localStorage.setItem(storageKey, JSON.stringify(value)); setStorageError(false); } catch { setStorageError(true); }
  }
  async function submit(organize: boolean) {
    if (!draft.raw_text.trim()) return;
    persist({ draft, uncertain: true });
    let captured;
    try { captured = await api.saveCapture(draft); }
    catch (error) {
      if (error instanceof ApiError && error.status >= 400 && error.status < 500) persist({ draft, uncertain: false });
      throw error;
    }
    persist({ draft: newDraft(), uncertain: false }); refresh();
    notify('原文已保存到服务端。');
    if (organize) {
      const queued = await api.analyze(captured.id, { mode }); setJob(queued);
      const result = await waitForJob(api, queued.id, setJob);
      analysisReady(result);
    }
  }
  return <><section className="page-heading"><div><h1>先记下来，<br className="mobile-break" />再慢慢理清。</h1><p>待办、灵感、一段沟通。这里接住你还没整理的想法。</p></div><span className="heading-counter">INBOX<span>{feed.data?.total ?? '—'}</span></span></section>
    <section className="capture-composer" aria-label="快速记录"><div className="composer-topline"><span className="composer-label"><span className="small-square" />一条新记录</span><select aria-label="记录来源" value={draft.source_type} disabled={operation.busy || draftState.uncertain} onChange={e => persist({ draft: { ...draft, source_type: e.target.value as CaptureDraft['source_type'] }, uncertain: false })}><option value="text">文字记录</option><option value="wechat">微信粘贴</option></select></div>
      <textarea className="capture-input" aria-label="记录内容" placeholder="比如，明天下午给李总发合同，再整理一下本周的项目进展…" value={draft.raw_text} disabled={operation.busy || draftState.uncertain} onChange={e => persist({ draft: { ...draft, raw_text: e.target.value }, uncertain: false })} />
      <div className="composer-footer"><div className="draft-meta"><span className={storageError ? 'danger-text' : ''}>{storageError ? '本机草稿保存失败，请勿关闭页面' : draft.raw_text ? '仅本机草稿 · 尚未确认上传' : '原文会被完整保留'}</span><small>{Array.from(draft.raw_text).length.toLocaleString()} / 10,000 字</small></div><div className="button-row"><button disabled={operation.busy || !draft.raw_text.trim() || Array.from(draft.raw_text).length > 10000} onClick={() => void operation.run(() => submit(false))}>只保存</button><button className="primary" disabled={operation.busy || !draft.raw_text.trim() || Array.from(draft.raw_text).length > 10000} onClick={() => void operation.run(() => submit(true))}>{operation.busy ? '正在处理…' : '保存并整理'}<Icon name="arrow" size={16} /></button></div></div>
      <div className="composer-note"><span>{mode === 'local' ? '本地规则解析，不发送给外部 AI。任务写入前需由你确认。' : '将此条原文交给已授权的外部 AI 解析，写入前仍需确认。'}</span><select aria-label="整理模式" value={mode} disabled={operation.busy} onChange={e => setMode(e.target.value as typeof mode)}><option value="local">本地规则</option><option value="external" disabled={!profile?.workspace.ai_enabled}>外部 AI{!profile?.workspace.ai_enabled ? '（未启用）' : ''}</option></select></div>
    </section>
    {draftState.uncertain && !operation.busy && <div className="warning-note">上次提交结果尚未确认。请重试原提交，以免重复创建。<button className="text-button" onClick={() => { if (window.confirm('上次提交可能已经到达服务端。清空本机草稿不会删除服务端记录，确定继续？')) persist({ draft: newDraft(), uncertain: false }); }}>清空本机草稿</button></div>}
    <ErrorNote error={operation.error} />{job && (job.status === 'pending' || job.status === 'running') && <div className="info-note"><Badge status={job.status} /> 整理在后台进行，原文已经保存。<button className="text-button" disabled={operation.busy} onClick={() => void operation.run(async () => { const result = await waitForJob(api, job.id, setJob); analysisReady(result); })}>查看进度</button></div>}
    <div className="section-heading"><div><h2>所有记录 <span className="count-label">{feed.data?.total ?? 0}</span></h2><p>从随手记录，到明确的下一步。</p></div><div className="filter-tabs" aria-label="记录状态">{[['', '全部'], ['unprocessed', '未整理'], ['needs_confirmation', '待确认'], ['processed', '已整理']].map(([value, label]) => <button aria-pressed={filter === value} key={value} onClick={() => { setFilter(value); setOffset(0); }}>{label}</button>)}</div></div>
    <ErrorNote error={feed.error} retry={feed.reload} />{feed.loading && !feed.data ? <Loading /> : !feed.error && feed.data?.items.length === 0 ? <Empty title="给脑海里的事，一个落点。">在上面记下第一条内容。不必选项目，也不必先定日期。</Empty> : <div className="capture-list">{feed.data?.items.map(capture => <article className="capture-row" key={capture.id}><span className="record-glyph"><Icon name={capture.source_type === 'wechat' ? 'inbox' : 'file'} size={19} /></span><button className="capture-copy" onClick={() => openCapture(capture.id)}><span>{capture.integrity_error ? '原文损坏或不可用，文件未被改写。' : capture.raw_text}</span><small>{capture.integrity_error?.message || (capture.source_type === 'wechat' ? '微信粘贴' : '文字记录')}<i />{dateLabel(capture.created_at, auth.workspace.timezone)}</small></button>{capture.integrity_error ? <button className="danger-link" disabled={operation.busy} onClick={() => void operation.run(async () => { if (!window.confirm('将不可用记录移入回收站？原始文件会保留，其他任务不受影响。')) return; await api.captureState(capture.id, 'trash'); refresh(); notify('不可用记录已移入回收站，原始文件仍保留。'); })}>移入回收站</button> : <><Badge status={capture.status} /><button className="icon-button" aria-label="打开记录" onClick={() => openCapture(capture.id)}><Icon name="arrow" size={18} /></button></>}</article>)}</div>}
    {feed.data && feed.data.total > 20 && <div className="pagination"><button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 20))}>上一页</button><span>{offset + 1}—{Math.min(offset + 20, feed.data.total)} / {feed.data.total}</span><button disabled={offset + 20 >= feed.data.total} onClick={() => setOffset(offset + 20)}>下一页</button></div>}
    <aside className="quiet-tip"><span>一点小提示</span><p>整理暂时失败也没关系。原文会留在这里，你随时可以重新处理，或在工作空间里手工建立任务。</p><button className="text-button" onClick={() => go('workspace')}>前往工作空间 <Icon name="arrow" size={14} /></button></aside>
  </>;
}

export function CaptureDetail({ id, onClose }: { id: string; onClose: () => void }) {
  const { api, epoch, auth, profile, refresh, notify, openProposal } = useUI();
  const resource = useResource(() => api.capture(id), `${id}:${epoch}`);
  const capture = resource.data;
  const [mode, setMode] = useState<'local' | 'external'>('local');
  const [reference, setReference] = useState('');
  const [job, setJob] = useState<Job | null>(null);
  const operation = useOperation();
  const mounted = useMounted();
  function analysisReady(result: Job) {
    refresh(); notify('整理已完成，可从收集箱的原文页查看提案。');
    if (mounted()) openProposal(result.result!.proposal_id!);
  }
  const needsReanalysis = capture && ['processed', 'needs_confirmation'].includes(capture.status);
  async function analyze() {
    if (!capture) return;
    if (needsReanalysis && !window.confirm('这条原文已有整理结果。重新分析可能得到重复任务，请在确认页仔细核对。继续？')) return;
    const queued = await api.analyze(id, { mode, reanalyze: Boolean(needsReanalysis), ...(reference ? { reference_time: new Date(reference).toISOString() } : {}) });
    setJob(queued); refresh();
    const result = await waitForJob(api, queued.id, setJob); analysisReady(result);
  }
  return <Modal title="原始记录" subtitle="原文不会被整理结果改写。" onClose={onClose}><ErrorNote error={resource.error} retry={resource.reload} />{!capture && resource.loading ? <Loading /> : capture && <>
    <div className="metadata-row"><Badge status={capture.status} /><span>{dateLabel(capture.created_at, auth.workspace.timezone)}</span><span>{capture.source_type === 'wechat' ? '微信粘贴' : '文字记录'}</span></div><pre className="raw-capture">{capture.raw_text}</pre>
    <div className="form-grid"><Field label="整理方式"><select value={mode} onChange={e => setMode(e.target.value as typeof mode)}><option value="local">本地规则解析</option><option value="external" disabled={!profile?.workspace.ai_enabled}>外部 AI（需在「我的」授权）</option></select></Field><Field label="参照时间（设备本地时间）" hint="留空使用原文保存时间；粘贴历史聊天时可调整。"><input type="datetime-local" value={reference} onChange={e => setReference(e.target.value)} /></Field></div>
    {capture.jobs?.[0]?.error && <div className="warning-note">上次处理：{capture.jobs[0].error.message}</div>}
    {job && <p className="metadata-row"><Badge status={job.status} /> 第 {job.attempts} 次尝试</p>}
    <ErrorNote error={operation.error} /><div className="button-row wrap"><button className="primary" disabled={operation.busy || capture.status === 'processing' || capture.status === 'archived'} onClick={() => void operation.run(analyze)}>{operation.busy ? '正在整理…' : needsReanalysis ? '重新分析' : '整理这条记录'}</button>
      {capture.status === 'processing' && capture.jobs?.[0] && <button disabled={operation.busy} onClick={() => void operation.run(async () => { const result = await waitForJob(api, capture.jobs![0].id, setJob); analysisReady(result); })}>查看后台结果</button>}
      <button disabled={operation.busy || capture.status === 'processing'} onClick={() => void operation.run(async () => { await api.captureState(id, capture.status === 'archived' ? 'unarchive' : 'archive'); refresh(); notify(capture.status === 'archived' ? '已恢复这条记录。' : '已归档，原文仍保留。'); onClose(); })}>{capture.status === 'archived' ? '恢复归档' : '归档记录'}</button>
      <button className="danger-link" disabled={operation.busy || capture.status === 'processing'} onClick={() => void operation.run(async () => { if (!window.confirm('将原文移入回收站？已经生成的任务不会被删除。')) return; await api.captureState(id, 'trash'); refresh(); notify('原文已移入回收站，可在「我的」恢复。'); onClose(); })}>移入回收站</button>
    </div>
    {capture.proposals && capture.proposals.length > 0 && <section className="detail-section"><h3>整理记录</h3>{capture.proposals.map(p => <button className="history-row" key={p.id} onClick={() => openProposal(p.id)}><span>提案修订 {p.revision}</span><Badge status={p.status} /><Icon name="arrow" size={16} /></button>)}</section>}
  </>}</Modal>;
}
