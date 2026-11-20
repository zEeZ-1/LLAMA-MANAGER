import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { X, Play, AlertCircle } from "lucide-react";
import { api, useModels, useNodes, errorText } from "../services/api";
import { useUI } from "../services/ui";
import type { Instance, InstanceConfig, PlacementDevice } from "../types";

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
    placement: { mode: "local", devices: [] },
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
  const placement = config.placement || { mode: "local", devices: [] };
  const options = (nodes.data?.nodes || []).flatMap((node) =>
    node.devices.flatMap((device) => {
      if (node.id === "local")
        return [
          {
            device_id: device.id,
            worker_id: null as string | null,
            label: `${node.name} · ${device.name}`,
          },
        ];
      const worker = node.rpc_workers?.find(
        (w) =>
          w.management_mode === "managed" &&
          w.state === "ready" &&
          !w.claim &&
          w.device_ids.includes(device.id),
      );
      return worker
        ? [
            {
              device_id: device.id,
              worker_id: worker.id,
              label: `${node.name} · ${device.name}`,
            },
          ]
        : [];
    }),
  );
  function setPlacement(devices: PlacementDevice[]) {
    setConfig({ ...config, placement: { mode: "rpc", devices } });
  }
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
                {(nodes.data?.nodes || [])
                  .filter((n) => n.role === "controller")
                  .map((n) => (
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
            推理方式
            <select
              aria-label="推理方式"
              value={placement.mode}
              onChange={(e) =>
                setConfig({
                  ...config,
                  n_gpu_layers:
                    e.target.value === "rpc" && config.n_gpu_layers === 0
                      ? 99
                      : config.n_gpu_layers,
                  placement: {
                    mode: e.target.value as "local" | "rpc",
                    devices: [],
                  },
                })
              }
            >
              <option value="local">单机</option>
              <option value="rpc">多机 RPC</option>
            </select>
          </label>
          {placement.mode === "rpc" && (
            <div className="placement-panel">
              <p className="muted">
                选择空闲的已验证 worker；设备按选中顺序分配，权重为相对比例。
              </p>
              {options.map((option) => {
                const selected = placement.devices.find(
                  (d) => d.device_id === option.device_id,
                );
                return (
                  <div className="placement-option" key={option.device_id}>
                    <label className="check">
                      <input
                        type="checkbox"
                        checked={!!selected}
                        onChange={(e) =>
                          setPlacement(
                            e.target.checked
                              ? [
                                  ...placement.devices,
                                  {
                                    device_id: option.device_id,
                                    worker_id: option.worker_id,
                                    weight: 1,
                                  },
                                ]
                              : placement.devices.filter(
                                  (d) => d.device_id !== option.device_id,
                                ),
                          )
                        }
                      />
                      {option.label}
                    </label>
                  </div>
                );
              })}
              {!options.some((o) => o.worker_id) && (
                <div className="notice">
                  暂无空闲且已验证的远端 RPC。请先在节点页面完成部署。
                </div>
              )}
              {placement.devices.map((item, index) => (
                <div className="placement-option" key={item.device_id}>
                  <span>
                    {index + 1}.{" "}
                    {options.find((o) => o.device_id === item.device_id)?.label}
                  </span>
                  <input
                    aria-label={`设备 ${index + 1} 分配权重`}
                    type="number"
                    min={0.001}
                    max={100000}
                    step="any"
                    required
                    value={item.weight}
                    onChange={(e) =>
                      setPlacement(
                        placement.devices.map((d) =>
                          d.device_id === item.device_id
                            ? { ...d, weight: Number(e.target.value) }
                            : d,
                        ),
                      )
                    }
                  />
                  {index > 0 && (
                    <button
                      type="button"
                      className="small"
                      onClick={() => {
                        const ordered = [...placement.devices];
                        [ordered[index - 1], ordered[index]] = [
                          ordered[index],
                          ordered[index - 1],
                        ];
                        setPlacement(ordered);
                      }}
                    >
                      前移
                    </button>
                  )}
                </div>
              ))}
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
              disabled={
                !modelId ||
                mutation.isPending ||
                (placement.mode === "rpc" &&
                  !placement.devices.some((d) => d.worker_id))
              }
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
