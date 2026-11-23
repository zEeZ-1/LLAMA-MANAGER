import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Boxes,
  Search,
  RefreshCw,
  Image,
  FileText,
  Cpu,
  Plus,
  X,
} from "lucide-react";
import { api, bytes, errorText, useModels, useNodes } from "../services/api";

export function ModelLibrary({
  onSelect,
  onClose,
}: {
  onSelect: (id: string) => void;
  onClose: () => void;
}) {
  const models = useModels(false),
    nodes = useNodes(false);
  const [search, setSearch] = useState("");
  const [modelKind, setModelKind] = useState("all");
  const client = useQueryClient();
  const refresh = useMutation({
    mutationFn: () => api("/models/refresh", { method: "POST" }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["models"] }),
  });
  const files = models.data?.models || [];
  const loading = models.isPending;
  const matchingModels = files.filter(
    (m) =>
      (modelKind === "all" || m.kind === modelKind) &&
      `${m.name} ${m.relative_path} ${m.architecture}`
        .toLowerCase()
        .includes(search.toLowerCase()),
  );
  return (
    <div className="modal-backdrop">
      <section
        className="modal model-library"
        role="dialog"
        aria-modal="true"
        aria-labelledby="library-title"
      >
        <div className="section-heading">
          <div>
            <span className="eyebrow">MODEL LIBRARY</span>
            <h2 id="library-title">模型目录</h2>
          </div>
          <button className="icon" aria-label="关闭模型目录" onClick={onClose}>
            <X size={20} />
          </button>
        </div>
        <p className="muted library-intro">
          自动扫描独立模型包，选择模型后在右侧配置实例。
        </p>
        {(models.error || refresh.error) && (
          <div className="error" role="alert">
            {errorText(models.error || refresh.error)}
            <button
              onClick={() => client.invalidateQueries({ queryKey: ["models"] })}
            >
              重试
            </button>
          </div>
        )}
        {loading && <p className="muted">正在读取模型目录…</p>}
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
                    {m.availability !== "available" ? " · 查看不可用原因" : ""}
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
                  onClick={() => onSelect(m.id)}
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
      </section>
    </div>
  );
}
