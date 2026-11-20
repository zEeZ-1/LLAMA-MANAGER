# V1 开发说明

范围依据 [development_v1.md](development_v1.md)。当前包含全部六个里程碑的单机实现。版本行为与已验证证据见 [validation_v1.md](validation_v1.md)。

| 目录 | 职责 |
| --- | --- |
| `backend/app/core` | YAML 配置、请求限制、单管理端锁 |
| `backend/app/schemas` | InstanceConfig、创建请求、文本 Chat 请求校验 |
| `backend/app/services` | 节点信息、模型目录、GGUF、实例状态和推理网关 |
| `backend/app/runtime` | RuntimeAdapter 与 llama.cpp 参数构造 |
| `backend/app/process` | 子进程控制、身份核验、日志维护、端口分配 |
| `backend/app/repositories` | SQLite 实例持久化 |
| `frontend/src/pages` | 管理页面和按需加载的 Chat |
| `frontend/src/database` | Dexie 会话摘要 / 消息存储及中断恢复 |
| `frontend/src/services` | API、SSE 解析和 Zustand 导航状态 |
| `backend/tests` | 文件系统、进程、恢复、网关和 API 集成测试 |
| `scripts` | 固定运行时准备、内存采样和可选浏览器验收 |

轻量实现取舍：保留 React、TypeScript、Vite、TanStack Query、Zustand、Dexie、FastAPI、httpx、watchdog、psutil。UI 使用原生 CSS 和 Lucide 图标，避免为少量控件引入整套 UI 系统；SQLite 使用标准库仓储，避免额外 ORM。生产构建不需要 Node 进程。未使用 CDN 字体、图片或第三方分析服务。

新增 runtime：实现 `RuntimeAdapter` 并注册到实例管理器，分离参数构造与进程控制；前端继续使用 Instance ID。远程节点扩展还需替换本地进程监督协议，V1 未提前实现。

测试使用本地假运行时 subprocess 提供健康和 SSE 端点，不需要下载权重；真实推理验收使用独立临时目录及小 GGUF，不触碰用户现有运行服务。浏览器依赖仅安装到独立 QA 环境。

不要为生产添加 `--reload` 或多个 workers。关闭管理进程保留运行时，显式 DELETE 实例才释放推理内存。历史实例记录保留在数据库中，不自动删除。
