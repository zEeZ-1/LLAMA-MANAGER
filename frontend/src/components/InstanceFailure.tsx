import type { Instance } from "../types";

export function InstanceFailure({
  instance,
  compact = false,
}: {
  instance: Pick<Instance, "error_message" | "error_detail">;
  compact?: boolean;
}) {
  const message = instance.error_detail?.message || instance.error_message;
  if (!message) return null;
  if (compact)
    return (
      <small className="error-text instance-failure-summary" title={message}>
        失败原因：{message}
      </small>
    );
  return (
    <div className="error instance-failure" role="alert">
      <strong>失败原因</strong>
      <div>{message}</div>
      {instance.error_detail?.exit_code != null && (
        <small>进程退出码：{instance.error_detail.exit_code}</small>
      )}
      {instance.error_detail?.log_excerpt && (
        <details>
          <summary>错误详情</summary>
          <pre>{instance.error_detail.log_excerpt}</pre>
        </details>
      )}
    </div>
  );
}
