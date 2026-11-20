import { useEffect, useState } from "react";
import { db } from "../database/chat";

export function AttachmentImage({ id }: { id: string }) {
  const [thumb, setThumb] = useState("");
  const [full, setFull] = useState("");
  const [error, setError] = useState("");
  const [dimensions, setDimensions] = useState("");
  useEffect(() => {
    let alive = true,
      url = "";
    db.attachments
      .get(id)
      .then((a) => {
        if (!alive) return;
        if (!a) {
          setError("图片已丢失");
          return;
        }
        url = URL.createObjectURL(a.thumbnail);
        setThumb(url);
        setDimensions(`${a.width} × ${a.height}`);
      })
      .catch(() => {
        if (alive) setError("无法读取图片");
      });
    return () => {
      alive = false;
      if (url) URL.revokeObjectURL(url);
    };
  }, [id]);
  useEffect(
    () => () => {
      if (full) URL.revokeObjectURL(full);
    },
    [full],
  );
  async function open() {
    try {
      const a = await db.attachments.get(id);
      if (a) setFull(URL.createObjectURL(a.blob));
    } catch {
      setError("无法读取图片");
    }
  }
  return (
    <>
      {thumb ? (
        <button
          type="button"
          className="attachment-preview"
          aria-label="查看图片"
          title={`送入模型的图片：${dimensions}`}
          onClick={() => void open()}
        >
          <img src={thumb} alt="消息附件" loading="lazy" />
        </button>
      ) : (
        <span className="muted">{error || "读取图片…"}</span>
      )}
      {full && (
        <div className="modal-backdrop" onClick={() => setFull("")}>
          <section
            className="modal image-viewer"
            role="dialog"
            aria-label="图片预览"
          >
            <button type="button" onClick={() => setFull("")}>
              关闭图片
            </button>
            <img src={full} alt="完整图片" />
          </section>
        </div>
      )}
    </>
  );
}
