// 面板级错误边界（阶段十模拟容错）：单个面板渲染崩溃只降级该面板，不拖垮整页。
// React 类组件是 ErrorBoundary 的唯一实现方式；重试按钮重置内部状态重新挂载子树。
import { Component, type ErrorInfo, type ReactNode } from "react";

type Props = {
  /** 面板名称，用于降级文案（如"智能协同问答"）。 */
  name: string;
  children: ReactNode;
};

type State = {
  error: Error | null;
};

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // 控制台保留完整堆栈便于排查；UI 只显示降级文案，不让用户看到原始堆栈
    console.error(`[ErrorBoundary:${this.props.name}]`, error, info.componentStack);
  }

  private reset = () => {
    this.setState({ error: null });
  };

  render() {
    if (this.state.error) {
      return (
        <section className="panel error-boundary-panel">
          <div className="panel-heading">
            <div>
              <h2>{this.props.name} · 模块暂时不可用</h2>
              <p>该面板渲染出现异常，其他功能不受影响；可点击重试恢复。</p>
            </div>
            <span className="badge red-badge">已降级</span>
          </div>
          <div className="error-boundary-body">
            <code>{this.state.error.message}</code>
            <button className="button ghost" onClick={this.reset}>重试恢复该面板</button>
          </div>
        </section>
      );
    }
    return this.props.children;
  }
}
