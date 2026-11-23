import { useQuery } from "@tanstack/react-query";
import type { Instance, Model, Node } from "../types";

export class HttpError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...options?.headers },
  });
  if (!response.ok) {
    let detail: unknown;
    try {
      detail = (await response.json()).detail;
    } catch {
      detail = response.statusText;
    }
    throw new HttpError(
      response.status,
      typeof detail === "string"
        ? detail
        : detail && typeof detail === "object" && "message" in detail
          ? String((detail as { message: unknown }).message)
          : JSON.stringify(detail),
    );
  }
  return response.json();
}
export const useNodes = (poll = true) =>
  useQuery({
    queryKey: ["nodes"],
    queryFn: () => api<{ nodes: Node[] }>("/nodes"),
    refetchInterval: poll ? 5000 : false,
  });
export const useModels = () =>
  useQuery({
    queryKey: ["models"],
    queryFn: () =>
      api<{
        models: Model[];
        rejected_files: number;
        last_scan: string | null;
        layout_issues?: {
          relative_path: string;
          code: string;
          message: string;
        }[];
      }>("/models"),
    refetchInterval: 5000,
  });
export const useInstances = (poll = true) =>
  useQuery({
    queryKey: ["instances"],
    queryFn: () => api<{ instances: Instance[] }>("/instances"),
    refetchInterval: poll ? 2000 : false,
  });
export const bytes = (n: number | null | undefined) =>
  n == null
    ? "未提供"
    : n >= 1024 ** 3
      ? `${(n / 1024 ** 3).toFixed(1)} GiB`
      : `${(n / 1024 ** 2).toFixed(1)} MiB`;
export const errorText = (e: unknown) =>
  e instanceof Error ? e.message : String(e);

export const percent = (value: number | null | undefined) =>
  value == null ? "尚未采集" : `${value.toFixed(1)}%`;
export const gpuMemory = (device: import("../types").Device) =>
  device.memory_kind === "shared"
    ? "共享系统内存"
    : device.memory_total == null
      ? "显存指标未提供"
      : `显存可用 ${bytes(device.memory_free)} / ${bytes(device.memory_total)}`;
