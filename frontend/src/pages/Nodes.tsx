import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Cpu,
  Network,
  Plus,
  RefreshCw,
  X,
  Play,
  Square,
  Terminal,
  Link,
  Trash2,
  ShieldCheck,
  Pencil,
} from "lucide-react";
import {
  api,
  bytes,
  errorText,
  gpuMemory,
  percent,
  useNodes,
} from "../services/api";
import type { Node, Worker, Job, Release } from "../types";

const connectionNames: Record<string, string> = {
  online: "在线",
  unknown: "待连接",
  offline: "离线",
  auth_failed: "认证失败",
  host_key_changed: "主机身份变化",
  host_key_untrusted: "待核验主机",
  connecting: "连接中",
};
const workerNames: Record<string, string> = {
  uninstalled: "未部署",
  stopped: "已停止",
  starting: "待核验",
  deploying: "部署中",
  ready: "就绪",
  stopping: "停止中",
  failed: "失败",
  unknown: "待核对",
};
const jobNames: Record<string, string> = {
  completed: "完成",
  failed: "失败",
  running: "进行中",
  queued: "排队",
  interrupted: "已中断",
};
const operationNames: Record<string, string> = {
  connect: "连接节点",
  refresh: "刷新信息",
  deploy: "部署 RPC",
  adopt: "验证接管",
  start: "启动 RPC",
  stop: "停止 RPC",
  clean: "清理缓存",
  reconcile: "核对占用",
};

export function Nodes() {
  const query = useNodes();
  const client = useQueryClient();
  const [connect, setConnect] = useState<Node | "new" | null>(null);
  const [edit, setEdit] = useState<Node | null>(null);
  const [details, setDetails] = useState<string | null>(null);
  const [workerNode, setWorkerNode] = useState<Node | null>(null);
  const [deployment, setDeployment] = useState<Worker | null>(null);
  const [logWorker, setLogWorker] = useState<Worker | null>(null);
  const [error, setError] = useState("");
  const [working, setWorking] = useState(false);
  const jobs = useQuery({
    queryKey: ["jobs"],
    queryFn: () => api<{ jobs: Job[] }>("/jobs"),
    refetchInterval: 2000,
  });
  const logs = useQuery({
    queryKey: ["worker-logs", logWorker?.id],
    enabled: !!logWorker,
    queryFn: () => api<{ text: string }>(`/workers/${logWorker!.id}/logs`),
    refetchInterval: logWorker ? 5000 : false,
  });
  const refresh = () => {
    void client.invalidateQueries({ queryKey: ["nodes"] });
    void client.invalidateQueries({ queryKey: ["jobs"] });
  };
  async function action(fn: () => Promise<unknown>) {
    setError("");
    setWorking(true);
    try {
      await fn();
      refresh();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setWorking(false);
    }
  }
  const activeJobs = jobs.data?.jobs.slice(0, 6) || [];
  return (
    <>
      <div className="section-heading">
        <span className="muted">控制端统一管理 · 远端仅运行 RPC</span>
        <button className="primary" onClick={() => setConnect("new")}>
          <Plus size={16} />
          添加节点
        </button>
      </div>
      {error && (
        <div className="error" role="alert">
          {error}
        </div>
      )}
      <div className="nodes-grid">
        {query.data?.nodes.map((node) => (
          <section key={node.id} className="node-card panel">
            <div className="section-heading">
              <div className="node-heading">
                <div className="device-icon">
                  <Network size={26} />
                </div>
                <div>
                  <h2>{node.name}</h2>
                  <span className="muted mono">
                    {node.address || node.hostname}
                  </span>
                </div>
              </div>
              <span
                className={`badge ${node.status === "online" ? "ready" : "stopped"}`}
              >
                {connectionNames[node.connection_state] ||
                  node.connection_state}
              </span>
            </div>
            <div className="node-specs">
              <div>
                <span>处理器</span>
                <strong>{node.cpu_info.name}</strong>
                <small>
                  {node.cpu_info.logical_count ?? "—"} 逻辑核心 ·{" "}
                  {node.architecture}
                </small>
              </div>
              <div>
                <span>系统内存</span>
                <strong>{bytes(node.memory_info.total)}</strong>
                <small>{bytes(node.memory_info.available)} 可用</small>
              </div>
              <div>
                <span>
                  {node.role === "controller" ? "模型 / 实例" : "关联实例"}
                </span>
                <strong>
                  {node.role === "controller" ? `${node.model_count} / ` : ""}
                  {node.instance_count}
                </strong>
                <small>
                  {node.role === "controller"
                    ? `${node.ready_count} 个实例就绪`
                    : "模型由控制主机提供"}
                </small>
              </div>
            </div>
            <div className="node-gpus">
              {node.devices.map((d) => (
                <div key={d.id}>
                  <Cpu size={18} />
                  <span>
                    {d.name}
                    <small className="device-caption">{gpuMemory(d)}</small>
                  </span>
                  <span className="muted">
                    {d.compute_state === "ready"
                      ? "计算就绪"
                      : "计算能力待验证"}
                  </span>
                </div>
              ))}
            </div>
            {node.telemetry_stale && (
              <div className="notice">
                数据已过期 · 最近采样{" "}
                {node.sampled_at
                  ? new Date(node.sampled_at).toLocaleString()
                  : "尚未采集"}
              </div>
            )}
            {node.last_error && (
              <div className="error">{node.last_error.message}</div>
            )}
            {details === node.id && (
              <dl className="details">
                <dt>硬件型号</dt>
                <dd>{node.hardware_model || "型号未提供"}</dd>
                <dt>Hostname</dt>
                <dd>{node.hostname}</dd>
                <dt>操作系统</dt>
                <dd>{node.os}</dd>
                <dt>CPU 使用率</dt>
                <dd>{percent(node.cpu_info.usage_percent)}</dd>
                <dt>内存使用率</dt>
                <dd>{percent(node.memory_info.usage_percent)}</dd>
                {node.devices.map((d) => (
                  <div className="detail-device" key={d.id}>
                    <dt>GPU / 驱动</dt>
                    <dd>
                      {d.name} / {d.driver || "未提供"}
                    </dd>
                    <dt>GPU 使用率</dt>
                    <dd>{percent(d.properties.utilization_percent)}</dd>
                  </div>
                ))}
                {node.ssh && (
                  <>
                    <dt>SSH 账户</dt>
                    <dd>
                      {node.ssh.username}@{node.address}:{node.ssh.port}
                    </dd>
                    <dt>主机指纹</dt>
                    <dd className="mono">
                      {node.identity?.ssh_host_key_fingerprint || "尚未核验"}
                    </dd>
                    <dt>后续认证</dt>
                    <dd>
                      {node.ssh.credential_id
                        ? "已登记专用密钥"
                        : "等待首次认证"}
                    </dd>
                  </>
                )}
                {Object.entries(node.inventory || {})
                  .filter(([k]) => !["machine_id_hash"].includes(k))
                  .map(([k, v]) => (
                    <div className="detail-device" key={k}>
                      <dt>{k}</dt>
                      <dd>{v == null || v === "" ? "未提供" : String(v)}</dd>
                    </div>
                  ))}
                <dt>最近采样</dt>
                <dd>
                  {node.sampled_at
                    ? new Date(node.sampled_at).toLocaleString()
                    : "尚未采集"}
                </dd>
              </dl>
            )}
            {node.rpc_workers?.map((worker) => (
              <div className="worker-box" key={worker.id}>
                <div className="section-heading">
                  <strong>
                    RPC · {worker.endpoint.host}:{worker.endpoint.port}
                  </strong>
                  <span
                    className={`badge ${worker.state === "ready" ? "ready" : worker.state === "failed" ? "failed" : "stopped"}`}
                  >
                    {workerNames[worker.state] || worker.state}
                  </span>
                </div>
                <p className="muted">
                  {worker.release_id || "尚无验证发布"} · 协议{" "}
                  {worker.protocol_version || "待核验"} · RSS{" "}
                  {bytes(worker.rss_bytes)}
                </p>
                {worker.claim && (
                  <div className="notice">
                    {worker.claim.state === "quarantined"
                      ? "占用待核对"
                      : "实例占用"}{" "}
                    · {worker.claim.instance_id}
                  </div>
                )}
                {worker.last_error && (
                  <div className="error">{worker.last_error.message}</div>
                )}
                <div className="row-actions worker-actions">
                  <button
                    className="small"
                    disabled={working || !!worker.claim}
                    onClick={() => setDeployment(worker)}
                  >
                    {worker.management_mode === "observed"
                      ? "验证并接管"
                      : "部署 / 更新"}
                  </button>
                  <button
                    className="small"
                    disabled={
                      working ||
                      !!worker.claim ||
                      !worker.release_id ||
                      worker.management_mode !== "managed"
                    }
                    onClick={() =>
                      void action(() =>
                        api(`/workers/${worker.id}/start`, {
                          method: "POST",
                          body: JSON.stringify({
                            idempotency_key: crypto.randomUUID(),
                          }),
                        }),
                      )
                    }
                  >
                    <Play size={14} />
                    启动
                  </button>
                  <button
                    className="small"
                    disabled={
                      working ||
                      !!worker.claim ||
                      worker.management_mode !== "managed" ||
                      !worker.release_id
                    }
                    onClick={() =>
                      void action(() =>
                        api(`/workers/${worker.id}/stop`, {
                          method: "POST",
                          body: JSON.stringify({
                            idempotency_key: crypto.randomUUID(),
                          }),
                        }),
                      )
                    }
                  >
                    <Square size={14} />
                    停止
                  </button>
                  <button
                    className="small"
                    onClick={() => setLogWorker(worker)}
                  >
                    <Terminal size={14} />
                    日志
                  </button>
                  {worker.claim?.state === "quarantined" && (
                    <button
                      className="small"
                      disabled={working}
                      onClick={() =>
                        void action(() =>
                          api(`/workers/${worker.id}/reconcile`, {
                            method: "POST",
                          }),
                        )
                      }
                    >
                      核对占用
                    </button>
                  )}
                  {worker.state === "stopped" &&
                    worker.management_mode === "managed" && (
                      <button
                        className="small"
                        disabled={working || !!worker.claim}
                        onClick={() => {
                          if (
                            window.confirm(
                              "仅清理该 worker 所属的磁盘张量缓存？下次加载可能需要重新传输。",
                            )
                          )
                            void action(() =>
                              api(`/workers/${worker.id}/cache/clean`, {
                                method: "POST",
                                body: "{}",
                              }),
                            );
                        }}
                      >
                        清理缓存
                      </button>
                    )}
                </div>
              </div>
            ))}
            <div className="card-footer">
              <button
                onClick={() => setDetails(details === node.id ? null : node.id)}
              >
                {details === node.id ? "收起详情" : "查看详情"}
              </button>
              {node.role === "rpc_worker" && (
                <>
                  <button disabled={working} onClick={() => setConnect(node)}>
                    <Link size={15} />
                    连接
                  </button>
                  <button
                    className="icon"
                    aria-label={`编辑 ${node.name}`}
                    onClick={() => setEdit(node)}
                  >
                    <Pencil size={16} />
                  </button>
                  <button
                    className="icon"
                    disabled={working || node.connection_state !== "online"}
                    aria-label={`刷新 ${node.name}`}
                    onClick={() =>
                      void action(() =>
                        api(`/nodes/${node.id}/refresh`, { method: "POST" }),
                      )
                    }
                  >
                    <RefreshCw size={16} />
                  </button>
                  {!node.rpc_workers?.length && (
                    <button
                      className="primary"
                      disabled={node.connection_state !== "online"}
                      onClick={() => setWorkerNode(node)}
                    >
                      <Plus size={15} />
                      配置 RPC
                    </button>
                  )}
                  <button
                    className="icon danger"
                    disabled={working}
                    aria-label={`移除 ${node.name}`}
                    onClick={() => {
                      if (
                        window.confirm(
                          `移除节点「${node.name}」登记？不会卸载远端文件，运行或未知状态的 RPC 会阻止移除。`,
                        )
                      )
                        void action(() =>
                          api(`/nodes/${node.id}`, { method: "DELETE" }),
                        );
                    }}
                  >
                    <Trash2 size={16} />
                  </button>
                </>
              )}
            </div>
          </section>
        ))}
      </div>
      {!!activeJobs.length && (
        <section className="panel jobs-panel">
          <div className="section-heading">
            <h2>最近节点任务</h2>
            <span className="muted">认证信息不保存</span>
          </div>
          {activeJobs.map((job) => (
            <div className="job" key={job.id}>
              <div>
                <strong>
                  {operationNames[job.operation] || "节点任务"} ·{" "}
                  {jobNames[job.state] || job.state}
                </strong>
                <p className="muted">{job.error?.message || job.message} </p>
              </div>
              {["running", "queued"].includes(job.state) && (
                <button
                  className="small"
                  onClick={() =>
                    void action(() =>
                      api(`/jobs/${job.id}/cancel`, { method: "POST" }),
                    )
                  }
                >
                  取消
                </button>
              )}
            </div>
          ))}
        </section>
      )}
      {edit && (
        <EditNodeModal
          node={edit}
          onClose={() => setEdit(null)}
          onDone={() => {
            setEdit(null);
            refresh();
          }}
        />
      )}
      {connect && (
        <ConnectModal
          existing={connect === "new" ? undefined : connect}
          onClose={() => setConnect(null)}
          onDone={() => {
            setConnect(null);
            refresh();
          }}
        />
      )}
      {workerNode && (
        <WorkerModal
          node={workerNode}
          onClose={() => setWorkerNode(null)}
          onDone={() => {
            setWorkerNode(null);
            refresh();
          }}
        />
      )}
      {deployment && (
        <DeployModal
          worker={deployment}
          onClose={() => setDeployment(null)}
          onDone={() => {
            setDeployment(null);
            refresh();
          }}
        />
      )}
      {logWorker && (
        <div className="modal-backdrop">
          <section className="modal wide" role="dialog" aria-modal="true">
            <div className="section-heading">
              <h2>RPC 运行日志</h2>
              <button
                className="icon"
                aria-label="关闭日志"
                onClick={() => setLogWorker(null)}
              >
                <X />
              </button>
            </div>
            <pre className="log">
              {logs.error
                ? errorText(logs.error)
                : logs.data?.text || "读取中…"}
            </pre>
          </section>
        </div>
      )}
    </>
  );
}

function EditNodeModal({
  node,
  onClose,
  onDone,
}: {
  node: Node;
  onClose: () => void;
  onDone: () => void;
}) {
  const [name, setName] = useState(node.name);
  const [address, setAddress] = useState(node.address || "");
  const [username, setUsername] = useState(node.ssh?.username || "");
  const [port, setPort] = useState(node.ssh?.port || 22);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function save() {
    setBusy(true);
    setError("");
    const body: Record<string, unknown> = { name };
    if (address !== node.address) body.address = address;
    if (username !== node.ssh?.username) body.username = username;
    if (port !== node.ssh?.port) body.port = port;
    try {
      await api(`/nodes/${node.id}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      });
      onDone();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="modal-backdrop">
      <section
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label="编辑节点"
      >
        <div className="section-heading">
          <h2>编辑节点</h2>
          <button
            className="icon"
            aria-label="关闭编辑"
            disabled={busy}
            onClick={onClose}
          >
            <X />
          </button>
        </div>
        <p className="muted">
          修改连接地址或账户前，请停止 RPC 并释放实例占用。更换机器请重新登记。
        </p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void save();
          }}
        >
          <div className="form-grid">
            <label>
              节点名称
              <input
                aria-label="节点名称"
                required
                maxLength={100}
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </label>
            <label>
              IP 地址
              <input
                aria-label="IP 地址"
                required
                value={address}
                onChange={(e) => setAddress(e.target.value)}
              />
            </label>
            <label>
              SSH 用户名
              <input
                aria-label="SSH 用户名"
                required
                value={username}
                onChange={(e) => setUsername(e.target.value)}
              />
            </label>
            <label>
              SSH 端口
              <input
                aria-label="SSH 端口"
                required
                type="number"
                min={1}
                max={65535}
                value={port}
                onChange={(e) => setPort(Number(e.target.value))}
              />
            </label>
          </div>
          {error && (
            <div className="error" role="alert">
              {error}
            </div>
          )}
          <div className="modal-actions">
            <button type="button" onClick={onClose} disabled={busy}>
              取消
            </button>
            <button className="primary" disabled={busy}>
              {busy ? "保存中…" : "保存"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}

function ConnectModal({
  existing,
  onClose,
  onDone,
}: {
  existing?: Node;
  onClose: () => void;
  onDone: () => void;
}) {
  const [node, setNode] = useState<Node | null>(existing || null);
  const [name, setName] = useState(existing?.name || "");
  const [address, setAddress] = useState(existing?.address || "");
  const [username, setUsername] = useState(existing?.ssh?.username || "");
  const [port, setPort] = useState(existing?.ssh?.port || 22);
  const [mode, setMode] = useState<"password" | "key">(
    existing?.ssh?.credential_id ? "key" : "password",
  );
  const [password, setPassword] = useState("");
  const [credential, setCredential] = useState(
    existing?.ssh?.credential_id || "",
  );
  const [challenge, setChallenge] = useState<{
    challenge_id: string;
    keys: { algorithm: string; fingerprint: string }[];
    current_fingerprint: string | null;
  } | null>(null);
  const [fingerprint, setFingerprint] = useState("");
  const [trusted, setTrusted] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const credentials = useQuery({
    queryKey: ["credentials"],
    queryFn: () =>
      api<{
        credentials: { id: string; label: string }[];
        available_keys: string[];
      }>("/credentials"),
  });
  async function scan() {
    setBusy(true);
    setError("");
    try {
      let target = node;
      if (!target) {
        target = await api<Node>("/nodes", {
          method: "POST",
          body: JSON.stringify({
            name: name.trim() || address,
            address,
            username,
            port,
            credential_id: credential || null,
          }),
        });
        setNode(target);
      } else if (credential && credential !== target.ssh?.credential_id) {
        target = await api<Node>(`/nodes/${target.id}`, {
          method: "PATCH",
          body: JSON.stringify({ credential_id: credential }),
        });
        setNode(target);
      }
      const result = await api<NonNullable<typeof challenge>>(
        `/nodes/${target.id}/host-key/scan`,
        { method: "POST" },
      );
      setChallenge(result);
      setFingerprint(result.keys[0].fingerprint);
      setTrusted(false);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  async function connectKnown() {
    if (!node) return;
    setBusy(true);
    setError("");
    try {
      if (credential && credential !== node.ssh?.credential_id)
        await api(`/nodes/${node.id}`, {
          method: "PATCH",
          body: JSON.stringify({ credential_id: credential }),
        });
      await api(`/nodes/${node.id}/connect`, {
        method: "POST",
        body: JSON.stringify({
          password: mode === "password" ? password : null,
        }),
      });
      setPassword("");
      onDone();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  async function connect() {
    if (!node || !challenge || !trusted) return;
    setBusy(true);
    setError("");
    try {
      const changed =
        !!challenge.current_fingerprint &&
        challenge.current_fingerprint !== fingerprint;
      if (
        changed &&
        !window.confirm(
          "此节点主机密钥发生变化。请确认机器身份；仅在确认重装或更换主机后继续。",
        )
      )
        return;
      await api(`/nodes/${node.id}/host-key/trust`, {
        method: "POST",
        body: JSON.stringify({
          challenge_id: challenge.challenge_id,
          fingerprint,
          replace: changed,
        }),
      });
      setChallenge(null);
      await api(`/nodes/${node.id}/connect`, {
        method: "POST",
        body: JSON.stringify({
          password: mode === "password" ? password : null,
        }),
      });
      setPassword("");
      onDone();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  async function importKey(key_name: string) {
    if (!key_name) return;
    try {
      const result = await api<{ id: string }>("/credentials", {
        method: "POST",
        body: JSON.stringify({ type: "existing", label: key_name, key_name }),
      });
      setCredential(result.id);
      void credentials.refetch();
    } catch (e) {
      setError(errorText(e));
    }
  }
  return (
    <div className="modal-backdrop">
      <section
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="connect-title"
      >
        <div className="section-heading">
          <h2 id="connect-title">{existing ? "连接节点" : "添加远端节点"}</h2>
          <button
            className="icon"
            aria-label="关闭节点连接"
            disabled={busy}
            onClick={() => {
              setPassword("");
              onClose();
            }}
          >
            <X />
          </button>
        </div>
        <p className="muted">
          远端开启 SSH
          后即可接入。首次密码登录会安装本控制端专用公钥，供后续自动管理；密码仅用于当前认证。
        </p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void (challenge
              ? connect()
              : node?.identity?.ssh_host_key_fingerprint &&
                  !["host_key_changed", "host_key_untrusted"].includes(node.connection_state)
                ? connectKnown()
                : scan());
          }}
        >
          <div className="form-grid">
            <label>
              节点名称
              <input
                aria-label="节点名称"
                value={name}
                onChange={(e) => setName(e.target.value)}
                disabled={!!node}
                maxLength={100}
              />
            </label>
            <label>
              IP 地址
              <input
                aria-label="IP 地址"
                required
                value={address}
                disabled={!!node}
                onChange={(e) => setAddress(e.target.value)}
                placeholder="192.168.50.12"
              />
            </label>
            <label>
              SSH 用户名
              <input
                aria-label="SSH 用户名"
                required
                value={username}
                disabled={!!node}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
              />
            </label>
            <label>
              SSH 端口
              <input
                aria-label="SSH 端口"
                required
                type="number"
                min={1}
                max={65535}
                value={port}
                disabled={!!node}
                onChange={(e) => setPort(Number(e.target.value))}
              />
            </label>
          </div>
          <label>
            认证方式
            <select
              aria-label="认证方式"
              disabled={!!challenge}
              value={mode}
              onChange={(e) => setMode(e.target.value as typeof mode)}
            >
              <option value="password">首次密码登录</option>
              <option value="key">已登记密钥 / 本机 agent</option>
            </select>
          </label>
          {mode === "password" ? (
            <label>
              登录密码
              <input
                aria-label="登录密码"
                type="password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                maxLength={4096}
              />
            </label>
          ) : (
            <>
              <label>
                已登记密钥
                <select
                  aria-label="已登记密钥"
                  disabled={!!challenge}
                  value={credential}
                  onChange={(e) => setCredential(e.target.value)}
                >
                  <option value="">使用本机默认密钥 / agent</option>
                  {credentials.data?.credentials.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.label}
                    </option>
                  ))}
                </select>
              </label>
              {!!credentials.data?.available_keys.length && (
                <label>
                  导入本机密钥
                  <select
                    aria-label="导入本机密钥"
                    disabled={!!challenge}
                    value=""
                    onChange={(e) => void importKey(e.target.value)}
                  >
                    <option value="">选择 ~/.ssh 中的密钥文件名</option>
                    {credentials.data.available_keys.map((k) => (
                      <option key={k}>{k}</option>
                    ))}
                  </select>
                </label>
              )}
            </>
          )}
          {!challenge && node?.identity?.ssh_host_key_fingerprint && (
            <div className="notice">
              已登记主机指纹
              <button
                type="button"
                className="small"
                disabled={busy}
                onClick={() => void scan()}
              >
                重新核验
              </button>
            </div>
          )}
          {challenge && (
            <div className="fingerprint-box">
              <strong>
                <ShieldCheck size={16} />
                核对主机指纹
              </strong>
              <label>
                主机公钥
                <select
                  aria-label="主机公钥"
                  value={fingerprint}
                  onChange={(e) => setFingerprint(e.target.value)}
                >
                  {challenge.keys.map((k) => (
                    <option value={k.fingerprint} key={k.fingerprint}>
                      {k.algorithm}
                    </option>
                  ))}
                </select>
              </label>
              <code>{fingerprint}</code>
              <label className="check">
                <input
                  type="checkbox"
                  checked={trusted}
                  onChange={(e) => setTrusted(e.target.checked)}
                />
                我已通过可信渠道核对该主机指纹
              </label>
            </div>
          )}
          {error && (
            <div className="error" role="alert">
              {error}
            </div>
          )}
          <div className="modal-actions">
            <button type="button" disabled={busy} onClick={onClose}>
              取消
            </button>
            <button
              className="primary"
              disabled={busy || (!!challenge && !trusted)}
            >
              {busy
                ? "正在处理…"
                : challenge
                  ? "信任并连接"
                  : node?.identity?.ssh_host_key_fingerprint
                    ? "连接节点"
                    : "检测主机"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}

function WorkerModal({
  node,
  onClose,
  onDone,
}: {
  node: Node;
  onClose: () => void;
  onDone: () => void;
}) {
  const [selected, setSelected] = useState(node.devices.map((d) => d.id));
  const [port, setPort] = useState(50052);
  const [mode, setMode] = useState("managed");
  const [service, setService] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit() {
    setBusy(true);
    setError("");
    try {
      await api(`/nodes/${node.id}/workers`, {
        method: "POST",
        body: JSON.stringify({
          port,
          device_ids: selected,
          mode,
          service_name: mode === "observed" ? service : null,
        }),
      });
      onDone();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="modal-backdrop">
      <section className="modal" role="dialog" aria-modal="true">
        <div className="section-heading">
          <h2>配置 RPC · {node.name}</h2>
          <button className="icon" aria-label="关闭 RPC 配置" onClick={onClose}>
            <X />
          </button>
        </div>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void submit();
          }}
        >
          <label>
            管理方式
            <select
              aria-label="RPC 管理方式"
              value={mode}
              onChange={(e) => setMode(e.target.value)}
            >
              <option value="managed">部署平台专用服务</option>
              <option value="observed">关联已有服务，验证后接管</option>
            </select>
          </label>
          <label>
            RPC 端口
            <input
              aria-label="RPC 端口"
              type="number"
              min={1024}
              max={65535}
              required
              value={port}
              onChange={(e) => setPort(Number(e.target.value))}
            />
          </label>
          {mode === "observed" && (
            <label>
              已有用户服务名称
              <input
                aria-label="已有服务名称"
                required
                value={service}
                onChange={(e) => setService(e.target.value)}
                placeholder="llama-rpc-worker.service"
              />
            </label>
          )}
          <p className="muted">
            选择此 RPC 服务暴露的设备；同一 worker 同时由一个推理实例使用。
          </p>
          {node.devices.map((d) => (
            <label className="check" key={d.id}>
              <input
                type="checkbox"
                checked={selected.includes(d.id)}
                onChange={(e) =>
                  setSelected(
                    e.target.checked
                      ? [...selected, d.id]
                      : selected.filter((id) => id !== d.id),
                  )
                }
              />
              {d.name}
            </label>
          ))}
          {error && <div className="error">{error}</div>}
          <div className="modal-actions">
            <button type="button" onClick={onClose}>
              取消
            </button>
            <button className="primary" disabled={busy || !selected.length}>
              保存配置
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}

function DeployModal({
  worker,
  onClose,
  onDone,
}: {
  worker: Worker;
  onClose: () => void;
  onDone: () => void;
}) {
  const releases = useQuery({
    queryKey: ["releases"],
    queryFn: () => api<{ releases: Release[] }>("/releases"),
  });
  const [release, setRelease] = useState(worker.release_id || "");
  const [linger, setLinger] = useState(false);
  const [autostart, setAutostart] = useState(worker.autostart || false);
  const [sudoPassword, setSudoPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const observed = worker.management_mode === "observed";
  async function submit() {
    setBusy(true);
    setError("");
    try {
      await api(`/workers/${worker.id}/${observed ? "adopt" : "deploy"}`, {
        method: "POST",
        body: JSON.stringify({
          release_id: release,
          enable_linger: linger,
          autostart,
          sudo_password: sudoPassword || null,
          start: true,
        }),
      });
      setSudoPassword("");
      onDone();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="modal-backdrop">
      <section className="modal" role="dialog" aria-modal="true">
        <div className="section-heading">
          <h2>{observed ? "验证并接管已有 RPC" : "部署 RPC 服务"}</h2>
          <button
            className="icon"
            aria-label="关闭部署"
            disabled={busy}
            onClick={onClose}
          >
            <X />
          </button>
        </div>
        <p className="muted">
          控制端匹配环境并校验发布包，不在远端编译或安装模型库。已有服务仅在明确验证后接管。
        </p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void submit();
          }}
        >
          <label>
            RPC 发布包
            <select
              aria-label="RPC 发布包"
              required
              value={release}
              onChange={(e) => setRelease(e.target.value)}
            >
              <option value="">选择匹配的发布版本</option>
              {releases.data?.releases.map((r) => (
                <option value={r.id} key={r.id}>
                  {r.id} · {r.architecture} · RPC {r.protocol_version}
                </option>
              ))}
            </select>
          </label>
          {!releases.data?.releases.length && (
            <div className="notice">
              控制端尚无可部署的 RPC 发布包，请按运行说明准备匹配版本。
            </div>
          )}
          {!observed && (
            <>
              <label className="check">
                <input
                  type="checkbox"
                  checked={autostart}
                  onChange={(e) => setAutostart(e.target.checked)}
                />
                远端开机后自动启动 RPC，等待控制端加载模型
              </label>
              <label className="check">
                <input
                  type="checkbox"
                  checked={linger}
                  onChange={(e) => setLinger(e.target.checked)}
                />
                必要时启用此账户 linger，保证退出 SSH 后服务仍可运行
              </label>
              {linger && (
                <label>
                  管理员密码（可留空以使用已有权限）
                  <input
                    aria-label="管理员密码"
                    type="password"
                    autoComplete="off"
                    value={sudoPassword}
                    maxLength={4096}
                    onChange={(e) => setSudoPassword(e.target.value)}
                  />
                </label>
              )}
            </>
          )}
          {error && <div className="error">{error}</div>}
          <div className="modal-actions">
            <button type="button" onClick={onClose}>
              取消
            </button>
            <button className="primary" disabled={busy || !release}>
              {busy ? "正在提交…" : observed ? "核验并接管" : "部署并启动"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
