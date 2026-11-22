# API（V3 实现，沿用 `/api/v1` 路径）

前缀 `/api/v1`。运行后可通过 `/docs` 查看自动生成的 OpenAPI 文档。管理端默认 `127.0.0.1:8000`，前端不访问推理进程端口。

| 方法 | 路径 | 行为 |
| --- | --- | --- |
| GET | `/health` | 管理健康、运行时是否存在、活动 Chat 数、目录扫描状态 |
| GET | `/nodes` | `{nodes: Node[]}` |
| GET | `/nodes/{node_id}` | 单个节点资源及模型、实例计数 |
| GET | `/models?node_id=local` | `{models: Model[], last_scan, rejected_files}`，不包含绝对路径 |
| POST | `/models/refresh` | 202，后台安排扫描，不阻塞 HTTP 等待文件扫描 |
| GET | `/models/{model_id}` | 单模型公开信息 |
| GET | `/instances` | `{instances: Instance[]}`，默认排除已销毁 / 已停止记录，保留失败实例供排查；`?include_stopped=true` 可包含历史记录 |
| POST | `/instances` | 202，创建并启动；模型加载异步进行，轮询状态 |
| GET | `/instances/{id}` | 配置、状态、运行信息和错误 |
| DELETE | `/instances/{id}` | 优雅终止，等待退出；幂等，保留 STOPPED 历史记录，但不再出现在默认列表和节点实例计数中 |
| GET | `/instances/{id}/logs` | `{text: string}`，最多最后 32 KiB |
| POST | `/instances/{id}/chat/completions` | JSON 或 SSE；必须 READY |

创建实例：

```json
{
  "name": "Qwen local",
  "node_id": "local",
  "model_id": "从 models 接口获取",
  "backend": "llama.cpp",
  "config": {
    "context_size": 4096,
    "batch_size": 512,
    "ubatch_size": 128,
    "parallel": 1,
    "threads": 4,
    "n_gpu_layers": 0,
    "cache_type_k": "f16",
    "cache_type_v": "f16",
    "extra_options": {"flash_attn": false}
  }
}
```

创建成功返回独立 UUID、`status: starting`，后台健康检查通过后变为 ready。可执行文件缺失或启动系统调用失败时同样保留并返回 failed 记录，具体原因在 `error_message` 中。`runtime` 子对象包含 `pid` 和 `internal_port`；`config` 不含运行态信息。

实例失败信息：异步创建返回 202 后，继续轮询实例列表或详情。`status:failed` 时保留兼容字段 `error_message`，同时返回：

```json
{"error_detail":{"code":"runtime_memory_allocation_failed","message":"远端 rpc-node:50052 内存分配失败，申请 960.00 MiB（KV 缓存）。","log_excerpt":"E ... failed to allocate buffer for kv cache","exit_code":1}}
```

`error_detail` 为可空字段；退出码只在确认可获取时提供。后端从最近 32 KiB 日志中提取原因，错误摘要最多 1000 字符，关键日志最多 10 行 / 4000 字符。内存分配失败保留节点、申请大小及可识别的 KV 阶段；端口占用、运行时错误、超时和无日志退出也保留诊断。公开摘要去掉终端控制符、已知凭据形式和绝对目录，完整日志仍通过原日志接口查看。重启可为保留了日志的旧失败记录补齐原因，不重载模型。

Chat：

```json
{
  "messages": [{"role":"user", "content":"你好"}],
  "stream": true,
  "temperature": 0.7,
  "top_p": 0.95,
  "max_tokens": 2048
}
```

每次发送完整历史。可选 `model` 字段会被网关覆盖为所选实例别名；请求目标仅由 URL 中 Instance ID 决定。`stream:false` 返回上游 JSON；流式返回 `Content-Type: text/event-stream`，事件透传，上游完成标记为 `data: [DONE]`。超过响应限制或流中异常时通过 `data: {"error":{"message":"..."}}` 通知，再结束流。

| 错误 | 含义 |
| --- | --- |
| 403 | 跨域修改请求被拒绝 |
| 404 | 节点、模型或实例不存在 |
| 409 | 模型不可用、端口 / 实例数量已满、实例未 READY |
| 413 | 请求超过大小限制（含 chunked 请求） |
| 422 | 配置不合法或包含不支持参数 |
| 429 | 并发 Chat 已满 |
| 502 | 上游连接、状态或响应大小异常 |

上游连接超时 5 秒，读取超时 300 秒；客户端断连后关闭上游请求。当前没有 API key / 账户管理；应只暴露给可信本机访问。

## V3 模型包与图片

`GET /models` 一包一条，新增 kind、availability、revision、variants、projectors、default_variant_id 和 detection；根目录散放文件及无效目录在 layout_issues 中给出原因。包内字段为相对路径，不返回实际文件路径。`gguf` 是文件格式，`image_text` 是输入类型；`candidate` 不代表已经加载验证。已有明确别名的旧模型 ID 可解析到指定包内变体。

创建 config 新增 `variant_id`、`model_revision`、`modality:"text"|"image_text"`、`vision_device_id:"cpu"|"local:cuda:0"`。单个合法变体可省略 variant_id；多个未指定默认的变体必须选择。旧请求默认 text，避免目录新增投影后意外增加加载。图文模式需要兼容投影；实际 `/props` 核验成功后 Instance.capabilities.image_input 才为 true。

图文实例还可配置 `image_max_tokens`，默认 1024，允许 64～4096；启动时显式传给原生动态分辨率控制。提高该值可能增加视觉计算缓冲和上下文占用。本机 GPU 视觉编码用于 RPC 模式时，当前要求该设备也在显式 Placement 中，未选择的设备不会被隐式加入。

| 方法 | 路径 | 行为 |
| --- | --- | --- |
| POST | `/media/images` | 原始二进制上传，最多 8 MiB；201 返回图片 ID、SHA256、实际尺寸、类型和下载引用 |
| POST | `/media/images?representation=normalized` | 恢复浏览器保存的规范图片；仍完整校验，不重新有损编码 |
| GET | `/media/images/{id}` | 有效图片元数据；暂存过期 410，未知 ID 404 |
| GET | `/media/images/{id}/content` | 规范图片文件，真实 MIME 和 nosniff |
| GET | `/media/images/{id}/thumbnail` | JPEG 缩略图 |
| DELETE | `/media/images/{id}` | 幂等删除暂存；推理使用中返回 409；不删除浏览器历史 |

上传示例：`curl --data-binary @sample.png -H 'Content-Type: image/png' http://127.0.0.1:8000/api/v1/media/images`。该路由单独限制上传流量，其他 JSON 请求仍保持原上限；客户端 MIME 不替代真实图片格式和像素检查。

图片请求仍发送到 `/instances/{id}/chat/completions`，保留字符串 content，或使用：

```json
{"messages":[{"role":"user","content":[
  {"type":"text","text":"描述这张图片"},
  {"type":"image","image_id":"上传得到的 UUID"}
]}],"stream":true}
```

只有 user 允许 image part，也支持仅图片消息。每条最多 4 张、完整历史最多 8 张 / 16 MiB，重复引用仍按出现次数计数。管理接口不接受任意 image_url、file://、HTTP URL 或 Base64；网关将已核验 ID 转成原生文件引用。图片请求全局最多一路；取消、非流式错误与 SSE 结束都会释放引用，防止回收正在读取的文件。

文件和预处理缓存命中由上传结果 cache_hit 表示；原生 cached prompt token 属于另一层缓存，不等同于通用视觉特征缓存。错误使用结构化 detail；图像格式 / 结构不合法 422、字节超限 413、使用中 / 模态不兼容 409、过期 410、并发队列满 429、暂存空间不足 507。浏览器旧会话恢复与上传失败均应保留可重试原因。


## V2 节点、RPC 与后台任务

创建多机实例时，模型和 `node_id: local` 仍属于控制端。在 config 中加入以下字段（其余字段与单机一致）：

```json
{
  "n_gpu_layers": 99,
  "placement": {
    "mode": "rpc",
    "devices": [
      {"device_id":"节点ID:cuda:0", "worker_id":"worker UUID", "weight":2},
      {"device_id":"local:cuda:0", "worker_id":null, "weight":1}
    ]
  }
}
```

数组顺序就是分配顺序；至少一个远端 worker，GPU layers 必须大于 0。创建前先原子预留所有 worker，冲突返回 409，接受后返回 202，远端准备和模型加载均在后台完成。销毁 starting 实例会取消准备并核对资源。`runtime.placement` 保存实际设备映射、RPC 端点与 worker 版本 / 进程身份。省略 placement 时仍为单机。

部署、连接、刷新和启停返回 202 Job，轮询 `/jobs/{id}`，状态为 queued/running/completed/failed/interrupted。

| 方法 | 路径 | 行为 |
| --- | --- | --- |
| POST | `/nodes` | 201，登记 name/address/username/port/credential_id；address 为允许网段中的 IPv4 |
| PATCH | `/nodes/{id}` | 修改名称 / SSH 配置；有占用时拒绝，修改连接前须停止 RPC |
| DELETE | `/nodes/{id}` | 归档空闲且 RPC 已停止的节点，保留历史和远端文件 |
| POST | `/nodes/{id}/host-key/scan` | 返回 challenge_id、候选公钥指纹和已信任指纹 |
| POST | `/nodes/{id}/host-key/trust` | challenge_id/fingerprint；主机密钥变化须 replace:true |
| POST | `/nodes/{id}/connect` | `{password:"一次性密码"}` 登录并配置专用公钥；后续 `{}` 使用密钥 / agent |
| POST | `/nodes/{id}/refresh` | 后台强制采集完整配置和 RPC 状态 |
| GET | `/nodes/{id}/telemetry` | 缓存指标，不触发 SSH |
| GET | `/nodes/{id}/workers` | worker 列表及 claim 占用 |
| POST | `/nodes/{id}/workers` | device_ids/port/mode；observed 模式还需 service_name |
| PATCH | `/workers/{id}` | 部署前修改完整 WorkerConfig（device_ids/port/mode/service_name）；拒绝有占用、已部署或已接管的 worker |
| DELETE | `/workers/{id}` | 202，删除指定 RPC；可提交 process_identity 与 force，核对身份后停止 / 移除，拒绝受管实例占用 |
| GET | `/releases` | 控制端发布清单的公开部分 |
| POST | `/workers/{id}/deploy` | release_id/enable_linger/sudo_password/start/autostart；默认 start:true、autostart:false |
| POST | `/workers/{id}/adopt` | 指定 release_id，验证正在运行的外部服务后接管 |
| POST | `/workers/{id}/start`、`/stop` | `{}` 或 `{idempotency_key:"..."}`，操作已核验所属的 RPC |
| POST | `/workers/{id}/cache/clean` | 仅清理已停止的平台自有缓存；拒绝外部关联缓存 |
| POST | `/workers/{id}/reconcile` | 核对已退出实例的隔离占用，不强行释放仍在运行实例的资源 |
| GET | `/workers/{id}/logs` | 最后 32 KiB 日志 |
| GET | `/jobs`、`/jobs/{id}` | 最近任务 / 单任务，最多保留 50 条已结束任务 |
| POST | `/jobs/{id}/cancel` | 取消任务，必要时后续核对实际远端状态 |
| GET | `/credentials` | 密钥摘要、可选本机文件名，不返回私钥内容 / 路径 |
| POST | `/credentials` | `{type:"generated",label:"..."}` 或 `{type:"existing",key_name:"id_ed25519",label:"..."}` |

主机指纹 challenge 5 分钟过期，绑定节点 IP 和端口。密码不进入 Node / Job / SQLite / 日志，参数错误不回显请求值，不要将密码放入 URL。

统一 Node 新增 connection_state、role、hardware_model、inventory、telemetry、rpc_workers；指标包含 status/source/sampled_at/stale。设备包含 memory_kind(shared/dedicated/unknown) 和 compute_state；memory_total/free:null 表示缺失或共享内存，不代表 GPU 不可计算。GET /nodes 使用缓存，不进行远端 SSH。GET /health 的 managed_lifecycle 表示是否使用默认系统托管入口，ssh_connections 为当前池大小。

远端错误为 `detail:{code,message}`，如 auth_failed、host_key_changed、unsupported_platform、checksum_mismatch、service_identity_changed、worker_busy、node_operation_busy。部分旧接口错误仍为字符串，客户端同时兼容两者。

RPC 操作任务返回 worker_id，去重范围为节点、操作和具体 RPC；不同 RPC 的任务互不替代。同一个 idempotency_key 不能跨 RPC 复用。旧历史任务可能没有 worker_id，只展示于节点最近任务列表。


节点连接 / 手动刷新现在自动执行 RPC 发现，日常轮询也刷新发现结果。`Node.rpc_discovery` 包含 state/count/sampled_at/suggested_port/listening_ports/error；`Node.auth_state` 区分已成功的 password/key 认证、automatic 是否可自动复连及可选 warning。专用密钥配置失败不会覆盖密码认证成功和已发现 RPC 的结果。

发现的 worker 新增 origin(existing/created)、usable、client_count、can_delete、can_start、service_scope 和设备选择。已知发布包二进制摘要、设备和协议匹配的空闲 RPC 自动关联可用，已有客户端连接时跳过协议探测，未匹配版本仍显示为 unverified。一个节点最多登记 16 个活动 RPC，端口不能重复；不提交新建端口时选择发现结果中的空闲建议端口。

DELETE /workers/{id} 支持 `{process_identity:{pid,start_time,boot_id},force:false}`；即使 process_identity 显式为 null，也不能误删后来启动的新进程。force 只允许已确认中断外部连接，不能绕过本平台实例 claim、账户权限、进程 / 服务身份检查。删除保留历史元数据，发现外部 RPC 时保留程序文件；系统级 / 其他账户服务只显示权限限制。

`POST /nodes/{id}/host-key/trust` 新增 replace_machine:true，用户明确确认同 IP 换机后，核查无资源占用，归档旧 worker、清除机器身份 / 凭据引用，保留历史，再执行新密码登录。replace:true 仍只表示同意更换 SSH 主机密钥，不隐式删除旧 RPC 绑定。默认端口 22 使用裸 IP 的受信记录，其他端口用 [IP]:port。
