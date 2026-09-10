import { describe, expect, it } from 'vitest';
import { ApiClient, ApiError, draftOf, emptyTask, formatTime, queryString, type Proposal, type Transport, type TransportRequest } from './index';

describe('shared API transport', () => {
  it('retains idempotency keys after an unknown network outcome', async () => {
    const requests: TransportRequest[] = [];
    const transport: Transport = async <T>(request: TransportRequest): Promise<T> => {
      requests.push(request);
      if (requests.length === 1) throw new ApiError('NETWORK_ERROR', 'offline');
      return { id: 'change-result' } as T;
    };
    const api = new ApiClient(transport, () => 'private-token');
    await expect(api.createGroup({ title: '合同' })).rejects.toThrow('offline');
    await api.createGroup({ title: '合同' });
    expect(requests[0].headers['Idempotency-Key']).toBe(requests[1].headers['Idempotency-Key']);
    expect(requests[1].headers.Authorization).toBe('Bearer private-token');
  });
  it('does not reuse another account token or send server-only task fields', async () => {
    let token = 'first';
    const requests: TransportRequest[] = [];
    const transport: Transport = async <T>(request: TransportRequest) => { requests.push(request); return {} as T; };
    const api = new ApiClient(transport, () => token);
    await api.me(); token = 'second'; await api.me();
    expect(requests[1].headers.Authorization).toBe('Bearer second');
    expect(draftOf({ ...emptyTask('任务'), ...{ id: 'server-task', is_today: true } })).not.toHaveProperty('id');
  });
  it('keeps dates and periods distinct and escapes query text', () => {
    expect(formatTime({ date: '2026-09-08', time: null, period: 'afternoon', timezone: 'Asia/Shanghai' })).toBe('2026-09-08 下午');
    expect(queryString({ q: '合同 & 方案', tag: null })).toBe('?q=%E5%90%88%E5%90%8C%20%26%20%E6%96%B9%E6%A1%88');
  });
});

function proposal(status: Proposal['status'] = 'pending_confirmation', revision = 3): Proposal {
  return { id: 'proposal-one', revision, status, capture_id: 'capture-one', created_at: 1,
    actions: [{ action_id: 'action-one', type: 'create', target_group_id: null, folder_id: null,
      group_title: '合同', file_name: null, tasks: [emptyTask('发送合同')], body_append: '', evidence: {}, field_confidence: {} }],
    previews: [], questions: [], classification: 'single_task', reference_time: '2026-09-08T00:00:00Z',
    timezone: 'Asia/Shanghai', provider: { provider: 'local-rules' }, candidates: [] };
}

describe('proposal preview refresh', () => {
  it.each(['stale', 'failed'] as const)('rebinds a server-side %s proposal even when the local copy is clean', async status => {
    const requests: TransportRequest[] = [], original = proposal();
    const api = new ApiClient(async <T>(request: TransportRequest) => {
      requests.push(request);
      return (request.method === 'GET' ? proposal(status) : proposal('pending_confirmation', 4)) as T;
    }, () => 'token');
    const latest = await api.refreshProposalForPreview(original, original.actions, original.questions, false);
    expect(requests.map(r => r.method)).toEqual(['GET', 'PATCH']);
    expect(requests[1].body).toEqual({ proposal_revision: 3, actions: original.actions, questions: [] });
    expect(latest.revision).toBe(4);
    expect(original.status).toBe('pending_confirmation');
  });

  it('does not create another revision for an unchanged pending proposal', async () => {
    const requests: TransportRequest[] = [], original = proposal();
    const api = new ApiClient(async <T>(request: TransportRequest) => { requests.push(request); return original as T; }, () => 'token');
    expect(await api.refreshProposalForPreview(original, original.actions, original.questions, false)).toEqual(original);
    expect(requests.map(r => r.method)).toEqual(['GET']);
  });

  it('saves local edits against the current revision', async () => {
    const requests: TransportRequest[] = [], original = proposal();
    const edited = [{ ...original.actions[0], group_title: '本机核对后的名称' }];
    const questions = [{ field_path: 'actions.0.tasks.0.owner', message: '核对负责人', required: true, resolved: true }];
    const api = new ApiClient(async <T>(request: TransportRequest) => {
      requests.push(request);
      return (request.method === 'GET' ? original : { ...original, revision: 4, actions: edited, questions }) as T;
    }, () => 'token');
    const latest = await api.refreshProposalForPreview(original, edited, questions, true);
    expect(requests[1].body).toEqual({ proposal_revision: 3, actions: edited, questions });
    expect(latest.actions[0].group_title).toBe('本机核对后的名称');
    expect(latest.questions[0].resolved).toBe(true);
  });

  it('rejects a concurrent revision without replacing or submitting local edits', async () => {
    const requests: TransportRequest[] = [], original = proposal();
    const edited = [{ ...original.actions[0], group_title: '尚未提交的修改' }], saved = structuredClone(edited);
    const api = new ApiClient(async <T>(request: TransportRequest) => { requests.push(request); return proposal('pending_confirmation', 4) as T; }, () => 'token');
    await expect(api.refreshProposalForPreview(original, edited, [], true)).rejects.toMatchObject({ code: 'PROPOSAL_REVISION_CONFLICT' });
    expect(requests.map(r => r.method)).toEqual(['GET']);
    expect(edited).toEqual(saved);
    expect(original.revision).toBe(3);
  });

  it.each(['applied', 'rejected'] as const)('does not edit a closed %s proposal', async status => {
    const requests: TransportRequest[] = [], original = proposal();
    const api = new ApiClient(async <T>(request: TransportRequest) => { requests.push(request); return proposal(status) as T; }, () => 'token');
    await expect(api.refreshProposalForPreview(original, original.actions, [], true)).rejects.toMatchObject({ code: 'PROPOSAL_CLOSED' });
    expect(requests.map(r => r.method)).toEqual(['GET']);
  });

  it('queries lightweight targets with search and an offset beyond the first 100', async () => {
    const requests: TransportRequest[] = [];
    const api = new ApiClient(async <T>(request: TransportRequest) => { requests.push(request); return { items: [], total: 0, offset: 100, limit: 20 } as T; }, () => 'token');
    await api.groupOptions({ q: '合同 & 附件', offset: 100, limit: 20 });
    expect(requests[0].path).toBe('/api/group-options?q=%E5%90%88%E5%90%8C%20%26%20%E9%99%84%E4%BB%B6&offset=100&limit=20');
  });
});
