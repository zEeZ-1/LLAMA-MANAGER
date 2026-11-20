import Dexie, { type Table } from "dexie";
import type { Attachment, ChatMessage, ChatSession } from "../types";

export class ChatDatabase extends Dexie {
  sessions!: Table<ChatSession, string>;
  messages!: Table<ChatMessage, string>;
  attachments!: Table<Attachment, string>;
  constructor(name = "llama-inference-manager") {
    super(name);
    this.version(1).stores({
      sessions: "id, updated_at",
      messages: "id, session_id, [session_id+created_at]",
    });
    this.version(2).stores({
      sessions: "id, updated_at",
      messages: "id, session_id, [session_id+created_at]",
      attachments: "id, session_id, sha256, created_at",
    });
    this.on("blocked", () =>
      window.dispatchEvent(new CustomEvent("chat-storage-blocked")),
    );
  }
}
export const db = new ChatDatabase();
export async function removeSession(id: string) {
  await db.transaction(
    "rw",
    db.sessions,
    db.messages,
    db.attachments,
    async () => {
      await db.messages.where("session_id").equals(id).delete();
      await db.sessions.delete(id);
      await db.attachments.where("session_id").equals(id).delete();
    },
  );
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
