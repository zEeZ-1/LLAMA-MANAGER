import "fake-indexeddb/auto";
import { afterEach, expect, it } from "vitest";
import { db, loadMessages, removeSession } from "./chat";
import { ChatDatabase } from "./chat";
import Dexie from "dexie";

afterEach(async () => {
  await db.messages.clear();
  await db.sessions.clear();
  await db.attachments.clear();
});

it("upgrades version-one text history without clearing sessions or messages", async () => {
  const name = "v3-migration-test";
  const old = new Dexie(name);
  old
    .version(1)
    .stores({
      sessions: "id, updated_at",
      messages: "id, session_id, [session_id+created_at]",
    });
  await old
    .table("messages")
    .put({ id: "old", session_id: "s", content: "keep me", created_at: 1 });
  old.close();
  const migrated = new ChatDatabase(name);
  try {
    await migrated.open();
    expect((await migrated.messages.get("old"))?.content).toBe("keep me");
    expect(await migrated.attachments.count()).toBe(0);
  } finally {
    migrated.close();
    await Dexie.delete(name);
  }
});

it("keeps image blobs through reopening and deletes only the selected session's attachments", async () => {
  const blob = new Blob(["image bytes"], { type: "image/png" });
  for (const id of ["a", "b"])
    await db.attachments.put({
      id,
      session_id: id,
      blob,
      thumbnail: blob,
      server_id: id,
      sha256: id,
      size_bytes: blob.size,
      width: 1,
      height: 1,
      mime_type: blob.type,
      created_at: 1,
    });
  db.close();
  await db.open();
  expect(await (await db.attachments.get("a"))?.blob.text()).toBe(
    "image bytes",
  );
  await removeSession("a");
  expect(await db.attachments.get("a")).toBeUndefined();
  expect(await db.attachments.get("b")).toBeDefined();
});
it("retains history, recovers interrupted responses and deletes session messages together", async () => {
  const session = {
    id: "s",
    title: "Hello",
    instance_id: "gone",
    node_id: "local",
    model_name: "Test",
    created_at: 1,
    updated_at: 1,
  };
  await db.sessions.put(session);
  await db.messages.put({
    id: "m",
    session_id: "s",
    role: "assistant",
    content: "partial answer",
    status: "streaming",
    created_at: 2,
  });
  db.close();
  await db.open();
  const history = await loadMessages("s");
  expect(history[0].content).toBe("partial answer");
  expect(history[0].status).toBe("failed");
  expect((await db.sessions.get("s"))?.instance_id).toBe("gone");
  await removeSession("s");
  expect(await db.messages.count()).toBe(0);
  expect(await db.sessions.count()).toBe(0);
});
