import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  Plus,
  Trash2,
  Send,
  Square,
  MessageSquare,
  PanelLeft,
  PanelRight,
  X,
  ArrowUpRight,
  Code2,
  Lightbulb,
  BookOpen,
  UserRound,
  ImagePlus,
} from "lucide-react";
import { BrandMark } from "../components/BrandMark";
import { ClusterPanel } from "../components/ClusterPanel";
import { db, loadMessages, removeSession } from "../database/chat";
import { useInstances, errorText, HttpError } from "../services/api";
import { streamChat } from "../services/sse";
import { prepareImageMessages } from "../services/images";
import { AttachmentImage } from "../components/AttachmentImage";
import { ImageAttachments } from "../components/ImageAttachments";
import { useUI } from "../services/ui";
import type { ChatMessage, ChatSession } from "../types";

export default function Chat() {
  const instances = useInstances(false);
  const [historyOpen, setHistoryOpen] = useState(() => window.innerWidth > 800);
  const [resourcesOpen, setResourcesOpen] = useState(
    () => window.innerWidth > 1280,
  );
  const [viewportWidth, setViewportWidth] = useState(() => window.innerWidth);
  const historyRef = useRef<HTMLElement>(null);
  const resourceRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const resize = () => {
      setViewportWidth(window.innerWidth);
      if (window.innerWidth <= 800) setHistoryOpen(false);
      if (window.innerWidth <= 1100) setResourcesOpen(false);
    };
    window.addEventListener("resize", resize);
    return () => window.removeEventListener("resize", resize);
  }, []);
  const drawerOpen =
    (historyOpen && viewportWidth <= 800) ||
    (resourcesOpen && viewportWidth <= 1100);
  useEffect(() => {
    if (!drawerOpen) return;
    const previous = document.activeElement as HTMLElement | null;
    const panel =
      historyOpen && viewportWidth <= 800
        ? historyRef.current
        : resourceRef.current;
    const focusable = () =>
      Array.from(
        panel?.querySelectorAll<HTMLElement>(
          'button:not(:disabled), [href], input, select, textarea, [tabindex="0"]',
        ) || [],
      );
    focusable()[0]?.focus();
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setHistoryOpen(false);
        setResourcesOpen(false);
      }
      if (event.key === "Tab") {
        const list = focusable();
        const first = list[0],
          last = list[list.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last?.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first?.focus();
        }
      }
    };
    window.addEventListener("keydown", key);
    return () => {
      window.removeEventListener("keydown", key);
      previous?.focus();
    };
  }, [drawerOpen, historyOpen, viewportWidth]);
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
  const [attachmentIds, setAttachmentIds] = useState<string[]>([]);
  const unsentAttachments = useRef<string[]>([]);
  const [imageFiles, setImageFiles] = useState<File[]>([]);
  const [imagePending, setImagePending] = useState(false);
  const [draftEpoch, setDraftEpoch] = useState(0);
  const draftSession = useRef(crypto.randomUUID());
  const fileInput = useRef<HTMLInputElement>(null);
  function addImages(files: File[]) {
    if (!files.length) return;
    if (busyRef.current || !selectedReady?.capabilities?.image_input) {
      setError("请先选择支持图片的就绪实例");
      return;
    }
    if (files.length + attachmentIds.length > 4) {
      setError("每条消息最多 4 张图片");
      return;
    }
    if (imagePending) {
      setError("请等待当前图片上传完成");
      return;
    }
    setImagePending(true);
    setImageFiles(files);
  }
  function resetDraft() {
    unsentAttachments.current = [];
    setAttachmentIds([]);
    setImageFiles([]);
    setImagePending(false);
    setDraftEpoch((e) => e + 1);
  }
  function discardDraft() {
    void db.attachments.bulkDelete(attachmentIds).catch(() => {});
    resetDraft();
  }
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
    if (busyRef.current || imagePending) return;
    const version = ++selectionVersion.current;
    try {
      const history = await loadMessages(s.id);
      if (!mounted.current || version !== selectionVersion.current) return;
      discardDraft();
      setSession(s);
      if (window.innerWidth <= 800) setHistoryOpen(false);
      setMessages(history);
      setInstanceId(s.instance_id);
      setVisible(100);
      setError("");
    } catch (e) {
      setError(`读取会话失败：${errorText(e)}`);
    }
  }
  useEffect(() => {
    const blocked = () =>
      setError("会话存储升级受阻，请关闭其他工作台标签后重试");
    window.addEventListener("chat-storage-blocked", blocked);
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
      window.removeEventListener("chat-storage-blocked", blocked);
      mounted.current = false;
      controller.current?.abort();
      void db.attachments.bulkDelete(unsentAttachments.current).catch(() => {});
    };
  }, []);
  useEffect(() => {
    if (!instanceId && !session && ready.length) setInstanceId(ready[0].id);
  }, [ready.length, instanceId, session]);
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: busy ? "instant" : "smooth" });
  }, [messages.length, messages.at(-1)?.content, busy]);

  function newChat() {
    if (busyRef.current || imagePending) return;
    ++selectionVersion.current;
    discardDraft();
    draftSession.current = crypto.randomUUID();
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
    if (
      busyRef.current ||
      imagePending ||
      (!input.trim() && !attachmentIds.length) ||
      !selectedReady
    )
      return;
    if (
      !selectedReady.capabilities?.image_input &&
      (attachmentIds.length || messages.some((m) => m.attachment_ids?.length))
    ) {
      setError("当前实例不支持历史图片，请选择图文实例或新建文本会话");
      return;
    }
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
      id: draftSession.current,
      title: input.trim().slice(0, 40) || "图片对话",
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
      attachment_ids: [...attachmentIds],
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
      let requestMessages = await prepareImageMessages(history, abort.signal);
      if (
        new TextEncoder().encode(
          JSON.stringify({ messages: requestMessages, stream: true }),
        ).length >
        2 * 1024 ** 2
      )
        throw new Error("会话已超过请求大小上限，请新建会话。");
      await db.transaction(
        "rw",
        db.sessions,
        db.messages,
        db.attachments,
        async () => {
          await db.sessions.put(current);
          await db.messages.bulkPut([user, assistant]);
        },
      );
      saved = true;
      if (mounted.current) {
        setSession(current);
        setInput("");
        resetDraft();
        updateView();
      }
      await refreshSessions();
      const receive = (text: string, reasoning: boolean) => {
        if (reasoning) assistant.reasoning = (assistant.reasoning || "") + text;
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
      };
      try {
        await streamChat(instanceId, requestMessages, abort.signal, receive);
      } catch (error) {
        if (error instanceof HttpError && error.status === 410) {
          requestMessages = await prepareImageMessages(
            history,
            abort.signal,
            true,
          );
          await streamChat(instanceId, requestMessages, abort.signal, receive);
        } else throw error;
      }
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
    <div
      className={`chat-layout ${historyOpen ? "history-open" : ""} ${resourcesOpen ? "resources-open" : ""}`}
    >
      {drawerOpen && (
        <button
          className="drawer-backdrop"
          aria-label="关闭侧栏"
          tabIndex={-1}
          onClick={() => {
            setHistoryOpen(false);
            setResourcesOpen(false);
          }}
        />
      )}
      {historyOpen && (
        <aside
          className="sessions"
          id="chat-history"
          ref={historyRef}
          aria-label="会话列表"
        >
          <div className="section-heading">
            <span className="eyebrow">YOUR CONVERSATIONS</span>
            <button
              className="icon"
              aria-label="关闭会话列表"
              onClick={() => setHistoryOpen(false)}
            >
              <X size={16} />
            </button>
          </div>
          <button
            className="new-chat"
            disabled={busy || imagePending}
            onClick={newChat}
          >
            <Plus size={16} /> 新建会话
          </button>
          <span className="nav-label">最近会话</span>
          <div className="session-list">
            {sessions.map((s) => (
              <div
                key={s.id}
                className={`session-item ${session?.id === s.id ? "selected" : ""}`}
              >
                <button
                  disabled={busy || imagePending}
                  onClick={() => void selectSession(s)}
                >
                  <MessageSquare size={15} />
                  <span>
                    {s.title}
                    <small>{s.model_name}</small>
                  </span>
                </button>
                <button
                  className="icon"
                  aria-label={`删除会话 ${s.title}`}
                  disabled={busy || imagePending}
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
          <div className="session-footer">
            <span className="dot ready" />
            会话仅保存在此浏览器
          </div>
        </aside>
      )}
      <section className="conversation" inert={drawerOpen}>
        <header className="chat-header">
          <div className="chat-title-group">
            <button
              className="icon"
              aria-label={historyOpen ? "隐藏会话列表" : "显示会话列表"}
              aria-expanded={historyOpen}
              aria-controls="chat-history"
              onClick={() => {
                setHistoryOpen((v) => !v);
                if (window.innerWidth <= 800) setResourcesOpen(false);
              }}
            >
              <PanelLeft size={18} />
            </button>
            <div>
              <span className="eyebrow">CHAT WORKSPACE</span>
              <h2>{session?.title || "新对话"}</h2>
            </div>
          </div>
          <div className="chat-header-controls">
            <label className="instance-select">
              <span className={`dot ${selectedReady ? "ready" : "stopped"}`} />
              <select
                aria-label="选择就绪实例"
                disabled={busy || imagePending}
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
            <button
              className={`icon ${resourcesOpen ? "accent" : ""}`}
              aria-label={resourcesOpen ? "隐藏计算资源" : "显示计算资源"}
              aria-expanded={resourcesOpen}
              aria-controls="chat-resources"
              onClick={() => {
                setResourcesOpen((v) => !v);
                if (window.innerWidth <= 800) setHistoryOpen(false);
              }}
            >
              <PanelRight size={18} />
            </button>
          </div>
        </header>
        <div className="messages" aria-live="polite" aria-busy={busy}>
          {!messages.length && (
            <div className="chat-welcome">
              <div className="welcome-icon">
                <BrandMark size={53} />
              </div>
              <span className="eyebrow">YOUR MODELS. YOUR NETWORK.</span>
              <h1>从一个问题开始。</h1>
              <p>连接你的模型，在自己的设备上思考、创作与探索。</p>
              <div className="suggestions">
                {[
                  {
                    icon: BookOpen,
                    title: "理解一个概念",
                    caption: "把复杂的问题讲简单",
                    text: "用简单的语言解释大模型推理",
                  },
                  {
                    icon: Code2,
                    title: "一起写代码",
                    caption: "从想法到第一行代码",
                    text: "帮我写一段 Python 示例",
                  },
                  {
                    icon: Lightbulb,
                    title: "探索新想法",
                    caption: "为灵感找到下一个方向",
                    text: "一起梳理一个新的想法",
                  },
                ].map((prompt) => (
                  <button
                    key={prompt.title}
                    onClick={() => {
                      setInput(prompt.text);
                      document
                        .querySelector<HTMLTextAreaElement>(
                          ".composer textarea",
                        )
                        ?.focus();
                    }}
                  >
                    <prompt.icon size={19} />
                    <strong>{prompt.title}</strong>
                    <span>{prompt.caption}</span>
                    <ArrowUpRight size={14} />
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
                <i className={`message-avatar ${m.role}`} aria-hidden="true">
                  {m.role === "user" ? (
                    <UserRound size={15} />
                  ) : (
                    <BrandMark size={20} />
                  )}
                </i>
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
              {!!m.attachment_ids?.length && (
                <div className="message-images">
                  {m.attachment_ids.map((id, index) => (
                    <div key={id}>
                      <AttachmentImage id={id} />
                      {m.attachment_ids!.length > 1 && (
                        <small>图片 {index + 1}</small>
                      )}
                    </div>
                  ))}
                </div>
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
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault();
              addImages(Array.from(e.dataTransfer.files));
            }}
            onSubmit={(e) => {
              e.preventDefault();
              void send();
            }}
          >
            <ImageAttachments
              key={`${session?.id || draftSession.current}:${draftEpoch}`}
              sessionId={session?.id || draftSession.current}
              disabled={busy}
              files={imageFiles}
              onChange={(ids) => {
                unsentAttachments.current = ids;
                setAttachmentIds(ids);
              }}
              onPending={setImagePending}
            />
            <input
              ref={fileInput}
              className="sr-only"
              type="file"
              aria-label="选择图片"
              accept="image/jpeg,image/png,image/webp"
              multiple
              onChange={(e) => {
                addImages(Array.from(e.target.files || []));
                e.target.value = "";
              }}
            />
            <textarea
              onPaste={(e) => {
                const files = Array.from(e.clipboardData.files);
                if (files.length) {
                  e.preventDefault();
                  addImages(files);
                }
              }}
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
              <button
                type="button"
                aria-label="添加图片"
                disabled={
                  busy ||
                  imagePending ||
                  !selectedReady?.capabilities?.image_input
                }
                onClick={() => fileInput.current?.click()}
              >
                <ImagePlus size={18} /> 图片
              </button>
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
                  disabled={
                    imagePending ||
                    (!input.trim() && !attachmentIds.length) ||
                    !selectedReady
                  }
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
      {resourcesOpen && (
        <div id="chat-resources" ref={resourceRef}>
          <ClusterPanel onClose={() => setResourcesOpen(false)} />
        </div>
      )}
    </div>
  );
}
