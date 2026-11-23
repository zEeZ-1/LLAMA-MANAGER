# AscNet v3.1 验证记录

日期：2026-11-23。范围为 v3.1 WebUI、品牌与版本元数据；继承 [V3 验证记录](validation_v3.md) 中的模型 / 图片链路及边界。

## 自动检查

| 检查 | 结果 |
| --- | --- |
| 后端 `pytest -q` | 71 passed；保留既有 Starlette TestClient 弃用提示 |
| 前端 `npm test` | 5 个测试文件、12 项通过 |
| TypeScript + Vite 生产构建 | 通过；对话 Markdown 保持独立加载 |
| 浏览器隔离回归 | 10 组场景通过，页面 JavaScript 异常为 0 |
| 新增浏览器脚本 Ruff 检查 | 通过 |
| `git diff --check` | 通过 |

新增前端测试检查：共享 GPU 不重复计入系统内存，离线 / 过期 / 未提供总量的节点不参与内存总量汇总；缺失 GPU 指标不等同设备不可用；后端 RSS 缺失与已采样显示正确且口径明确。

## 浏览器验证

使用 Chromium，连接临时管理服务、临时 SQLite 与微型 GGUF 目录。创建、删除、日志、媒体上传、网关和 SSE 均经过真实 HTTP 接口及确定性原生测试子进程。远端节点信息是浏览器路由生成的 TEST-NET 示例，未尝试真实 SSH 登录、RPC 操作或大模型加载。

通过场景：

1. 模型类型筛选、搜索、无匹配状态、不可用模型的禁用创建与原因展开。
2. 从模型卡片创建实例，测试子进程产生 KV 分配失败；列表展示具体原因，详情展示错误片段与退出状态。
3. 从创建表单启动文本和图文实例，状态自动转为就绪。
4. 流式聊天、停止生成、保留部分内容。
5. PNG 二进制上传、媒体 ID 对话、缩略图、图片预览；测试原生进程检查同机 file:// 资源存在。请求 Content-Type 实测为 `image/png`。
6. 刷新页面后仍使用原 IndexedDB 名称，恢复消息和图片附件。
7. 节点角色、共享内存与 RPC 操作入口；添加节点密码表单可打开，Escape 正常关闭。
8. 总览、节点、模型、实例、对话五个页面在 1920、1440、1024、768、390、375 CSS px 下均无文档横向溢出。
9. 手机会话 / 资源抽屉开关、背景不可交互、Tab / Shift+Tab 焦点循环、Escape 与窄屏到桌面尺寸变化后的交互恢复。手机创建弹窗位于视口内，底部提交按钮可通过弹窗内部滚动到达。
10. 确认销毁实例后，记录从现有实例卡片列表移除。

## 复现

先按 README 安装后端开发依赖并构建前端。Playwright 仅是可选验收工具，单独安装，不进入生产后端环境：

```bash
uv venv /tmp/ascnet-browser
uv pip install --python /tmp/ascnet-browser/bin/python playwright==1.65.2
/tmp/ascnet-browser/bin/python -m playwright install chromium
/tmp/ascnet-browser/bin/python scripts/verify_ui_v31.py --dist frontend/dist
```

脚本自动选择本机临时端口、创建隔离配置与模型，完成后终止自己启动的测试后台并删除临时数据；截图和 `browser.json` 写到输出目录。可用 `--output /tmp/ascnet-v31-evidence` 指定目录，`--backend-python` 指定已安装项目开发依赖的解释器。没有连接生产后台的参数。

## 资源和发布状态

前后端生产依赖列表无新增项，后台继续直接提供静态文件；没有引入常驻前端服务或远端 Agent。节点和实例轮询由全局工作空间统一维护，子面板复用查询缓存。

本次未执行真实三机模型负载或管理层峰值内存复测；不将浏览器截图的 RSS 采样值当作完整进程树预算结论。1 GiB 后台预算、图片缓存与共享内存的口径沿用 V3，可使用 README 的 `scripts/check_memory.py` 在实际运行期间复核。

本机 `frontend/dist` 已更新为 v3.1。修改时生产后台处于停止状态，本次保持停机，没有启动生产模型、重启远端 RPC 或修改其服务配置。下一次按原命令启动后台后提供新版界面和 `0.3.1` 健康接口版本。
