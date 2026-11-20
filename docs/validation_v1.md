# V1 验收记录

日期：2026-11-20（Asia/Shanghai）。

本轮实现覆盖设计文档六个里程碑：项目骨架、Node / Model、独立 Instance、Chat 网关、浏览器多会话及生命周期稳定性。测试在独立临时模型目录、数据库、日志及端口运行，没有修改或停止机器上原有的推理服务。

## 环境与范围

- Linux aarch64 / NVIDIA Orin，系统 RAM 约 7.4 GiB。
- Python 3.13.14，Node.js 22.23.4，React 19 / Vite 6，Chromium Headless 159。
- 复用本机 llama.cpp：`9214bdb554544c35e4a9e2c2e8b75d8cfee330ab`，普通 server 模式；上游源码未修改。
- 真实双实例使用上游测试模型 [stories15M-q4_0.gguf](https://huggingface.co/ggml-org/models-moved/blob/main/tinyllamas/stories15M-q4_0.gguf)，19,077,344 字节，CPU 推理。
- 实例配置：context 512、parallel 1、threads 2、GPU layers 0。双实例 API 验证 batch 64 / ubatch 32；浏览器创建的第三个实例使用表单默认 batch 512 / ubatch 128。
- 另对机器现有 Qwen3 4B Q8_0、Qwen3 14B Q4_K_M、Qwen3 14B Q8_0 完成 GGUF 元数据和张量完整性解析；未为这些大模型新建实例。

## 自动化检查

| 检查 | 结果 |
| --- | --- |
| `python -m pytest -q` | 12 passed |
| `npm test` | 4 passed |
| `npm run build` | TypeScript 与生产构建通过 |
| `npm audit` | 0 vulnerabilities |
| 真实双实例创建、健康检查、独立端口 | 通过 |
| 两实例真实 SSE Chat | 通过，均收到 `[DONE]` |
| 管理后端重启恢复 | 通过，两实例 PID 保持一致，重新健康检查后 READY |
| 独立销毁及幂等销毁 | 通过，确认进程退出 |
| 浏览器完整流程 | 7 类操作通过，0 页面脚本错误 |

后端测试覆盖：目录新增 / 替换 / 删除、稳定窗口、截断张量数据、符号链接越界、双子进程、JSON 与 SSE、参数白名单、模型删除后保留运行实例、启动超时、启动异常退出、运行期崩溃、优雅停止超时后的强杀、重启恢复及进程身份不匹配、请求大小 / Origin / Host 边界、实时首段转发、并发限制和断连清理（含上游响应头到达前取消）。

前端测试覆盖：跨数据包 CRLF / 多行 SSE、UTF-8 多字节拆包、缺失完成标记、IndexedDB 刷新恢复、中断消息恢复和会话级联删除。

测试输出有 1 条 Starlette TestClient 对 httpx 的上游弃用提示，不影响本轮测试结果；运行服务不使用 TestClient。

## 浏览器验收

使用真实后端和真实推理实例，完成：

1. 总览、节点详情和资源展示。
2. 模型目录刷新、创建实例、等待 READY、打开运行日志。
3. 发送消息、实时显示、停止生成。
4. 刷新页面后恢复 IndexedDB 会话。
5. 销毁原实例后保留消息并选择其他 READY 实例。
6. 390 × 844 手机视口，无页面水平溢出。
7. 删除会话并同步删除消息。

证据：[操作结果](validation/v1/browser.json)、[总览截图](validation/v1/dashboard.png)、[桌面对话](validation/v1/chat-desktop.png)、[手机对话](validation/v1/chat-mobile.png)。

## 内存与负载

测量采用 `scripts/check_memory.py`，对管理后端和**专用 Chromium 的完整进程树**求 RSS 总和，排除 llama-server。浏览器测试驱动、构建进程和机器原有的其他应用不属于生产工作台，不计入管理层预算。重启后由系统接管的两个推理进程也不在管理进程树中。

| 测量 | 结果 |
| --- | --- |
| 管理后端单独采样（10 秒） | RSS 峰值约 55.5 MiB |
| 持续负载期间采样（30 秒） | 管理后端 + Chromium RSS 总和峰值 **522.64 MiB** |
| 上述总和峰值时管理后端 | 57.63 MiB |
| 上述峰值时 USS 总和 | 178.45 MiB |
| 上述峰值时 PSS 总和 | 278.25 MiB |
| 预算 | 1,024 MiB，**通过** |
| 七路流式请求，45 秒 | **1,257 完成，0 错误，0 限流** |
| 单请求双实例链路的首个 SSE 事件 | 分别约 0.098 / 0.095 秒 |

RSS 对共享页面重复计数，因此该求和比物理内存实际增量更保守。峰值包含 Chromium 主进程、renderer、GPU / 网络等辅助进程。采样期间后台对两个真实实例施加七路流式负载，并运行浏览器对话。压力请求每次最多生成 24 tokens，浏览器 Chat 通过第三个实例生成。

原始证据：[memory.json](validation/v1/memory.json)、[stress.json](validation/v1/stress.json)。这验证当前单机、小会话和单浏览器标签下的预算；未执行数小时泄漏测试，也不代表任意历史规模、扩展或多标签场景都有同样占用。llama-server 的权重、KV cache、显存 / 统一内存分配由模型参数决定，不属于本项预算。

前端构建总量约 600 KiB，gzip 后 JS / CSS / HTML 合计约 185 KiB；Chat / Markdown 按需分块。生产环境不启动 Vite 或 Node 服务。

## 复测

```bash
.venv/bin/python -m pytest -q
cd frontend
npm test
npm run build
npm audit
cd ..
.venv/bin/python scripts/stress_gateway.py --url http://127.0.0.1:8000 --instance <测试实例A> --instance <测试实例B> --seconds 45 --concurrency 7
```

浏览器与 30 秒内存复测，在另一终端运行：

```bash
/tmp/lim-browser-qa/bin/python scripts/browser_smoke.py --url http://127.0.0.1:8000 --model-id <小模型ID> --manager-pid <管理后端PID> --memory-seconds 30
```

浏览器测试会创建 `Browser QA ...` 测试实例并在结束后销毁；压力脚本仅访问显式指定的实例。生产默认模型目录仍为项目内 `models/`，验收下载的小模型保留在 `/tmp` 验收目录，不混入正式模型目录。
