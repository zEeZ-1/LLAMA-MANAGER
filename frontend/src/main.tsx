import React, { Suspense, lazy } from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  LayoutDashboard,
  Network,
  Boxes,
  Layers,
  MessageSquare,
  Plus,
  Cpu,
  Github,
} from "lucide-react";
import { useUI } from "./services/ui";
import { useInstances } from "./services/api";
import { Management } from "./pages/Management";
import { CreateInstance } from "./components/CreateInstance";
import "./style.css";
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
  const { page, navigate, openCreate, createModel } = useUI();
  const instances = useInstances();
  const ready =
    instances.data?.instances.filter((i) => i.status === "ready").length ?? 0;
  const nav = [
    ["dashboard", LayoutDashboard, "总览"],
    ["nodes", Network, "节点"],
    ["models", Boxes, "模型"],
    ["instances", Layers, "实例"],
    ["chat", MessageSquare, "对话"],
  ] as const;
  return (
    <div className="shell">
      <aside className="sidebar">
        <a className="brand" href="/" aria-label="Llama 首页">
          <span className="brand-icon">
            <Cpu size={24} />
          </span>
          <span>
            llama<span className="brand-sub">INFERENCE MANAGER</span>
          </span>
        </a>
        <div className="nav-label">
          WORKSPACE <span>V2</span>
        </div>
        <nav>
          {nav.map(([key, Icon, label]) => (
            <button
              key={key}
              className={page === key ? "nav active" : "nav"}
              onClick={() => navigate(key)}
            >
              <Icon size={19} />
              {label}
              {key === "instances" && (
                <span className="nav-count">{ready}</span>
              )}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="local-status">
            <span className={`dot ${instances.isError ? "failed" : "ready"}`} />
            {instances.isError ? "管理服务未连接" : "Local workspace"}
          </div>
          <span className="muted">llama.cpp · 集中管理</span>
          <a
            href="https://github.com/ggml-org/llama.cpp"
            target="_blank"
            rel="noreferrer"
          >
            <Github size={15} /> llama.cpp
          </a>
        </div>
      </aside>
      <main className={page === "chat" ? "main chat-main" : "main"}>
        {page !== "chat" && (
          <header className="topbar">
            <span>
              工作空间 <span className="slash">/</span>{" "}
              {nav.find((n) => n[0] === page)?.[2]}
            </span>
            <button className="primary small" onClick={() => openCreate()}>
              <Plus size={16} /> 创建实例
            </button>
          </header>
        )}
        <ErrorBoundary>
          <Suspense fallback={<div className="empty">加载对话…</div>}>
            {page === "chat" ? <Chat /> : <Management />}
          </Suspense>
        </ErrorBoundary>
      </main>
      {createModel !== null && <CreateInstance />}
    </div>
  );
}
ReactDOM.createRoot(document.getElementById("root")!).render(
  <QueryClientProvider client={client}>
    <App />
  </QueryClientProvider>,
);
