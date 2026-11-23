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
} from "lucide-react";
import { useUI } from "./services/ui";
import { DialogAccessibility } from "./components/DialogAccessibility";
import { BrandMark } from "./components/BrandMark";
import { useInstances, useNodes } from "./services/api";
import { Management } from "./pages/Management";
import { CreateInstance } from "./components/CreateInstance";
import "./style.css";
import "./ascnet.css";
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
  const nodes = useNodes();
  const online =
    nodes.data?.nodes.filter((n) => n.status === "online").length ?? 0;
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
      <DialogAccessibility />
      <header className="app-header">
        <button
          className="ascnet-brand"
          aria-label="AscNet 首页"
          onClick={() => navigate("dashboard")}
        >
          <BrandMark size={32} />
          <span>AscNet</span>
          <span className="version-tag">v3.1</span>
        </button>
        <nav className="app-nav" aria-label="主导航">
          {nav.map(([key, Icon, label]) => (
            <button
              key={key}
              aria-label={label}
              aria-current={page === key ? "page" : undefined}
              className={page === key ? "active" : ""}
              onClick={() => navigate(key)}
            >
              <Icon size={17} />
              <span>{label}</span>
              {key === "instances" && ready > 0 && (
                <span className="nav-count" aria-hidden="true">
                  {ready}
                </span>
              )}
            </button>
          ))}
        </nav>
        <div className="header-actions">
          <span className="workspace-connection">
            <span
              className={`dot ${instances.isError || nodes.isError ? "failed" : nodes.isPending ? "starting" : "ready"}`}
            />
            {instances.isError || nodes.isError
              ? "连接异常"
              : nodes.isPending
                ? "连接中"
                : `${online} 节点在线`}
          </span>
          <button
            className="primary small"
            aria-label="创建实例"
            onClick={() => openCreate()}
          >
            <Plus size={16} />
            <span>创建实例</span>
          </button>
        </div>
      </header>
      <main className={page === "chat" ? "main chat-main" : "main"}>
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
