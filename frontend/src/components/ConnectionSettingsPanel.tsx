// 系统连接面板（信息架构改版 §六：会话设置从真实业务标题区迁移为独立模块）。
// 普通用户只看数据连接与项目登录；Bearer/写入令牌等调试信息收进「高级联调设置」。
// 状态与登录逻辑由 RealBusinessPage 持有（审批操作依赖同一 identity），本面板只做展示。
import { useEffect, useState } from "react";
import { formatSessionRemaining, getSessionRemainingMs } from "../api";
import type { RealIdentity } from "../api";

export type ConnState = "pending" | "connected" | "error";

type Props = {
  identity: RealIdentity | null;
  /** 真实探测的连接状态（与顶栏/侧栏同一状态源；评审意见③统一文案） */
  connErp: ConnState;
  connMes: ConnState;
  hasProjectSession: boolean;
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
  onLogout: () => void;
  onSessionTokenChange: (value: string) => void;
  onWriteTokenChange: (value: string) => void;
  onSaveTokens: () => void;
  onClearTokens: () => void;
};

export default function ConnectionSettingsPanel(props: Props) {
  const {
    identity,
    hasProjectSession,
    sessionExpiringSoon,
    onLogin,
    loginBusy,
    loginError,
  } = props;
  // 会话剩余时间每秒重算（交接文档 §6：临期必须显示具体剩余秒数；登录/登出
  // 状态变化时立即重算一次）。tick 只驱动重渲染，剩余时间在渲染时实时计算。
  const [, setRemainingTick] = useState(0);
  useEffect(() => {
    const recompute = () => setRemainingTick((t) => t + 1);
    recompute();
    if (!hasProjectSession) return;
    const timer = window.setInterval(recompute, 1000);
    return () => window.clearInterval(timer);
  }, [hasProjectSession]);

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
            <span className="source-tag erp">项目身份</span>
            <span>只读查询无需登录；审批、报工、处置等写入需要项目账号登录</span>
            <b className={props.hasProjectSession ? "text-green" : ""}>
              {props.hasProjectSession ? "已登录" : "未登录（需要项目账号）"}
            </b>
          </div>
        </div>
      </section>

      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2>项目登录</h2>
            <p>登录一次即可操作 ERPNext 与 OpenMES；审批、报工、NCR 处置等写入都使用该项目会话。</p>
          </div>
        </div>
        {hasProjectSession && identity ? (
          <div className="connection-account">
            <div className="connection-account-row">
              <span>当前项目用户</span><b>{identity.actor_id}</b>
            </div>
            <div className="connection-account-row">
              <span>显示名</span><b>{identity.display_name}</b>
            </div>
            <div className="connection-account-row">
              <span>会话状态</span>
              <b className={sessionExpiringSoon ? "text-red" : "text-green"}>
                {(() => {
                  const remaining = getSessionRemainingMs();
                  if (remaining === null) return "剩余时间未知（历史会话），请重新登录";
                  if (remaining <= 0) return "已过期，请重新登录";
                  const text = formatSessionRemaining(remaining);
                  return sessionExpiringSoon ? `即将过期，${text}，请准备重新登录` : `有效，${text}后过期`;
                })()}
              </b>
            </div>
            <div className="real-session-actions">
              <button className="button ghost" onClick={props.onLogout}>退出登录</button>
            </div>
          </div>
        ) : (
          <div className="real-session-login">
            {identity && (
              <p className="real-session-hint">
                ERPNext 数据连接账号：{identity.actor_id}（服务端集成账号，用于读取 ERP 数据，不是浏览器用户，不代表当前审批人）。
              </p>
            )}
            <strong>项目账号登录</strong>
            <div className="real-session-login-row">
              <input
                value={props.loginUsername}
                onChange={(e) => props.onLoginUsernameChange(e.target.value)}
                placeholder="项目用户名"
                autoComplete="username"
              />
              <input
                type="password"
                value={props.loginPassword}
                onChange={(e) => props.onLoginPasswordChange(e.target.value)}
                placeholder="项目密码"
                autoComplete="current-password"
              />
              <button className="button primary" disabled={loginBusy || !props.loginUsername.trim() || !props.loginPassword} onClick={onLogin}>
                {loginBusy ? "登录中…" : "登录"}
              </button>
            </div>
            <p className="real-session-hint">
              登录由本项目创建和管理；ERPNext 与 OpenMES 账号只作为后端服务连接，不需要在这里分别登录。
            </p>
            {loginError && <p className="real-session-error">登录失败：{loginError}（请确认项目用户名和密码）</p>}
          </div>
        )}
      </section>

      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2>高级联调设置</h2>
            <p>用于部署或开发联调；普通业务操作无需展开，也不需要填写。</p>
          </div>
        </div>
        <details className="advanced-settings">
          <summary>展开高级联调设置（Bearer 会话 / 本地写入令牌）</summary>
          <p>仅在当前浏览器会话内保存联调凭据，不写入项目配置或审计记录。本地写入令牌是可选的联调校验值，普通项目登录请留空；旧令牌不匹配时可能导致写入被拒绝。清除联调配置不会退出项目登录。</p>
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
            <button className="button ghost" onClick={props.onClearTokens}>清除联调配置</button>
          </div>
        </details>
      </section>
    </div>
  );
}
