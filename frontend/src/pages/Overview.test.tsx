import { expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { Overview } from "./Overview";
import type { Node } from "../types";

function node(id: string, changes: Partial<Node> = {}): Node {
  return {
    id,
    name: id,
    hostname: id,
    status: "online",
    role: id === "local" ? "controller" : "rpc_worker",
    connection_state: "online",
    os: "Linux",
    architecture: "aarch64",
    cpu_info: { name: "CPU", logical_count: 6, usage_percent: null },
    memory_info: {
      total: 8 * 1024 ** 3,
      used: 3 * 1024 ** 3,
      available: 5 * 1024 ** 3,
      usage_percent: 37.5,
    },
    devices: [
      {
        id: "gpu",
        name: "Integrated GPU",
        memory_total: 8 * 1024 ** 3,
        memory_free: null,
        memory_kind: "shared",
        backend: "CUDA",
        backend_index: 0,
        driver: null,
        compute_state: "unverified",
        properties: { utilization_percent: null },
      },
    ],
    model_count: 0,
    instance_count: 0,
    ready_count: 0,
    manager_memory_bytes: null,
    ...changes,
  };
}
function render(nodes: Node[]) {
  return renderToStaticMarkup(
    <Overview nodes={nodes} models={[]} instances={[]} loading={false} />,
  );
}
it("counts shared system RAM once, excluding stale, offline and unknown totals", () => {
  const html = render([
    node("local"),
    node("edge"),
    node("old", { telemetry_stale: true }),
    node("offline", { status: "offline" }),
    node("unknown", {
      memory_info: {
        total: null,
        used: null,
        available: null,
        usage_percent: null,
      },
    }),
  ]);
  expect(html).toContain("16.0 GiB");
  expect(html).toContain("2 个在线节点 · 共享内存仅计一次");
  expect(html).toContain("数据已过期");
  expect(html).not.toContain("40.0 GiB");
});
it("does not present missing GPU telemetry as an unusable GPU", () => {
  const html = render([node("local")]);
  expect(html).toContain("Integrated GPU");
  expect(html).not.toContain("GPU 不可用");
  expect(html).not.toContain("计算就绪");
  expect(html).toContain("非实时推理流量图");
});
it("labels backend RSS independently of inference memory and does not invent a sample", () => {
  expect(render([node("local")])).toContain("未提供");
  const html = render([
    node("local", { manager_memory_bytes: 64 * 1024 ** 2 }),
  ]);
  expect(html).toContain("64.0 MiB");
  expect(html).toContain("不含推理进程");
});
