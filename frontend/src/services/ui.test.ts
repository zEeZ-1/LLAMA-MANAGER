import { beforeEach, expect, it } from "vitest";
import { useUI } from "./ui";

beforeEach(() =>
  useUI.setState({
    centerView: "nodes",
    activeSessionId: null,
    chatInstance: null,
    chatRequest: 0,
    chatLocked: false,
    createModel: null,
    createRequest: 0,
    createPending: false,
  }),
);

it("inspecting nodes preserves the current conversation request and instance draft", () => {
  useUI.getState().openConversation("saved-session");
  useUI.getState().openCreate("vision-model");
  const request = useUI.getState().chatRequest;
  useUI.getState().showNodes();
  expect(useUI.getState()).toMatchObject({
    centerView: "nodes",
    activeSessionId: "saved-session",
    chatRequest: request,
    createModel: "vision-model",
  });
  useUI.getState().closeCreate();
  expect(useUI.getState().activeSessionId).toBe("saved-session");
});

it("locks conversation changes during a stream or upload while allowing node controls", () => {
  useUI.getState().openConversation("in-flight");
  useUI.setState({ chatLocked: true });
  const request = useUI.getState().chatRequest;
  useUI.getState().openConversation("other-session");
  useUI.getState().newConversation();
  useUI.getState().openChat("other-instance");
  expect(useUI.getState()).toMatchObject({
    activeSessionId: "in-flight",
    chatInstance: null,
    chatRequest: request,
  });
  useUI.getState().showNodes();
  useUI.getState().openCreate();
  expect(useUI.getState()).toMatchObject({
    centerView: "nodes",
    createModel: "",
  });
});

it("explicitly starting a conversation chooses the requested instance", () => {
  useUI.getState().openConversation("old-session");
  useUI.getState().openChat("vision-instance");
  expect(useUI.getState()).toMatchObject({
    centerView: "chat",
    activeSessionId: null,
    chatInstance: "vision-instance",
    chatRequest: 2,
  });
  useUI.getState().newConversation();
  expect(useUI.getState()).toMatchObject({
    chatInstance: null,
    activeSessionId: null,
    chatRequest: 3,
  });
});

it("a new model choice replaces the draft even when the originally selected model is chosen again", () => {
  useUI.getState().openCreate("vision");
  const first = useUI.getState().createRequest;
  useUI.getState().openCreate("vision");
  expect(useUI.getState().createRequest).toBe(first + 1);
  useUI.setState({ createPending: true });
  useUI.getState().openCreate("other-model");
  expect(useUI.getState()).toMatchObject({
    createModel: "vision",
    createRequest: first + 1,
  });
});
