export interface Device {
  id: string;
  name: string;
  memory_total: number | null;
  memory_free: number | null;
  properties: { utilization_percent: number | null };
  backend: string;
  backend_index: number;
  memory_kind: "shared" | "dedicated" | "unknown";
  driver: string | null;
  compute_state: "ready" | "unverified" | "error";
}
export interface Node {
  id: string;
  name: string;
  hostname: string;
  status: string;
  role: "controller" | "rpc_worker";
  address?: string;
  connection_state: string;
  ssh?: { port: number; username: string; credential_id: string | null };
  identity?: {
    machine_id_hash: string | null;
    ssh_host_key_fingerprint: string | null;
  };
  hardware_model?: string | null;
  sampled_at?: string;
  telemetry_stale?: boolean;
  inventory?: Record<string, unknown>;
  last_error?: { code: string; message: string } | null;
  rpc_workers?: Worker[];
  rpc_discovery?: {
    state: string;
    count?: number;
    sampled_at?: string;
    suggested_port?: number | null;
    error?: { code: string; message: string } | null;
  };
  auth_state?: {
    mode: string;
    automatic: boolean;
    warning?: string | null;
  } | null;
  os: string;
  architecture: string;
  cpu_info: {
    name: string;
    logical_count: number | null;
    usage_percent: number | null;
  };
  memory_info: {
    total: number | null;
    used: number | null;
    available: number | null;
    usage_percent: number | null;
  };
  devices: Device[];
  model_count: number;
  instance_count: number;
  ready_count: number;
  manager_memory_bytes: number | null;
}
export interface Model {
  id: string;
  name: string;
  filename: string;
  relative_path: string;
  format: string;
  size_bytes: number;
  architecture: string | null;
  node_id: string;
  metadata: Record<string, unknown>;
  kind: string;
  availability: string;
  revision: string;
  default_variant_id?: string;
  variants: {
    id: string;
    name: string;
    relative_path: string;
    projector_id?: string | null;
  }[];
  projectors: { id: string; relative_path: string }[];
  detection: { reasons: string[]; verified: boolean };
}
export interface InstanceConfig {
  variant_id?: string;
  model_revision?: string;
  modality?: "text" | "image_text";
  vision_device_id?: string;
  image_max_tokens?: number;
  context_size: number;
  batch_size: number;
  ubatch_size: number;
  parallel: number;
  threads: number;
  n_gpu_layers: number;
  cache_type_k: string;
  cache_type_v: string;
  extra_options: Record<string, boolean>;
  placement?: { mode: "local" | "rpc"; devices: PlacementDevice[] };
}
export type InstanceStatus =
  | "created"
  | "starting"
  | "ready"
  | "stopping"
  | "stopped"
  | "failed";
export interface Instance {
  capabilities?: {
    image_input: boolean;
    input_modalities: string[];
    output_modalities: string[];
  };
  id: string;
  name: string;
  node_id: string;
  model_id: string;
  model_name: string;
  runtime_type: string;
  status: InstanceStatus;
  config: InstanceConfig;
  runtime: {
    pid: number | null;
    internal_port: number;
    placement?: {
      worker_ids: string[];
      device_names: string[];
      rpc_endpoints: string[];
      tensor_split: number[];
    };
  };
  error_message: string | null;
  created_at: string;
  started_at: string | null;
  stopped_at: string | null;
}
export interface ChatMessage {
  attachment_ids?: string[];
  id: string;
  session_id: string;
  role: "system" | "user" | "assistant";
  content: string;
  status: "pending" | "streaming" | "completed" | "failed";
  created_at: number;
  error?: string;
  reasoning?: string;
}
export interface Attachment {
  id: string;
  session_id: string;
  blob: Blob;
  thumbnail: Blob;
  server_id: string;
  sha256: string;
  size_bytes: number;
  width: number;
  height: number;
  mime_type: string;
  created_at: number;
}
export type RequestContent =
  | string
  | ({ type: "text"; text: string } | { type: "image"; image_id: string })[];
export interface ChatSession {
  id: string;
  title: string;
  node_id: string;
  instance_id: string;
  model_name: string;
  created_at: number;
  updated_at: number;
}

export interface PlacementDevice {
  device_id: string;
  worker_id: string | null;
  weight: number;
}
export interface Worker {
  autostart?: boolean;
  id: string;
  node_id: string;
  device_ids: string[];
  endpoint: { host: string; port: number };
  management_mode: string;
  controller_id: string | null;
  service_name: string | null;
  state: string;
  release_id: string | null;
  runtime_commit?: string | null;
  protocol_version: string | null;
  process_identity: { pid: number; start_time: string; boot_id: string } | null;
  claim?: { instance_id: string; state: string } | null;
  last_error?: { code: string; message: string } | null;
  rss_bytes?: number;
  origin?: "existing" | "created";
  usable?: boolean;
  client_count?: number;
  bind_address?: string;
  can_delete?: boolean;
  can_start?: boolean;
  device_names?: string[] | null;
  service_scope?: string;
}
export interface Job {
  id: string;
  node_id: string;
  worker_id?: string | null;
  operation: string;
  state: string;
  phase: string;
  message: string;
  error: { code: string; message: string } | null;
  created_at: string;
  updated_at: string;
}
export interface Release {
  id: string;
  commit: string;
  protocol_version: string;
  architecture: string;
  platform: string;
  supported_hardware?: string;
}
