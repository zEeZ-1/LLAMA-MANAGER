import { useEffect, useState } from "react";
import { liveQuery } from "dexie";
import { MessageSquare, Plus, Search, Trash2, X, Network } from "lucide-react";
import { BrandMark } from "./BrandMark";
import { db, removeSession } from "../database/chat";
import { errorText } from "../services/api";
import { useUI } from "../services/ui";
import type { ChatSession } from "../types";

export function ConversationList({ onClose }: { onClose: () => void }) {
  const ui = useUI();
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [search, setSearch] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    const subscription = liveQuery(() =>
      db.sessions.orderBy("updated_at").reverse().toArray(),
    ).subscribe({
      next: setSessions,
      error: (e) => setError(`无法读取会话：${errorText(e)}`),
    });
    return () => subscription.unsubscribe();
  }, []);
  async function remove(session: ChatSession) {
    if (ui.chatLocked || !window.confirm(`删除会话「${session.title}」？`))
      return;
    try {
      await removeSession(session.id);
      if (useUI.getState().activeSessionId === session.id) ui.newConversation();
    } catch (e) {
      setError(errorText(e));
    }
  }
  const filtered = sessions.filter((s) =>
    `${s.title} ${s.model_name}`.toLowerCase().includes(search.toLowerCase()),
  );
  return (
    <aside className="conversation-list" aria-label="会话列表">
      <div className="conversation-brand">
        <button
          className="ascnet-brand"
          aria-label="AscNet 节点工作空间"
          onClick={() => {
            ui.showNodes();
            onClose();
          }}
        >
          <BrandMark size={30} />
          <span>AscNet</span>
          <span className="version-tag">v3.2</span>
        </button>
        <button
          className="icon pane-close"
          aria-label="关闭会话列表"
          onClick={onClose}
        >
          <X size={17} />
        </button>
      </div>
      <button
        className="new-conversation"
        disabled={ui.chatLocked}
        onClick={() => {
          ui.newConversation();
          onClose();
        }}
      >
        <Plus size={17} />
        新建会话
      </button>
      <div className="conversation-search search">
        <Search size={14} />
        <input
          aria-label="搜索会话"
          placeholder="搜索会话…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>
      <div className="session-list-label">
        <span>最近会话</span>
        <span className="mono">
          {sessions.length.toString().padStart(2, "0")}
        </span>
      </div>
      {error && (
        <div className="error" role="alert">
          {error}
        </div>
      )}
      <div className="conversation-items">
        {filtered.map((s) => (
          <div
            className={`conversation-item ${ui.activeSessionId === s.id ? "selected" : ""}`}
            key={s.id}
          >
            <button
              disabled={ui.chatLocked}
              aria-label={`打开会话 ${s.title}`}
              onClick={() => {
                ui.openConversation(s.id);
                onClose();
              }}
            >
              <MessageSquare size={15} />
              <span>
                {s.title}
                <small>{s.model_name}</small>
              </span>
              {ui.chatLocked && ui.activeSessionId === s.id && (
                <i className="dot starting" />
              )}
            </button>
            <button
              className="icon"
              aria-label={`删除会话 ${s.title}`}
              disabled={ui.chatLocked}
              onClick={() => void remove(s)}
            >
              <Trash2 size={14} />
            </button>
          </div>
        ))}
        {!filtered.length && (
          <div className="conversation-empty">
            <MessageSquare size={24} />
            <p>{sessions.length ? "没有匹配的会话" : "从一次对话开始"}</p>
            <small>
              {sessions.length
                ? "试试其他关键词。"
                : "选择右侧就绪实例，或新建一个会话。"}
            </small>
          </div>
        )}
      </div>
      <div className="conversation-bottom">
        <button
          onClick={() => {
            ui.showNodes();
            onClose();
          }}
        >
          <Network size={15} />
          计算网络<span>查看节点</span>
        </button>
        <p>
          <span className="dot ready" />
          会话保存在当前浏览器
        </p>
      </div>
    </aside>
  );
}
