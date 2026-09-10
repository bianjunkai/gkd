import React, { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { ApiClient, ApiError, type AuthResult, type Transport } from '@gkd/client';
import { AppShell } from './shell';
import { Brand, browserTransport, ErrorNote, Field, useOperation, useResource } from './ui';
import './styles.css';

const SESSION_KEY = 'guike.session.v1';
function readSession(): AuthResult | null {
  try { const value = JSON.parse(sessionStorage.getItem(SESSION_KEY) || 'null'); return value?.token && value?.user?.id && value?.workspace?.id ? value : null; } catch { return null; }
}
function App() {
  const [session, setSession] = useState<AuthResult | null>(readSession);
  const api = useMemo(() => {
    const transport: Transport = async <T,>(request: Parameters<Transport>[0]) => {
      try { return await browserTransport<T>(request); }
      catch (error) {
        if (error instanceof ApiError && error.code === 'SESSION_EXPIRED') { sessionStorage.removeItem(SESSION_KEY); setSession(null); }
        throw error;
      }
    };
    return new ApiClient(transport, () => session?.token || null);
  }, [session?.token]);
  function login(value: AuthResult) { sessionStorage.setItem(SESSION_KEY, JSON.stringify(value)); setSession(value); }
  function logout() { sessionStorage.removeItem(SESSION_KEY); setSession(null); }
  return session ? <AppShell key={session.user.id} auth={session} api={api} onLogout={logout} /> : <Login api={api} onLogin={login} />;
}

function Login({ api, onLogin }: { api: ApiClient; onLogin: (auth: AuthResult) => void }) {
  const [register, setRegister] = useState(false);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [name, setName] = useState('');
  const health = useResource(api.health, 'health');
  const operation = useOperation();
  return <main className="login-layout"><section className="login-story"><Brand /><div className="login-editorial"><span className="eyebrow">A LITTLE ORDER, EVERY DAY</span><h1>把想法留下，<br />让行动发生。</h1><p>随手记录，还原清晰的下一步。<br />你的任务，以 Markdown 留在你手中。</p><div className="flow-labels"><span>01 收集</span><i /><span>02 确认</span><i /><span>03 行动</span></div></div><p className="login-footnote">归刻 · 私有的 GTD 工作空间</p></section>
    <section className="login-form-wrap"><div className="login-form"><span className="eyebrow">YOUR PRIVATE WORKSPACE</span><h2>{register ? '建立你的工作空间' : '欢迎回来'}</h2><p className="muted">{register ? '从一条记录开始，不必先把一切想清楚。' : '继续整理想法，推进重要的事情。'}</p>
      <ErrorNote error={health.error} retry={health.reload} />
      {health.data && !health.data.password_auth ? <div className="info-note">当前服务使用微信登录，请在微信小程序中打开。此网页是本地开发工作台。</div> : <form onSubmit={e => { e.preventDefault(); void operation.run(async () => onLogin(register ? await api.register(username, password, name) : await api.login(username, password))); }}>
        {register && <Field label="如何称呼你"><input autoComplete="nickname" value={name} onChange={e => setName(e.target.value)} maxLength={60} required /></Field>}
        <Field label="账号"><input autoComplete="username" value={username} onChange={e => setUsername(e.target.value)} minLength={register ? 3 : 1} maxLength={80} required /></Field>
        <Field label="密码" hint={register ? '至少 8 位。请不要复用其他服务的密码。' : undefined}><input type="password" autoComplete={register ? 'new-password' : 'current-password'} value={password} onChange={e => setPassword(e.target.value)} minLength={register ? 8 : 1} maxLength={128} required /></Field>
        <ErrorNote error={operation.error} /><button className="primary wide" disabled={operation.busy} type="submit">{operation.busy ? '请稍候…' : register ? '创建工作空间' : '进入工作空间'}</button>
        <button className="text-button wide" type="button" onClick={() => { setRegister(!register); operation.clearError(); }}>{register ? '已有账号？去登录' : '第一次使用？创建本地账号'}</button>
      </form>}
      <div className="login-disclosure"><strong>本地开发模式</strong><p>内容保存在所连接服务的数据目录中。默认使用本地规则解析；只有你主动授权，才会向已配置的外部 AI 发送内容。</p></div>
    </div></section></main>;
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>);
