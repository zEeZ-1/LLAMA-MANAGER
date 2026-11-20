import { describe, expect, it } from "vitest";
import { SSEDecoder, streamChat } from "./sse";

describe("SSE parsing", () => {
  it("preserves partial CRLF and multiline events", () => {
    const sse = new SSEDecoder();
    expect(sse.push('data: {"a":')).toEqual([]);
    expect(sse.push("1}\r\n\r")).toEqual([]);
    expect(sse.push("\ndata: [DONE]\n\n")).toEqual(['{"a":1}', "[DONE]"]);
    expect(sse.push(": heartbeat\n\ndata: first\ndata: second\n\n")).toEqual([
      "first\nsecond",
    ]);
  });
  it("streams UTF-8 text split across packets and closes the reader", async () => {
    const original = globalThis.fetch;
    const raw = new TextEncoder().encode(
      'data: {"choices":[{"delta":{"content":"你好"}}]}\n\ndata: [DONE]\n\n',
    );
    globalThis.fetch = async () =>
      new Response(
        new ReadableStream({
          start(c) {
            for (const byte of raw) c.enqueue(new Uint8Array([byte]));
            c.close();
          },
        }),
      );
    const output: string[] = [];
    try {
      await streamChat("test", [], new AbortController().signal, (t) =>
        output.push(t),
      );
      expect(output.join("")).toBe("你好");
    } finally {
      globalThis.fetch = original;
    }
  });
  it("rejects a truncated stream instead of marking it complete", async () => {
    const original = globalThis.fetch;
    globalThis.fetch = async () => new Response('data: {"choices":[]}\n\n');
    try {
      await expect(
        streamChat("test", [], new AbortController().signal, () => {}),
      ).rejects.toThrow("未收到");
    } finally {
      globalThis.fetch = original;
    }
  });
});
