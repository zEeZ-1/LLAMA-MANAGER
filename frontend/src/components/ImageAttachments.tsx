import { useEffect, useRef, useState } from "react";
import { saveAttachment } from "../services/images";
import { db } from "../database/chat";
import { errorText } from "../services/api";
import { AttachmentImage } from "./AttachmentImage";

type Draft = {
  id: string;
  file: File;
  status: "uploading" | "ready" | "failed";
  progress: number;
  attachment?: string;
  error?: string;
};
export function ImageAttachments({
  sessionId,
  disabled,
  onChange,
  onPending,
  files,
}: {
  sessionId: string;
  disabled: boolean;
  onChange: (ids: string[]) => void;
  onPending: (pending: boolean) => void;
  files: File[];
}) {
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const current = useRef<Draft[]>([]);
  const controls = useRef(new Map<string, AbortController>());
  const alive = useRef(true);
  const callbacks = useRef({ onChange, onPending });
  callbacks.current = { onChange, onPending };
  function update(next: Draft[]) {
    current.current = next;
    if (alive.current) {
      setDrafts([...next]);
      callbacks.current.onChange(
        next.flatMap((d) => (d.attachment ? [d.attachment] : [])),
      );
      callbacks.current.onPending(next.some((d) => d.status !== "ready"));
    }
  }
  function patch(id: string, value: Partial<Draft>) {
    update(current.current.map((d) => (d.id === id ? { ...d, ...value } : d)));
  }
  async function upload(draft: Draft) {
    const control = new AbortController();
    controls.current.set(draft.id, control);
    patch(draft.id, { status: "uploading", error: undefined });
    try {
      const a = await saveAttachment(
        draft.file,
        sessionId,
        control.signal,
        (p) => patch(draft.id, { progress: p }),
      );
      if (!alive.current || !current.current.some((d) => d.id === draft.id)) {
        await db.attachments.delete(a.id);
        return;
      }
      patch(draft.id, { status: "ready", attachment: a.id });
    } catch (error) {
      if (alive.current && !control.signal.aborted)
        patch(draft.id, { status: "failed", error: errorText(error) });
    } finally {
      controls.current.delete(draft.id);
    }
  }
  useEffect(() => {
    const added = files
      .slice(0, Math.max(0, 4 - current.current.length))
      .map((file) => ({
        id: crypto.randomUUID(),
        file,
        status: "uploading" as const,
        progress: 0,
      }));
    if (!added.length) return;
    update([...current.current, ...added]);
    void (async () => {
      for (const draft of added) {
        if (!alive.current) break;
        if (current.current.some((d) => d.id === draft.id)) await upload(draft);
      }
    })();
  }, [files]);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      for (const c of controls.current.values()) c.abort();
    };
  }, []);
  async function remove(draft: Draft) {
    controls.current.get(draft.id)?.abort();
    if (draft.attachment) await db.attachments.delete(draft.attachment);
    update(current.current.filter((d) => d.id !== draft.id));
  }
  return (
    <div className="draft-images">
      {drafts.map((d) => (
        <div className="draft-image" key={d.id}>
          {d.attachment ? (
            <AttachmentImage id={d.attachment} />
          ) : (
            <span>
              {d.status === "uploading"
                ? d.progress < 100
                  ? `上传 ${d.progress}%`
                  : "验证图片…"
                : d.error}
            </span>
          )}
          <small>{d.file.name}</small>
          {d.status === "failed" && (
            <button
              type="button"
              onClick={() => void upload(d)}
              disabled={disabled}
            >
              重试上传
            </button>
          )}
          <button
            type="button"
            aria-label={`移除图片 ${d.file.name}`}
            disabled={disabled}
            onClick={() => void remove(d)}
          >
            {d.status === "uploading" ? "取消上传" : "移除"}
          </button>
        </div>
      ))}
    </div>
  );
}
