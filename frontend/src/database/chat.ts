import Dexie, { type Table } from "dexie";
import type { ChatMessage, ChatSession } from "../types";

class ChatDatabase extends Dexie {
  sessions!: Table<ChatSession, string>;
  messages!: Table<ChatMessage, string>;
  constructor() {
    super("llama-inference-manager");
    this.version(1).stores({
      sessions: "id, updated_at",
      messages: "id, session_id, [session_id+created_at]",
    });
  }
}
export const db = new ChatDatabase();
export async function removeSession(id: string) {
  await db.transaction("rw", db.sessions, db.messages, async () => {
    await db.messages.where("session_id").equals(id).delete();
    await db.sessions.delete(id);
  });
}
export async function loadMessages(id: string) {
  const messages = await db.messages
    .where("session_id")
    .equals(id)
    .sortBy("created_at");
  const interrupted = messages.filter(
    (m) => m.status === "pending" || m.status === "streaming",
  );
  for (const m of interrupted) {
    m.status = "failed";
    m.error = "上次生成已中断，可以继续发送消息。";
  }
  if (interrupted.length) await db.messages.bulkPut(interrupted);
  return messages;
}
