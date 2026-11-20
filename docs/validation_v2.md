# V2 实现与验证记录

日期：2026-11-20（Asia/Shanghai）。依据 [V2 设计](development_v2.md)，在 V1 基线之后实现。本文区分代码实现、隔离验证和真实硬件验证；没有将模拟机器当作真实三机验收。

## 已实现范围

- 统一本机 / 远端节点：CPU 型号与架构分开、RAM / swap、GPU、采样来源和时间、过期数据。Jetson 显示共享系统内存，null 指标不会变成 0 或“GPU 不可用”。
- 添加 / 编辑 / 归档 SSH 节点，主机指纹确认、首次密码登录、自动安装本控制端专用公钥、后续密钥连接；无需提前免密，密码不长期保存。
- 控制端验证并上传原生 RPC 发布包；所属目录、二进制、systemd unit 和 runner 摘要核对，暂存解压、不可变版本、失败回滚；远端可选开机自启 RPC，默认关闭。
- RPC 启停、有限日志、停止后清理所属缓存、外部服务明确验证接管；不为释放端口误停其他服务。
- 多机创建的显式设备顺序、相对权重和 --rpc 映射，SQLite 事务预留全部 worker，后台准备、取消、占用隔离与释放核对。
- 默认 systemd 托管管理后端和全部受管 llama-server；正常退出 / 崩溃 / SIGKILL 清理模型进程，远端 RPC 保持待命，不自动重载模型。SSH 单独断开不终止推理。

生产依赖未增加推理框架、AsyncSSH、浏览器工具或数据库服务。控制端仅一个 FastAPI + SQLite + 静态前端；远端无管理 Agent。

## 自动化和隔离联调

后端 32 项测试、前端 4 项测试和生产构建通过。后端覆盖原 V1 功能，以及 Jetson N/A、零值 / 空值、原子占用竞争、慢准备立即返回 202 与取消、密码校验不回显、主机指纹变更 / 过期 / 目标绑定、任务去重 / 中断 / 脱敏、工作进程超时和输出上限、归档摘要 / 路径逃逸 / 链接 / 重复文件 / 路径别名、真实 shell 安装与回滚、隔离占用核对。测试使用假运行时验证状态机，真实模型链路另测。

10 节点环境由 scripts/qa_ssh_server.py 在 127.0.0.2～127.0.0.11 提供。Linux inventory 和 systemd 操作是模拟的；控制端使用真实 OpenSSH 密码认证 / 密钥复连、主机密钥检查和原生 SFTP。两个计算服务是固定提交的原生 ggml-rpc-server，以 CPU 设备运行，并非 Python 模拟推理。

验证了密码接入、公钥引导、发布包 SFTP 上传、协议 10.0.0、双 worker 原生模型加载、显式 RPC0/RPC1 映射、独占冲突 409、占用时拒绝停止、原生 SSE 完成标记。模型为独立临时目录中的 stories15M-q4_0.gguf（约 19 MiB）。双 CPU RPC 的结果不代表真实多 GPU 的性能、容量或网络吞吐。

七路流式压力：60 秒完成 665 次请求，0 错误、0 限流。浏览器完成 IP / 密码 / 指纹 / 在线流程、双 worker 选择与排序 / 比例、模型启动、RPC 启停和日志；单机 Chat 回归覆盖停止生成、刷新恢复 IndexedDB、保留历史和切换可用实例、删除会话、390px 布局。未发现 pageerror。

## 生命周期和故障

- 实际 SIGKILL 隔离管理后端，其同时管理的本机和双 worker llama-server 全部退出；两个 RPC 仍存活并接受新协议会话。重启后旧记录变为 manager_terminated，未自动重载，旧占用释放。
- 杀掉 SSH masters 后，原生 RPC 推理继续成功。
- 杀掉一个原生 RPC，所属 llama-server 失败并退出；核对后释放占用。手动重新启动 RPC 不自动恢复旧模型。
- 真实 OpenSSH 拒绝错误密码、拒绝与已信任文件不一致的主机密钥，恢复正确配置后可用原密钥重新连接。
- 额外 systemd 测试子进程忽略 SIGTERM，并通过 setsid 脱离父进程会话；主进程 SIGKILL 后，仍在同一 cgroup 的子进程于约 10.28 秒被强杀，验证 TimeoutStopSec=10 的兜底。
- 扫描本轮 SQLite / 实例日志 / 私有 SSH 文件，测试密码没有落盘；私有目录 0700、私钥 0600，无遗留 askpass socket。

故障测试仅操作本轮 UUID / loopback 服务，不操作用户原有模型服务。突然掉电、全链路断网、不可中断驱动故障的释放时限未纳入本版。

## 真实本机 Orin CUDA 验证

本机硬件为 NVIDIA Jetson Orin Nano Engineering Reference Developer Kit Super。使用独立 UUID 目录、真实安装脚本和真实 systemd 用户服务部署 RPC CUDA0，完成 HELLO / 设备 / 内存握手、二进制 SHA256、停止、不可变版本重装及回滚；测试后删除所属单元和目录。

通过该 RPC 加载 TinyStories，日志确认 7/7 层卸载到 GPU，模型 buffer 12.56 MiB、KV buffer 1.69 MiB，流式推理得到 [DONE]，退出模型后 RPC 接受新会话。此项是真实单个 Orin GPU，经回环 RPC 计算，不能替代两台远端真实 GPU 的 SSH / 网络验收。

过程中发现上游自动 Flash Attention 会为该小模型选择没有可用 CUDA 内核的操作并中止 RPC。已修正开关语义：未勾选明确传 --flash-attn off，勾选传 on。测试用关闭模式通过，未承诺所有模型都支持 Flash Attention。

RPC 未加载模型时 RSS 约 286 MiB（包含原生 CUDA 上下文和库），属于远端计算运行时单独统计。释放会话 buffer 不保证分配器立刻向操作系统归还全部 RSS。不能把设备 MemAvailable 当成保证可分配的模型容量。

## 内存与稳定性

| 场景 | 采样时长 | RSS 峰值 | USS / PSS（同一峰值样本） |
| --- | --- | --- | --- |
| 10 模拟 SSH 节点 + 管理后端 + 专用浏览器 | 1800.3 秒，875 次 | 708.84 MiB | 346.66 / 440.77 MiB |
| 同一工作区，额外计入启动包装进程，独立 0.5 秒间隔采样 | 1800 秒 | **811.75 MiB** | 422.96 / 525.63 MiB |
| 后续修正后的完整流程短时复测，计入包装进程 | 10.5 秒 | 703.16 MiB | 192.70 / 319.24 MiB |

完整口径记录的最高 RSS 为 811.75 MiB，低于 1024 MiB。本次还把原生推理异常生成的 GDB 子进程约 120 MiB 计入峰值，没有因其属于崩溃调试而隐藏。两组长采样窗口并非完全对齐：浏览器先完成 30 分钟，完整口径采样约后移数分钟，末段为浏览器关闭后的管理端；各原始记录注明统计范围。

同构建、同模型目录的零远端节点管理进程与启动器为 98.39 MiB（不含浏览器）。10 节点稳态管理部分约 152.36 MiB，约增加 53.96 MiB；这是稳态对照，不能当作所有故障 / 部署阶段的增量峰值。

30 分钟浏览器组 RSS 从 662.14 增至 708.84 MiB，连接池最大 4。预算通过不等于证明无限时长没有增长。复核后补上 SQLite 短连接显式关闭，并增加关闭 GC 后连续读写不残留数据库句柄的测试；修正后另做 95 秒复测，10 节点与包装进程 / 浏览器合计峰值 594.05 MiB，管理端文件句柄 14～22 个；关闭 GC 的连续数据库读写测试没有残留数据库句柄。浏览器 JS heap 在采样结束后诊断性 GC 前 / 后为 7.06 / 3.71 MiB；GC 只在采样结束后用于检查保留对象，没有用来通过预算。见 [短时复核](validation/v2/repository-fix-profile.json)。不将短测冒充第二次 30 分钟验收。

原始证据：[完整口径](validation/v2/memory-full-30min.json)、[浏览器 30 分钟](validation/v2/browser-soak-30min.json)、[零节点基线](validation/v2/baseline-manager.json)、[最终代码短测](validation/v2/final-browser-soak.json)。

统计控制端管理后端、其 SSH / 短时子进程、systemd-run 与等待包装进程，以及一个专用 Chromium 的完整进程树；排除 llama-server。模拟 SSH 服务和模拟远端原生 RPC 单独运行，不计作控制管理进程。RSS 求和重复统计共享页，另报 USS / PSS；没有将远端模型内存或文件缓存伪装成控制端可忽略内存。

10 个节点是真实 SSH 连接的模拟主机，动态采样加快到 3 秒，持续刷新节点页面；连接池上限 4、并行命令上限 2。采样期间进行了原生多机 Chat、七路压力、RPC 故障 / 恢复及其他隔离验收。短时原生编译 / 打包工具、QA 驱动不属于生产常驻进程。

此结果用于当前单浏览器标签、小会话和测试模型，不承诺任意长历史、插件、多标签或其他硬件均同样占用。

## 真实三机待验收项

192.168.50.12 和 192.168.50.11 的 SSH 登录、公钥安装、对应平台发布包、真实 systemd / linger、双远端 GPU Placement、真实链路吞吐及各机 GPU / RAM / swap，尚未执行本轮验收。用户可以直接在节点表单输入密码，无需先配置免密。未为本轮测试修改这些远端机器。

部署的字节级进度、远端缓存目录大小 / 磁盘连续趋势尚未提供，目前展示任务阶段、部署前磁盘检查和可用的系统指标。x86_64 / 其他 CUDA 平台需独立构建并验证发布包。没有将这些待验收项标为完整 M5 验收通过。

## 复测入口

```bash
.venv/bin/python -m pytest -q
cd frontend
npm test
npm run build
cd ..
.venv/bin/python scripts/check_memory.py --pid <管理PID> --browser-pid <专用浏览器PID> --seconds 1800
```

隔离 QA 依赖在单独虚拟环境安装 scripts/requirements-qa.txt，然后安装 Playwright Chromium。以独立 LIM_CONFIG、数据库、SSH 目录、监听端口运行管理端；只在测试配置中放行 loopback。qa_ssh_server.py --root /tmp/测试目录/machines --output /tmp/测试目录/ssh-endpoints.json --binary /绝对路径/ggml-rpc-server 提供模拟节点，LIM_QA_PASSWORD 为合成测试密码。

qa_v2_flow.py 支持 --root、--url、--rpc-port-base；qa_v2_lifecycle.py 通过 LIM_QA_ROOT/LIM_QA_URL 选择隔离配置，执行 SIGKILL 并重启该测试后端。qa_v2_browser.py 支持 --root、--url、--seconds，操作模拟节点并执行浏览器 / 内存测试。qa_v2_negative.py 仅用于它标明的固定 loopback fixture。qa_local_rpc.py 使用本机临时 UUID 单元执行原生 CUDA 校验并清理，不修改 SSH 公钥；可加 --model 指定测试小模型。


其他原始证据：[双 worker 流程](validation/v2/final-flow.json)、[SIGKILL 生命周期](validation/v2/final-lifecycle.json)、[真实 CUDA RPC](validation/v2/local-rpc-cuda.json)、[强杀兜底](validation/v2/systemd-fallback.json)、[七路压力](validation/v2/stress.json)、[故障注入](validation/v2/negative.json)、[密码落盘检查](validation/v2/secrets.json)、[Chat 回归](validation/v2/chat-regression.json)。截图：[桌面](validation/v2/final-nodes-desktop.png)、[390px 移动端](validation/v2/final-nodes-mobile.png)。


## 补充修复：SSH 默认端口的受信记录

真实节点 192.168.50.12:22 首次连接暴露一个隔离环境随机高端口测试未覆盖的问题：OpenSSH 默认端口按裸 IP 查找 known_hosts，原实现统一写成 [IP]:22，导致“找不到受信主机记录”被误报为主机密钥变化。实际扫描指纹与用户已确认指纹相同。

已修复默认端口写裸 IP、非默认端口写 [IP]:port；旧记录只在公钥指纹与原有信任完全匹配时修正格式，不替换公钥、不关闭严格校验。同时区分“未找到受信记录”和“主机密钥实际变化”。新增 4 个回归用例，通过原生 ssh-keygen 的查找验证默认 / 非默认端口以及旧格式迁移与信任保护。全量后端测试现为 36 项。

现有节点经修正后，真实 SSH 已通过主机身份核验并进入用户认证阶段；该诊断没有发送用户密码或启动远端 RPC。


## 补充修复：部署任务反馈与端口配置

真实两节点部署请求已接受，但已有 ggml-rpc-server 占用各自的 50052 端口，检查阶段以 port_conflict 失败。原界面在接受任务后立即关闭部署窗口，错误仅在长页面底部出现，用户容易认为没有反应。

现在部署窗口在提交后轮询本次任务，显示实际阶段；失败原因保留在窗口中并允许重试，成功完成后才关闭。关闭窗口后仍可在 worker 卡片就近看到最近任务的进度或错误。端口冲突错误包含具体地址和端口。

新增未部署 worker 的“修改 RPC 配置”入口与 PATCH /workers/{id}，可修改端口或切换为关联已有服务；有占用、已经部署 / 验证接管的 worker 拒绝直接改配置。端口占用不触发停止外部服务或覆盖安装。

新增 3 项后台回归覆盖配置修改、占用 / 部署保护、端口冲突不停止外部进程，全量后台测试现为 39 项。浏览器通过拦截部署和任务响应验证进度、失败内联提示、重试、成功关闭以及配置 PATCH，没有实际部署、修改真实节点配置或停止现有 RPC。
