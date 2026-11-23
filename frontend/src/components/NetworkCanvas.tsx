import { ArrowUpRight, Cpu, Network, Plus, Server } from "lucide-react";
import { BrandMark } from "./BrandMark";
import { bytes } from "../services/api";
import type { Node } from "../types";

export function MemoryBar({ node }: { node: Node }) {
  const value = node.memory_info.usage_percent;
  return (
    <div
      className={`memory-bar ${node.telemetry_stale ? "stale" : ""}`}
      aria-label={`${node.name} 内存使用率 ${value == null ? "未提供" : `${value.toFixed(1)}%`}`}
    >
      <i style={{ width: `${Math.min(100, Math.max(0, value ?? 0))}%` }} />
    </div>
  );
}
export function NetworkCanvas({
  nodes,
  onSelect,
  onAdd,
}: {
  nodes: Node[];
  onSelect: (id: string) => void;
  onAdd: () => void;
}) {
  const online = nodes.filter((n) => n.status === "online");
  const known = online.filter(
    (n) => !n.telemetry_stale && n.memory_info.total != null,
  );
  const total = known.reduce((sum, n) => sum + n.memory_info.total!, 0);
  const controller = nodes.find((n) => n.role === "controller");
  const workers = nodes.filter((n) => n.role !== "controller");
  function card(node: Node, control = false) {
    return (
      <button
        className={`network-node ${control ? "control" : ""} ${node.status === "online" ? "online" : ""}`}
        onClick={() => onSelect(node.id)}
        aria-label={`查看节点 ${node.name}`}
        key={node.id}
      >
        <div className="network-node-top">
          <span className="network-node-icon">
            {control ? <BrandMark size={27} /> : <Server size={23} />}
          </span>
          <span className="node-role">
            {control ? "控制节点" : "RPC 计算节点"}
          </span>
          <span
            className={`node-online ${node.status === "online" ? "online" : ""}`}
          >
            <i
              className={`dot ${node.status === "online" ? "ready" : "stopped"}`}
            />
            {node.status === "online" ? "在线" : "离线"}
          </span>
        </div>
        <h3>{node.name}</h3>
        <span className="network-node-address mono">
          {node.address || node.hostname}
        </span>
        <p className="network-node-hardware">
          <Cpu size={12} />
          {node.devices[0]?.name || node.architecture}
          {node.devices[0]?.memory_kind === "shared" ? " · 共享内存" : ""}
        </p>
        <div className="network-node-memory">
          <span>系统内存</span>
          <strong>{bytes(node.memory_info.total)}</strong>
        </div>
        <MemoryBar node={node} />
        <div className="network-node-available">
          <span>可用 {bytes(node.memory_info.available)}</span>
          <span>
            {node.telemetry_stale
              ? "数据已过期"
              : node.memory_info.usage_percent == null
                ? "使用率未提供"
                : `${node.memory_info.usage_percent.toFixed(0)}% 已使用`}
          </span>
        </div>
        <div className="network-node-bottom">
          <span>
            {control
              ? `${node.ready_count} 个就绪实例`
              : `${node.rpc_workers?.length ?? 0} 个 RPC · ${node.rpc_workers?.filter((w) => w.state === "ready").length ?? 0} 个就绪`}
          </span>
          <ArrowUpRight size={14} />
        </div>
      </button>
    );
  }
  return (
    <div className="network-stage">
      <div className="network-summary">
        <span>
          <i className="dot ready" />
          {online.length} / {nodes.length} 节点在线
        </span>
        <span>
          <Network size={13} />
          {known.length ? bytes(total) : "未提供"} 系统内存
        </span>
      </div>
      <p className="network-summary-note">
        {known.length} 个在线节点 · 共享内存仅计一次
      </p>
      <div className="network-diagram" aria-label="SSH 集中管理拓扑">
        <div className="network-control">
          {controller ? (
            card(controller, true)
          ) : (
            <div className="network-pending">等待控制节点信息…</div>
          )}
        </div>
        <div className="network-wire" aria-hidden="true">
          <span>SSH / RPC</span>
        </div>
        <div className="network-workers">
          {workers.map((n) => card(n))}
          <button className="network-add" onClick={onAdd}>
            <span>
              <Plus size={23} />
            </span>
            <strong>添加计算节点</strong>
            <small>输入 IP，通过 SSH 接入远端算力</small>
          </button>
        </div>
      </div>
      <div className="network-notes">
        <span>
          <i className="dot ready" />
          节点状态
        </span>
        <span>
          <i className="line-key" />
          SSH 管理关系 · 非实时推理流量图
        </span>
      </div>
      <div className="network-manager-memory">
        <span>管理后端 RSS</span>
        <strong className="mono">
          {bytes(controller?.manager_memory_bytes)}
        </strong>
        <small>仅管理后端进程，不含推理进程</small>
      </div>
    </div>
  );
}
