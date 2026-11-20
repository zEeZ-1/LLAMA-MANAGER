import { useQuery } from "@tanstack/react-query";
import type { Instance, Model, Node } from "../types";

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
    throw new Error(
      typeof detail === "string" ? detail : JSON.stringify(detail),
    );
  }
  return response.json();
}
export const useNodes = () =>
  useQuery({
    queryKey: ["nodes"],
    queryFn: () => api<{ nodes: Node[] }>("/nodes"),
    refetchInterval: 5000,
  });
export const useModels = () =>
  useQuery({
    queryKey: ["models"],
    queryFn: () =>
      api<{
        models: Model[];
        rejected_files: number;
        last_scan: string | null;
      }>("/models"),
    refetchInterval: 5000,
  });
export const useInstances = () =>
  useQuery({
    queryKey: ["instances"],
    queryFn: () => api<{ instances: Instance[] }>("/instances"),
    refetchInterval: 2000,
  });
export const bytes = (n: number | null | undefined) =>
  n == null
    ? "不可用"
    : n >= 1024 ** 3
      ? `${(n / 1024 ** 3).toFixed(1)} GiB`
      : `${(n / 1024 ** 2).toFixed(1)} MiB`;
export const errorText = (e: unknown) =>
  e instanceof Error ? e.message : String(e);
