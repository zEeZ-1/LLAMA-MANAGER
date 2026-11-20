import type { RequestContent } from "../types";
import { HttpError } from "./api";
/** SSE decoder supports CRLF, multi-line data and UTF-8 split across chunks. */
export class SSEDecoder {
  private buffer = "";
  push(text: string): string[] {
    this.buffer += text;
    if (this.buffer.length > 4 * 1024 * 1024)
      throw new Error("流式响应事件过大");
    const events: string[] = [];
    while (true) {
      const boundary = /\r?\n\r?\n/.exec(this.buffer);
      if (!boundary) break;
      const block = this.buffer.slice(0, boundary.index);
      this.buffer = this.buffer.slice(boundary.index + boundary[0].length);
      const data = block
        .split(/\r?\n/)
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).replace(/^ /, ""))
        .join("\n");
      if (data) events.push(data);
    }
    return events;
  }
}

export async function streamChat(
  instanceId: string,
  messages: { role: string; content: RequestContent }[],
  signal: AbortSignal,
  onDelta: (text: string, reasoning: boolean) => void,
) {
  const response = await fetch(
    `/api/v1/instances/${encodeURIComponent(instanceId)}/chat/completions`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal,
      body: JSON.stringify({ messages, stream: true }),
    },
  );
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new HttpError(
      response.status,
      typeof body.detail === "object"
        ? body.detail.message || JSON.stringify(body.detail)
        : body.detail || `请求失败 (${response.status})`,
    );
  }
  if (!response.body) throw new Error("浏览器不支持流式响应");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  const sse = new SSEDecoder();
  let doneEvent = false;
  try {
    while (!doneEvent) {
      const { done, value } = await reader.read();
      const events = sse.push(decoder.decode(value, { stream: !done }));
      for (const event of events) {
        if (event === "[DONE]") {
          doneEvent = true;
          break;
        }
        const json = JSON.parse(event);
        if (json.error) throw new Error(json.error.message || "推理中断");
        const choice = json.choices?.[0];
        if (choice?.delta?.reasoning_content)
          onDelta(choice.delta.reasoning_content, true);
        if (choice?.delta?.content) onDelta(choice.delta.content, false);
      }
      if (done) break;
    }
    if (!doneEvent) throw new Error("连接中断：未收到生成完成标记");
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
