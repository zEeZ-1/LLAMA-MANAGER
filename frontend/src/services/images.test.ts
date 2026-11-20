import "fake-indexeddb/auto";
import { afterEach, expect, it, vi } from "vitest";
import { db } from "../database/chat";
import { prepareImageMessages } from "./images";
import type { ChatMessage } from "../types";

afterEach(async () => {
  vi.unstubAllGlobals();
  await db.attachments.clear();
});
it("preserves ordered image references and never puts Blob or Base64 in the chat JSON", async () => {
  const blob = new Blob(["bytes"], { type: "image/png" });
  await db.attachments.put({
    id: "a",
    session_id: "s",
    server_id: "server-id",
    blob,
    thumbnail: blob,
    sha256: "digest",
    size_bytes: 5,
    width: 1,
    height: 1,
    mime_type: "image/png",
    created_at: 1,
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response("{}", {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
    ),
  );
  const message: ChatMessage = {
    id: "m",
    session_id: "s",
    role: "user",
    content: "look",
    attachment_ids: ["a"],
    status: "completed",
    created_at: 1,
  };
  const prepared = await prepareImageMessages(
    [message],
    new AbortController().signal,
  );
  expect(prepared).toEqual([
    {
      role: "user",
      content: [
        { type: "text", text: "look" },
        { type: "image", image_id: "server-id" },
      ],
    },
  ]);
  expect(JSON.stringify(prepared)).not.toContain("base64");
  await expect(
    prepareImageMessages(Array(9).fill(message), new AbortController().signal),
  ).rejects.toThrow("8");
  await db.attachments.clear();
  await expect(
    prepareImageMessages([message], new AbortController().signal),
  ).rejects.toThrow("丢失");
});
