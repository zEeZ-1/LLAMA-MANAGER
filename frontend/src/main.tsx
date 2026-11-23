import React, { Suspense, lazy, useEffect, useRef, useState } from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Layers, PanelLeft, X } from "lucide-react";
import { useUI } from "./services/ui";
import { DialogAccessibility } from "./components/DialogAccessibility";
import { BrandMark } from "./components/BrandMark";
import { ConversationList } from "./components/ConversationList";
import { InstancePanel } from "./components/InstancePanel";
import { useInstances, useModels, useNodes } from "./services/api";
import { Nodes } from "./pages/Nodes";
import "./style.css";
import "./ascnet.css";
import "./workspace.css";
const Chat = lazy(() => import("./pages/Chat"));
const client = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, staleTime: 1500, refetchOnWindowFocus: false },
  },
});
class ErrorBoundary extends React.Component<
  { children: React.ReactNode },
  { error: string }
> {
  state = { error: "" };
  static getDerivedStateFromError(error: Error) {
    return { error: error.message };
  }
  render() {
    return this.state.error ? (
      <div className="empty">
        <h2>页面加载失败</h2>
        <p>{this.state.error}</p>
        <button onClick={() => location.reload()}>重新加载</button>
      </div>
    ) : (
      this.props.children
    );
  }
}
function App() {
  const center = useUI((s) => s.centerView);
  const [drawer, setDrawer] = useState<"history" | "instances" | null>(null);
  const nodes = useNodes(),
    instances = useInstances();
  useModels();
  const left = useRef<HTMLDivElement>(null),
    right = useRef<HTMLDivElement>(null);
  const close = () => setDrawer(null);
  useEffect(() => {
    const resize = () => setDrawer(null);
    window.addEventListener("resize", resize);
    return () => window.removeEventListener("resize", resize);
  }, []);
  useEffect(() => {
    if (!drawer) return;
    const previous = document.activeElement as HTMLElement | null;
    const panel = drawer === "history" ? left.current : right.current;
    const focusable = () =>
      Array.from(
        panel?.querySelectorAll<HTMLElement>(
          "button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), summary",
        ) || [],
      ).filter((el) => el.getClientRects().length > 0);
    focusable()[0]?.focus();
    const key = (event: KeyboardEvent) => {
      if (document.querySelector('[role="dialog"]')) return;
      if (event.key === "Escape") setDrawer(null);
      if (event.key === "Tab") {
        const list = focusable(),
          first = list[0],
          last = list[list.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last?.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first?.focus();
        }
      }
    };
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("keydown", key);
      previous?.focus();
    };
  }, [drawer]);
  const online =
    nodes.data?.nodes.filter((n) => n.status === "online").length ?? 0;
  return (
    <div className={`ascnet-workspace ${drawer ? `drawer-${drawer}` : ""}`}>
      <DialogAccessibility />
      <header className="mobile-workspace-bar">
        <button
          className="icon"
          aria-label="显示会话列表"
          aria-expanded={drawer === "history"}
          aria-controls="workspace-history"
          onClick={() => setDrawer("history")}
        >
          <PanelLeft size={19} />
        </button>
        <button
          className="mobile-brand"
          aria-label="AscNet 节点工作空间"
          onClick={() => {
            useUI.getState().showNodes();
            close();
          }}
        >
          <BrandMark size={24} />
          <strong>AscNet</strong>
          <span>v3.2</span>
        </button>
        <button
          className="icon"
          aria-label="显示实例管理"
          aria-expanded={drawer === "instances"}
          aria-controls="workspace-instances"
          onClick={() => setDrawer("instances")}
        >
          <Layers size={19} />
        </button>
      </header>
      {drawer && (
        <button
          className="workspace-backdrop"
          aria-label="关闭侧栏"
          tabIndex={-1}
          onClick={close}
        >
          <X />
        </button>
      )}
      <div id="workspace-history" className="workspace-history" ref={left}>
        <ErrorBoundary>
          <ConversationList onClose={close} />
        </ErrorBoundary>
      </div>
      <main className="workspace-center" inert={!!drawer}>
        <div className="center-nodes" hidden={center !== "nodes"}>
          <ErrorBoundary>
            <Nodes />
          </ErrorBoundary>
        </div>
        <div className="center-chat" hidden={center !== "chat"}>
          <ErrorBoundary>
            <Suspense fallback={<div className="empty">加载对话…</div>}>
              <Chat />
            </Suspense>
          </ErrorBoundary>
        </div>
        <div className="workspace-status">
          <span
            className={`dot ${nodes.isError || instances.isError ? "failed" : nodes.isPending ? "starting" : "ready"}`}
          />
          {nodes.isError || instances.isError
            ? "管理服务连接异常"
            : nodes.isPending
              ? "连接中…"
              : `${online} 个节点在线`}
          <span className="workspace-status-caption">你的模型 · 你的设备</span>
        </div>
      </main>
      <div id="workspace-instances" className="workspace-instances" ref={right}>
        <ErrorBoundary>
          <InstancePanel onClose={close} />
        </ErrorBoundary>
      </div>
    </div>
  );
}
ReactDOM.createRoot(document.getElementById("root")!).render(
  <QueryClientProvider client={client}>
    <App />
  </QueryClientProvider>,
);
