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

初版验收没有覆盖 192.168.50.12 和 192.168.50.11。后续已完成两台真实节点的密钥复连、配置采集、现有用户级 RPC 自动发现与协议核验，见本文末尾；未重新部署或停止其现有服务。双远端 GPU Placement、实际模型推理、真实链路吞吐与各机持续资源趋势仍待完整三机验收。用户可以直接在节点表单输入密码，无需先配置免密。

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

## V2.1：密码接入后自动发现与 RPC 新建 / 删除

密码验证成功后，先采集节点配置并发现正在运行的原生 RPC，结果直接显示在节点卡片。匹配可信发布包摘要、设备和协议且空闲的 RPC 自动变为可用，不再要求手动登记和“验证接管”；无法确认版本、权限或监听地址时显示具体原因。已有客户端连接或平台占用时不主动建立探测连接。

一个节点支持多个 RPC。新建默认避开已监听和已登记端口；实例对同一 GPU 的独占校验覆盖不同 RPC 入口。删除使用页面确认的进程身份，并再次检查机器、启动时间、二进制、服务配置和占用。平台新建 RPC 删除其专属目录；外部 RPC 停止并停用对应用户服务或终止独立进程，保留原程序文件。系统服务及其他账户的进程不自动提权操作。

SSH 修复覆盖：输入密码时始终重新认证，不能复用旧密钥连接绕过密码校验；先保留密码会话，再通过独立连接验证自动安装的密钥；安装失败只提示后续需要密码，不把已认证节点变成连接失败；丢失私钥可重新引导；失效的复用 socket 对只读命令重连一次，写入和删除不自动重试。同 IP 换机经用户明确确认后清除旧机器绑定，保留任务历史。

本轮全量后端 **54 项**、前端 **4 项**测试及生产构建通过。新增覆盖自动发现、空闲 / 占用 / 未知版本、原子设备占用、进程身份变化、显式空身份删除保护、真实 shell 定向删除与所属目录清理、密码错误脱敏，以及同节点多个 RPC 的任务去重和幂等标识隔离。任务携带 worker_id，卡片按具体 RPC 显示进度；旧的节点级历史任务仅保留在最近任务中。

隔离联调继续使用真实 OpenSSH / SFTP 和原生 CPU RPC，Linux 配置与用户 systemd 由 fixture 模拟。验证了双 RPC 原生小模型推理、错误密码不被旧连接绕过、SSH socket 丢失 / 私钥丢失后的恢复、新建空闲 RPC、禁止同 GPU 重复占用，以及删除空闲 RPC 不影响正在推理的其他 RPC。浏览器实际执行密码接入、自动发现、公钥安装受限后重连恢复、新建部署和定向删除；390px 无横向溢出，无 pageerror。

本轮 60.48 秒、55 次采样中，管理后端、launcher、三条 SSH master 和一个专用 Chromium 的合计 RSS 峰值 **658.88 MiB**，低于 1 GiB。未强制 GC，未计入 llama-server / RPC 推理进程；这是本次短时回归，不替代前述 30 分钟测试，也不保证任意标签数量和会话历史的内存占用。

正式管理端更新后，两台真实 Orin 节点只执行密钥登录、采集和空闲 RPC 核验，均识别各自 50052 端口、协议 10.0.0，并自动复用原 worker 记录。既有 PID 分别仍为 11076、10954，没有创建、停止或删除真实远端服务。之后两个 RPC 出现外部客户端连接，正式页面正确转为“正在使用”，普通停止禁用，发现跳过协议探测。正式静态页面另做只读桌面和移动端检查。外部正在运行的模型不作为本轮完整三机推理验收证据。

证据：[原生隔离流程](validation/v2/discovery-flow.json)、[浏览器与内存](validation/v2/discovery-browser.json)、[密码落盘检查](validation/v2/discovery-secrets.json)、[真实节点只读发现](validation/v2/physical-rpc-discovery.json)、[正式页面检查](validation/v2/physical-browser.json)。截图：[隔离桌面](validation/v2/discovery-nodes-desktop.png)、[隔离移动端](validation/v2/discovery-nodes-mobile.png)、[真实节点桌面](validation/v2/physical-nodes-desktop.png)、[真实节点移动端](validation/v2/physical-nodes-mobile.png)。

复测入口为 scripts/qa_discovery_flow.py 和 scripts/qa_discovery_browser.py。先以独立数据库、SSH 目录和端口启动管理端；qa_ssh_server.py 的 --prestart-rpc --count 3 为 fixture 预置既有服务。脚本会操作这些隔离节点，不用于真实节点；浏览器脚本 --dist 指向待验收构建。
