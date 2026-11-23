import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Boxes,
  Layers,
  MessageSquare,
  Plus,
  Terminal,
  Trash2,
  X,
  ArrowUpRight,
} from "lucide-react";
import { api, errorText, useInstances, useModels } from "../services/api";
import { useUI } from "../services/ui";
import { Status } from "./Status";
import { CreateInstance } from "./CreateInstance";
import { ModelLibrary } from "./ModelLibrary";
import { InstanceDetail } from "./InstanceDetail";
import { InstanceFailure } from "./InstanceFailure";

export function InstancePanel({ onClose }: { onClose: () => void }) {
  const {
    createModel,
    createRequest,
    createPending,
    openCreate,
    openChat,
    chatLocked,
  } = useUI();
  const instances = useInstances(false),
    models = useModels(false);
  const [library, setLibrary] = useState(false);
  const [detail, setDetail] = useState<string | null>(null);
  const [filter, setFilter] = useState("all");
  const scroll = useRef<HTMLDivElement>(null);
  const client = useQueryClient();
  const destroy = useMutation({
    mutationFn: (id: string) => api(`/instances/${id}`, { method: "DELETE" }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["instances"] }),
  });
  useEffect(() => {
    setFilter("all");
    scroll.current?.scrollTo({ top: 0 });
  }, [createModel, createRequest]);
  const records = instances.data?.instances || [];
  const ready = records.filter((i) => i.status === "ready");
  const selected = records.find((i) => i.id === detail);
  const matching = records.filter(
    (i) =>
      filter === "all" ||
      i.status === filter ||
      (filter === "starting" && i.status === "created"),
  );
  return (
    <aside className="instance-manager" aria-label="实例管理">
      <header className="instance-manager-header">
        <div>
          <span className="eyebrow">INFERENCE</span>
          <h2>
            实例管理<span className="pane-count">{records.length}</span>
          </h2>
        </div>
        <button
          className="icon pane-close"
          aria-label="关闭实例管理"
          onClick={onClose}
        >
          <X size={17} />
        </button>
      </header>
      <div className="instance-tools">
        <button
          className="model-library-entry"
          disabled={createPending}
          onClick={() => setLibrary(true)}
        >
          <Boxes size={16} />
          <span>
            模型目录
            <small>
              {models.isPending
                ? "读取中…"
                : `${models.data?.models.length ?? 0} 个模型包`}
            </small>
          </span>
          <ArrowUpRight size={15} />
        </button>
        <button
          className="primary"
          aria-label="新建实例"
          disabled={createPending}
          onClick={() => openCreate()}
        >
          <Plus size={16} />
          <span>新建实例</span>
        </button>
      </div>
      <div className="instance-panel-scroll" ref={scroll}>
        {createModel !== null && <CreateInstance key={createRequest} />}
        <div className="instance-list-heading">
          <span>
            <i className="dot ready" />
            {ready.length} 个就绪 / {records.length} 个实例
          </span>
          <select
            aria-label="筛选实例状态"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          >
            <option value="all">全部状态</option>
            <option value="ready">就绪</option>
            <option value="starting">启动中</option>
            <option value="failed">失败</option>
            <option value="stopping">停止中</option>
          </select>
        </div>
        {(instances.error || destroy.error) && (
          <div className="error" role="alert">
            {errorText(instances.error || destroy.error)}
            <button
              className="small"
              onClick={() =>
                client.invalidateQueries({ queryKey: ["instances"] })
              }
            >
              重试
            </button>
          </div>
        )}
        {instances.isPending && <p className="muted">正在读取实例…</p>}
        <div className="managed-instances">
          {matching.map((i) => (
            <article className="managed-instance" key={i.id}>
              <div className="managed-instance-title">
                <Layers size={17} />
                <button onClick={() => setDetail(i.id)}>{i.name}</button>
                <Status instance={i} />
              </div>
              <p className="managed-instance-model">
                {i.model_name}
                <span>{i.capabilities?.image_input ? "图文" : "文本"}</span>
              </p>
              <div className="managed-instance-params">
                <span>
                  CTX <b>{i.config.context_size.toLocaleString()}</b>
                </span>
                <span>
                  并发 <b>{i.config.parallel}</b>
                </span>
                <span>
                  GPU <b>{i.config.n_gpu_layers} 层</b>
                </span>
              </div>
              {!!i.runtime.placement?.device_names?.length && (
                <p className="managed-instance-devices">
                  {i.runtime.placement.device_names.join(" · ")}
                </p>
              )}
              <InstanceFailure instance={i} compact />
              <div className="managed-instance-actions">
                <button
                  className={i.status === "ready" ? "start-chat" : "small"}
                  disabled={i.status !== "ready" || chatLocked}
                  onClick={() => {
                    openChat(i.id);
                    onClose();
                  }}
                >
                  <MessageSquare size={14} />
                  开始对话
                </button>
                <button
                  className="icon"
                  aria-label={`查看 ${i.name} 详情`}
                  onClick={() => setDetail(i.id)}
                >
                  <Terminal size={15} />
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
                  <Trash2 size={15} />
                </button>
              </div>
            </article>
          ))}
        </div>
        {!matching.length && !instances.isPending && (
          <div className="instance-empty">
            <Layers size={28} />
            <h3>
              {records.length ? "此状态下暂无实例" : "模型就位，随时开始"}
            </h3>
            <p>
              {records.length
                ? "切换状态查看其他实例。"
                : "选择本机模型，配置参数，创建你的第一个推理实例。"}
            </p>
            {!records.length && createModel === null && (
              <button onClick={() => openCreate()}>
                <Plus size={15} />
                创建实例
              </button>
            )}
          </div>
        )}
      </div>
      <footer className="instance-panel-footer">
        <span className="dot ready" />
        本地模型 · 集中管理 · 每 2 秒更新
      </footer>
      {library && (
        <ModelLibrary
          onClose={() => setLibrary(false)}
          onSelect={(id) => {
            setLibrary(false);
            openCreate(id);
          }}
        />
      )}
      {selected && (
        <InstanceDetail instance={selected} onClose={() => setDetail(null)} />
      )}
    </aside>
  );
}
