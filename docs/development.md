# 开发说明（V2）

范围依据 [V2 设计](development_v2.md)，保留 V1 的单机与 Chat 能力。实现、隔离验证和真实硬件待验收项见 [validation_v2.md](validation_v2.md)。

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

新增 runtime：实现 `RuntimeAdapter` 并注册到实例管理器，分离参数构造与进程控制；前端继续使用 Instance ID。V2 在本机继续监督 llama-server，并用独立 SSH transport 控制远端 RPC；推理数据不经过 SSH 命令管道。

测试使用本地假运行时 subprocess 提供健康和 SSE 端点，不需要下载权重；真实推理验收使用独立临时目录及小 GGUF，不触碰用户现有运行服务。浏览器依赖仅安装到独立 QA 环境。

不要为生产添加 `--reload` 或多个 workers。默认托管入口在管理后端退出时终止全部受管 llama-server，远端 RPC 保持待命；不要使用裸 Uvicorn 冒充正式入口。历史实例记录保留在数据库中，不自动删除。


V2 新增模块：remote/ssh_transport.py（OpenSSH 池与凭据桥）、remote/scripts.py（固定所属服务脚本）、remote/releases.py（发布清单校验）、nodes/telemetry.py（统一指标）、services/remote_manager.py（节点管理）、deployment_service.py（部署 / 接管）、resource_manager.py（预留与释放）、job_manager.py（有界任务），以及 runtime/rpc_probe.py（固定上游 v10 协议）。不要为采集引入 CUDA Python 库或远端 Agent。

生产依赖保持不变。额外 QA 依赖通过 `scripts/requirements-qa.txt` 安装到独立虚拟环境；qa_ssh_server.py 只模拟 Linux 系统管理 / inventory，实际 SSH 是 OpenSSH 连接 AsyncSSH 测试服务，模型计算由原生 ggml-rpc-server 和 llama-server 完成。模拟器的 systemd 操作不能替代真实远端 systemd 验收，测试报告必须区分。

修改退出策略时，应同时验证普通停止、SIGKILL、远端 RPC 待命与历史记录不自动重载。测试用专属配置 / 数据库 / 目录 / systemd 单元，不使用 killall / pkill 操作用户模型服务。发布包、私钥和真实配置不提交仓库。部署 shell 的暂存 / 校验 / 回滚在临时 HOME 和替代 systemctl 下直接执行测试，不能只测试模拟 SSH 的字符串分支。
