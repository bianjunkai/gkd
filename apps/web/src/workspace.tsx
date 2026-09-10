import { useState } from 'react';
import { draftOf, emptyTask, formatTime, versionOf, type Folder, type Group, type Task, type TaskDraft, type TaskItem, type VersionPreview } from '@gkd/client';
import { Badge, dateLabel, Empty, ErrorNote, Field, Icon, Loading, Modal, TaskFields, useOperation, useResource, useUI } from './ui';

export function TaskRow({ task, group, onEdit }: { task: Task | TaskItem; group?: Group; onEdit?: () => void }) {
  const { api, refresh, notify, openGroup, auth } = useUI();
  const operation = useOperation();
  const item = task as TaskItem;
  const groupId = group?.id || item.group_id;
  const version = group ? versionOf(group) : { expected_revision: item.group_revision, expected_hash: item.group_hash };
  return <div className={`task-row ${task.status === 'done' ? 'task-done' : ''}`}><button className="task-check" aria-label={`${task.status === 'done' ? '重新打开' : '完成'}：${task.title}`} aria-pressed={task.status === 'done'} disabled={operation.busy || group?.status === 'archived' || task.status === 'archived'} onClick={() => void operation.run(async () => { const change = await api.updateTask(groupId, task.id, version, { status: task.status === 'done' ? 'next' : 'done' }); refresh(); notify(task.status === 'done' ? '任务已重新打开。' : '这一步，完成了。', change); })}>{task.status === 'done' && <Icon name="check" size={14} />}</button>
    <div className="task-content"><button className="task-title" onClick={onEdit || (() => openGroup(groupId))}>{task.title}</button><div className="task-meta">{!group && <button onClick={() => openGroup(groupId)}>{item.group_title}</button>}{task.owner && <span>{task.owner}</span>}{task.scheduled && <span>计划 {formatTime(task.scheduled)}{task.scheduled.timezone !== auth.workspace.timezone ? ` (${task.scheduled.timezone})` : ''}</span>}{task.deadline && <span className={task.is_overdue ? 'danger-text' : ''}>截止 {formatTime(task.deadline)}</span>}{task.tags.map(tag => <span className="tag" key={tag}>{tag}</span>)}</div><ErrorNote error={operation.error} retry={refresh} /></div><Badge status={task.status} />{task.priority === 'high' && <span className="priority-high">高优先</span>}</div>;
}

export function Today() {
  const { api, epoch, go } = useUI();
  const [view, setView] = useState('today');
  const [offset, setOffset] = useState(0);
  const tasks = useResource(() => api.tasks({ view, include_completed: view === 'done', offset, limit: 30 }), `${epoch}:${view}:${offset}`);
  const overdue = useResource(() => api.tasks({ view: 'overdue', limit: 100 }), epoch);
  return <><section className="page-heading"><div><h1>把注意力，留给下一步。</h1><p>计划决定什么时候做，截止时间决定什么时候到期。</p></div><span className="heading-counter">ACTION<span>{tasks.data?.total ?? '—'}</span></span></section>
    <div className="task-view-tabs filter-tabs">{[['today', '今日'], ['all', '全部待办'], ['next', '下一步'], ['waiting', '等待'], ['someday', '将来'], ['done', '已完成']].map(([key, label]) => <button key={key} aria-pressed={view === key} onClick={() => { setView(key); setOffset(0); }}>{label}</button>)}</div>
    {view === 'today' && overdue.data && overdue.data.total > 0 && <section className="overdue-section"><div className="section-heading"><h2>已逾期 <span className="count-label">{overdue.data.total}</span></h2><span className="small-muted">按截止时间判断</span></div>{overdue.data.items.map(task => <TaskRow key={task.id} task={task} />)}</section>}
    <div className="section-heading"><h2>{({ today: '今天的行动', all: '所有待办', next: '可以推进的事', waiting: '等待进展', someday: '留给将来', done: '已完成的行动' } as Record<string, string>)[view]}</h2><span className="small-muted">{tasks.data?.total ?? 0} 项</span></div>
    <ErrorNote error={tasks.error} retry={tasks.reload} />{tasks.loading && !tasks.data ? <Loading /> : !tasks.error && tasks.data?.items.length === 0 ? <Empty title={view === 'today' ? '今天，还有留白。' : '这里还没有任务。'} action={<button onClick={() => go('inbox')}>记录一件事 <Icon name="plus" size={16} /></button>}>从收集箱整理一条记录，或在任务详情中安排计划日期。</Empty> : <section className="task-list">{tasks.data?.items.map(task => <TaskRow key={task.id} task={task} />)}</section>}
    {tasks.data && tasks.data.total > 30 && <div className="pagination"><button disabled={offset === 0} onClick={() => setOffset(offset - 30)}>上一页</button><span>{offset + 1} / {tasks.data.total}</span><button disabled={offset + 30 >= tasks.data.total} onClick={() => setOffset(offset + 30)}>下一页</button></div>}
  </>;
}

export function WorkspacePanel() {
  const { api, epoch, openGroup } = useUI();
  const [folderId, setFolderId] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);
  const [archived, setArchived] = useState(false);
  const [dialog, setDialog] = useState<'group' | 'folder' | Folder | null>(null);
  const folders = useResource(api.folders, epoch);
  const groups = useResource(() => api.groups({ folder_id: folderId || 'root', status: archived ? 'archived' : null, offset, limit: 25 }), `${epoch}:${folderId}:${archived}:${offset}`);
  const currentFolder = folders.data?.find(f => f.id === folderId);
  const breadcrumbs: Folder[] = [];
  for (let folder = currentFolder; folder; folder = folders.data?.find(f => f.id === folder!.parent_id)) breadcrumbs.unshift(folder);
  function enter(id: string | null) { setFolderId(id); setOffset(0); }
  return <><section className="page-heading"><div><h1>每件事，都有它的位置。</h1><p>文件夹管理归属，Markdown 记录目标、任务与进展。</p></div><div className="button-row"><button onClick={() => setDialog('folder')}><Icon name="plus" size={17} />文件夹</button><button className="primary" onClick={() => setDialog('group')}><Icon name="plus" size={17} />新建任务组</button></div></section>
    <div className="workspace-toolbar"><div className="breadcrumbs"><button onClick={() => enter(null)}>工作空间</button>{breadcrumbs.map(f => <span key={f.id}><Icon name="arrow" size={14} /><button onClick={() => enter(f.id)}>{f.name}</button></span>)}</div><label className="check-label"><input type="checkbox" checked={archived} onChange={e => setArchived(e.target.checked)} />显示已归档文件</label></div>
    <ErrorNote error={folders.error} retry={folders.reload} /><div className="folder-grid">{folders.data?.filter(f => f.parent_id === folderId && !f.effective_archived).map(f => <div className="folder-tile" key={f.id}><button onClick={() => enter(f.id)}><Icon name="workspace" size={25} /><strong>{f.name}</strong><Icon name="arrow" size={15} /></button><button className="folder-edit" aria-label={`编辑文件夹：${f.name}`} onClick={() => setDialog(f)}>管理</button></div>)}</div>
    {currentFolder && <div className="folder-caption"><span>{currentFolder.path}</span><button className="text-button" onClick={() => setDialog(currentFolder)}>重命名 / 移动文件夹</button></div>}
    <div className="section-heading"><h2>{archived ? '已归档文件' : '任务组'} <span className="count-label">{groups.data?.total ?? 0}</span></h2><span className="small-muted">一个文件，一个工作单元</span></div><ErrorNote error={groups.error} retry={groups.reload} />
    {groups.loading && !groups.data ? <Loading /> : groups.data?.items.length === 0 && !groups.error ? <Empty title="为一件重要的事，建一个文件。" action={<button onClick={() => setDialog('group')}>新建任务组</button>}>可以只有一段笔记，也可以是带有行动清单的项目。</Empty> : <div className="group-list">{groups.data?.items.map(g => <button className="group-row" key={g.id} onClick={() => openGroup(g.id)}><span className="file-glyph"><Icon name="file" size={22} /></span><span className="group-copy"><strong>{g.title}</strong><small>{g.path}{g.tags.length > 0 ? ' · ' + g.tags.join(' / ') : ''}</small></span><span className="group-progress">{g.progress.total ? `${g.progress.done} / ${g.progress.total} 完成` : '无任务'}</span><Badge status={g.status} /><Icon name="arrow" size={17} /></button>)}</div>}
    {groups.data && groups.data.total > 25 && <div className="pagination"><button disabled={!offset} onClick={() => setOffset(offset - 25)}>上一页</button><span>{offset + 1}—{Math.min(offset + 25, groups.data.total)} / {groups.data.total}</span><button disabled={offset + 25 >= groups.data.total} onClick={() => setOffset(offset + 25)}>下一页</button></div>}
    {dialog === 'group' && <NewGroup folderId={folderId} folders={folders.data || []} onClose={() => setDialog(null)} />}
    {dialog && dialog !== 'group' && <FolderEditor folder={typeof dialog === 'object' ? dialog : null} parentId={folderId} folders={folders.data || []} onClose={() => setDialog(null)} />}
  </>;
}

function FolderEditor({ folder, parentId, folders, onClose }: { folder: Folder | null; parentId: string | null; folders: Folder[]; onClose: () => void }) {
  const { api, refresh, notify } = useUI();
  const [name, setName] = useState(folder?.name || '');
  const [parent, setParent] = useState(folder ? folder.parent_id || '' : parentId || '');
  const operation = useOperation();
  return <Modal title={folder ? '管理文件夹' : '新建文件夹'} onClose={onClose}><form onSubmit={e => { e.preventDefault(); void operation.run(async () => { const change = folder ? await api.updateFolder(folder.id, { expected_revision: folder.revision, name, parent_id: parent || null }) : await api.createFolder(name, parent || null); refresh(); notify('文件夹已保存。', change); onClose(); }); }}><Field label="文件夹名称"><input value={name} onChange={e => setName(e.target.value)} required maxLength={100} /></Field><Field label="上级目录"><select value={parent} onChange={e => setParent(e.target.value)}><option value="">根目录</option>{folders.filter(f => !f.effective_archived && (!folder || (f.id !== folder.id && !f.path.startsWith(folder.path + '/')))).map(f => <option key={f.id} value={f.id}>{f.path}</option>)}</select></Field><p className="small-muted">移动会同步调整下属文件路径。文件和任务的稳定 ID 不变。</p><ErrorNote error={operation.error} /><button className="primary" disabled={operation.busy} type="submit">{operation.busy ? '正在保存…' : '保存文件夹'}</button></form></Modal>;
}

function NewGroup({ folderId, folders, onClose }: { folderId: string | null; folders: Folder[]; onClose: () => void }) {
  const { api, refresh, notify, openGroup } = useUI();
  const [title, setTitle] = useState('');
  const [body, setBody] = useState('');
  const [taskTitle, setTaskTitle] = useState('');
  const [folder, setFolder] = useState(folderId || '');
  const operation = useOperation();
  return <Modal title="为这件事，留一个位置。" subtitle="手动保存就是对本次变更的确认，不需要调用 AI。" onClose={onClose}><form onSubmit={e => { e.preventDefault(); void operation.run(async () => { const change = await api.createGroup({ title, body, folder_id: folder || null, tasks: taskTitle.trim() ? [emptyTask(taskTitle)] : [] }); refresh(); notify('任务组已创建。', change); onClose(); openGroup(change.group_ids[0]); }); }}><Field label="任务组名称"><input value={title} onChange={e => setTitle(e.target.value)} required maxLength={200} placeholder="比如，秋季项目交付" /></Field><Field label="文件夹"><select value={folder} onChange={e => setFolder(e.target.value)}><option value="">工作空间根目录</option>{folders.filter(f => !f.effective_archived).map(f => <option key={f.id} value={f.id}>{f.path}</option>)}</select></Field><Field label="第一项任务（可选）"><input value={taskTitle} onChange={e => setTaskTitle(e.target.value)} maxLength={200} placeholder="一个具体、可执行的下一步" /></Field><Field label="正文（可选）"><textarea rows={7} value={body} onChange={e => setBody(e.target.value)} placeholder="目标、背景、需要保留的资料…" /></Field><ErrorNote error={operation.error} /><button type="submit" className="primary" disabled={operation.busy}>{operation.busy ? '正在保存…' : '创建任务组'}</button></form></Modal>;
}

export function GroupDetail({ id, onClose }: { id: string; onClose: () => void }) {
  const { api, epoch, refresh, notify, openCapture } = useUI();
  const resource = useResource(() => api.group(id), `${id}:${epoch}`);
  const group = resource.data;
  const [tab, setTab] = useState('content');
  const [editing, setEditing] = useState<Group | null>(null);
  const [editingTask, setEditingTask] = useState<{ group: Group; task: TaskDraft; id: string | null } | null>(null);
  const operation = useOperation();
  function close() { if ((!editing && !editingTask) || window.confirm('编辑尚未保存，确定关闭？')) onClose(); }
  return <Modal title={group?.title || '任务组'} subtitle={group?.path || '正在读取 Markdown…'} onClose={close} wide><ErrorNote error={resource.error} retry={resource.reload} />{!group ? resource.loading && <Loading /> : <>
    <div className="group-detail-meta"><Badge status={group.status} /><span>修订 {group.revision}</span><span>{group.progress.total ? `${group.progress.done} / ${group.progress.total} 完成` : '无任务'}</span><span className="mono subtle">{group.id.slice(0, 18)}…</span></div>
    <div className="filter-tabs detail-tabs">{[['content', '任务与正文'], ['source', 'Markdown 源码'], ['history', '版本记录']].map(([key, label]) => <button key={key} aria-pressed={tab === key} disabled={Boolean(editing || editingTask)} onClick={() => setTab(key)}>{label}</button>)}</div>
    {tab === 'content' && <>{editing ? <GroupEditor original={editing} onDone={() => setEditing(null)} /> : editingTask ? <TaskEditor original={editingTask.group} taskId={editingTask.id} initial={editingTask.task} onDone={() => setEditingTask(null)} /> : <>
      <div className="section-heading"><h3>行动清单</h3><button disabled={group.status === 'archived'} onClick={() => setEditingTask({ group, task: emptyTask(), id: null })}><Icon name="plus" size={16} />添加任务</button></div>
      {group.tasks.length ? group.tasks.map(task => <TaskRow key={task.id} task={task} group={group} onEdit={() => setEditingTask({ group, task: draftOf(task), id: task.id })} />) : <p className="empty-inline">这是一个资料文件。你可以随时添加第一项任务。</p>}
      <div className="section-heading"><h3>正文</h3><button onClick={() => setEditing(group)}>编辑任务组</button></div><pre className="document-body">{group.body || '还没有补充正文。'}</pre>
      {group.tags.length > 0 && <div className="tag-row">{group.tags.map(tag => <span className="tag" key={tag}>{tag}</span>)}</div>}
      {group.tasks.some(t => t.source_refs.length > 0) && <div className="source-links"><h4>原始来源</h4>{[...new Set(group.tasks.flatMap(t => t.source_refs))].map(ref => <button className="text-button" key={ref} onClick={() => openCapture(ref)}>查看原文 · {ref.slice(-8)} <Icon name="arrow" size={14} /></button>)}</div>}
      <div className="detail-danger-zone"><span>删除仅进入回收站，可恢复。</span><button className="danger-link" disabled={operation.busy} onClick={() => void operation.run(async () => { if (!window.confirm(`将「${group.title}」和其中 ${group.tasks.length} 项任务移入回收站？`)) return; const change = await api.trashGroup(group); refresh(); notify('任务组已移入回收站。', change); onClose(); })}>移入回收站</button></div><ErrorNote error={operation.error} />
    </>}</>}
    {tab === 'source' && <><p className="info-note">只读预览。YAML 中的 tasks 是事实来源，正文任务区是同步生成的阅读视图。HTML 与远程图片不会在这里执行或加载。</p><pre className="source-code">{group.markdown}</pre></>}
    {tab === 'history' && <HistoryPanel group={group} />}
  </>}</Modal>;
}

function TaskEditor({ original, taskId, initial, onDone }: { original: Group; taskId: string | null; initial: TaskDraft; onDone: () => void }) {
  const { api, auth, refresh, notify } = useUI();
  const [task, setTask] = useState(initial);
  const operation = useOperation();
  return <form className="structured-editor" onSubmit={e => { e.preventDefault(); void operation.run(async () => { const change = taskId ? await api.updateTask(original.id, taskId, versionOf(original), task) : await api.addTask(original, task); refresh(); notify('任务已保存。', change); onDone(); }); }}><h3>{taskId ? '编辑任务' : '添加任务'}</h3><TaskFields task={task} onChange={setTask} timezone={auth.workspace.timezone} /><ErrorNote error={operation.error} /><div className="button-row"><button className="primary" type="submit" disabled={operation.busy}>保存任务</button><button type="button" onClick={onDone} disabled={operation.busy}>取消</button></div></form>;
}

function GroupEditor({ original, onDone }: { original: Group; onDone: () => void }) {
  const { api, refresh, notify } = useUI();
  const [title, setTitle] = useState(original.title), [body, setBody] = useState(original.body);
  const [folder, setFolder] = useState(original.folder_id || ''), [tags, setTags] = useState(original.tags.join(', '));
  const [status, setStatus] = useState(original.status), [fileName, setFileName] = useState(original.path.split('/').at(-1)!);
  const folders = useResource(api.folders, 'edit-folders');
  const operation = useOperation();
  return <form className="structured-editor" onSubmit={e => { e.preventDefault(); void operation.run(async () => { const change = await api.updateGroup(original.id, { ...versionOf(original), title, body, folder_id: folder || null, file_name: fileName, status, tags: tags.split(/[,，]/).map(t => t.trim()).filter(Boolean) }); refresh(); notify('任务组已保存。', change); onDone(); }); }}><h3>编辑任务组</h3><div className="form-grid"><Field label="任务组名称"><input value={title} onChange={e => setTitle(e.target.value)} required maxLength={200} /></Field><Field label="状态"><select value={status} onChange={e => setStatus(e.target.value as Group['status'])}><option value="active">进行中</option><option value="done">已完成（需先完成任务）</option><option value="archived">归档</option></select></Field><Field label="文件夹"><select value={folder} onChange={e => setFolder(e.target.value)}><option value="">工作空间根目录</option>{folders.data?.filter(f => !f.effective_archived).map(f => <option key={f.id} value={f.id}>{f.path}</option>)}</select></Field><Field label="文件名"><input value={fileName} onChange={e => setFileName(e.target.value)} required maxLength={100} /></Field></div><Field label="标签（逗号分隔）"><input value={tags} onChange={e => setTags(e.target.value)} /></Field><Field label="正文" hint="仅编辑非托管正文。任务清单由结构化字段自动生成。"><textarea rows={10} value={body} onChange={e => setBody(e.target.value)} /></Field><ErrorNote error={folders.error} /><ErrorNote error={operation.error} /><div className="button-row"><button type="submit" className="primary" disabled={operation.busy}>保存任务组</button><button type="button" disabled={operation.busy} onClick={onDone}>取消</button></div></form>;
}

function HistoryPanel({ group }: { group: Group }) {
  const { api, epoch, refresh, notify } = useUI();
  const history = useResource(() => api.history(group.id), `${group.id}:${epoch}`);
  const [diff, setDiff] = useState<string | null>(null);
  const [restoring, setRestoring] = useState<VersionPreview | null>(null);
  const operation = useOperation();
  return <section className="history-panel"><p className="muted">撤销以整个变更批次为单位。若已有后续编辑，请先预览历史版本，再恢复为一个新版本。</p><ErrorNote error={history.error} retry={history.reload} />{history.loading && !history.data && <Loading />}
    {history.data?.map(change => <article className="history-entry" key={change.id}><div><strong>{change.summary}</strong><small>{dateLabel(change.created_at)} · {change.group_ids.length} 个文件 {change.undone_by ? '· 已撤销' : ''}</small></div><div className="button-row wrap"><button disabled={operation.busy} onClick={() => void operation.run(async () => { const detail = await api.change(change.id); setDiff(detail.differences?.map(d => d.diff).join('\n\n') || '此变更仅修改目录属性。'); setRestoring(null); })}>查看差异</button><button disabled={operation.busy || Boolean(change.undone_by)} onClick={() => void operation.run(async () => { if (change.group_ids.length > 1 && !window.confirm(`这会撤销整个批次，影响 ${change.group_ids.length} 个文件。继续？`)) return; const result = await api.undo(change.id); refresh(); notify('变更已撤销。', result); })}>撤销</button>{change.versions?.find(v => v.group_id === group.id)?.after_hash && <button disabled={operation.busy} onClick={() => void operation.run(async () => { const hash = change.versions!.find(v => v.group_id === group.id)!.after_hash!; setRestoring(await api.version(group.id, hash)); setDiff(null); })}>预览恢复</button>}</div></article>)}
    <ErrorNote error={operation.error} />{diff !== null && <pre className="diff-code">{diff}</pre>}{restoring && <div className="restore-preview"><h3>恢复内容预览</h3><p>将以这份历史内容替换当前任务组内容，产生新的修订号。当前版本仍然留在历史中。</p><pre className="diff-code">{restoring.diff || '业务内容无差异；恢复仍会产生新版本。'}</pre><div className="button-row"><button className="primary" disabled={operation.busy} onClick={() => void operation.run(async () => { const change = await api.restoreVersion(group.id, restoring); setRestoring(null); refresh(); notify('历史内容已恢复为新版本。', change); })}>确认恢复为新版本</button><button onClick={() => setRestoring(null)}>取消</button></div></div>}
  </section>;
}
