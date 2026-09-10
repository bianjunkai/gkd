import { useEffect, useState } from 'react';
import { ApiError, type Preview, type Proposal, type ProposalAction, type Question } from '@gkd/client';
import { Badge, ErrorNote, Field, Icon, Loading, Modal, TaskFields, useOperation, useResource, useUI } from './ui';

export function ProposalEditor({ id, onClose }: { id: string; onClose: () => void }) {
  const { api, auth, refresh, notify } = useUI();
  const resource = useResource(() => api.proposal(id), id);
  const [destinationQuery, setDestinationQuery] = useState('');
  const [destinationOffset, setDestinationOffset] = useState(0);
  const destinations = useResource(() => api.groupOptions({ q: destinationQuery, offset: destinationOffset, limit: 20 }), `${id}:${destinationQuery}:${destinationOffset}`);
  const folders = useResource(api.folders, id);
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [actions, setActions] = useState<ProposalAction[]>([]);
  const [questions, setQuestions] = useState<Question[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [dirty, setDirty] = useState(false);
  const [preview, setPreview] = useState<{ previews: Preview[]; task_count: number; file_count: number } | null>(null);
  const operation = useOperation();
  useEffect(() => { if (resource.data) { setProposal(resource.data); setActions(resource.data.actions); setQuestions(resource.data.questions); setSelected(resource.data.selected_action_ids || resource.data.actions.map(a => a.action_id)); setPreview(null); setDirty(false); } }, [resource.data]);
  const capture = useResource(() => resource.data ? api.capture(resource.data.capture_id) : Promise.resolve(null), resource.data?.capture_id || 'none');
  function edit(index: number, value: ProposalAction) { setActions(list => list.map((item, i) => i === index ? value : item)); setDirty(true); setPreview(null); }
  function close() { if (!dirty || window.confirm('有尚未保存的提案修改，确定关闭？')) onClose(); }
  function reloadLatest() {
    if (!dirty || window.confirm('重新读取会放弃尚未保存的本机修改，继续？')) { setPreview(null); operation.clearError(); resource.reload(); }
  }
  const readonly = proposal?.status === 'applied' || proposal?.status === 'rejected';
  async function updatePreview() {
    if (!proposal) return;
    setPreview(null);
    const next = await api.refreshProposalForPreview(proposal, actions, questions, dirty);
    setProposal(next); setActions(next.actions); setQuestions(next.questions); setDirty(false);
    setPreview(await api.previewProposal(next, selected));
  }
  async function apply() {
    if (!proposal) return;
    try {
      const change = await api.confirmProposal(proposal, selected);
      refresh(); notify('整理结果已写入 Markdown。', change); onClose();
    } catch (error) {
      if (error instanceof ApiError && error.code === 'PROPOSAL_STALE') { setPreview(null); setProposal({ ...proposal, status: 'stale' }); }
      throw error;
    }
  }
  return <Modal title="把建议，变成下一步。" subtitle="核对内容与归属。只有确认后，才会写入 Markdown。" onClose={close} wide><ErrorNote error={resource.error} retry={resource.reload} />{!proposal ? <Loading /> : <>
    <div className="proposal-summary"><div className="metadata-row"><Badge status={proposal.status} /><span>修订 {proposal.revision}</span><span>{proposal.provider.provider === 'local-rules' ? '本地规则解析 · 请核对语义' : `外部 AI · ${proposal.provider.model || '已配置模型'}`}</span></div><p>{proposal.provider.provider === 'local-rules' ? '规则解析只识别常见行动与明确日期，不等同于 AI 理解。缺失的负责人和截止时间不会自动补齐。' : '以下均为建议。请逐项核对原文依据，不要把模型判断当作已确认事实。'}</p></div>
    <p className="info-note">任务写在任务组内，不用任务内容命名文件。优先使用已有任务组；没有合适的，可在当前工作空间或文件夹下新建。无法判断主题时，默认放入「日常任务」。</p>
    <details className="source-preview"><summary>查看原始记录</summary>{capture.data ? <pre>{capture.data.raw_text}</pre> : <p>原始记录暂时不可用或已删除。</p>}</details>
    {proposal.status === 'stale' && <div className="warning-note">目标已发生变化。请核对目标，然后点击「更新预览」，再次确认最新差异。</div>}
    <ErrorNote error={destinations.error} retry={destinations.reload} />
    <ErrorNote error={folders.error} retry={folders.reload} />
    {!readonly && <section aria-label="追加目标查找"><Field label="查找追加目标" hint="按任务组名称或路径搜索。翻页和搜索不会更改已选归属。"><input value={destinationQuery} maxLength={200} disabled={operation.busy} onChange={e => { setDestinationQuery(e.target.value); setDestinationOffset(0); }} placeholder="搜索全部可写任务组" /></Field><div className="pagination"><button disabled={operation.busy || destinations.loading || destinationOffset === 0} onClick={() => setDestinationOffset(v => Math.max(0, v - 20))}>上一页目标</button><span>{destinations.loading ? '正在查找…' : `共 ${destinations.data?.total || 0} 个目标 · 第 ${destinationOffset / 20 + 1} 页`}</span><button disabled={operation.busy || destinations.loading || destinationOffset + 20 >= (destinations.data?.total || 0)} onClick={() => setDestinationOffset(v => v + 20)}>下一页目标</button></div></section>}
    <div className="proposal-layout"><div className="proposal-actions">{actions.map((action, index) => <section className={`proposal-action ${selected.includes(action.action_id) ? '' : 'unselected'}`} key={action.action_id}>
      <div className="action-header"><label className="check-label"><input type="checkbox" checked={selected.includes(action.action_id)} disabled={readonly || operation.busy} onChange={e => { setSelected(ids => e.target.checked ? [...ids, action.action_id] : ids.filter(v => v !== action.action_id)); setPreview(null); }} /><strong>{action.tasks.length ? `行动 ${String(index + 1).padStart(2, '0')}` : '保存为资料'}</strong></label><span className="small-muted">{selected.includes(action.action_id) ? '将被写入' : '本次忽略'}</span></div>
      <fieldset disabled={readonly || operation.busy}>
        {action.tasks.map((task, ti) => <TaskFields key={ti} task={task} timezone={proposal.timezone || auth.workspace.timezone} onChange={value => edit(index, { ...action, tasks: action.tasks.map((t, i) => i === ti ? value : t) })} />)}
        {!action.tasks.length && <Field label="资料正文"><textarea rows={6} value={action.body_append} onChange={e => edit(index, { ...action, body_append: e.target.value })} /></Field>}
        <div className="destination-fields"><h4>保存到哪里：所属任务组</h4><Field label="写入方式"><select disabled={destinations.loading} value={action.type === 'create' ? 'create' : action.target_group_id || ''} onChange={e => { const target = destinations.data?.items.find(g => g.id === e.target.value); if (e.target.value !== 'create' && !target) return; edit(index, target ? { ...action, type: 'append', target_group_id: target.id, folder_id: target.folder_id, group_title: target.title } : { ...action, type: 'create', target_group_id: null, folder_id: null }); }}><option value="create">新建任务组（Markdown 文件）</option>{action.type === 'append' && !destinations.data?.items.some(g => g.id === action.target_group_id) && <option value={action.target_group_id!}>已选追加：{proposal.previews.find(p => p.group_id === action.target_group_id)?.path || action.group_title}</option>}{destinations.data?.items.map(g => <option value={g.id} key={g.id}>追加：{g.path}</option>)}</select></Field>
        {action.type === 'create' && <div className="form-grid"><Field label="任务组名称" hint="使用主题名称，如「合同跟进」；不要照抄任务标题。同一位置同名的可写任务组会复用。"><input value={action.group_title} maxLength={200} onChange={e => edit(index, { ...action, group_title: e.target.value })} /></Field><Field label="归属位置"><select value={action.folder_id || ''} onChange={e => edit(index, { ...action, folder_id: e.target.value || null })}><option value="">工作空间根目录</option>{folders.data?.filter(f => !f.effective_archived).map(f => <option value={f.id} key={f.id}>{f.path}</option>)}</select></Field></div>}
        {action.type === 'append' && <p className="small-muted">归属：工作空间 / {destinations.data?.items.find(g => g.id === action.target_group_id)?.path || proposal.previews.find(p => p.group_id === action.target_group_id)?.path || action.group_title}。只追加任务或正文，不替换现有内容。已完成的任务组会重新打开。</p>}</div>
      </fieldset>
      {Object.keys(action.evidence).length > 0 && <details className="evidence"><summary>原文依据与置信提示</summary>{Object.entries(action.evidence).map(([key, quote]) => <div key={key}><span>{({ title: '标题', scheduled: '计划', deadline: '截止', owner: '负责人', body: '正文' } as Record<string, string>)[key] || key} · {({ high: '较高', medium: '需核对', low: '待澄清' } as Record<string, string>)[action.field_confidence[key]] || '需核对'}</span><q>{quote}</q></div>)}</details>}
    </section>)}</div>
    <aside className="confirmation-rail"><span className="eyebrow">BEFORE YOU CONFIRM</span><h3>由你做最后确认。</h3><dl><div><dt>选中动作</dt><dd>{selected.length} / {actions.length}</dd></div><div><dt>影响文件</dt><dd>{preview ? `${preview.file_count} 个` : '预览后确定'}</dd></div><div><dt>新增任务</dt><dd>{actions.filter(a => selected.includes(a.action_id)).reduce((n, a) => n + a.tasks.length, 0)} 项</dd></div></dl>
      <p>任务是文件内的内容，任务组是容器。请核对所属位置和预览路径；未选动作仅标为忽略，原始记录仍然保留。</p>
      {questions.length > 0 && <div className="clarifications"><h4>需要留意</h4>{questions.map((question, i) => <label className="question" key={i}><input type="checkbox" checked={question.resolved} disabled={readonly || operation.busy} onChange={e => { setQuestions(q => q.map((item, index) => index === i ? { ...item, resolved: e.target.checked } : item)); setDirty(true); setPreview(null); }} /><span>{question.message}<small>{question.required ? '必需：处理后勾选' : '可选：留空也可确认'}</small></span></label>)}</div>}
      {!readonly && <><button className="wide" disabled={operation.busy || selected.length === 0} onClick={() => void operation.run(updatePreview)}>{operation.busy ? '处理中…' : dirty || proposal.status === 'stale' ? '保存修订并更新预览' : '更新预览'}</button><button className="primary wide" disabled={operation.busy || dirty || !preview || selected.length === 0 || questions.some(q => q.required && !q.resolved)} onClick={() => void operation.run(apply)}>确认写入</button><button className="text-button wide" disabled={operation.busy} onClick={() => void operation.run(async () => { await api.rejectProposal(proposal); refresh(); notify('已取消提案，原文仍然保留。'); onClose(); })}>取消整个提案</button></>}
      {readonly && <p className="info-note">{proposal.status === 'applied' ? '这份提案已经写入。可从工作空间查看文件和撤销记录。' : '这份提案已取消，没有新增写入。'}</p>}
      <ErrorNote error={operation.error} />
      {Boolean(operation.error) && <button disabled={operation.busy} onClick={reloadLatest}>重新读取提案</button>}
    </aside></div>
    {(preview?.previews || proposal.previews).length > 0 && <section className="diff-section"><h3>{preview ? '本次选中动作的最终差异' : '建议差异（请更新预览后确认）'}</h3>{(preview?.previews || proposal.previews).map(item => <details key={item.group_id} className="diff-item"><summary><Icon name="file" size={17} />{item.path}{item.will_reopen && <span className="badge status-waiting">恢复为进行中</span>}</summary><pre className="diff-code">{item.diff}</pre></details>)}</section>}
  </>}</Modal>;
}
