export interface Device {
  id: string;
  name: string;
  memory_total: number | null;
  memory_free: number | null;
  properties: { utilization_percent: number | null };
}
export interface Node {
  id: string;
  name: string;
  hostname: string;
  status: string;
  os: string;
  architecture: string;
  cpu_info: { name: string; logical_count: number; usage_percent: number };
  memory_info: {
    total: number;
    used: number;
    available: number;
    usage_percent: number;
  };
  devices: Device[];
  model_count: number;
  instance_count: number;
  ready_count: number;
  manager_memory_bytes: number;
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
}
export interface InstanceConfig {
  context_size: number;
  batch_size: number;
  ubatch_size: number;
  parallel: number;
  threads: number;
  n_gpu_layers: number;
  cache_type_k: string;
  cache_type_v: string;
  extra_options: Record<string, boolean>;
}
export type InstanceStatus =
  | "created"
  | "starting"
  | "ready"
  | "stopping"
  | "stopped"
  | "failed";
export interface Instance {
  id: string;
  name: string;
  node_id: string;
  model_id: string;
  model_name: string;
  runtime_type: string;
  status: InstanceStatus;
  config: InstanceConfig;
  runtime: { pid: number | null; internal_port: number };
  error_message: string | null;
  created_at: string;
  started_at: string | null;
  stopped_at: string | null;
}
export interface ChatMessage {
  id: string;
  session_id: string;
  role: "system" | "user" | "assistant";
  content: string;
  status: "pending" | "streaming" | "completed" | "failed";
  created_at: number;
  error?: string;
  reasoning?: string;
}
export interface ChatSession {
  id: string;
  title: string;
  node_id: string;
  instance_id: string;
  model_name: string;
  created_at: number;
  updated_at: number;
}
