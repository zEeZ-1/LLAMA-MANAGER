import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Cpu,
  MemoryStick,
  Network,
  Boxes,
  Layers,
  Plus,
  RefreshCw,
  Search,
  MessageSquare,
  Trash2,
  X,
  Terminal,
  CircleCheck,
  HardDrive,
} from "lucide-react";
import {
  api,
  bytes,
  errorText,
  useInstances,
  useModels,
  useNodes,
} from "../services/api";
import { useUI } from "../services/ui";
import type { Instance } from "../types";

const statusNames = {
  created: "已创建",
  starting: "启动中",
  ready: "就绪",
  stopping: "停止中",
  stopped: "已停止",
  failed: "失败",
};
export function Status({ instance }: { instance: Instance }) {
  return (
    <span className={`badge ${instance.status}`}>
      <span className={`dot ${instance.status}`} />
      {statusNames[instance.status]}
    </span>
  );
}
function Meter({ value }: { value: number }) {
  return (
    <div className="meter">
      <i style={{ width: `${Math.min(100, Math.max(0, value))}%` }} />
    </div>
  );
}

export function Management() {
  const { page, navigate, openCreate, openChat } = useUI();
  const nodes = useNodes(),
    models = useModels(),
    instances = useInstances();
  const [search, setSearch] = useState("");
  const [detail, setDetail] = useState<string | null>(null);
  const [nodeExpanded, setNodeExpanded] = useState(false);
  const client = useQueryClient();
  const refresh = useMutation({
    mutationFn: () => api("/models/refresh", { method: "POST" }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["models"] }),
  });
  const destroy = useMutation({
    mutationFn: (id: string) => api(`/instances/${id}`, { method: "DELETE" }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["instances"] }),
  });
  const logs = useQuery({
    queryKey: ["logs", detail],
    enabled: !!detail,
    queryFn: () => api<{ text: string }>(`/instances/${detail}/logs`),
    refetchInterval: detail ? 3000 : false,
  });
  const node = nodes.data?.nodes[0];
  const records = instances.data?.instances || [];
  const files = models.data?.models || [];
  const ready = records.filter((i) => i.status === "ready");
  const activeDetail = records.find((i) => i.id === detail);
  const error =
    (page === "models"
      ? models.error
      : page === "instances"
        ? instances.error
        : nodes.error || instances.error || models.error) ||
    destroy.error ||
    refresh.error;
  const loading =
    page === "models"
      ? models.isPending
      : page === "instances"
        ? instances.isPending
        : nodes.isPending;
  const titles = {
    dashboard: ["工作空间总览", "在一个地方管理模型、计算资源与推理实例。"],
    nodes: ["计算节点", "查看本机设备与可用资源。"],
    models: ["模型目录", "自动发现本地 GGUF 模型，无需手动登记。"],
    instances: ["推理实例", "为每个任务创建独立的推理环境。"],
    chat: ["", ""],
  };
  return (
    <div className="content">
      <div className="page-heading">
        <div>
          <span className="eyebrow">
            LOCAL INFERENCE / {page.toUpperCase()}
          </span>
          <h1>{titles[page][0]}</h1>
          <p className="muted">{titles[page][1]}</p>
        </div>
        <span className="workspace-tag">V1 · LOCAL</span>
      </div>
      {error && (
        <div className="error" role="alert">
          {errorText(error)}{" "}
          <button className="small" onClick={() => client.invalidateQueries()}>
            重试
          </button>
        </div>
      )}
      {loading && <div className="notice">正在读取工作空间…</div>}
      {page === "dashboard" && (
        <>
          <div className="stats-grid">
            {[
              [
                Network,
                "在线节点",
                nodes.data?.nodes.length || 0,
                "本地计算资源",
              ],
              [Boxes, "已发现模型", files.length, "GGUF 文件"],
              [Layers, "全部实例", records.length, "独立推理进程"],
              [CircleCheck, "就绪实例", ready.length, "可以开始对话"],
            ].map(([Icon, label, value, caption], index) => {
              const I = Icon as typeof Cpu;
              return (
                <div className="stat" key={index}>
                  <div className="stat-label">
                    <span>{String(label)}</span>
                    <I size={18} />
                  </div>
                  <strong>{String(value).padStart(2, "0")}</strong>
                  <span className="muted">{String(caption)}</span>
                </div>
              );
            })}
          </div>
          <div className="section-heading">
            <h2>计算资源</h2>
            <button className="text-button" onClick={() => navigate("nodes")}>
              查看节点
            </button>
          </div>
          <div className="resource-grid">
            <div className="panel">
              <div className="resource-label">
                <Cpu size={18} /> CPU{" "}
                <span>{node?.cpu_info.usage_percent.toFixed(1) || "0"}%</span>
              </div>
              <Meter value={node?.cpu_info.usage_percent || 0} />
              <p className="muted">
                {node?.cpu_info.logical_count || "—"} 逻辑核心 ·{" "}
                {node?.architecture || "—"}
              </p>
            </div>
            <div className="panel">
              <div className="resource-label">
                <MemoryStick size={18} /> 内存{" "}
                <span>
                  {node?.memory_info.usage_percent.toFixed(1) || "0"}%
                </span>
              </div>
              <Meter value={node?.memory_info.usage_percent || 0} />
              <p className="muted">
                {bytes(node?.memory_info.used)} /{" "}
                {bytes(node?.memory_info.total)}
              </p>
            </div>
            <div className="panel">
              <div className="resource-label">
                <HardDrive size={18} /> 管理服务{" "}
                <span className="accent">
                  {bytes(node?.manager_memory_bytes)}
                </span>
              </div>
              <Meter
                value={((node?.manager_memory_bytes || 0) / 1024 ** 3) * 100}
              />
              <p className="muted">管理后端 RSS / 1 GiB 预算</p>
            </div>
          </div>
          <div className="section-heading">
            <h2>推理工作台</h2>
            <span className="muted">{ready.length} 个实例就绪</span>
          </div>
          {!ready.length ? (
            <div className="empty panel">
              <div className="empty-icon">
                <Layers />
              </div>
              <h3>从第一个实例开始</h3>
              <p>放入模型，创建实例，然后开始本地对话。</p>
              <button className="primary" onClick={() => openCreate()}>
                <Plus size={16} /> 创建实例
              </button>
            </div>
          ) : (
            <div className="instance-grid">
              {ready.map((i) => (
                <div className="panel" key={i.id}>
                  <div className="section-heading">
                    <h3>{i.name}</h3>
                    <Status instance={i} />
                  </div>
                  <p className="muted">{i.model_name}</p>
                  <button onClick={() => openChat(i.id)}>
                    <MessageSquare size={16} /> 开始对话
                  </button>
                </div>
              ))}
            </div>
          )}
          <div className="section-heading">
            <h2>GPU 设备</h2>
          </div>
          <div className="resource-grid">
            {node?.devices.length ? (
              node.devices.map((d) => (
                <div className="panel" key={d.id}>
                  <div className="resource-label">
                    <Cpu size={18} />
                    {d.name}
                    <span>{d.properties.utilization_percent ?? "—"}%</span>
                  </div>
                  <p className="muted">
                    可用 {bytes(d.memory_free)} / {bytes(d.memory_total)}
                  </p>
                </div>
              ))
            ) : (
              <div className="panel muted">
                未检测到独立 GPU 指标。统一内存设备请参考系统内存。
              </div>
            )}
          </div>
        </>
      )}
      {page === "nodes" && node && (
        <div className="node-card panel">
          <div className="section-heading">
            <div className="node-heading">
              <div className="device-icon">
                <Network size={30} />
              </div>
              <div>
                <h2>{node.name}</h2>
                <span className="muted mono">{node.hostname}</span>
              </div>
            </div>
            <span className="badge ready">
              <span className="dot ready" />
              在线
            </span>
          </div>
          <div className="node-specs">
            <div>
              <span>处理器</span>
              <strong>{node.cpu_info.name}</strong>
              <small>{node.cpu_info.logical_count} 逻辑核心</small>
            </div>
            <div>
              <span>系统内存</span>
              <strong>{bytes(node.memory_info.total)}</strong>
              <small>{bytes(node.memory_info.available)} 可用</small>
            </div>
            <div>
              <span>模型 / 实例</span>
              <strong>
                {node.model_count} / {node.instance_count}
              </strong>
              <small>{node.ready_count} 个实例就绪</small>
            </div>
          </div>
          <div className="node-gpus">
            {node.devices.map((d) => (
              <div key={d.id}>
                <Cpu size={18} />
                <span>{d.name}</span>
                <span className="muted">{bytes(d.memory_total)}</span>
              </div>
            ))}
          </div>
          {nodeExpanded && (
            <dl className="details">
              <dt>操作系统</dt>
              <dd>{node.os}</dd>
              <dt>架构</dt>
              <dd>{node.architecture}</dd>
              <dt>CPU 使用率</dt>
              <dd>{node.cpu_info.usage_percent}%</dd>
              <dt>内存使用率</dt>
              <dd>{node.memory_info.usage_percent}%</dd>
            </dl>
          )}
          <div className="card-footer">
            <button onClick={() => setNodeExpanded(!nodeExpanded)}>
              {nodeExpanded ? "收起详情" : "查看详情"}
            </button>
            <button className="primary" onClick={() => openCreate()}>
              <Plus size={16} /> 创建实例
            </button>
          </div>
        </div>
      )}
      {page === "models" && (
        <>
          <div className="toolbar">
            <div className="search">
              <Search size={17} />
              <input
                aria-label="搜索模型"
                placeholder="搜索模型名称、文件或架构…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
            <button
              disabled={refresh.isPending}
              onClick={() => refresh.mutate()}
            >
              <RefreshCw size={16} />{" "}
              {refresh.isPending ? "正在刷新…" : "刷新目录"}
            </button>
          </div>
          <div className="catalog-note">
            <span className="dot ready" />每 5 秒自动更新 · {files.length}{" "}
            个模型
            {models.data?.last_scan && (
              <span>
                最近扫描 {new Date(models.data.last_scan).toLocaleTimeString()}
              </span>
            )}
          </div>
          {!!models.data?.rejected_files && (
            <div className="notice">
              {models.data.rejected_files}{" "}
              个文件尚不可用：可能正在复制、格式不完整或使用了暂不支持的分片 /
              张量格式。
            </div>
          )}
          {files.length ? (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>模型</th>
                    <th>架构 / 格式</th>
                    <th>文件大小</th>
                    <th>节点</th>
                    <th>
                      <span className="sr-only">操作</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {files
                    .filter((m) =>
                      `${m.name} ${m.relative_path} ${m.architecture}`
                        .toLowerCase()
                        .includes(search.toLowerCase()),
                    )
                    .map((m) => (
                      <tr key={m.id}>
                        <td>
                          <div className="table-model">
                            <Boxes size={22} />
                            <div>
                              <strong>{m.name}</strong>
                              <small className="mono" title={m.relative_path}>
                                {m.relative_path}
                              </small>
                            </div>
                          </div>
                        </td>
                        <td>
                          {m.architecture || "未知"}
                          <small>GGUF</small>
                        </td>
                        <td className="mono">{bytes(m.size_bytes)}</td>
                        <td>{m.node_id}</td>
                        <td>
                          <button
                            className="small"
                            onClick={() => openCreate(m.id)}
                          >
                            <Plus size={15} /> 创建实例
                          </button>
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          ) : (
            !loading && (
              <div className="empty panel">
                <Boxes size={34} />
                <h3>等待发现模型</h3>
                <p>将 GGUF 文件放入 models/，复制完成后会自动出现在这里。</p>
              </div>
            )
          )}
        </>
      )}
      {page === "instances" && (
        <>
          <div className="section-heading">
            <span className="muted">
              {records.length} 个实例 · {ready.length} 个就绪
            </span>
            <span className="muted">每 2 秒更新</span>
          </div>
          {records.length ? (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>实例 / 模型</th>
                    <th>状态</th>
                    <th>运行信息</th>
                    <th>创建时间</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {records.map((i) => (
                    <tr key={i.id}>
                      <td>
                        <button
                          className="text-button instance-name"
                          onClick={() => setDetail(i.id)}
                        >
                          {i.name}
                        </button>
                        <small>{i.model_name}</small>
                        {i.error_message && (
                          <small className="error-text">
                            {i.error_message}
                          </small>
                        )}
                      </td>
                      <td>
                        <Status instance={i} />
                      </td>
                      <td>
                        <span className="mono">PID {i.runtime.pid || "—"}</span>
                        <small>
                          {i.node_id} · {i.runtime_type}
                        </small>
                      </td>
                      <td>
                        <small>{new Date(i.created_at).toLocaleString()}</small>
                      </td>
                      <td>
                        <div className="row-actions">
                          <button
                            className="small"
                            disabled={i.status !== "ready"}
                            onClick={() => openChat(i.id)}
                          >
                            <MessageSquare size={15} /> 对话
                          </button>
                          <button
                            className="icon"
                            aria-label={`查看 ${i.name} 详情和日志`}
                            onClick={() => setDetail(i.id)}
                          >
                            <Terminal size={17} />
                          </button>
                          <button
                            className="icon danger"
                            aria-label={`销毁 ${i.name}`}
                            disabled={
                              destroy.isPending ||
                              i.status === "stopped" ||
                              i.status === "stopping"
                            }
                            onClick={() => {
                              if (
                                window.confirm(
                                  `销毁实例「${i.name}」并停止推理进程？历史对话会保留。`,
                                )
                              )
                                destroy.mutate(i.id);
                            }}
                          >
                            <Trash2 size={17} />
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            !loading && (
              <div className="empty panel">
                <Layers size={34} />
                <h3>还没有推理实例</h3>
                <p>从已发现的模型创建你的第一个实例。</p>
                <button className="primary" onClick={() => openCreate()}>
                  <Plus size={16} /> 创建实例
                </button>
              </div>
            )
          )}
        </>
      )}
      {activeDetail && (
        <div className="modal-backdrop">
          <section
            className="modal wide"
            role="dialog"
            aria-modal="true"
            aria-labelledby="detail-title"
          >
            <div className="section-heading">
              <h2 id="detail-title">{activeDetail.name}</h2>
              <button
                className="icon"
                aria-label="关闭详情"
                onClick={() => setDetail(null)}
              >
                <X />
              </button>
            </div>
            <Status instance={activeDetail} />
            <dl className="details">
              <dt>Instance ID</dt>
              <dd className="mono">{activeDetail.id}</dd>
              <dt>模型</dt>
              <dd>{activeDetail.model_name}</dd>
              <dt>节点 / Runtime</dt>
              <dd>
                {activeDetail.node_id} / {activeDetail.runtime_type}
              </dd>
              <dt>PID</dt>
              <dd>{activeDetail.runtime.pid || "—"}</dd>
              <dt>Context / Parallel</dt>
              <dd>
                {activeDetail.config.context_size} /{" "}
                {activeDetail.config.parallel}
              </dd>
              <dt>GPU layers / Threads</dt>
              <dd>
                {activeDetail.config.n_gpu_layers} /{" "}
                {activeDetail.config.threads}
              </dd>
            </dl>
            {activeDetail.error_message && (
              <div className="error">{activeDetail.error_message}</div>
            )}
            <h3>
              运行日志 <span className="muted">最近 32 KiB</span>
            </h3>
            <pre className="log">
              {logs.error
                ? errorText(logs.error)
                : logs.data?.text || "读取中…"}
            </pre>
          </section>
        </div>
      )}
    </div>
  );
}
