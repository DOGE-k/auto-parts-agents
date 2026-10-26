import { type FormEvent, type ReactNode, useEffect, useState } from "react";
import { api, clearProjectSessionExpiry, loginRealSession, setProjectSessionExpiresIn, setRealSessionToken, setRealWriteToken, type RealIdentity } from "../api";

export const PROJECT_AUTH_REQUIRED_EVENT = "project-auth-required";

/** Mount business components only after the server has verified the project session. */
export default function BrowserLoginGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<"checking" | "login" | "ready">("checking");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    const requireLogin = () => {
      clearProjectSessionExpiry();
      setRealSessionToken("");
      setRealWriteToken("");
      // A new account must not reuse the previous account's selected chat.
      window.sessionStorage.removeItem("assistant_session_id");
      if (active) setState("login");
    };
    window.addEventListener(PROJECT_AUTH_REQUIRED_EVENT, requireLogin);
    void api<{ identity: RealIdentity; expires_in: number }>("/auth/me")
      .then((result) => {
        if (!active) return;
        if (result.identity?.provider !== "project") {
          requireLogin();
          return;
        }
        setProjectSessionExpiresIn(result.expires_in);
        setState("ready");
      })
      .catch(() => { if (active) setState("login"); });
    return () => {
      active = false;
      window.removeEventListener(PROJECT_AUTH_REQUIRED_EVENT, requireLogin);
    };
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || !username.trim() || !password) return;
    setBusy(true);
    setError("");
    try {
      // Clear stale advanced credentials; project login is the only browser identity.
      setRealSessionToken("");
      const result = await loginRealSession(username.trim(), password);
      setPassword("");
      window.sessionStorage.removeItem("assistant_session_id");
      setProjectSessionExpiresIn(result.expires_in ?? 0);
      setState("ready");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "登录失败，请稍后重试");
    } finally {
      setBusy(false);
    }
  }

  if (state === "ready") return <>{children}</>;
  return (
    <main className="browser-login-screen">
      <section className="login-brand" aria-label="平台介绍">
        <div className="login-brand-lockup">
          <span className="login-brand-mark" aria-hidden="true">
            <svg viewBox="0 0 32 32" fill="none"><path d="M10 9h12M10 23h12M9 10v12m14-12v12" stroke="currentColor" strokeWidth="1.6" /><rect x="4" y="4" width="10" height="10" rx="3" fill="currentColor" /><rect x="18" y="4" width="10" height="10" rx="3" fill="currentColor" opacity=".65" /><rect x="4" y="18" width="10" height="10" rx="3" fill="currentColor" opacity=".65" /><rect x="18" y="18" width="10" height="10" rx="3" fill="currentColor" /></svg>
          </span>
          <span>制造运营智能体平台</span>
        </div>
        <div className="login-brand-content">
          <p className="login-kicker">汽车零部件 · 多智能体协同</p>
          <h1>汽车零部件制造<br />运营管理智能体平台</h1>
          <p className="login-brand-description">从订单报价到采购、生产跟单与质量文档，<br className="login-desktop-break" />在同一工作台连接业务信息与处理过程。</p>
          <div className="login-capabilities">
            <div><span className="login-feature-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none"><path d="M4 7h16M4 12h10M4 17h13" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" /><circle cx="18" cy="12" r="2" stroke="currentColor" strokeWidth="1.7" /></svg></span><div><strong>四岗协同，业务串联</strong><p>报价、采购、跟单与质量文档分工协作</p></div></div>
            <div><span className="login-feature-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none"><path d="M5 5h14v14H5zM8 9h8M8 13h5" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" /></svg></span><div><strong>数据有来源，过程可追溯</strong><p>集中查看业务记录、调用过程与处理结果</p></div></div>
            <div><span className="login-feature-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none"><path d="m12 3 8 3v5c0 5-8 10-8 10S4 16 4 11V6l8-3Z" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" /><path d="m8.5 11.5 2.5 2.5 4.5-5" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" /></svg></span><div><strong>关键操作，由人确认</strong><p>保留审批环节，按业务权限执行写入</p></div></div>
          </div>
        </div>
        <p className="login-brand-footer">汽车零部件制造运营管理智能体平台</p>
      </section>
      <section className="login-form-panel" aria-label="项目登录">
        <div className="browser-login-panel">
          <div className="login-form-heading">
            <h2>欢迎回来</h2>
            <p>使用项目账号，继续你的制造协同工作。</p>
          </div>
          {state === "checking" ? <div className="login-checking" role="status"><span aria-hidden="true" />正在验证登录状态…</div> : (
            <form onSubmit={(event) => void submit(event)}>
              <label>用户名<span className="login-input-wrap"><svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle cx="12" cy="8" r="3.5" stroke="currentColor" strokeWidth="1.6" /><path d="M5 20v-2a7 7 0 0 1 14 0v2" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" /></svg><input autoComplete="username" placeholder="请输入用户名" value={username} onChange={(event) => setUsername(event.target.value)} required disabled={busy} /></span></label>
              <label>密码<span className="login-input-wrap"><svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><rect x="5" y="10" width="14" height="11" rx="2" stroke="currentColor" strokeWidth="1.6" /><path d="M8 10V7a4 4 0 0 1 8 0v3M12 14v3" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" /></svg><input type={showPassword ? "text" : "password"} autoComplete="current-password" placeholder="请输入密码" value={password} onChange={(event) => setPassword(event.target.value)} required disabled={busy} /><button className="login-password-toggle" type="button" aria-label={showPassword ? "隐藏密码" : "显示密码"} aria-pressed={showPassword} onClick={() => setShowPassword((shown) => !shown)} disabled={busy}><svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z" stroke="currentColor" strokeWidth="1.6" /><circle cx="12" cy="12" r="3" stroke="currentColor" strokeWidth="1.6" />{showPassword && <path d="m4 3 16 18" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />}</svg></button></span></label>
              {error && <p className="login-error" role="alert">{error}</p>}
              <button className="button primary login-submit" type="submit" disabled={busy || !username.trim() || !password}>{busy ? "正在登录…" : "登录工作台"}<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M5 12h14m-5-5 5 5-5 5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg></button>
              <p className="login-account-hint">账号由项目管理员分配，请联系管理员获取访问权限。</p>
            </form>
          )}
          <div className="login-privacy-note"><svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="m12 3 7 3v5c0 5-7 9-7 9s-7-4-7-9V6l7-3Z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" /><path d="m9 11 2 2 4-4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" /></svg><span>登录后仅显示当前账号的聊天历史</span></div>
        </div>
      </section>
    </main>
  );
}
