import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Plus, Trash2, Send, Square, MessageSquare, Cpu } from "lucide-react";
import { db, loadMessages, removeSession } from "../database/chat";
import { useInstances, errorText } from "../services/api";
import { streamChat } from "../services/sse";
import { useUI } from "../services/ui";
import type { ChatMessage, ChatSession } from "../types";

export default function Chat() {
  const instances = useInstances();
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [session, setSession] = useState<ChatSession | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [instanceId, setInstanceId] = useState(
    useUI.getState().chatInstance || "",
  );
  const [input, setInput] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [visible, setVisible] = useState(100);
  const controller = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const busyRef = useRef(false);
  const selectionVersion = useRef(0);
  const mounted = useRef(true);
  const ready =
    instances.data?.instances.filter((i) => i.status === "ready") || [];
  const selectedReady = ready.find((i) => i.id === instanceId);

  async function refreshSessions() {
    const list = await db.sessions.orderBy("updated_at").reverse().toArray();
    if (mounted.current) setSessions(list);
    return list;
  }
  async function selectSession(s: ChatSession) {
    if (busyRef.current) return;
    const version = ++selectionVersion.current;
    try {
      const history = await loadMessages(s.id);
      if (!mounted.current || version !== selectionVersion.current) return;
      setSession(s);
      setMessages(history);
      setInstanceId(s.instance_id);
      setVisible(100);
      setError("");
    } catch (e) {
      setError(`读取会话失败：${errorText(e)}`);
    }
  }
  useEffect(() => {
    mounted.current = true;
    const preferred = useUI.getState().chatInstance;
    useUI.setState({ chatInstance: null });
    refreshSessions()
      .then((list) => {
        if (!preferred && list.length && mounted.current)
          void selectSession(list[0]);
      })
      .catch((e) => setError(`无法打开会话存储：${errorText(e)}`));
    return () => {
      mounted.current = false;
      controller.current?.abort();
    };
  }, []);
  useEffect(() => {
    if (!instanceId && !session && ready.length) setInstanceId(ready[0].id);
  }, [ready.length, instanceId, session]);
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: busy ? "instant" : "smooth" });
  }, [messages.length, messages.at(-1)?.content, busy]);

  function newChat() {
    if (busyRef.current) return;
    ++selectionVersion.current;
    setSession(null);
    setMessages([]);
    setInput("");
    setError("");
    setVisible(100);
    if (!selectedReady) setInstanceId(ready[0]?.id || "");
  }
  async function changeInstance(id: string) {
    setInstanceId(id);
    if (session) {
      const model = ready.find((i) => i.id === id);
      const updated = {
        ...session,
        instance_id: id,
        model_name: model?.model_name || "",
        node_id: model?.node_id || "local",
        updated_at: Date.now(),
      };
      setSession(updated);
      try {
        await db.sessions.put(updated);
        await refreshSessions();
      } catch (e) {
        setError(errorText(e));
      }
    }
  }
  async function deleteChat(s: ChatSession) {
    if (busyRef.current || !window.confirm(`删除会话「${s.title}」？`)) return;
    try {
      await removeSession(s.id);
      const list = await refreshSessions();
      if (session?.id === s.id) {
        newChat();
        if (list[0]) await selectSession(list[0]);
      }
    } catch (e) {
      setError(errorText(e));
    }
  }

  async function send() {
    if (busyRef.current || !input.trim() || !selectedReady) return;
    if (messages.length >= 499) {
      setError("当前会话已达到 500 条消息，请新建会话。");
      return;
    }
    busyRef.current = true;
    setBusy(true);
    setError("");
    const abort = new AbortController();
    controller.current = abort;
    const now = Date.now();
    const current = session || {
      id: crypto.randomUUID(),
      title: input.trim().slice(0, 40),
      node_id: selectedReady.node_id,
      instance_id: instanceId,
      model_name: selectedReady.model_name,
      created_at: now,
      updated_at: now,
    };
    current.updated_at = now;
    const user: ChatMessage = {
      id: crypto.randomUUID(),
      session_id: current.id,
      role: "user",
      content: input.trim(),
      status: "completed",
      created_at: now,
    };
    const assistant: ChatMessage = {
      id: crypto.randomUUID(),
      session_id: current.id,
      role: "assistant",
      content: "",
      status: "streaming",
      created_at: now + 1,
    };
    const history = [...messages, user];
    let lastSaved = 0;
    let saved = false;
    let persistence = Promise.resolve();
    const updateView = () => {
      if (mounted.current) setMessages([...history, { ...assistant }]);
    };
    try {
      const requestMessages = history
        .filter(
          (m) => m.content && (m.status === "completed" || m.role === "user"),
        )
        .map(({ role, content }) => ({ role, content }));
      if (
        new TextEncoder().encode(
          JSON.stringify({ messages: requestMessages, stream: true }),
        ).length >
        2 * 1024 ** 2
      )
        throw new Error("会话已超过请求大小上限，请新建会话。");
      await db.transaction("rw", db.sessions, db.messages, async () => {
        await db.sessions.put(current);
        await db.messages.bulkPut([user, assistant]);
      });
      saved = true;
      if (mounted.current) {
        setSession(current);
        setInput("");
        updateView();
      }
      await refreshSessions();
      await streamChat(
        instanceId,
        requestMessages,
        abort.signal,
        (text, reasoning) => {
          if (reasoning)
            assistant.reasoning = (assistant.reasoning || "") + text;
          else assistant.content += text;
          const timestamp = Date.now();
          if (timestamp - lastSaved > 150) {
            lastSaved = timestamp;
            updateView();
            const snapshot = { ...assistant };
            persistence = persistence
              .then(() => db.messages.put(snapshot))
              .then(() => {});
            // Keep a rejected write observed until final cleanup reports the storage error.
            void persistence.catch(() => {});
          }
        },
      );
      assistant.status = "completed";
    } catch (e) {
      assistant.status = abort.signal.aborted ? "completed" : "failed";
      assistant.error = abort.signal.aborted ? "已停止生成" : errorText(e);
      if (mounted.current) setError(assistant.error);
    } finally {
      try {
        if (saved) {
          await persistence;
          await db.messages.put({ ...assistant });
          await db.sessions.update(current.id, { updated_at: Date.now() });
          await refreshSessions();
        }
      } catch (e) {
        if (mounted.current) setError(`会话保存失败：${errorText(e)}`);
      }
      if (mounted.current) {
        if (saved) updateView();
        setBusy(false);
      }
      busyRef.current = false;
      controller.current = null;
    }
  }

  return (
    <div className="chat-layout">
      <aside className="sessions">
        <div className="section-heading">
          <h2>对话</h2>
          <button
            className="icon"
            aria-label="新建会话"
            disabled={busy}
            onClick={newChat}
          >
            <Plus size={20} />
          </button>
        </div>
        <button className="new-chat" disabled={busy} onClick={newChat}>
          <Plus size={16} /> 新建会话
        </button>
        <span className="nav-label">最近会话</span>
        <div className="session-list">
          {sessions.map((s) => (
            <div
              key={s.id}
              className={`session-item ${session?.id === s.id ? "selected" : ""}`}
            >
              <button disabled={busy} onClick={() => void selectSession(s)}>
                <MessageSquare size={15} />
                <span>
                  {s.title}
                  <small>{s.model_name}</small>
                </span>
              </button>
              <button
                className="icon"
                aria-label={`删除会话 ${s.title}`}
                disabled={busy}
                onClick={() => void deleteChat(s)}
              >
                <Trash2 size={14} />
              </button>
            </div>
          ))}
          {!sessions.length && (
            <p className="muted">你的对话会自动保存在本机浏览器。</p>
          )}
        </div>
        <div className="session-footer">本地会话 · IndexedDB</div>
      </aside>
      <section className="conversation">
        <header className="chat-header">
          <div>
            <span className="eyebrow">CHAT WORKSPACE</span>
            <h2>{session?.title || "新对话"}</h2>
          </div>
          <label className="instance-select">
            <span className={`dot ${selectedReady ? "ready" : "stopped"}`} />
            <select
              aria-label="选择就绪实例"
              disabled={busy}
              value={instanceId}
              onChange={(e) => void changeInstance(e.target.value)}
            >
              <option value="">选择就绪实例</option>
              {instanceId && !selectedReady && (
                <option value={instanceId}>原实例不可用，请切换</option>
              )}
              {ready.map((i) => (
                <option key={i.id} value={i.id}>
                  {i.name}
                </option>
              ))}
            </select>
          </label>
        </header>
        <div className="messages" aria-live="polite" aria-busy={busy}>
          {!messages.length && (
            <div className="chat-welcome">
              <div className="welcome-icon">
                <Cpu size={34} />
              </div>
              <span className="eyebrow">YOUR MODELS. YOUR MACHINE.</span>
              <h1>让想法开始对话。</h1>
              <p>选择一个就绪实例，开启属于你的本地 AI 对话。</p>
              <div className="suggestions">
                {[
                  "用简单的语言解释大模型推理",
                  "帮我写一段 Python 示例",
                  "一起梳理一个新的想法",
                ].map((text) => (
                  <button key={text} onClick={() => setInput(text)}>
                    {text}
                  </button>
                ))}
              </div>
            </div>
          )}
          {messages.length > visible && (
            <button
              className="load-history"
              onClick={() => setVisible(visible + 100)}
            >
              显示更早的消息
            </button>
          )}
          {messages.slice(-visible).map((m) => (
            <article key={m.id} className={`message ${m.role}`}>
              <div className="message-author">
                {m.role === "user"
                  ? "你"
                  : m.role === "system"
                    ? "System"
                    : session?.model_name || "Assistant"}
                <span>{m.status === "streaming" ? "正在生成" : ""}</span>
              </div>
              {m.reasoning && (
                <details className="reasoning">
                  <summary>思考过程</summary>
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {m.reasoning}
                  </ReactMarkdown>
                </details>
              )}
              <div className="markdown">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {m.content || (m.status === "streaming" ? "正在思考…" : "")}
                </ReactMarkdown>
              </div>
              {m.error && <p className="error-text">{m.error}</p>}
            </article>
          ))}
          <div ref={bottom} />
        </div>
        <div className="composer-area">
          {(error || instances.error) && (
            <div className="error" role="alert">
              {error || errorText(instances.error)}
            </div>
          )}
          {!selectedReady && (
            <div className="notice">
              {ready.length
                ? "原实例不可用。选择其他就绪实例，可以保留历史继续对话。"
                : "暂无就绪实例。请先创建推理实例并等待模型加载完成。"}
            </div>
          )}
          <form
            className="composer"
            onSubmit={(e) => {
              e.preventDefault();
              void send();
            }}
          >
            <textarea
              aria-label="输入消息"
              placeholder="输入消息，Enter 发送，Shift + Enter 换行…"
              value={input}
              maxLength={500000}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (
                  e.key === "Enter" &&
                  !e.shiftKey &&
                  !e.nativeEvent.isComposing
                ) {
                  e.preventDefault();
                  void send();
                }
              }}
            />
            <div className="composer-bottom">
              <span>{selectedReady?.model_name || "等待实例就绪"}</span>
              {busy ? (
                <button
                  type="button"
                  aria-label="停止生成"
                  onClick={() => controller.current?.abort()}
                >
                  <Square size={15} /> 停止生成
                </button>
              ) : (
                <button
                  className="primary icon"
                  aria-label="发送消息"
                  disabled={!input.trim() || !selectedReady}
                  type="submit"
                >
                  <Send size={18} />
                </button>
              )}
            </div>
          </form>
          <p className="composer-note">
            消息保存在当前浏览器 · 生成内容仅供参考
          </p>
        </div>
      </section>
    </div>
  );
}
