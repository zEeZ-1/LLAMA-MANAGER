import type { Instance } from "../types";
const names = {
  created: "已创建",
  starting: "启动中",
  ready: "就绪",
  stopping: "停止中",
  stopped: "已停止",
  failed: "失败",
};
export function Status({ instance }: { instance: Instance }) {
  return (
    <span className={`badge ${instance.status}`}>
      <span className={`dot ${instance.status}`} />
      {names[instance.status]}
    </span>
  );
}
