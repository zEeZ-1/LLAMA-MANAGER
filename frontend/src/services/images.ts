import { db } from "../database/chat";
import { api, HttpError } from "./api";
import type { Attachment, ChatMessage, RequestContent } from "../types";

type Media = {
  id: string;
  sha256: string;
  size_bytes: number;
  width: number;
  height: number;
  mime_type: string;
  content_url: string;
  thumbnail_url: string;
};

export function uploadImage(
  blob: Blob,
  signal: AbortSignal,
  progress: (value: number) => void,
  normalized = false,
): Promise<Media> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const abort = () => xhr.abort();
    if (signal.aborted) {
      reject(new DOMException("已取消", "AbortError"));
      return;
    }
    signal.addEventListener("abort", abort, { once: true });
    const done = () => signal.removeEventListener("abort", abort);
    xhr.open(
      "POST",
      "/api/v1/media/images" + (normalized ? "?representation=normalized" : ""),
    );
    xhr.setRequestHeader(
      "Content-Type",
      blob.type || "application/octet-stream",
    );
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) progress(Math.round((e.loaded / e.total) * 100));
    };
    xhr.onload = () => {
      done();
      try {
        const data = JSON.parse(xhr.responseText);
        if (xhr.status >= 400)
          reject(
            new HttpError(
              xhr.status,
              typeof data.detail === "object"
                ? data.detail.message
                : data.detail,
            ),
          );
        else resolve(data);
      } catch {
        reject(new Error("图片上传响应无效"));
      }
    };
    xhr.onerror = () => {
      done();
      reject(new Error("图片上传失败，请检查连接"));
    };
    xhr.onabort = () => {
      done();
      reject(new DOMException("已取消上传", "AbortError"));
    };
    xhr.send(blob);
  });
}

export async function saveAttachment(
  file: File,
  session_id: string,
  signal: AbortSignal,
  progress: (value: number) => void,
): Promise<Attachment> {
  if (file.size > 8 * 1024 ** 2) throw new Error("单张图片不得超过 8 MiB");
  const result = await uploadImage(file, signal, progress);
  const responses = await Promise.all([
    fetch(result.content_url, { signal }),
    fetch(result.thumbnail_url, { signal }),
  ]);
  if (responses.some((r) => !r.ok))
    throw new Error("无法读取已上传图片，请重试");
  const [blob, thumbnail] = await Promise.all(responses.map((r) => r.blob()));
  const attachment: Attachment = {
    ...result,
    id: crypto.randomUUID(),
    session_id,
    blob,
    thumbnail,
    server_id: result.id,
    created_at: Date.now(),
  };
  await db.transaction("rw", db.attachments, async () => {
    const used = (await db.attachments.toArray()).reduce(
      (n, a) => n + a.blob.size + a.thumbnail.size,
      0,
    );
    if (used + blob.size + thumbnail.size > 256 * 1024 ** 2)
      throw new Error("本地图片存储已达 256 MiB，请整理旧会话");
    await db.attachments.put(attachment);
  });
  return attachment;
}

export async function prepareImageMessages(
  history: ChatMessage[],
  signal: AbortSignal,
  force = false,
): Promise<{ role: string; content: RequestContent }[]> {
  const included = history.filter(
    (m) =>
      (m.content || m.attachment_ids?.length) &&
      (m.status === "completed" || m.role === "user"),
  );
  const ids = included.flatMap((m) => m.attachment_ids || []);
  if (ids.length > 8) throw new Error("完整历史最多包含 8 张图片，请新建会话");
  const media = new Map<string, Attachment>();
  let total = 0;
  for (const id of ids) {
    const attachment = media.get(id) || (await db.attachments.get(id));
    if (!attachment) throw new Error("历史图片已丢失，请重新添加图片");
    total += attachment.size_bytes;
    if (total > 16 * 1024 ** 2)
      throw new Error("历史图片合计超过 16 MiB，请新建会话");
    if (media.has(id)) continue;
    try {
      if (force) throw new HttpError(410, "过期");
      await api(`/media/images/${attachment.server_id}`, { signal });
    } catch (error) {
      if (!(error instanceof HttpError) || ![404, 410].includes(error.status))
        throw error;
      const restored = await uploadImage(
        attachment.blob,
        signal,
        () => {},
        true,
      );
      if (restored.sha256 !== attachment.sha256)
        throw new Error("恢复图片内容不一致，未发送请求");
      attachment.server_id = restored.id;
      await db.attachments.put(attachment);
    }
    media.set(id, attachment);
  }
  return included.map((m) => ({
    role: m.role,
    content: m.attachment_ids?.length
      ? [
          ...(m.content ? [{ type: "text" as const, text: m.content }] : []),
          ...m.attachment_ids.map((id) => ({
            type: "image" as const,
            image_id: media.get(id)!.server_id,
          })),
        ]
      : m.content,
  }));
}
