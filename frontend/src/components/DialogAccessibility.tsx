import { useEffect } from "react";

/** Shared keyboard behavior for the existing operational and image dialogs. */
export function DialogAccessibility() {
  useEffect(() => {
    let dialog: HTMLElement | null = null;
    let opener: HTMLElement | null = null;
    const focusable = () =>
      Array.from(
        dialog?.querySelectorAll<HTMLElement>(
          'button:not(:disabled), a[href], input:not(:disabled):not([type="hidden"]), select:not(:disabled), textarea:not(:disabled), summary, [tabindex="0"]',
        ) || [],
      ).filter((el) => el.getClientRects().length > 0);
    const update = () => {
      const dialogs = document.querySelectorAll<HTMLElement>('[role="dialog"]');
      const next = dialogs[dialogs.length - 1] || null;
      if (next === dialog) return;
      if (!dialog) opener = document.activeElement as HTMLElement | null;
      dialog = next;
      if (dialog) focusable()[0]?.focus();
      else if (opener?.isConnected) opener.focus();
    };
    const observer = new MutationObserver(update);
    observer.observe(document.body, { childList: true, subtree: true });
    const key = (event: KeyboardEvent) => {
      if (!dialog) return;
      if (event.key === "Escape") {
        const close =
          dialog.querySelector<HTMLButtonElement>(
            'button[aria-label^="关闭"]:not(:disabled)',
          ) ||
          Array.from(
            dialog.querySelectorAll<HTMLButtonElement>("button:not(:disabled)"),
          ).find((b) => /^(取消|关闭图片)$/.test(b.textContent?.trim() || ""));
        if (close) {
          event.preventDefault();
          event.stopImmediatePropagation();
          close.click();
        }
      }
      if (event.key === "Tab") {
        const list = focusable(),
          first = list[0],
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
    document.addEventListener("keydown", key, true);
    update();
    return () => {
      observer.disconnect();
      document.removeEventListener("keydown", key, true);
    };
  }, []);
  return null;
}
