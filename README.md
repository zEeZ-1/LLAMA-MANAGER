# llama.cpp Inference Manager

轻量的 llama.cpp 推理工作台，支持单机和 SSH 集中管理的多机 RPC。包含节点信息、GGUF 自动发现、实例管理、流式 Chat 和本地多会话。界面参考 EXO 的深色工作台、节点卡片和双栏对话交互。

生产运行只有一个 FastAPI 进程，直接提供 React 静态构建产物；每个实例单独启动一个普通模式 `llama-server`。管理层不加载模型权重，不依赖 PyTorch、Transformers、Redis 或常驻 Node 服务。

V2 已实现密码接入与自动密钥引导、主机指纹确认、远端配置采集、匹配发布包部署、RPC 自动发现、新建 / 删除 / 启停 / 日志 / 缓存，以及按设备顺序和权重创建多机实例。远端无需安装管理 Agent。Jetson 显示共享系统内存，显存指标缺失不会被显示为 GPU 不可用。测试范围和真实硬件联调状态见 [V2 验证记录](docs/validation_v2.md)。

V3 Image 的目录规范、自动类型识别、多模态实例及前后端图片链路见 [V3 开发设计](docs/development_v3.md)。该版本目前处于设计阶段，以下快速开始仍对应已实现的 V2。

## 快速开始

要求 Linux、可用的 systemd 用户服务、OpenSSH 客户端（ssh/scp/ssh-keyscan/ssh-keygen）、Python 3.12+、Node.js 22.12+（仅开发 / 构建）和可用的 llama.cpp。默认入口通过 systemd 托管后端及其推理进程，以保证后端崩溃后回收模型进程。

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

打开 **http://127.0.0.1:8000**，在模型页面创建实例，等待「就绪」后打开对话。CPU 是默认模式（GPU layers = 0），需要 GPU 时在创建表单调整。Flash Attention 默认明确关闭，勾选后开启，需要对应模型和设备支持。每个实例有自己的上下文、端口、日志和状态；加载同一文件的多个实例仍可能重复占用显存。

## 多机接入

远端先准备 Linux、SSH 登录账户、支持 CUDA 的驱动 / 运行库、基础系统工具和可用的 systemd 用户服务。首次部署可以在页面授权启用该账户 linger（需要相应管理员权限），使退出 SSH 后用户服务仍可运行。日常使用只需开机联网。

控制端准备固定版本的 RPC 包，生产节点不执行编译：

```bash
cmake -S inference_backends/llama.cpp -B inference_backends/llama.cpp/build -DGGML_CUDA=ON -DGGML_RPC=ON
cmake --build inference_backends/llama.cpp/build --target llama-server ggml-rpc-server -j 2
.venv/bin/python scripts/package_rpc.py --platform jetson
```

打包脚本生成被 Git 忽略的 `inference_backends/rpc_packages/` 和 `config/rpc_releases.yaml`，包含提交、协议、架构、glibc 与 SHA256 清单。已有同名发布不会被覆盖；更新时指定新的 `--id`。打包机器应与目标环境兼容；x86_64 / 其他 CUDA 平台必须准备并验证自己的包，`--platform linux` 不是通用兼容承诺。不分发系统 CUDA 库。

1. 节点页添加 IP、用户名、SSH 端口和密码；通过可信渠道核对并确认主机指纹。
2. 密码验证成功后自动检测现有 RPC，在节点上显示端口、服务名、PID、设备、版本、占用和可用状态。无需手动登记 / 验证接管。后台尝试配置并验证后续专用密钥；失败时保留当前密码会话并单独提示，不把已成功的密码认证误报成失败。密码不保存。
3. 可直接使用已识别且验证通过的空闲 RPC；也可点“新建 RPC”，默认选择空闲端口，再选择发布包完成部署。一个节点可登记多个 RPC，同一 GPU 不会被不同 RPC 重复分配。开机自启默认关闭。
4. 创建实例选择“多机 RPC”，选择空闲 worker 的设备，调整顺序及相对权重。模型文件留在控制端，由原生 RPC 传输所需数据；权重比例不等于精确显存或层数保证。

RPC 数据链路走可信局域网直连，SSH 用于控制。SSH 认证不会给原生 RPC 增加加密或鉴权。平台只启停已验证所属的服务；现有 RPC 通过进程、服务、二进制摘要与协议核对后自动关联；正在使用的 RPC 跳过协议探测。页面可删除指定 RPC：平台创建的服务清理专属程序 / 缓存，已有外部服务停止并停用，外部程序文件保留。删除前核对进程身份，有受管实例占用时拒绝；外部客户端连接需明确确认断开。

同一个 IP 更换机器时，重新核验主机指纹，并勾选“此 IP 已更换为新机器”。在没有受管实例占用的前提下，清除旧身份、凭据引用和 RPC 绑定，再重新登录采集；历史记录保留，不自动信任新指纹。

## 配置

默认读取 `config/config.yaml`，不存在时使用内置默认值。所有相对路径以项目根目录为基准，和当前工作目录无关。

| 环境变量 | 覆盖内容 |
| --- | --- |
| `LIM_CONFIG` | YAML 配置文件 |
| `LIM_LLAMA_SERVER` | llama-server 可执行文件 |
| `LIM_MODEL_DIRS` | 模型目录列表，使用操作系统路径分隔符，Linux 为 `:` |
| `LIM_DATABASE` | SQLite 数据库路径 |
| `LIM_LOG_DIR` | 实例日志目录 |

完整配置见 [config.example.yaml](config/config.example.yaml)。推理实例强制监听 `127.0.0.1`。管理端默认只监听本机，无账户系统；需要局域网访问时配置外部认证和 HTTPS 代理，再调整管理端监听及受信 Host。节点默认允许 RFC1918 IPv4 私网，可用 `nodes.allowed_networks` 缩小范围。

## 生命周期与会话

- GGUF 经过至少两次稳定观察和张量数据完整性检查后入库；文件监听 + 30 秒定期扫描兜底，扫描 / 解析在工作线程运行。
- 创建实例后异步等待 `/health`，通过才进入 READY。加载失败、超时和异常退出保留实例及错误，可在详情查看日志。
- 销毁会先停止接收新推理请求，再终止进程；超时强杀，确认退出后设为 STOPPED。重复销毁幂等，历史记录保留。
- 关闭网页或单独断开 SSH，不终止推理。SSH 连接按需复用，数量和空闲保留时间有上限，推理不依赖 SSH 长连接。
- **V2 管理后端正常退出或崩溃时，终止全部受管 llama-server**。默认 systemd 托管入口以 cgroup 清理兜底，包括 SIGKILL；远端 RPC 保持待命，在实际检测到 RPC 连接断开后释放该会话资源。驱动 / 分配器可能保留可复用内存，不能用 RSS 是否立即下降判断模型仍在加载。
- 后端恢复时核对旧记录与资源占用，不自动重载模型。资源释放无法核实时隔离 worker，核对前禁止复用。突然掉电或全链路断网的限时回收留待后续版本。
- `--direct` 仅用于隔离开发测试，没有后端 SIGKILL 后清理子进程的保证。正式运行使用默认入口；不要用裸 Uvicorn 启动替代它。
- V1 数据库自动备份到 `.pre-v2.bak` 后升级，保留模型 ID、实例和浏览器会话。升级后旧活动实例不会自动继续运行；切换前先销毁旧实例。
- Chat 会话和消息存于当前浏览器 IndexedDB；刷新恢复，删除会话同时删除消息，销毁实例不删除会话。原实例不可用时，可选另一个 READY 实例继续。
- 生成中切换页面会中止请求并保存已有输出。刷新 / 关闭导致未完成的消息会在重开时标记为中断。

## 内存预算

当前目标（V3）：**llama-server 之外的控制端后端、启动包装进程、SSH 和临时图片处理等辅助进程合计 RSS 低于 1 GiB，浏览器内存不计入**。llama-server 和远端 RPC 的模型、视觉编码器、KV / prompt cache / GPU 分配另计。[V2 验证记录](docs/validation_v2.md) 保留含专用浏览器的历史测量口径；V3 的新预算和图片缓存分层见 [开发设计](docs/development_v3.md)。

控制措施：单 worker、静态前端、共享 HTTP 连接池、默认最多 8 路 Chat、2 MiB 请求上限、4 MiB 响应上限、有界日志尾部及按需加载 Markdown。SSH 默认最多 4 个连接、2 条并行命令、20 个排队任务，空闲 45 秒后回收；静态配置缓存 1 小时，动态采样每 30 秒，失败退避至最多 300 秒。发布包由原生 SFTP 流式传输。默认最多 16 个活动实例；实例本身的推理内存由模型和参数决定。

```bash
.venv/bin/python scripts/check_memory.py --pid <管理后端PID> --seconds 30
.venv/bin/python scripts/check_memory.py --pid <管理后端PID> --browser-pid <专用浏览器PID> --seconds 30
```

脚本默认统计管理后端进程树，并自动查找同一受管单元的启动包装进程，排除 `llama-server`，超过预算返回非零。同时报告 USS / PSS；RSS 会重复统计共享页。V3 后台验收使用不带 `--browser-pid` 的命令；附加浏览器 PID 可复现旧口径或辅助诊断，不作为新后台预算门槛。

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

V2 的原生 RPC / SSH / 崩溃联调使用独立 QA 环境，说明见 [验证记录](docs/validation_v2.md)。AsyncSSH、Playwright 只用于模拟 SSH 和浏览器测试，不进入生产依赖。

更多内容：[架构](docs/architecture.md) · [API](docs/api.md) · [开发说明](docs/development.md) · [V2 设计](docs/development_v2.md)。
