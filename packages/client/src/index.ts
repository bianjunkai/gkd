export type TaskStatus = 'inbox' | 'next' | 'active' | 'waiting' | 'scheduled' | 'someday' | 'blocked' | 'done' | 'cancelled' | 'archived';
export type Period = 'morning' | 'afternoon' | 'evening';
export interface TimeSpec { date: string; time: string | null; period: Period | null; timezone: string }
export interface TaskDraft {
  title: string; status: TaskStatus; owner: string | null; scheduled: TimeSpec | null;
  deadline: TimeSpec | null; priority: 'low' | 'medium' | 'high'; location: string | null;
  context: string | null; tags: string[];
}
export interface Task extends TaskDraft {
  id: string; source_refs: string[]; created_at: string; updated_at: string; completed_at: string | null;
  archived_from_status: TaskStatus | null; is_today: boolean; is_overdue: boolean;
}
export interface TaskItem extends Task { group_id: string; group_title: string; group_revision: number; group_hash: string; folder_id: string | null; path: string }
export interface Group {
  id: string; title: string; status: 'active' | 'done' | 'archived'; revision: number; content_hash: string;
  created_at: string; updated_at: string; tags: string[]; tasks: Task[]; path: string;
  folder_id: string | null; body: string; markdown: string; deleted_at: number | null;
  progress: { done: number; total: number };
}
export interface Folder { id: string; name: string; path: string; parent_id: string | null; revision: number; archived: boolean; effective_archived: boolean }
export type GroupOption = Pick<Group, 'id' | 'title' | 'path' | 'folder_id'>;
export interface CaptureDraft { client_capture_id: string; raw_text: string; source_type: 'text' | 'wechat' }
export interface Capture extends CaptureDraft {
  id: string; status: 'unprocessed' | 'processing' | 'needs_confirmation' | 'processed' | 'failed' | 'archived';
  created_at: string; deleted_at: number | null; reference_timezone: string | null;
  integrity_error?: { code: string; message: string };
  proposals?: Array<{ id: string; revision: number; status: string }>;
  jobs?: Array<{ id: string; status: string; error: { code: string; message: string } | null }>;
}
export interface Page<T> { items: T[]; total: number; offset: number; limit: number }
export interface User { id: string; username: string; display_name: string }
export interface Workspace { id: string; name: string; timezone: string; ai_enabled: boolean }
export interface AuthResult { token: string; user: User; workspace: Workspace }
export interface Profile {
  user: User; workspace: Workspace;
  ai: { configured: boolean; provider: string; model: string; consent_version: string; disclosure: string;
    usage: { requests: number; limit: number; date: string; timezone: string } };
  storage: { bytes: number; limit: number };
}
export interface Health { status: string; version: string; password_auth: boolean; wechat_configured: boolean; external_ai_configured: boolean }
export interface ProposalAction {
  action_id: string; type: 'create' | 'append'; target_group_id: string | null; folder_id: string | null;
  group_title: string; file_name: string | null; tasks: TaskDraft[]; body_append: string;
  evidence: Record<string, string>; field_confidence: Record<string, 'high' | 'medium' | 'low'>;
}
export interface Question { field_path: string; message: string; required: boolean; resolved: boolean }
export interface Preview { group_id: string; title: string; path: string; diff: string; will_reopen: boolean; before_revision: number | null }
export interface Proposal {
  id: string; revision: number; status: 'pending_confirmation' | 'applied' | 'rejected' | 'stale' | 'failed';
  capture_id: string; created_at: number; actions: ProposalAction[]; previews: Preview[]; questions: Question[];
  classification: string; reference_time: string; timezone: string;
  provider: { provider: string; model?: string }; selected_action_ids?: string[]; change_set_id?: string;
  candidates: Array<{ id: string; title: string; path: string; reason: string }>;
}
export interface Change {
  id: string; kind: string; summary: string; status: string; created_at: number; group_ids: string[]; folder_ids: string[]; undone_by: string | null;
  versions?: Array<{ group_id: string; before_hash: string | null; after_hash: string | null }>;
  differences?: Array<{ group_id: string; diff: string }>;
}
export interface Job {
  id: string; kind: 'analysis' | 'reindex'; status: 'pending' | 'running' | 'succeeded' | 'failed';
  attempts: number; created_at: number; finished_at: number | null; run_after: number;
  error: { code: string; message: string; retryable: boolean } | null;
  result: { proposal_id?: string;
    groups?: number; tasks?: number; errors?: Array<{ path: string; code: string; message: string }> } | null;
}
export interface SearchHit { type: 'task' | 'group' | 'capture'; id: string; title: string; snippet: string; group_id: string | null }
export interface Version { expected_revision: number; expected_hash: string }
export interface VersionPreview extends Version { content_hash: string; markdown: string; diff: string; group: Group }
export type Params = Record<string, string | number | boolean | null | undefined>;
export interface TransportRequest { method: 'GET' | 'POST' | 'PATCH'; path: string; headers: Record<string, string>; body?: unknown }
export type Transport = <T>(request: TransportRequest) => Promise<T>;

export class ApiError extends Error {
  constructor(public code: string, message: string, public status = 0, public details: Record<string, unknown> = {}) {
    super(message); this.name = 'ApiError';
  }
}
export function errorFromResponse(status: number, data: unknown): ApiError {
  const value = data as { error?: { code?: string; message?: string; details?: Record<string, unknown> } };
  return new ApiError(value?.error?.code || 'HTTP_ERROR', value?.error?.message || `请求失败（${status}），请重试。`, status, value?.error?.details);
}
export function requestKey(prefix = 'request'): string {
  // These are idempotency labels, never authentication secrets or server object IDs.
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`;
}
export function newDraft(): CaptureDraft { return { client_capture_id: requestKey('capture'), raw_text: '', source_type: 'text' }; }
export function emptyTask(title = ''): TaskDraft {
  return { title, status: 'next', owner: null, scheduled: null, deadline: null, priority: 'medium', location: null, context: null, tags: [] };
}
export function draftOf(task: TaskDraft): TaskDraft {
  return { title: task.title, status: task.status, owner: task.owner, scheduled: task.scheduled, deadline: task.deadline,
    priority: task.priority, location: task.location, context: task.context, tags: [...task.tags] };
}
export function versionOf(group: Group): Version { return { expected_revision: group.revision, expected_hash: group.content_hash }; }
export const statusLabels: Record<string, string> = {
  inbox: '待澄清', next: '下一步', active: '进行中', waiting: '等待', scheduled: '已安排', someday: '将来', blocked: '阻塞',
  done: '已完成', cancelled: '已取消', archived: '已归档', unprocessed: '未整理', processing: '整理中',
  needs_confirmation: '待确认', processed: '已整理', failed: '失败', pending: '排队中', running: '处理中',
  succeeded: '已完成', pending_confirmation: '待确认', applied: '已写入', rejected: '已取消', stale: '需重新预览',
};
export function formatTime(spec: TimeSpec | null): string {
  if (!spec) return '未设置';
  const period = spec.period ? { morning: '上午', afternoon: '下午', evening: '晚上' }[spec.period] : '';
  return `${spec.date}${spec.time ? ' ' + spec.time : period ? ' ' + period : ''}`;
}
export function queryString(params: Params = {}): string {
  const values = Object.entries(params).filter(([, value]) => value !== null && value !== undefined && value !== '');
  return values.length ? '?' + values.map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`).join('&') : '';
}

export class ApiClient {
  private pendingKeys = new Map<string, string>();
  constructor(private transport: Transport, private token: () => string | null) {}
  async request<T>(method: TransportRequest['method'], path: string, body?: unknown, key?: string): Promise<T> {
    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    const token = this.token();
    if (token) headers.Authorization = `Bearer ${token}`;
    if (key) headers['Idempotency-Key'] = key;
    return this.transport<T>({ method, path: '/api' + path, headers, body });
  }
  private async mutation<T>(method: 'POST' | 'PATCH', path: string, body?: unknown, explicitKey?: string): Promise<T> {
    const signature = method + path + JSON.stringify(body);
    const key = explicitKey || this.pendingKeys.get(signature) || requestKey();
    this.pendingKeys.set(signature, key);
    try {
      const result = await this.request<T>(method, path, body, key);
      this.pendingKeys.delete(signature);
      return result;
    } catch (error) {
      // Keep the key after a lost response. A retry must not duplicate a committed operation.
      if (error instanceof ApiError && error.status >= 400 && error.status < 500 && error.status !== 429) this.pendingKeys.delete(signature);
      throw error;
    }
  }
  health = () => this.request<Health>('GET', '/health');
  me = () => this.request<Profile>('GET', '/me');
  login = (username: string, password: string) => this.request<AuthResult>('POST', '/auth/login', { username, password });
  register = (username: string, password: string, display_name: string) => this.request<AuthResult>('POST', '/auth/register', { username, password, display_name });
  wechatLogin = (code: string) => this.request<AuthResult>('POST', '/auth/wechat', { code });
  logout = () => this.request<{ ok: boolean }>('POST', '/auth/logout');
  updateWorkspace = (data: Partial<Workspace> & { ai_consent_version?: string }) => this.request<Workspace>('PATCH', '/workspace', data);
  saveCapture = (draft: CaptureDraft) => this.request<Capture>('POST', '/captures', draft);
  captures = (params?: Params) => this.request<Page<Capture>>('GET', '/captures' + queryString(params));
  capture = (id: string) => this.request<Capture>('GET', `/captures/${encodeURIComponent(id)}`);
  captureState = (id: string, action: 'archive' | 'unarchive' | 'trash' | 'restore') => this.request<Capture>('POST', `/captures/${encodeURIComponent(id)}/state`, { action });
  analyze = (id: string, data: { mode: 'local' | 'external'; reanalyze?: boolean; reference_time?: string; target_group_id?: string | null }) => this.mutation<Job>('POST', `/captures/${encodeURIComponent(id)}/analyze`, data);
  job = (id: string) => this.request<Job>('GET', `/jobs/${encodeURIComponent(id)}`);
  jobs = () => this.request<Job[]>('GET', '/jobs');
  proposal = (id: string) => this.request<Proposal>('GET', `/proposals/${encodeURIComponent(id)}`);
  editProposal = (p: Proposal, actions: ProposalAction[], questions: Question[]) => this.request<Proposal>('PATCH', `/proposals/${encodeURIComponent(p.id)}`, { proposal_revision: p.revision, actions, questions });
  async refreshProposalForPreview(p: Proposal, actions: ProposalAction[], questions: Question[], dirty: boolean): Promise<Proposal> {
    const latest = await this.proposal(p.id);
    if (latest.revision !== p.revision) throw new ApiError('PROPOSAL_REVISION_CONFLICT', '提案已被其他客户端修改。本机编辑尚未提交，请重新读取提案并核对。', 409);
    if (latest.status === 'applied' || latest.status === 'rejected') throw new ApiError('PROPOSAL_CLOSED', '提案已经结束，请重新读取最新结果。', 409);
    return dirty || latest.status === 'stale' || latest.status === 'failed' ? this.editProposal(latest, actions, questions) : latest;
  }
  previewProposal = (p: Proposal, ids: string[]) => this.request<{ previews: Preview[]; task_count: number; file_count: number }>('POST', `/proposals/${encodeURIComponent(p.id)}/preview`, { proposal_revision: p.revision, selected_action_ids: ids });
  confirmProposal = (p: Proposal, ids: string[]) => this.mutation<Change>('POST', `/proposals/${encodeURIComponent(p.id)}/confirm`, { proposal_revision: p.revision, selected_action_ids: ids });
  rejectProposal = (p: Proposal) => this.request<Proposal>('POST', `/proposals/${encodeURIComponent(p.id)}/reject`, { proposal_revision: p.revision });
  folders = () => this.request<Folder[]>('GET', '/folders');
  createFolder = (name: string, parent_id: string | null) => this.mutation<Change>('POST', '/folders', { name, parent_id });
  updateFolder = (id: string, data: { expected_revision: number; name?: string; parent_id?: string | null; archived?: boolean }) => this.mutation<Change>('PATCH', `/folders/${encodeURIComponent(id)}`, data);
  groups = (params?: Params) => this.request<Page<Group>>('GET', '/groups' + queryString(params));
  groupOptions = (params?: Params) => this.request<Page<GroupOption>>('GET', '/group-options' + queryString(params));
  group = (id: string) => this.request<Group>('GET', `/groups/${encodeURIComponent(id)}`);
  createGroup = (data: { title: string; folder_id?: string | null; file_name?: string; body?: string; tags?: string[]; tasks?: Partial<TaskDraft>[] }) => this.mutation<Change>('POST', '/groups', data);
  updateGroup = (id: string, data: Version & { title?: string; folder_id?: string | null; file_name?: string; body?: string; tags?: string[]; status?: Group['status'] }) => this.mutation<Change>('PATCH', `/groups/${encodeURIComponent(id)}`, data);
  addTask = (group: Group, task: TaskDraft) => this.mutation<Change>('POST', `/groups/${encodeURIComponent(group.id)}/tasks`, { ...versionOf(group), task });
  updateTask = (groupId: string, taskId: string, version: Version, patch: Partial<TaskDraft>) => this.mutation<Change>('PATCH', `/groups/${encodeURIComponent(groupId)}/tasks/${encodeURIComponent(taskId)}`, { ...version, patch });
  trashGroup = (g: Group) => this.mutation<Change>('POST', `/groups/${encodeURIComponent(g.id)}/trash`, versionOf(g));
  restoreGroup = (g: Group) => this.mutation<Change>('POST', `/groups/${encodeURIComponent(g.id)}/restore`, versionOf(g));
  tasks = (params?: Params) => this.request<Page<TaskItem>>('GET', '/tasks' + queryString(params));
  search = (q: string, params?: Params) => this.request<Page<SearchHit>>('GET', '/search' + queryString({ q, ...params }));
  history = (group_id?: string) => this.request<Change[]>('GET', '/changes' + queryString({ group_id }));
  change = (id: string) => this.request<Change>('GET', `/changes/${encodeURIComponent(id)}`);
  undo = (id: string) => this.mutation<Change>('POST', `/changes/${encodeURIComponent(id)}/undo`);
  version = (groupId: string, hash: string) => this.request<VersionPreview>('GET', `/groups/${encodeURIComponent(groupId)}/versions/${encodeURIComponent(hash)}`);
  restoreVersion = (groupId: string, preview: VersionPreview) => this.mutation<Change>('POST', `/groups/${encodeURIComponent(groupId)}/restore-version`, { expected_revision: preview.expected_revision, expected_hash: preview.expected_hash, content_hash: preview.content_hash });
  rebuildIndex = () => this.mutation<Job>('POST', '/index/rebuild');
}
