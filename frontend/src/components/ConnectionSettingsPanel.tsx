// 系统连接面板（信息架构改版 §六：会话设置从真实业务标题区迁移为独立模块）。
// 普通用户只看数据连接与审批账号；Bearer/写入令牌等调试信息收进「高级联调设置」。
// 状态与登录逻辑由 RealBusinessPage 持有（审批操作依赖同一 identity），本面板只做展示。
import { useEffect, useState } from "react";
import { getSessionIssuedAt } from "../api";
import type { RealIdentity } from "../api";

const OPENMES_SESSION_TTL_MS = 15 * 60 * 1000;

export type ConnState = "pending" | "connected" | "error";

type Props = {
  identity: RealIdentity | null;
  /** 真实探测的连接状态（与顶栏/侧栏同一状态源；评审意见③统一文案） */
  connErp: ConnState;
  connMes: ConnState;
  hasOpenmesSession: boolean;
  sessionExpiringSoon: boolean;
  sessionToken: string;
  writeToken: string;
  loginUsername: string;
  loginPassword: string;
  loginBusy: boolean;
  loginError: string;
  onLoginUsernameChange: (value: string) => void;
  onLoginPasswordChange: (value: string) => void;
  onLogin: () => void;
  onSessionTokenChange: (value: string) => void;
  onWriteTokenChange: (value: string) => void;
  onSaveTokens: () => void;
  onClearTokens: () => void;
};

function sessionRemainingText(): string {
  const issued = getSessionIssuedAt();
  if (!issued) return "未知（历史会话，建议重新登录）";
  const remaining = Math.max(0, OPENMES_SESSION_TTL_MS - (Date.now() - issued));
  const minutes = Math.floor(remaining / 60000);
  const seconds = Math.floor((remaining % 60000) / 1000);
  return minutes > 0 ? `有效，约 ${minutes} 分 ${seconds} 秒后过期` : `即将过期（${seconds} 秒）`;
}

export default function ConnectionSettingsPanel(props: Props) {
  const {
    identity,
    hasOpenmesSession,
    sessionExpiringSoon,
    onLogin,
    loginBusy,
    loginError,
  } = props;
  // 会话剩余时间每 20 秒刷新一次（与 RealBusinessPage 的过期提醒同一口径）
  const [remaining, setRemaining] = useState(() => sessionRemainingText());
  useEffect(() => {
    if (!hasOpenmesSession) return;
    const timer = window.setInterval(() => setRemaining(sessionRemainingText()), 20000);
    return () => window.clearInterval(timer);
  }, [hasOpenmesSession]);

  return (
    <div className="connection-panel">
      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2>数据连接</h2>
            <p>业务数据来自本地部署的真实系统；AI 只读查询，写入需审批门禁。</p>
          </div>
          <span className="badge green">真实数据</span>
        </div>
        <div className="connection-rows">
          <div className="connection-row">
            <span className="source-tag erp">ERPNext</span>
            <span>客户 / 物料 / BOM / 价格 / 库存 · 只读 + 草稿写入</span>
            <b className={props.connErp === "connected" ? "text-green" : props.connErp === "error" ? "text-red" : ""}>
              {props.connErp === "connected" ? "已连接（身份已解析）" : props.connErp === "error" ? "连接异常" : "探测中…"}
            </b>
          </div>
          <div className="connection-row">
            <span className="source-tag mes">OpenMES</span>
            <span>工单 / 进度 / 质量 · 只读 + 审批执行</span>
            <b className={props.connMes === "connected" ? "text-green" : props.connMes === "error" ? "text-red" : ""}>
              {props.connMes === "connected" ? "已连接（工单数据可读）" : props.connMes === "error" ? "连接异常" : "探测中…"}
            </b>
          </div>
          <div className="connection-row">
            <span className="source-tag erp">审批账号</span>
            <span>只读查询无需登录；审批/报工/处置等写入门禁需要 OpenMES 登录会话</span>
            <b className={props.hasOpenmesSession ? "text-green" : ""}>
              {props.hasOpenmesSession ? "已登录" : "未登录（需要审批账号）"}
            </b>
          </div>
        </div>
      </section>

      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2>审批账号</h2>
            <p>只读查询无需登录；审批、报工、NCR 处置等写入门禁需要审批账号会话。</p>
          </div>
        </div>
        {hasOpenmesSession && identity ? (
          <div className="connection-account">
            <div className="connection-account-row">
              <span>当前审批人</span><b>{identity.display_name}</b>
            </div>
            <div className="connection-account-row">
              <span>来源</span><b>{identity.provider === "openmes" ? "OpenMES" : identity.provider}</b>
            </div>
            <div className="connection-account-row">
              <span>会话状态</span>
              <b className={sessionExpiringSoon ? "text-red" : "text-green"}>
                {sessionExpiringSoon ? "即将过期，请重新登录" : remaining}
              </b>
            </div>
            <div className="real-session-actions">
              <button className="button ghost" onClick={props.onClearTokens}>退出登录（清除本浏览器会话）</button>
            </div>
          </div>
        ) : (
          <div className="real-session-login">
            <strong>OpenMES 账号登录</strong>
            <div className="real-session-login-row">
              <input
                value={props.loginUsername}
                onChange={(e) => props.onLoginUsernameChange(e.target.value)}
                placeholder="OpenMES 用户名"
                autoComplete="username"
              />
              <input
                type="password"
                value={props.loginPassword}
                onChange={(e) => props.onLoginPasswordChange(e.target.value)}
                placeholder="OpenMES 密码"
                autoComplete="current-password"
              />
              <button className="button primary" disabled={loginBusy || !props.loginUsername.trim() || !props.loginPassword} onClick={onLogin}>
                {loginBusy ? "登录中…" : "登录"}
              </button>
            </div>
            <p className="real-session-hint">
              登录走真实 OpenMES 认证接口，返回默认 15 分钟 TTL 的短时会话；登出只清除本浏览器会话，不吊销上游令牌。
            </p>
            {loginError && <p className="real-session-error">登录失败：{loginError}（请确认 OpenMES 的用户名和密码）</p>}
          </div>
        )}
      </section>

      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2>高级联调设置</h2>
            <p>技术调试与安全门禁信息，普通操作无需展开。</p>
          </div>
        </div>
        <details className="advanced-settings">
          <summary>展开高级联调设置（Bearer 会话 / 本地写入令牌）</summary>
          <p>仅在当前浏览器会话内保存短期 Bearer 会话和本地写入门禁令牌，不写入项目配置或审计记录。</p>
          <label>
            Bearer 会话（可选）
            <input
              type="password"
              value={props.sessionToken}
              onChange={(e) => props.onSessionTokenChange(e.target.value)}
              placeholder="粘贴短期企业会话令牌"
              autoComplete="off"
            />
          </label>
          <label>
            本地写入令牌（可选）
            <input
              type="password"
              value={props.writeToken}
              onChange={(e) => props.onWriteTokenChange(e.target.value)}
              placeholder="服务端 REAL_WRITE_API_TOKEN"
              autoComplete="off"
            />
          </label>
          <div className="real-session-actions">
            <button className="button primary" onClick={props.onSaveTokens}>保存并重新解析身份</button>
            <button className="button ghost" onClick={props.onClearTokens}>清除会话</button>
          </div>
        </details>
      </section>
    </div>
  );
}
