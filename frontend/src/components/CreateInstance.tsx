import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { X, Play, AlertCircle } from "lucide-react";
import { api, useModels, useNodes, errorText } from "../services/api";
import { useUI } from "../services/ui";
import type { Instance, InstanceConfig } from "../types";

export function CreateInstance() {
  const { createModel, closeCreate, navigate } = useUI();
  const models = useModels(),
    nodes = useNodes();
  const client = useQueryClient();
  const [modelId, setModelId] = useState(createModel || "");
  const [nodeId, setNodeId] = useState("local");
  const [name, setName] = useState("");
  const [config, setConfig] = useState<InstanceConfig>({
    context_size: 4096,
    batch_size: 512,
    ubatch_size: 128,
    parallel: 1,
    threads: 4,
    n_gpu_layers: 0,
    cache_type_k: "f16",
    cache_type_v: "f16",
    extra_options: {},
  });
  const mutation = useMutation({
    mutationFn: () =>
      api<Instance>("/instances", {
        method: "POST",
        body: JSON.stringify({
          name:
            name.trim() ||
            models.data?.models.find((m) => m.id === modelId)?.name,
          node_id: nodeId,
          model_id: modelId,
          config,
        }),
      }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["instances"] });
      navigate("instances");
      closeCreate();
    },
  });
  const fields = [
    ["context_size", "Context size", 128, 1048576],
    ["n_gpu_layers", "GPU layers", 0, 999],
    ["parallel", "Parallel", 1, 32],
    ["threads", "CPU threads", 1, 1024],
    ["batch_size", "Batch size", 1, 8192],
    ["ubatch_size", "Micro batch", 1, 8192],
  ] as const;
  return (
    <div className="modal-backdrop">
      <section
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-title"
      >
        <div className="section-heading">
          <div>
            <span className="eyebrow">NEW INSTANCE</span>
            <h2 id="create-title">创建推理实例</h2>
          </div>
          <button
            className="icon"
            aria-label="关闭"
            onClick={closeCreate}
            disabled={mutation.isPending}
          >
            <X />
          </button>
        </div>
        <p className="muted">
          每个实例使用独立的 llama-server 进程和推理上下文。
        </p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            mutation.mutate();
          }}
        >
          <div className="form-grid">
            <label>
              节点
              <select
                aria-label="节点"
                value={nodeId}
                onChange={(e) => {
                  setNodeId(e.target.value);
                  setModelId("");
                }}
              >
                {(nodes.data?.nodes || []).map((n) => (
                  <option key={n.id} value={n.id}>
                    {n.name} · {n.hostname}
                  </option>
                ))}
              </select>
            </label>
            <label>
              模型
              <select
                aria-label="模型"
                required
                value={modelId}
                onChange={(e) => setModelId(e.target.value)}
              >
                <option value="">选择已发现的 GGUF</option>
                {models.data?.models
                  .filter((m) => m.node_id === nodeId)
                  .map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.name}
                    </option>
                  ))}
              </select>
            </label>
          </div>
          {!models.data?.models.length && (
            <div className="notice">
              将完整 GGUF 文件放入配置的模型目录后，这里会自动更新。
            </div>
          )}
          <label>
            实例名称
            <input
              aria-label="实例名称"
              maxLength={100}
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="默认使用模型名称"
            />
          </label>
          <div className="form-grid">
            {fields.map(([key, label, min, max]) => (
              <label key={key}>
                {label}
                <input
                  aria-label={label}
                  type="number"
                  required
                  min={min}
                  max={max}
                  value={config[key]}
                  onChange={(e) =>
                    setConfig({ ...config, [key]: Number(e.target.value) })
                  }
                />
              </label>
            ))}
            {(["cache_type_k", "cache_type_v"] as const).map((key) => (
              <label key={key}>
                {key === "cache_type_k" ? "K" : "V"} cache
                <select
                  value={config[key]}
                  onChange={(e) =>
                    setConfig({ ...config, [key]: e.target.value })
                  }
                >
                  {[
                    "f16",
                    "bf16",
                    "f32",
                    "q8_0",
                    "q4_0",
                    "q4_1",
                    "q5_0",
                    "q5_1",
                    "iq4_nl",
                  ].map((v) => (
                    <option key={v}>{v}</option>
                  ))}
                </select>
              </label>
            ))}
          </div>
          <label className="check">
            <input
              type="checkbox"
              checked={!!config.extra_options.flash_attn}
              onChange={(e) =>
                setConfig({
                  ...config,
                  extra_options: { flash_attn: e.target.checked },
                })
              }
            />{" "}
            Flash Attention
          </label>
          {mutation.error && (
            <div className="error" role="alert">
              <AlertCircle size={16} />
              {errorText(mutation.error)}
            </div>
          )}
          <div className="modal-actions">
            <button
              type="button"
              onClick={closeCreate}
              disabled={mutation.isPending}
            >
              取消
            </button>
            <button
              className="primary"
              disabled={!modelId || mutation.isPending}
            >
              <Play size={16} />
              {mutation.isPending ? "正在创建…" : "创建并启动"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
