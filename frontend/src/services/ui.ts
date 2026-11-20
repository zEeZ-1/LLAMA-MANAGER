import { create } from "zustand";
export type Page = "dashboard" | "nodes" | "models" | "instances" | "chat";
interface UI {
  page: Page;
  navigate: (page: Page) => void;
  createModel: string | null;
  openCreate: (model?: string) => void;
  closeCreate: () => void;
  chatInstance: string | null;
  openChat: (id: string) => void;
}
export const useUI = create<UI>((set) => ({
  page: "dashboard",
  navigate: (page) => set({ page }),
  createModel: null,
  openCreate: (model = "") => set({ createModel: model }),
  closeCreate: () => set({ createModel: null }),
  chatInstance: null,
  openChat: (id) => set({ page: "chat", chatInstance: id }),
}));
