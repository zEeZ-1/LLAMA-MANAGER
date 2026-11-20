# llama.cpp Inference Manager

轻量的单机推理工作台，按 [V1 设计](docs/development_v1.md) 实现节点信息、GGUF 自动发现、独立实例管理、流式 Chat 和本地多会话。界面参考 EXO 的深色工作台、节点卡片和双栏对话交互，未引入 EXO 的运行时依赖。

生产运行只有一个 FastAPI 进程，直接提供 React 静态构建产物；每个实例单独启动一个普通模式 `llama-server`。管理层不加载模型权重，不依赖 PyTorch、Transformers、Redis 或常驻 Node 服务。

当前可运行版本为 V1。下一版的 [V2 开发设计](docs/development_v2.md) 已规划 SSH 集中接入、远端 RPC 自动部署与启停、多机设备分配，以及 Jetson 共享内存和缺失指标的显示修正；这些 V2 功能尚未实现。

## 快速开始

要求 Python 3.12+、Node.js 22.12+（仅开发 / 构建）和可用的 llama.cpp。

```bash
cd /home/nvidia/llama-inference-manager
uv venv --python 3.12 .venv
uv pip sync --python .venv/bin/python backend/requirements.lock
cd frontend
npm ci
npm run build
cd ..
cp config/config.example.yaml config/config.yaml
```

没有 uv 时，可用 `python3 -m venv .venv` 和 `.venv/bin/python -m pip install -r backend/requirements.lock`。

复用已有 llama.cpp 源码 / 构建（不修改该目录）：

```bash
python3 scripts/setup_runtime.py --source /path/to/llama.cpp
```

也可运行 `python3 scripts/setup_runtime.py`，下载验收所用的固定上游提交，再自行编译：

```bash
cmake -S inference_backends/llama.cpp -B inference_backends/llama.cpp/build -DGGML_CUDA=OFF
cmake --build inference_backends/llama.cpp/build --target llama-server -j 2
```

CUDA 环境将 `GGML_CUDA` 改成 `ON`。也可以通过 `LIM_LLAMA_SERVER` 指向现有可执行文件；无需把绝对路径写入源代码。

将**完整、单文件 GGUF** 放进 `models/`，支持任意子目录；或在 `config/config.yaml` 的 `models.directories` 中添加已有目录。符号链接只能指向同一允许目录内部，不能通过 `models/` 中的链接访问外部模型。

```bash
.venv/bin/python -m backend.app
```

打开 **http://127.0.0.1:8000**，在模型页面创建实例，等待「就绪」后打开对话。CPU 是默认模式（GPU layers = 0），需要 GPU 时在创建表单调整。每个实例有自己的上下文、端口、日志和状态；加载同一文件的多个实例仍可能重复占用显存。

## 配置

默认读取 `config/config.yaml`，不存在时使用内置默认值。所有相对路径以项目根目录为基准，和当前工作目录无关。

| 环境变量 | 覆盖内容 |
| --- | --- |
| `LIM_CONFIG` | YAML 配置文件 |
| `LIM_LLAMA_SERVER` | llama-server 可执行文件 |
| `LIM_MODEL_DIRS` | 模型目录列表，使用操作系统路径分隔符，Linux 为 `:` |
| `LIM_DATABASE` | SQLite 数据库路径 |
| `LIM_LOG_DIR` | 实例日志目录 |

完整配置见 [config.example.yaml](config/config.example.yaml)。推理实例强制监听 `127.0.0.1`。管理端默认只监听本机，无账户和远程节点能力；V1 面向可信本机使用。需要局域网访问时应配置管理端监听地址及外部认证代理。

## 生命周期与会话

- GGUF 经过至少两次稳定观察和张量数据完整性检查后入库；文件监听 + 30 秒定期扫描兜底，扫描 / 解析在工作线程运行。
- 创建实例后异步等待 `/health`，通过才进入 READY。加载失败、超时和异常退出保留实例及错误，可在详情查看日志。
- 销毁会先停止接收新推理请求，再终止进程；超时强杀，确认退出后设为 STOPPED。重复销毁幂等，历史记录保留。
- **关闭管理后端会保留推理进程**。重启时从 SQLite 恢复，核对 PID、进程创建时间和完整启动参数，再重新健康检查。退出前如需释放模型内存，请先销毁实例。
- Chat 会话和消息存于当前浏览器 IndexedDB；刷新恢复，删除会话同时删除消息，销毁实例不删除会话。原实例不可用时，可选另一个 READY 实例继续。
- 生成中切换页面会中止请求并保存已有输出。刷新 / 关闭导致未完成的消息会在重开时标记为中断。

## 内存预算

目标：**llama-server 之外的管理后端 + 单个工作台浏览器的内存低于 1 GiB**。测量口径和结果见 [验收报告](docs/validation_v1.md)。这不是将 llama-server 的模型权重、KV cache 或 GPU 分配计入管理层预算。

控制措施：单 worker、静态前端、共享 HTTP 连接池、默认最多 8 路 Chat、2 MiB 请求上限、4 MiB 响应上限、64 KiB 级网络读取、元数据定向读取、有界日志尾部及按需加载 Markdown。默认最多 16 个活动实例；实例本身的推理内存由模型和参数决定。

```bash
.venv/bin/python scripts/check_memory.py --pid <管理后端PID> --seconds 30
.venv/bin/python scripts/check_memory.py --pid <管理后端PID> --browser-pid <专用浏览器PID> --seconds 30
```

脚本对独立浏览器的完整进程树和管理后端进程树求 RSS 总和，排除 `llama-server`，超过预算返回非零。RSS 会重复统计共享页，是保守口径；同时报告 USS / PSS。超大历史、额外浏览器标签和扩展会增加占用，不能用一次测量承诺任意工作负载都低于预算。

## 开发与验证

```bash
uv pip sync --python .venv/bin/python backend/requirements-dev.lock
.venv/bin/python -m pytest -q
cd frontend
npm test
npm run build
npm run dev
```

开发时后端仍监听 8000，Vite 监听 5173，通过同源代理访问 `/api`。只运行一个后端 worker；数据库文件锁会拒绝第二个管理进程。不要用多个 Uvicorn workers。

可选浏览器测试需要独立测试空间和小模型，不在生产服务安装浏览器依赖：

```bash
python3 -m venv /tmp/lim-browser-qa
/tmp/lim-browser-qa/bin/pip install playwright
/tmp/lim-browser-qa/bin/playwright install chromium
/tmp/lim-browser-qa/bin/python scripts/browser_smoke.py --url http://127.0.0.1:8000 --model-id <小模型ID>
```

测试会创建和销毁名为 Browser QA 的实例，验证会话后删除测试会话；应仅对测试环境运行。

更多内容：[架构](docs/architecture.md) · [API](docs/api.md) · [开发说明](docs/development.md)。
