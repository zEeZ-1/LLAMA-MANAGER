import { Nodes } from "./Nodes";
import { Overview } from "./Overview";
import { InstanceFailure } from "../components/InstanceFailure";
import { Status } from "../components/Status";
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Boxes,
  Layers,
  Plus,
  RefreshCw,
  Search,
  MessageSquare,
  Trash2,
  X,
  Terminal,
  ArrowUpRight,
  Image,
  FileText,
  Cpu,
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

export function Management() {
  const { page, openCreate, openChat } = useUI();
  const nodes = useNodes(false),
    models = useModels(),
    instances = useInstances(false);
  const [search, setSearch] = useState("");
  const [modelKind, setModelKind] = useState("all");
  const [instanceFilter, setInstanceFilter] = useState("all");
  const [detail, setDetail] = useState<string | null>(null);
  useEffect(() => {
    setDetail(null);
    if (page === "instances") setInstanceFilter("all");
  }, [page]);
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
    dashboard: [
      "你的本地 AI 工作空间",
      "连接设备，运行模型，让每一份算力触手可及。",
      "WORKSPACE OVERVIEW",
    ],
    nodes: [
      "连接你的计算资源",
      "控制本机与远端节点，在同一处管理 SSH 和 RPC。",
      "COMPUTE NETWORK",
    ],
    models: [
      "模型库",
      "文本与图像，各就其位。自动发现本机模型目录。",
      "MODEL LIBRARY",
    ],
    instances: [
      "推理实例",
      "从模型到服务，掌握每个实例的运行状态。",
      "INFERENCE INSTANCES",
    ],
    chat: ["", "", ""],
  };
  const matchingModels = files.filter(
    (m) =>
      (modelKind === "all" || m.kind === modelKind) &&
      `${m.name} ${m.relative_path} ${m.architecture}`
        .toLowerCase()
        .includes(search.toLowerCase()),
  );
  const matchingInstances = records.filter(
    (i) =>
      instanceFilter === "all" ||
      i.status === instanceFilter ||
      (instanceFilter === "starting" && i.status === "created"),
  );
  return (
    <div className={`content page-${page}`}>
      <div className="page-heading">
        <div>
          <span className="eyebrow">ASCNET / {titles[page][2]}</span>
          <h1>{titles[page][0]}</h1>
          <p className="muted">{titles[page][1]}</p>
        </div>
        <span className="workspace-tag">
          <span className="dot ready" />
          LOCAL FIRST
        </span>
      </div>
      {error && (
        <div className="error" role="alert">
          {errorText(error)}
          <button className="small" onClick={() => client.invalidateQueries()}>
            重试
          </button>
        </div>
      )}
      {loading && (
        <div className="notice" role="status">
          正在读取工作空间…
        </div>
      )}
      {page === "dashboard" && (
        <Overview
          nodes={nodes.data?.nodes || []}
          models={files}
          instances={records}
          loading={loading || instances.isPending || models.isPending}
        />
      )}
      {page === "nodes" && <Nodes />}
      {page === "models" && (
        <>
          <div className="library-toolbar">
            <div className="filter-tabs" aria-label="模型类型">
              {[
                ["all", "全部模型"],
                ["image_text", "图文模型"],
                ["text", "文本模型"],
              ].map(([key, label]) => (
                <button
                  key={key}
                  aria-pressed={modelKind === key}
                  className={modelKind === key ? "active" : ""}
                  onClick={() => setModelKind(key)}
                >
                  {label}
                  <span>
                    {key === "all"
                      ? files.length
                      : files.filter((m) => m.kind === key).length}
                  </span>
                </button>
              ))}
            </div>
            <div className="library-actions">
              <div className="search">
                <Search size={16} />
                <input
                  aria-label="搜索模型"
                  placeholder="搜索模型、文件或架构…"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
              </div>
              <button
                aria-label="刷新目录"
                disabled={refresh.isPending}
                onClick={() => refresh.mutate()}
              >
                <RefreshCw
                  size={16}
                  className={refresh.isPending ? "spin" : ""}
                />
                <span>刷新目录</span>
              </button>
            </div>
          </div>
          <div className="catalog-note">
            <span className="dot ready" />
            自动发现 · 每 5 秒更新
            {models.data?.last_scan && (
              <span>
                最近扫描 {new Date(models.data.last_scan).toLocaleTimeString()}
              </span>
            )}
          </div>
          {!!models.data?.rejected_files && (
            <div className="notice">
              {models.data.rejected_files}{" "}
              个模型包尚不可用，请检查包内资产完整性。
            </div>
          )}
          {models.data?.layout_issues?.map((issue) => (
            <div className="notice" key={issue.relative_path}>
              {issue.relative_path}：{issue.message}
            </div>
          ))}
          <div className="model-grid">
            {matchingModels.map((m) => (
              <article className="model-card panel" key={m.id}>
                <div className="model-card-top">
                  <span
                    className={`model-glyph ${m.kind === "image_text" ? "vision" : ""}`}
                  >
                    {m.kind === "image_text" ? (
                      <Image size={23} />
                    ) : (
                      <FileText size={23} />
                    )}
                  </span>
                  <span
                    className={`model-availability ${m.availability === "available" ? "" : "unavailable"}`}
                  >
                    <span
                      className={`dot ${m.availability === "available" ? "ready" : "stopped"}`}
                    />
                    {m.availability === "available" ? "可创建" : "暂不可用"}
                  </span>
                </div>
                <h2>{m.name}</h2>
                <p className="model-path mono" title={m.relative_path}>
                  {m.relative_path}
                </p>
                <div className="model-tags">
                  <span>
                    {m.kind === "image_text"
                      ? "图文"
                      : m.kind === "text"
                        ? "文本"
                        : "未识别类型"}
                  </span>
                  <span>{m.format.toUpperCase()}</span>
                  <span>{m.architecture || "未知架构"}</span>
                </div>
                <div className="model-specs">
                  <div>
                    <span>模型文件</span>
                    <strong className="mono">{bytes(m.size_bytes)}</strong>
                  </div>
                  <div>
                    <span>量化变体</span>
                    <strong className="mono">
                      {m.variants.length.toString().padStart(2, "0")}
                    </strong>
                  </div>
                  <div>
                    <span>投影文件</span>
                    <strong className="mono">
                      {m.projectors.length.toString().padStart(2, "0")}
                    </strong>
                  </div>
                </div>
                {m.detection.reasons.length > 0 && (
                  <details className="model-detection">
                    <summary>
                      识别信息
                      {m.availability !== "available"
                        ? " · 查看不可用原因"
                        : ""}
                    </summary>
                    {m.detection.reasons.map((reason) => (
                      <p key={reason}>{reason}</p>
                    ))}
                  </details>
                )}
                <div className="model-card-footer">
                  <span>
                    <Cpu size={13} />
                    {nodes.data?.nodes.find((n) => n.id === m.node_id)?.name ||
                      m.node_id}
                  </span>
                  <button
                    className="small"
                    disabled={m.availability !== "available"}
                    onClick={() => openCreate(m.id)}
                  >
                    <Plus size={15} />
                    创建实例
                  </button>
                </div>
              </article>
            ))}
          </div>
          {!matchingModels.length && !loading && (
            <div className="empty panel">
              <Boxes size={32} />
              <h3>{files.length ? "没有匹配的模型" : "为工作空间添加模型"}</h3>
              <p>
                {files.length
                  ? "试试其他名称、架构，或切换模型类型。"
                  : "在 models/ 中为每个模型建立独立文件夹，放入主权重与匹配的投影文件，AscNet 会自动发现。"}
              </p>
              {files.length > 0 && (
                <button
                  onClick={() => {
                    setSearch("");
                    setModelKind("all");
                  }}
                >
                  重置筛选
                </button>
              )}
            </div>
          )}
          <div className="library-footnote">
            <Boxes size={15} />
            <span>一个目录，一个模型包。支持独立变体与图文投影自动识别。</span>
          </div>
        </>
      )}
      {page === "instances" && (
        <>
          <div className="library-toolbar">
            <div className="filter-tabs" aria-label="实例状态">
              {[
                ["all", "全部"],
                ["ready", "就绪"],
                ["starting", "启动中"],
                ["failed", "失败"],
              ].map(([key, label]) => (
                <button
                  key={key}
                  aria-pressed={instanceFilter === key}
                  className={instanceFilter === key ? "active" : ""}
                  onClick={() => setInstanceFilter(key)}
                >
                  {label}
                  <span>
                    {key === "all"
                      ? records.length
                      : records.filter(
                          (i) =>
                            i.status === key ||
                            (key === "starting" && i.status === "created"),
                        ).length}
                  </span>
                </button>
              ))}
            </div>
            <span className="muted instance-update">
              {ready.length} 个就绪 · 每 2 秒更新
            </span>
          </div>
          <div className="instance-card-grid">
            {matchingInstances.map((i) => (
              <article className="inference-card panel" key={i.id}>
                <div className="inference-card-header">
                  <span className="workbench-icon">
                    <Layers size={21} />
                  </span>
                  <Status instance={i} />
                </div>
                <button
                  className="instance-title"
                  onClick={() => setDetail(i.id)}
                >
                  {i.name}
                  <ArrowUpRight size={17} />
                </button>
                <p className="muted instance-model-name">{i.model_name}</p>
                <div className="instance-config-summary">
                  <div>
                    <span>上下文</span>
                    <strong className="mono">
                      {i.config.context_size.toLocaleString()}
                    </strong>
                  </div>
                  <div>
                    <span>并发</span>
                    <strong className="mono">{i.config.parallel}</strong>
                  </div>
                  <div>
                    <span>GPU 层数</span>
                    <strong className="mono">{i.config.n_gpu_layers}</strong>
                  </div>
                  <div>
                    <span>输入类型</span>
                    <strong>
                      {i.capabilities?.image_input ? "图文" : "文本"}
                    </strong>
                  </div>
                </div>
                <div className="instance-placement">
                  <Cpu size={14} />
                  <span>
                    {i.runtime.placement?.device_names?.join(" · ") ||
                      nodes.data?.nodes.find((n) => n.id === i.node_id)?.name ||
                      i.node_id}
                  </span>
                </div>
                <InstanceFailure instance={i} compact />
                <div className="instance-created">
                  <span>{new Date(i.created_at).toLocaleString()}</span>
                  <span className="mono">PID {i.runtime.pid || "—"}</span>
                </div>
                <div className="instance-card-actions">
                  <button
                    className={i.status === "ready" ? "primary small" : "small"}
                    disabled={i.status !== "ready"}
                    onClick={() => openChat(i.id)}
                  >
                    <MessageSquare size={15} />
                    开始对话
                  </button>
                  <button
                    className="small"
                    aria-label={`查看 ${i.name} 详情`}
                    onClick={() => setDetail(i.id)}
                  >
                    <Terminal size={15} />
                    详情
                  </button>
                  <button
                    className="icon danger"
                    aria-label={`销毁 ${i.name}`}
                    disabled={destroy.isPending || i.status === "stopping"}
                    onClick={() => {
                      if (
                        window.confirm(
                          `销毁实例「${i.name}」并停止推理进程？历史对话会保留。`,
                        )
                      )
                        destroy.mutate(i.id);
                    }}
                  >
                    <Trash2 size={16} />
                  </button>
                </div>
              </article>
            ))}
          </div>
          {!matchingInstances.length && !loading && (
            <div className="empty panel">
              <Layers size={34} />
              <h3>{records.length ? "此状态下暂无实例" : "从一个模型开始"}</h3>
              <p>
                {records.length
                  ? "切换状态查看其他实例。"
                  : "选择已发现的模型与计算设备，创建你的第一个推理实例。"}
              </p>
              {!records.length && (
                <button className="primary" onClick={() => openCreate()}>
                  <Plus size={16} />
                  创建实例
                </button>
              )}
            </div>
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
            <InstanceFailure instance={activeDetail} />
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
