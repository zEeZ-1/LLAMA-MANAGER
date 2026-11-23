import {
  ArrowRight,
  ArrowUpRight,
  Boxes,
  Cpu,
  Layers,
  MessageSquare,
  Network,
  Plus,
  Server,
} from "lucide-react";
import { bytes } from "../services/api";
import { useUI } from "../services/ui";
import { BrandMark } from "../components/BrandMark";
import { MemoryBar } from "../components/ClusterPanel";
import { Status } from "../components/Status";
import type { Node, Model, Instance } from "../types";

export function Overview({
  nodes,
  models,
  instances,
  loading,
}: {
  nodes: Node[];
  models: Model[];
  instances: Instance[];
  loading: boolean;
}) {
  const { navigate, openCreate, openChat } = useUI();
  const controller = nodes.find((n) => n.role === "controller");
  const online = nodes.filter((n) => n.status === "online");
  const ready = instances.filter((i) => i.status === "ready");
  const failed = instances.filter((i) => i.status === "failed");
  const starting = instances.filter(
    (i) => i.status === "starting" || i.status === "created",
  );
  const knownMemory = online.filter(
    (n) => !n.telemetry_stale && n.memory_info.total != null,
  );
  const totalMemory = knownMemory.reduce(
    (sum, n) => sum + n.memory_info.total!,
    0,
  );
  return (
    <>
      <div className="overview-stats">
        {[
          {
            icon: Network,
            label: "在线节点",
            value: loading ? "—" : `${online.length}`,
            detail: `${nodes.length} 个已接入节点`,
            target: "nodes" as const,
          },
          {
            icon: Cpu,
            label: "系统内存总量",
            value: knownMemory.length ? bytes(totalMemory) : "—",
            detail: `${knownMemory.length} 个在线节点 · 共享内存仅计一次`,
            target: "nodes" as const,
          },
          {
            icon: Boxes,
            label: "模型目录",
            value: loading ? "—" : `${models.length}`,
            detail: `${models.filter((m) => m.kind === "image_text").length} 个图文模型`,
            target: "models" as const,
          },
          {
            icon: Layers,
            label: "就绪实例",
            value: loading ? "—" : `${ready.length}`,
            detail: `${starting.length} 个启动中 · ${failed.length} 个失败`,
            target: "instances" as const,
          },
        ].map((s) => (
          <button
            className="overview-stat"
            key={s.label}
            onClick={() => navigate(s.target)}
          >
            <div className="stat-top">
              <s.icon size={16} />
              <span>{s.label}</span>
              <ArrowUpRight size={14} />
            </div>
            <strong>{s.value}</strong>
            <small>{s.detail}</small>
          </button>
        ))}
      </div>
      <div className="overview-grid">
        <section className="topology-panel panel">
          <div className="section-heading">
            <div>
              <span className="eyebrow">YOUR NETWORK</span>
              <h2>算力，由你连接。</h2>
            </div>
            <button className="text-button" onClick={() => navigate("nodes")}>
              管理节点 <ArrowUpRight size={15} />
            </button>
          </div>
          <p className="muted topology-description">
            一台控制主机，连接你的计算资源。
          </p>
          <div className="topology" aria-label="SSH 集中管理拓扑">
            <div className="topology-controller">
              <div className="topology-brand">
                <BrandMark size={35} />
              </div>
              <div>
                <span className="node-role">控制节点</span>
                <strong>{controller?.name || "等待控制节点"}</strong>
                <small>
                  {controller?.devices[0]?.name ||
                    controller?.architecture ||
                    "正在读取配置"}
                </small>
              </div>
              <span
                className={`badge ${controller?.status === "online" ? "ready" : "stopped"}`}
              >
                <span
                  className={`dot ${controller?.status === "online" ? "ready" : "stopped"}`}
                />
                {controller?.status === "online" ? "在线" : "待连接"}
              </span>
            </div>
            <div className="topology-branches">
              {nodes
                .filter((n) => n.role !== "controller")
                .map((n) => (
                  <button
                    className={`topology-worker ${n.status === "online" ? "online" : ""}`}
                    key={n.id}
                    onClick={() => navigate("nodes")}
                  >
                    <span className="topology-connector" aria-hidden="true" />
                    <div className="topology-worker-head">
                      <Server size={18} />
                      <span
                        className={`dot ${n.status === "online" ? "ready" : "stopped"}`}
                      />
                    </div>
                    <strong>{n.name}</strong>
                    <small className="mono">{n.address || n.hostname}</small>
                    <span className="topology-worker-spec">
                      {n.devices[0]?.name || n.architecture}
                    </span>
                    <span className="topology-worker-memory">
                      {bytes(n.memory_info.total)}
                      <span>
                        {n.telemetry_stale
                          ? "数据已过期"
                          : n.status === "online"
                            ? "在线"
                            : "离线"}
                      </span>
                    </span>
                  </button>
                ))}
              {!nodes.some((n) => n.role !== "controller") && (
                <button
                  className="topology-add"
                  onClick={() => navigate("nodes")}
                >
                  <Plus size={22} />
                  <strong>接入更多算力</strong>
                  <small>添加 IP，通过 SSH 管理远端 RPC</small>
                </button>
              )}
            </div>
          </div>
          <div className="topology-legend">
            <span>
              <i className="dot ready" />
              在线节点
            </span>
            <span className="connection-legend" />
            SSH 管理连接{" "}
            <span className="topology-not-live">非实时推理流量图</span>
          </div>
        </section>
        <section className="overview-resources panel">
          <div className="section-heading">
            <div>
              <span className="eyebrow">MEMORY SNAPSHOT</span>
              <h2>节点内存</h2>
            </div>
            <Cpu size={18} className="muted" />
          </div>
          <div className="memory-snapshots">
            {nodes.map((n) => (
              <div className="memory-snapshot" key={n.id}>
                <div>
                  <strong>{n.name}</strong>
                  <span>
                    {n.telemetry_stale
                      ? "数据已过期"
                      : n.status === "online"
                        ? "在线"
                        : "离线"}
                  </span>
                </div>
                <MemoryBar node={n} />
                <small>
                  可用 {bytes(n.memory_info.available)}
                  <span>/ {bytes(n.memory_info.total)}</span>
                </small>
              </div>
            ))}
            {!nodes.length && <p className="muted">等待节点信息</p>}
          </div>
          <div className="manager-rss">
            <span>管理后端 RSS</span>
            <strong className="mono">
              {bytes(controller?.manager_memory_bytes)}
            </strong>
          </div>
          <p className="muted memory-note">
            RSS 为管理后端进程采样值，不含推理进程。
          </p>
        </section>
      </div>
      <section className="overview-workbench">
        <div className="section-heading">
          <div>
            <span className="eyebrow">INFERENCE WORKSPACE</span>
            <h2>让模型开始工作</h2>
          </div>
          <button className="text-button" onClick={() => navigate("instances")}>
            全部实例 <ArrowRight size={15} />
          </button>
        </div>
        {failed.length > 0 && (
          <button
            className="failure-notice"
            onClick={() => navigate("instances")}
          >
            <span className="dot failed" />
            {failed.length} 个实例启动失败，查看原因与日志
            <ArrowUpRight size={15} />
          </button>
        )}
        <div className="workbench-grid">
          {ready.map((i) => (
            <div className="workbench-card panel" key={i.id}>
              <div className="section-heading">
                <span className="workbench-icon">
                  <Layers size={20} />
                </span>
                <Status instance={i} />
              </div>
              <h3>{i.name}</h3>
              <p className="muted">{i.model_name}</p>
              <div className="workbench-meta">
                <span>
                  {i.capabilities?.image_input ? "图文对话" : "文本对话"}
                </span>
                <span className="mono">
                  CTX {i.config.context_size.toLocaleString()}
                </span>
              </div>
              <button onClick={() => openChat(i.id)}>
                <MessageSquare size={15} />
                开始对话
                <ArrowUpRight size={15} />
              </button>
            </div>
          ))}
          <button className="new-workbench" onClick={() => openCreate()}>
            <span className="new-workbench-icon">
              <Plus size={24} />
            </span>
            <strong>
              {ready.length ? "创建新实例" : "开启你的第一个实例"}
            </strong>
            <span>选择模型与设备，开始本地推理。</span>
            <span className="accent">
              创建实例 <ArrowRight size={15} />
            </span>
          </button>
        </div>
      </section>
    </>
  );
}
