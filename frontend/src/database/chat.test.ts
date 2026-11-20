import "fake-indexeddb/auto";
import { afterEach, expect, it } from "vitest";
import { db, loadMessages, removeSession } from "./chat";

afterEach(async () => {
  await db.messages.clear();
  await db.sessions.clear();
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
