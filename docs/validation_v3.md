# V3 Image 实现与验证记录

日期：2026-11-20（Asia/Shanghai）。依据 [V3 设计](development_v3.md)，在 `e12767e` 文档基线后实现。正式管理后台保持停止；开发服务、数据库、模型、图片和浏览器均在独立测试空间，不搬动用户现有模型、不升级正式数据库。

## 实现范围

- 扫描改为一个独立目录一个模型包，区分文本、图文及其他类型；读取有界 GGUF 元数据，不加载权重。主权重、投影、量化变体与原生分片组在包内关联，支持可选 model.json。缺片、歧义、越界链接和未支持架构会显示原因。
- HF safetensors + config.json 可识别为未支持运行；音频投影和嵌入模型不会被误当作可用图片对话模型。格式检测与运行能力分开，不声称支持任意 GGUF 或 HF 模型。
- 图文实例绑定资产修订和视觉设备，进程启动前复核。CPU 默认明确禁用投影 offload，本机 GPU 显式指定设备，语言模型沿用单机或 RPC Placement。/health 后核验 /props.modalities.vision，缺少视觉能力立即失败并报告 vision_not_loaded。
- 前端选择、拖入、粘贴图片，支持多图、仅图片消息、预览、取消与重试、停止生成，以及含图片历史的多轮 SSE。切换到文本实例时阻止发送含图片历史，保留附件。
- 图片原始二进制上传；后端验证、规范化、按 SHA256 存盘。Chat JSON 只含 image_id，原生运行时使用固定 media-path 下的 file:// 文件，不在管理 API 接受 Base64 或任意图片 URL。
- IndexedDB version 2 保留原文本记录，增加规范图片及缩略图 Blob。服务端缓存丢失 / 过期时，从浏览器恢复相同字节并校验摘要；删除会话清理其本地附件。
- 服务端暂存有上传大小、像素、并发、磁盘、记录数和 TTL 上限。图片处理按需启动单个受限子进程，FastAPI 不导入 Pillow。缓存使用控制端归属标记与排他锁，推理期间固定文件引用，GC 不删除使用中的文件和其他目录文件。
- SQLite schema 3 迁移前保留 .pre-v3.bak；旧模型 ID 别名持久化。preview_model_layout.py 默认只预览，不移动文件；显式整理完成后可在后台停止时登记经过文件身份核验的映射。

生产仅增加 Pillow 12.3.0（Pillow 的 HPND 许可证，依赖已锁定），无 Torch、Transformers、常驻 Node、图像守护进程或远端管理 Agent。视觉模型及投影仅在原生 llama-server 中运行。

## 自动化与构建

后端 **65 项**、前端 **7 项**通过，TypeScript 检查和生产构建通过，Python 未使用名称检查通过。后端测试保留原单机 / SSH / RPC / 生命周期覆盖，并增加以下场景：

- 包内变体、原生分片缺失、旧布局提示、显式迁移映射、歧义投影、资产变化、未支持格式和其他模态。
- 原始上传与规范恢复、重复上传缓存、实际尺寸、EXIF 清除、非法图片 / 格式 / 像素 / 字节上限、推理期间删除保护。
- 真正的 HTTP 文件引用转发、图文能力核验失败、仅图片请求、图片顺序标记、全历史数量限制、禁止任意 URL 和路径。
- 真实 HTTP 流式客户端断开后释放附件引用和并发计数；缓存目录归属冲突、未所属文件保留。
- 浏览器数据库旧文本迁移、Blob 重新打开、按会话删除、图片引用顺序和历史缺失 / 超限；Chat JSON 不包含 Blob / Base64。

测试运行时有现有 FastAPI TestClient / httpx 弃用提示，不影响结果；本版未升级测试客户端体系。

## 真实原生与三机验证

固定 llama.cpp 提交为 `9214bdb554544c35e4a9e2c2e8b75d8cfee330ab`，未修改其源码。独立服务监听 127.0.0.1:8018，实例使用 19100～19120；SIGKILL 验证另用 8019。正式 8000 服务未启动。

使用控制机本地 Orin GPU，以及两台真实远端节点上已有空闲 RPC GPU。经信任记录连接与自动发现，测试不部署、不停止、不替换这些 RPC。每次销毁测试实例后核对远端进程身份不变。公开证据已去除实际内网地址、PID、启动身份与持久化标识，只保留配置角色、断言、测量和公开模型来源；完整环境记录留在本机隔离验收目录，不上传仓库。

SmolVLM-500M Q8 + 对应 Q8 投影完成以下链路：

| 配置 | 已验证结果 |
| --- | --- |
| 控制机 CPU 视觉 + 三机 GPU 语言模型 | 图文能力就绪；红、蓝输入分别正确识别；多轮续问、双图 SSE 完成；销毁释放 worker |
| 控制机 GPU 视觉 + 三机 GPU 语言模型 | 同上；视觉设备明确为本机 CUDA0，远端仍仅执行原生 RPC 计算 |
| 单机 CPU 视觉 / 语言模型 | 实际加载、视觉回答、文件与 Base64 原生兼容性对照及缓存测量 |

证据：[CPU 视觉三机](validation/v3/native-rpc.json)、[GPU 视觉三机](validation/v3/native-rpc-gpu.json)、[原 RPC 发现](validation/v3/physical-discovery.json)。权重来自固定仓库修订，未写入 Git：[SmolVLM-256M](validation/v3/model-source.json)、[SmolVLM-500M](validation/v3/model-source-500.json)、[Qwen2-VL-2B](validation/v3/model-source-qwen.json)。原生报告包含实际权重 SHA256。

## 浏览器端到端

独立 Chromium 使用本次生产构建，通过实际 HTTP / SSE 和 Qwen2-VL-2B Q4_K_M + Q8 投影完成扫描、表单创建、能力就绪、原始上传、图文回答、双图顺序比较、刷新恢复、按需大图预览，以及删除服务端缓存后从 IndexedDB 恢复并继续多轮提问。测试固定 temperature=0、max_tokens=128，未替换模型响应。

红蓝两张图的实际回答为 “The first image has a red background. The second image has a blue background.”；刷新恢复后询问第一张图，回答 “Red”。另外完成仅图片消息的实际推理，以及停止生成后刷新仍保留用户图片。390px 布局无横向溢出，浏览器无 pageerror。多图增加固定 First / Second 位置文本：实测相邻原生图片缺少位置提示时可能只描述末图，固定标记改善了此模型的顺序区分，不使用带答案的文件名，也不保证所有模型的视觉准确率。

记录及截图：[浏览器](validation/v3/browser.json)、[桌面](validation/v3/v3-chat-desktop.png)、[移动端](validation/v3/v3-chat-mobile.png)、[位置标记对照](validation/v3/image-labels.json)。

动态分辨率模型默认视觉工作缓冲可能很大，因此新增 image_max_tokens 默认 1024（64～4096）。这是原生模型参数，不能据此推算管理后台占用；提高上限仍需考虑上下文与整机容量。已验证的 Qwen 组合使用 context=2048、micro batch=512、本机 GPU 视觉、语言模型 CPU；其他模型与 micro batch 组合需单独验证。

## 缓存证据及边界

产品仍使用二进制上传 + ID + 本地文件。SmolVLM 相同规范字节、相同提示的原生对照记录如下；Base64 只在隔离 QA 中调用原生端口：

| 请求 | cache_n | prompt_n | prompt_ms |
| --- | --- | --- | --- |
| 首次文件输入红图 | 0 | 153 | 7870.858 |
| 相同字节经 Base64 输入 | 152 | 1 | 26.904 |
| 同提示换蓝图 | 16 | 137 | 7284.966 |

说明固定运行时可以复用包含媒体身份的前缀，换图会重算相关部分；Base64 没有天然缓存优势。三机续问另外记录 cache_n=153。cache_n 与 prompt_ms 是原生运行时字段，未声称它们是单独的视觉编码耗时或独立视觉特征缓存命中率。文件 / 预处理去重、规范字节恢复由上传和浏览器链路分别验证，见 [缓存对照](validation/v3/cache.json)。

未增加管理端 embedding 张量缓存，未承诺跨实例、跨模型、不同前缀或被淘汰槽位免编码；多槽位切换和独立视觉阶段耗时尚无完整性能矩阵。实例销毁后新实例重新加载，管理端不持久化 KV。

## 生命周期与后台预算

隔离管理后端在真实 CPU 图文实例和图片处理子进程同时存活时被 SIGKILL，两者约 **0.30 秒**内退出；重启将旧实例记为 manager_terminated，无模型自动重载，孤立 staging 被清理。远端 RPC 独立待命；此次不扩展断电、断网限时回收。验收结束时无遗留测试实例，正式与测试后台均已停止，原 RPC 空闲且身份不变，见 [清理记录](validation/v3/cleanup.json)。[生命周期证据](validation/v3/lifecycle.json)。

| 场景 | 时长 | 合计 RSS 峰值 | 同一峰值 USS / PSS |
| --- | --- | --- | --- |
| V3 图片与三机联调、16MP 大图处理 | 1800 秒 | **296.61 MiB** | 238.93 / 251.48 MiB |
| 最终候选版连续处理 12 张独立 16MP 图片、三机图文请求 | 120 秒 | **265.81 MiB** | 218.13 / 227.22 MiB |

统计管理后端、启动包装 Python、systemd-run、SSH 与临时图片处理完整进程树；仅排除 llama-server，浏览器与远端 RPC 推理不计入控制后台。RSS 求和会重复统计共享页，另报 USS / PSS，没有通过强制 GC 获得预算结果。两次均低于 1024 MiB；30 分钟采样后补充了位置提示、缓存目录归属及视觉 token 上限，随后完成短时复测，未把短测描述为第二次 30 分钟测试。最终能力错误信息的修正不改变进程模型。

图片子进程峰值样本约 178 MiB；16MP 图片规范化为 2048×2048，scaled=true。地址空间 384 MiB、CPU 5 秒、墙钟 10 秒，单进程处理；上限 / 队列保护不代表任意恶意编码图都能成功转换。原生 Qwen 权重、投影、KV 和视觉工作区另外占用整机内存，Jetson 共享 RAM 容量仍需单独核对。

证据：[30 分钟](validation/v3/memory.json)、[最终短测](validation/v3/memory-final.json)、[大图](validation/v3/large-image-upload.json)、[最终大图](validation/v3/large-image-final.json)。这是当前三机、小历史和测试模型的验证，不是任意规模节点或无限历史的内存保证。

## 运行与复测

正式数据库和用户模型文件未更改；前端构建已准备，启动前先核对模型包目录。SQLite / IndexedDB 在各自下一次实际打开时迁移，现有会话不会被清空。浏览器和后端缓存均被删除后，附件无法恢复，应重新添加。

```bash
.venv/bin/python -m pytest -q
cd frontend
npm test
npm run build
cd ..
.venv/bin/python scripts/preview_model_layout.py --output /tmp/model-layout.json
.venv/bin/python scripts/check_memory.py --pid <隔离后端PID> --seconds 1800 --output /tmp/memory.json
```

真实 QA 脚本为 qa_v3_native.py、qa_v3_cache.py、qa_v3_browser.py、qa_v3_lifecycle.py。它们是本轮固定临时环境的验收入口：需要先准备隔离配置、模型、空闲 RPC 和候选静态目录；不是会自动下载 / 部署模型的通用安装脚本。浏览器依赖只在独立测试环境，生命周期脚本仅杀掉核验过配置身份的测试单元。


## 2026-11-21：qwen35 架构补充

下载的 Unsloth Qwen3.8-27B UD-Q4_K_M 使用 `general.architecture=qwen35`，配套 F16 投影使用 `qwen3vl_merger`，投影输出与主模型 embedding 均为 5120。固定 llama.cpp 源码已包含该架构实现；原管理端识别列表遗漏，导致有效图文包显示 unsupported。

现将 qwen35 加入已知主模型与视觉架构列表，复用已有变体选择、投影关联、文本 / 图文创建和原生能力核验。缺少投影仍为 incomplete，未知架构仍为 unsupported；没有将所有 GGUF 一律放行，也未升级推理引擎或 RPC 包。扫描结果 available / candidate 表示允许创建，仍不等于实例已完成实际视觉加载。

新增两项回归，覆盖 qwen35 包识别、投影关联、图文 / 显式文本参数、模拟运行时的实际 HTTP 图片转发，以及缺失投影与未知架构拒绝创建。后端全量 **67 项**通过，Python 名称检查与 diff 检查通过；前端代码未变更。

使用真实 16.56 GB 主权重和 0.93 GB 投影完成有界扫描，并调用本机 libllama 的 vocab_only 模式：架构、超参数和词表解析成功，跳过权重张量加载，没有启动模型推理。该检查不替代完整 27B 推理、视觉编码或三机容量验证，也不新增性能 / 内存验收结论。见 [脱敏结果](validation/v3/qwen35.json)。

本轮检查时正式管理后台已由用户启动，且无运行实例和活动 Chat。测试通过后重启空闲管理后台使识别规则生效，实际 GET /models 已返回该模型为 image_text / available 并关联投影。远端 RPC 保持原状态，未创建、加载或遗留 27B 实例。
