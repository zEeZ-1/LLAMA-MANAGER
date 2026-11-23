import { ArrowUpRight, Cpu, Network, X } from "lucide-react";
import { bytes, useNodes } from "../services/api";
import { useUI } from "../services/ui";
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
export function ClusterPanel({ onClose }: { onClose: () => void }) {
  const nodes = useNodes(false);
  const navigate = useUI((s) => s.navigate);
  return (
    <aside className="cluster-panel" aria-label="计算资源">
      <div className="section-heading">
        <span className="eyebrow">COMPUTE</span>
        <button className="icon" aria-label="关闭计算资源" onClick={onClose}>
          <X size={16} />
        </button>
      </div>
      <h2>计算资源</h2>
      <p className="muted cluster-caption">节点状态 · 每 5 秒更新</p>
      {nodes.isError && (
        <p className="error" role="alert">
          暂时无法读取节点
        </p>
      )}
      {nodes.isPending && <p className="muted">正在读取节点…</p>}
      <div className="cluster-node-list">
        {nodes.data?.nodes.map((n) => (
          <div className="cluster-node" key={n.id}>
            <div className="cluster-node-title">
              <Cpu size={15} />
              <strong title={n.name}>{n.name}</strong>
              <span
                className={`dot ${n.status === "online" ? "ready" : "stopped"}`}
                title={n.status === "online" ? "在线" : "离线"}
              />
            </div>
            <span className="node-role">
              {n.role === "controller" ? "控制节点" : "RPC 计算节点"}
              {n.telemetry_stale ? " · 数据已过期" : ""}
            </span>
            <div className="cluster-memory">
              <span>系统内存</span>
              <span>
                {bytes(n.memory_info.used)} / {bytes(n.memory_info.total)}
              </span>
            </div>
            <MemoryBar node={n} />
            <p className="muted">
              {n.devices[0]?.name || n.architecture}
              {n.devices[0]?.memory_kind === "shared" ? " · 共享内存" : ""}
            </p>
            {n.role === "rpc_worker" && (
              <div className="cluster-rpc">
                <Network size={12} />
                {n.rpc_workers?.length ?? 0} 个 RPC ·{" "}
                {n.rpc_workers?.filter((w) =>
                  ["ready", "busy"].includes(w.state),
                ).length ?? 0}{" "}
                个就绪 / 使用中
              </div>
            )}
          </div>
        ))}
      </div>
      <button className="cluster-manage" onClick={() => navigate("nodes")}>
        管理计算节点 <ArrowUpRight size={15} />
      </button>
      <div className="cluster-footnote">
        集中管理，按需分配。
        <br />
        远端仅运行 RPC 计算服务。
      </div>
    </aside>
  );
}
