import { expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { InstanceFailure } from "./InstanceFailure";

it("renders the returned cause, native excerpt and exit status as escaped text", () => {
  const html = renderToStaticMarkup(
    <InstanceFailure
      instance={{
        error_message: "old generic error",
        error_detail: {
          code: "runtime_memory_allocation_failed",
          message: "远端申请 960 MiB KV 缓存失败",
          log_excerpt: "E failed to allocate <script>alert(1)</script>",
          exit_code: 17,
        },
      }}
    />,
  );
  expect(html).toContain("远端申请 960 MiB KV 缓存失败");
  expect(html).toContain("错误详情");
  expect(html).toContain("进程退出码：17");
  expect(html).toContain("&lt;script&gt;");
  expect(html).not.toContain("<script>");
  expect(html).not.toContain("old generic error");
});

it("supports old failure records and omits empty diagnostics", () => {
  expect(
    renderToStaticMarkup(
      <InstanceFailure instance={{ error_message: "旧错误" }} compact />,
    ),
  ).toContain("失败原因：旧错误");
  expect(
    renderToStaticMarkup(
      <InstanceFailure instance={{ error_message: null }} />,
    ),
  ).toBe("");
});
