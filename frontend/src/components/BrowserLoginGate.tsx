import { type FormEvent, type ReactNode, useEffect, useState } from "react";
import { api, clearProjectSessionExpiry, loginRealSession, setProjectSessionExpiresIn, setRealSessionToken, setRealWriteToken, type RealIdentity } from "../api";

export const PROJECT_AUTH_REQUIRED_EVENT = "project-auth-required";

/** Mount business components only after the server has verified the project session. */
export default function BrowserLoginGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<"checking" | "login" | "ready">("checking");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
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
      <section className="panel browser-login-panel" aria-label="项目登录">
        <h1>汽车零部件制造运营管理智能体平台</h1>
        <p>请使用项目账号登录后查询业务、使用问答或查看历史记录。</p>
        {state === "checking" ? <p role="status">正在验证登录状态…</p> : (
          <form onSubmit={(event) => void submit(event)}>
            <label>用户名<input autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} required disabled={busy} /></label>
            <label>密码<input type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} required disabled={busy} /></label>
            {error && <p className="text-red" role="alert">{error}</p>}
            <button className="button primary" type="submit" disabled={busy || !username.trim() || !password}>{busy ? "登录中…" : "登录"}</button>
          </form>
        )}
      </section>
    </main>
  );
}
