# V1 API

前缀 `/api/v1`。运行后可通过 `/docs` 查看自动生成的 OpenAPI 文档。管理端默认 `127.0.0.1:8000`，前端不访问推理进程端口。

| 方法 | 路径 | 行为 |
| --- | --- | --- |
| GET | `/health` | 管理健康、运行时是否存在、活动 Chat 数、目录扫描状态 |
| GET | `/nodes` | `{nodes: Node[]}` |
| GET | `/nodes/{node_id}` | 单个节点资源及模型、实例计数 |
| GET | `/models?node_id=local` | `{models: Model[], last_scan, rejected_files}`，不包含绝对路径 |
| POST | `/models/refresh` | 202，后台安排扫描，不阻塞 HTTP 等待文件扫描 |
| GET | `/models/{model_id}` | 单模型公开信息 |
| GET | `/instances` | `{instances: Instance[]}`，包含停止及失败历史 |
| POST | `/instances` | 202，创建并启动；模型加载异步进行，轮询状态 |
| GET | `/instances/{id}` | 配置、状态、运行信息和错误 |
| DELETE | `/instances/{id}` | 优雅终止，等待退出；幂等，保留 STOPPED 记录 |
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

上游连接超时 5 秒，读取超时 300 秒；客户端断连后关闭上游请求。V1 没有 API key / 账户管理；应只暴露给可信本机访问。
