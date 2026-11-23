import { create } from "zustand";
interface UI {
  centerView: "nodes" | "chat";
  activeSessionId: string | null;
  chatInstance: string | null;
  chatRequest: number;
  chatLocked: boolean;
  showNodes: () => void;
  openConversation: (id: string) => void;
  newConversation: () => void;
  openChat: (id: string) => void;
  createModel: string | null;
  createRequest: number;
  createPending: boolean;
  openCreate: (model?: string) => void;
  closeCreate: () => void;
}
export const useUI = create<UI>((set, get) => ({
  centerView: "nodes",
  activeSessionId: null,
  chatInstance: null,
  chatRequest: 0,
  chatLocked: false,
  showNodes: () => set({ centerView: "nodes" }),
  openConversation: (id) => {
    if (!get().chatLocked)
      set((s) => ({
        centerView: "chat",
        activeSessionId: id,
        chatInstance: null,
        chatRequest: s.chatRequest + 1,
      }));
  },
  newConversation: () => {
    if (!get().chatLocked)
      set((s) => ({
        centerView: "chat",
        activeSessionId: null,
        chatInstance: null,
        chatRequest: s.chatRequest + 1,
      }));
  },
  openChat: (id) => {
    if (!get().chatLocked)
      set((s) => ({
        centerView: "chat",
        activeSessionId: null,
        chatInstance: id,
        chatRequest: s.chatRequest + 1,
      }));
  },
  createModel: null,
  createRequest: 0,
  createPending: false,
  openCreate: (model = "") => {
    if (!get().createPending)
      set((s) => ({ createModel: model, createRequest: s.createRequest + 1 }));
  },
  closeCreate: () => set({ createModel: null }),
}));
