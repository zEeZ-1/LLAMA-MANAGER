import { useQuery } from "@tanstack/react-query";
import { X } from "lucide-react";
import { api, errorText } from "../services/api";
import { Status } from "./Status";
import { InstanceFailure } from "./InstanceFailure";
import type { Instance } from "../types";
export function InstanceDetail({
  instance,
  onClose,
}: {
  instance: Instance;
  onClose: () => void;
}) {
  const logs = useQuery({
    queryKey: ["logs", instance.id],
    queryFn: () => api<{ text: string }>(`/instances/${instance.id}/logs`),
    refetchInterval: 3000,
  });
  return (
    <div className="modal-backdrop">
      <section
        className="modal wide"
        role="dialog"
        aria-modal="true"
        aria-labelledby="detail-title"
      >
        <div className="section-heading">
          <h2 id="detail-title">{instance.name}</h2>
          <button className="icon" aria-label="关闭详情" onClick={onClose}>
            <X />
          </button>
        </div>
        <Status instance={instance} />
        <dl className="details">
          <dt>Instance ID</dt>
          <dd className="mono">{instance.id}</dd>
          <dt>模型</dt>
          <dd>{instance.model_name}</dd>
          <dt>节点 / Runtime</dt>
          <dd>
            {instance.node_id} / {instance.runtime_type}
          </dd>
          <dt>创建时间</dt>
          <dd>{new Date(instance.created_at).toLocaleString()}</dd>
          <dt>启动时间</dt>
          <dd>
            {instance.started_at
              ? new Date(instance.started_at).toLocaleString()
              : "尚未就绪"}
          </dd>
          <dt>PID</dt>
          <dd>{instance.runtime.pid || "—"}</dd>
          <dt>Context / Parallel</dt>
          <dd>
            {instance.config.context_size} / {instance.config.parallel}
          </dd>
          <dt>GPU layers / Threads</dt>
          <dd>
            {instance.config.n_gpu_layers} / {instance.config.threads}
          </dd>
        </dl>
        <InstanceFailure instance={instance} />
        <h3>
          运行日志 <span className="muted">最近 32 KiB</span>
        </h3>
        <pre className="log">
          {logs.error ? errorText(logs.error) : logs.data?.text || "读取中…"}
        </pre>
      </section>
    </div>
  );
}
