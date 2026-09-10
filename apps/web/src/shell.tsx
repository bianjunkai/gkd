import { useEffect, useState } from 'react';
import { type ApiClient, type AuthResult, type Change } from '@gkd/client';
import { Inbox, CaptureDetail } from './inbox';
import { ProposalEditor } from './proposal';
import { Today, WorkspacePanel, GroupDetail } from './workspace';
import { SearchPanel, SettingsPanel } from './settings';
import { Brand, Icon, UIContext, useOperation, useResource, type Nav, ErrorNote } from './ui';

const navigation: Array<{ id: Nav; label: string; english: string }> = [
  { id: 'inbox', label: '收集箱', english: 'Inbox' }, { id: 'today', label: '今日', english: 'Today' },
  { id: 'workspace', label: '工作空间', english: 'Workspace' }, { id: 'search', label: '搜索', english: 'Search' },
  { id: 'settings', label: '我的', english: 'Settings' },
];

export function AppShell({ auth, api, onLogout }: { auth: AuthResult; api: ApiClient; onLogout: () => void }) {
  const [nav, setNav] = useState<Nav>('inbox');
  const [epoch, setEpoch] = useState(0);
  const [modal, setModal] = useState<{ type: 'capture' | 'group' | 'proposal'; id: string } | null>(null);
  const [notice, setNotice] = useState<{ message: string; change?: Change } | null>(null);
  const [online, setOnline] = useState(navigator.onLine);
  const profile = useResource(api.me, epoch);
  const operation = useOperation();
  const refresh = () => setEpoch(v => v + 1);
  useEffect(() => {
    const on = () => setOnline(true), off = () => setOnline(false);
    window.addEventListener('online', on); window.addEventListener('offline', off);
    const focus = () => refresh(); window.addEventListener('focus', focus);
    return () => { window.removeEventListener('online', on); window.removeEventListener('offline', off); window.removeEventListener('focus', focus); };
  }, []);
  const current = navigation.find(item => item.id === nav)!;
  const value = { api, auth: { ...auth, workspace: profile.data?.workspace || auth.workspace }, profile: profile.data, epoch, refresh, notify: (message: string, change?: Change) => setNotice({ message, change }),
    openGroup: (id: string) => setModal({ type: 'group', id }), openCapture: (id: string) => setModal({ type: 'capture', id }),
    openProposal: (id: string, options?: { onlyIfIdle?: boolean }) => setModal(current => options?.onlyIfIdle && current ? current : { type: 'proposal', id }), go: setNav };
  return <UIContext.Provider value={value}><div className="app-layout"><aside className="sidebar"><Brand /><div className="space-caption">个人工作空间<span>PRIVATE</span></div><nav aria-label="主导航">{navigation.map(item => <button key={item.id} className={nav === item.id ? 'nav-item selected' : 'nav-item'} aria-current={nav === item.id ? 'page' : undefined} onClick={() => setNav(item.id)}><Icon name={item.id} /><span>{item.label}</span>{nav === item.id && <i className="nav-dot" />}</button>)}</nav><div className="sidebar-bottom"><div className="storage-note"><span className="small-square" />Markdown 驱动<br /><small>文件属于你，思路也是。</small></div><button className="profile-button" onClick={() => setNav('settings')}><span className="avatar">{auth.user.display_name.slice(0, 1)}</span><span>{auth.user.display_name}<small>我的私有空间</small></span><Icon name="arrow" size={16} /></button></div></aside>
    <div className="main-column"><header className="topbar"><span>{profile.data?.workspace.name || auth.workspace.name}<span className="breadcrumb-slash">/</span><strong>{current.label}</strong></span><span className="topbar-meta"><i />{profile.data?.workspace.ai_enabled ? '外部 AI 已授权' : '本地规则模式'}</span></header>
      {!online && <div className="offline-banner" role="status">当前离线。收集箱文字保留在本机，恢复连接后请再次提交。</div>}
      <main className="page-content" id="main-content"><div className="page-kicker"><span>{current.english.toUpperCase()}</span><span>{new Intl.DateTimeFormat('zh-CN', { month: 'long', day: 'numeric', weekday: 'long', timeZone: profile.data?.workspace.timezone || auth.workspace.timezone }).format(new Date())}</span></div>
        {nav === 'inbox' && <Inbox />}{nav === 'today' && <Today />}{nav === 'workspace' && <WorkspacePanel />}{nav === 'search' && <SearchPanel />}{nav === 'settings' && <SettingsPanel onLogout={onLogout} />}
        <footer className="page-footer"><span>归刻 · 把想法变成行动</span><span>LOCAL MVP / 0.1</span></footer>
      </main>
    </div></div>
    {notice && <div className="toast" role="status"><Icon name="check" /><span>{notice.message}</span>{notice.change && !notice.change.undone_by && <button disabled={operation.busy} onClick={() => void operation.run(async () => { await api.undo(notice.change!.id); refresh(); setNotice({ message: '已撤销，历史版本仍保留。' }); })}>撤销</button>}<button className="toast-close" aria-label="关闭提示" onClick={() => { setNotice(null); operation.clearError(); }}><Icon name="close" size={16} /></button><ErrorNote error={operation.error} /></div>}
    {modal?.type === 'capture' && <CaptureDetail key={modal.id} id={modal.id} onClose={() => setModal(null)} />}
    {modal?.type === 'proposal' && <ProposalEditor key={modal.id} id={modal.id} onClose={() => setModal(null)} />}
    {modal?.type === 'group' && <GroupDetail key={modal.id} id={modal.id} onClose={() => setModal(null)} />}
  </UIContext.Provider>;
}
