# llama.cpp Inference Manager
## V1 开发设计文档

**版本：** V1.1  
**项目阶段：** MVP  
**核心目标：** 构建一个具有基础实例管理和 Chat 能力的 llama.cpp 推理管理平台。

---

# 1. 项目概述

## 1.1 项目目标

开发一个前后端分离的 LLM 推理实例管理平台。

平台通过独立的管理后端控制推理实例生命周期，提供统一的前端界面。

第一版重点实现：

- 节点信息展示。
- 模型文件自动发现与展示。
- 创建 llama.cpp 推理实例。
- 启动和销毁实例。
- 实例运行状态管理。
- 选择实例进行 Chat 对话。
- 多会话管理。
- Chat 请求的流式转发。

系统采用前后端分离架构，Management Backend 不参与模型推理，只负责管理推理进程和代理请求。

## 1.2 核心设计原则

1. Runtime 独立：每个推理实例对应一个独立的普通模式 llama-server 进程。
2. 管理与推理分离：管理后端负责实例生命周期，推理后端负责推理执行。
3. 统一 Instance 抽象：不将 Instance 简单等同于 PID。
4. 统一请求入口：前端不直接访问 llama-server，由管理后端代理请求。
5. 后端可插拔：不同推理后端统一放置在 `inference_backends/`。
6. 模型集中管理：所有模型统一放置在 `models/`，支持自动扫描和发现。
7. 可扩展：为未来多节点、Node Agent、vLLM 等 Runtime Adapter 预留接口。
8. MVP 优先：第一版只实现必要功能，不提前开发复杂调度系统。

## 1.3 V1 范围

| 功能 | V1 是否实现 |
|---|---|
| 节点展示 | 是 |
| 单机节点信息 | 是 |
| 模型目录管理 | 是 |
| GGUF 自动扫描 | 是 |
| 模型自动发现 | 是 |
| 创建 Instance | 是 |
| 销毁 Instance | 是 |
| Instance 状态管理 | 是 |
| llama.cpp 普通模式启动 | 是 |
| Chat 请求代理 | 是 |
| SSE 流式响应 | 是 |
| 多会话管理 | 是 |
| 会话持久化 | 是 |
| GPU 自动资源调度 | 否 |
| 多节点部署 | 否 |
| Node Agent | 否 |
| llama.cpp Router Mode | 否 |
| vLLM Runtime | 否 |
| 分布式 KV Cache | 否 |
| 自动负载均衡 | 否 |

---

# 2. 项目目录与文件组织

这一部分属于项目的基础设计，V1 就确定下来。

## 2.1 项目根目录

```text
llama-inference-manager/
|
+--- frontend/
|    +--- src/
|
+--- backend/
|    +--- app/
|
+--- inference_backends/
|    |
|    +--- llama.cpp/
|    |    +--- ggml/
|    |    +--- src/
|    |    +--- tools/
|    |    +--- include/
|    |    +--- build/
|    |    +--- CMakeLists.txt
|    |
|    +--- vllm/                 # Future
|    |
|    +--- other-runtime/        # Future
|
+--- models/
|    |
|    +--- .gitkeep
|    |
|    +--- qwen/
|    |    +--- Qwen3-4B-Q4_K_M.gguf
|    |
|    +--- llama/
|    |    +--- Llama-3-8B-Q4_K_M.gguf
|    |
|    +--- other/
|
+--- config/
|    +--- config.yaml
|    +--- config.example.yaml
|
+--- data/
|    +--- database/
|    +--- logs/
|
+--- docs/
|    +--- architecture.md
|    +--- api.md
|    +--- development.md
|
+--- README.md
+--- .gitignore
```

## 2.2 推理后端目录：inference_backends/

该目录专门存放实际的推理后端代码和运行环境。

V1 只放置 llama.cpp。

建议将上游 llama.cpp 仓库作为 Git Submodule，固定到经过验证的版本或 Commit。

目录：

```text
inference_backends/
    |
    +--- llama.cpp/
         |
         +--- ggml/
         +--- src/
         +--- tools/
         +--- include/
         +--- build/
         +--- CMakeLists.txt
```

编译后主要使用：

```text
inference_backends/llama.cpp/build/bin/llama-server
```

具体可执行文件位置需要通过配置确定，以兼容不同平台和构建方式。

重要约束：

- 不修改 llama.cpp 上游源码。
- 不将管理后端代码混入 llama.cpp。
- 编译产物不提交到 Git。
- Runtime Adapter 负责调用对应的后端可执行文件。
- 不允许管理后端依赖某个固定的绝对路径。

未来扩展：

```text
inference_backends/
    |
    +--- llama.cpp/
    |
    +--- vllm/
    |
    +--- other-runtime/
```

增加新推理后端时，只需要实现对应的 Runtime Adapter，不需要改变前端的 Instance 管理逻辑。

## 2.3 模型目录：models/

`models/` 是平台默认的模型存储目录。

用户只需要将模型文件放入该目录，后端就应该能够自动识别。

例如：

```text
models/
    |
    +--- qwen/
    |    +--- Qwen3-4B-Q4_K_M.gguf
    |    +--- Qwen3-8B-Q4_K_M.gguf
    |
    +--- llama/
    |    +--- Llama-3-8B-Q4_K_M.gguf
    |
    +--- deepseek/
         +--- DeepSeek-R1-Distill-Qwen-7B.gguf
```

设计要求：

1. 支持 `.gguf` 文件。
2. 支持子目录递归扫描。
3. 不要求固定的目录层级。
4. 文件放入目录后自动发现。
5. 文件删除后自动更新模型列表。
6. 文件扫描不能阻塞 HTTP API。
7. 模型路径由后端管理，前端不直接指定任意文件路径。

V1 暂时不实现模型下载、模型转换、量化等功能。

模型文件由用户自行放入目录。

## 2.4 自动模型发现机制

这是 V1 的必要功能。

不能只在 Management Backend 启动时扫描一次。

建议使用：

- 启动时全量扫描。
- 文件系统事件监听。
- 定时扫描作为兜底。

实现可以使用 Python `watchdog`。

工作流程：

```text
              Backend Start
                    |
                    v
              Initial Scan
                    |
                    v
              Model Catalog
                    |
          +---------+---------+
          |                   |
          v                   v
    File Watcher         Periodic Scan
          |                   |
          +---------+---------+
                    |
                    v
             Detect Changes
                    |
                    v
             Update Catalog
                    |
                    v
             Frontend Refresh
```

### 文件新增

当用户放入一个新的 GGUF 文件：

1. Watchdog 检测文件新增或移动事件。
2. 对事件进行短暂防抖。
3. 检查文件大小是否稳定，避免将正在复制的模型识别为完整模型。
4. 检查文件扩展名。
5. 读取 GGUF 元数据。
6. 生成 Model ID。
7. 更新 Model Catalog。
8. 前端下一次刷新模型列表时显示新模型。

文件大小稳定检查建议采用连续两次检查，间隔约 1 秒。

如果文件还在写入，则延迟识别。

### 文件删除

检测到文件删除：

1. 从 Model Catalog 中移除对应的可用模型。
2. 前端不再允许使用该文件创建新实例。
3. 如果已经有实例加载了该模型，不主动终止现有实例。
4. 保留已有 Instance 记录和运行状态。

### 定时扫描

建议默认每 30 秒执行一次增量扫描。

文件系统事件负责及时发现变化，定时扫描负责补偿事件丢失、网络文件系统等情况。

### 前端刷新

V1 可以使用 TanStack Query 定时刷新模型列表，间隔 5～10 秒。

不需要为了模型发现单独建立复杂的消息队列。

## 2.5 配置文件

建议统一使用 YAML。

示例：

```yaml
server:
  host: "127.0.0.1"
  port: 8000

runtime:
  default_backend: "llama.cpp"

  llama_cpp:
    root: "./inference_backends/llama.cpp"
    server_binary: "./inference_backends/llama.cpp/build/bin/llama-server"

models:
  directories:
    - "./models"

  recursive: true
  watch: true
  scan_interval_seconds: 30

storage:
  database: "./data/database/manager.db"
  logs: "./data/logs"

instances:
  host: "127.0.0.1"
  port_range:
    start: 18000
    end: 18999
  startup_timeout_seconds: 180
```

要求：

- 所有路径以项目根目录为基准解析。
- 支持通过环境变量覆盖路径。
- Windows、Linux、macOS 均可以通过配置指定实际 llama-server 路径。
- 模型目录允许未来配置多个路径。
- V1 默认使用项目内的 `models/`。

---

# 3. 整体架构

## 3.1 系统架构

采用 Frontend + Management Backend + Inference Runtime 三层结构。

```text
                   Frontend
                       |
          +------------+------------+
          |            |            |
       Dashboard     Instances     Chat
          |            |            |
          +------------+------------+
                       |
                  HTTP / SSE
                       |
                       v
             Management Backend
                       |
        +--------------+--------------+
        |              |              |
   Node Manager   Instance Manager  Gateway
        |              |              |
   Model Catalog   Process Manager    |
        |              |              |
        +--------------+--------------+
                       |
                Runtime Adapter
                       |
              LlamaCpp Runtime
                       |
          +------------+------------+
          |            |            |
       Instance A   Instance B   Instance C
          |            |            |
      llama-server llama-server llama-server
          |            |            |
          +------------+------------+
                       |
                  GGML Backend
                       |
                  CPU / GPU
```

## 3.2 Frontend

负责：

- 展示节点和设备。
- 展示自动发现的模型。
- 创建、查看和销毁实例。
- Chat 对话。
- 会话管理。
- 展示实例状态。

Frontend 不直接操作系统进程，也不直接访问 llama-server 的内部端口。

## 3.3 Management Backend

系统核心控制层。

负责：

- Node 信息管理。
- Model 自动扫描和管理。
- Instance 创建和销毁。
- llama-server 进程管理。
- Instance 状态管理。
- 推理请求转发。
- Chat 流式响应。
- Instance 信息持久化。

## 3.4 Inference Runtime

V1 只支持 llama.cpp。

每个 Instance 启动一个独立的 llama-server 普通模式进程。

不使用 llama.cpp Router Mode。

---

# 4. 核心数据结构设计

## 4.1 Node

Node 表示一个能够运行推理实例的计算节点。

V1 只有一个本地节点，但从数据结构上支持未来多个节点。

```python
class Node:
    id: str
    name: str

    node_type: str
    hostname: str

    status: str

    os: str
    architecture: str

    cpu_info: dict
    memory_info: dict

    devices: list

    created_at: datetime
    updated_at: datetime
```

V1：

- 自动注册一个 Local Node。
- ID 固定为 `local`。
- 启动时获取本机系统信息。
- 获取 CPU 和内存信息。
- 尽可能获取 GPU 信息。
- 不实现远程节点通信。

## 4.2 Device

```python
class Device:
    id: str
    name: str

    type: str
    index: int

    memory_total: int
    memory_free: int

    properties: dict
```

Node 表示机器，Device 表示机器上的计算设备。

## 4.3 Model

Model 表示 Model Catalog 自动发现的一个模型文件。

```python
class Model:
    id: str

    name: str
    filename: str

    path: str
    relative_path: str

    format: str
    size_bytes: int

    architecture: str | None
    metadata: dict

    node_id: str

    modified_at: datetime
    fingerprint: str
```

设计要求：

- `path` 是后端内部使用的规范化绝对路径，不直接暴露给前端。
- `relative_path` 用于前端展示。
- `fingerprint` 用于判断文件是否被替换或修改。
- `id` 应保持稳定，建议由 node_id 和规范化相对路径生成。
- 模型文件内容发生变化时重新解析元数据。
- 不需要对数十 GB 的模型文件每次扫描都计算完整 SHA256。

V1 只识别 GGUF。

Model Catalog 是模型信息的统一管理入口。

不能由前端自行维护模型文件列表。

## 4.4 InstanceConfig

```python
class InstanceConfig:
    name: str

    node_id: str
    model_id: str

    backend: str

    context_size: int
    batch_size: int
    ubatch_size: int

    parallel: int
    threads: int

    n_gpu_layers: int

    cache_type_k: str
    cache_type_v: str

    extra_options: dict
```

第一版支持基本推理参数。

## 4.5 Instance

```python
class Instance:
    id: str
    name: str

    node_id: str
    model_id: str

    runtime_type: str

    config: InstanceConfig

    status: str

    pid: int | None

    endpoint: str | None
    internal_port: int | None

    error_message: str | None

    created_at: datetime
    started_at: datetime | None
    stopped_at: datetime | None
```

同一个 Model 可以创建多个 Instance。

不同 Instance 拥有独立进程、推理上下文和 KV Cache。

注意：多个进程加载同一模型文件通常会重复占用模型权重显存。

## 4.6 Instance 状态

```python
class InstanceStatus:
    CREATED = "created"
    STARTING = "starting"
    READY = "ready"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"
```

状态转换：

```text
CREATED
   |
   v
STARTING
   |
   +--------> FAILED
   |
   v
READY
   |
   v
STOPPING
   |
   v
STOPPED
```

READY 表示：

- 子进程已经启动。
- 模型加载完成。
- 健康检查通过。
- 可以接受推理请求。

## 4.7 ChatSession

V1 使用 IndexedDB 持久化会话。

```typescript
interface ChatSession {
    id: string;

    title: string;

    node_id: string;
    instance_id: string;

    model_name: string;

    messages: ChatMessage[];

    created_at: number;
    updated_at: number;
}
```

## 4.8 ChatMessage

```typescript
interface ChatMessage {
    id: string;

    role: "system" | "user" | "assistant";

    content: string;

    status: "pending" | "streaming" | "completed" | "failed";

    created_at: number;

    error?: string;
}
```

会话历史由前端维护。

每次发送消息时提交完整 messages。

ChatSession 不绑定 llama.cpp 内部 Slot，也不依赖 KV Cache 跨请求持久化。

---

# 5. Backend 模块设计

使用 Python + FastAPI，采用模块化单体。

## 5.1 Node Manager

负责：

- 获取本机节点信息。
- 获取 CPU、内存和 GPU 信息。
- 获取节点上的模型数量。
- 获取节点上的 Instance 数量。

V1 只实现 LocalNodeProvider。

## 5.2 Model Catalog

这是新增的核心模块。

负责：

- 管理模型扫描目录。
- 启动时全量扫描。
- 监听模型目录变化。
- 定时增量扫描。
- 识别 GGUF 文件。
- 解析 GGUF 元数据。
- 生成稳定 Model ID。
- 更新模型列表。
- 检测模型文件删除和替换。

建议实现接口：

```python
class ModelCatalog:

    async def initialize(self):
        pass

    async def scan_all(self):
        pass

    async def refresh(self):
        pass

    async def list_models(self, node_id=None):
        pass

    async def get_model(self, model_id):
        pass

    async def on_file_changed(self, path):
        pass

    async def on_file_deleted(self, path):
        pass
```

扫描任务必须异步执行，不能阻塞 API。

模型路径必须进行白名单和规范化校验，禁止通过符号链接访问允许目录以外的文件。

## 5.3 Instance Manager

负责：

- 创建实例。
- 验证实例配置。
- 根据 model_id 查询 Model Catalog。
- 调用 Runtime Adapter。
- 启动实例。
- 获取实例信息。
- 销毁实例。
- 更新实例状态。
- 处理启动失败。

创建实例时，前端只提交 `model_id`，不能直接指定模型绝对路径。

## 5.4 Runtime Adapter

```python
class RuntimeAdapter:

    async def create(self, config):
        pass

    async def start(self, instance):
        pass

    async def stop(self, instance):
        pass

    async def get_status(self, instance):
        pass

    async def health_check(self, instance):
        pass
```

V1：

```text
RuntimeAdapter
      |
      +--- LlamaCppAdapter
```

未来：

```text
RuntimeAdapter
      |
      +--- LlamaCppAdapter
      +--- VllmAdapter
      +--- OtherAdapter
```

## 5.5 Process Manager

使用 asyncio subprocess 管理 llama-server。

启动参数示意：

```python
cmd = [
    llama_server_binary,
    "-m", model_path,
    "--host", "127.0.0.1",
    "--port", str(port),
    "-c", str(context_size),
    "-ngl", str(n_gpu_layers),
]
```

要求：

- 使用参数数组，不使用 Shell 字符串。
- 可执行文件路径来自配置。
- 模型路径由 Model Catalog 提供。
- 端口由后端分配。
- stdout/stderr 保存到独立日志。
- 监听进程退出。
- 优雅终止。
- 超时后强制终止。

## 5.6 Health Check

实例启动后：

1. 启动 llama-server。
2. 获取 PID。
3. 检查进程状态。
4. 定期请求内部 `/health`。
5. 等待模型加载完成。
6. 成功后进入 READY。
7. 超时或异常进入 FAILED。

## 5.7 Gateway

负责将前端请求转发给指定 Instance。

请求链路：

```text
Frontend
   |
   v
Management Backend
   |
   v
Gateway
   |
   | 查找 Instance
   | 检查 READY
   | 获取 Endpoint
   v
llama-server
   |
   v
GGML Backend
   |
   v
SSE Response
   |
   v
Frontend
```

支持：

- 普通 JSON 响应。
- SSE 流式响应。
- 请求超时。
- 客户端断开后的上游请求取消。
- 上游异常处理。

---

# 6. Backend API 设计

统一使用 `/api/v1` 前缀。

## 6.1 Node API

```http
GET /api/v1/nodes
GET /api/v1/nodes/{node_id}
```

## 6.2 Model API

### 获取模型列表

```http
GET /api/v1/models
```

支持：

```http
GET /api/v1/models?node_id=local
```

返回：

```json
{
  "models": [
    {
      "id": "model-uuid",
      "name": "Qwen3-4B",
      "filename": "Qwen3-4B-Q4_K_M.gguf",
      "relative_path": "qwen/Qwen3-4B-Q4_K_M.gguf",
      "format": "gguf",
      "size_bytes": 2500000000,
      "architecture": "qwen3",
      "node_id": "local"
    }
  ]
}
```

### 手动刷新模型目录

```http
POST /api/v1/models/refresh
```

该接口用于手动触发扫描。

正常情况下不需要用户手动调用，文件监听器会自动更新模型列表。

### 获取模型详情

```http
GET /api/v1/models/{model_id}
```

## 6.3 Instance API

```http
GET /api/v1/instances

GET /api/v1/instances/{instance_id}

POST /api/v1/instances

DELETE /api/v1/instances/{instance_id}
```

创建请求：

```json
{
  "name": "Qwen3-4B",
  "node_id": "local",
  "model_id": "model-uuid",
  "config": {
    "context_size": 4096,
    "batch_size": 512,
    "ubatch_size": 128,
    "parallel": 1,
    "threads": 4,
    "n_gpu_layers": 99,
    "cache_type_k": "f16",
    "cache_type_v": "f16"
  }
}
```

创建请求使用 HTTP 202，返回 Instance ID 和 STARTING 状态。

## 6.4 Chat API

```http
POST /api/v1/instances/{instance_id}/chat/completions
```

请求尽量兼容 OpenAI Chat Completions。

Backend 根据 Instance ID 找到对应的 llama-server，转发请求。

## 6.5 Health API

```http
GET /api/v1/health
```

---

# 7. Frontend 页面设计

使用 React + TypeScript + Vite。

整体采用侧边栏 + 主内容区域。

## 7.1 Dashboard

展示：

- 节点数量。
- 实例数量。
- READY 实例数量。
- CPU 使用情况。
- 内存使用情况。
- GPU 使用情况。
- 模型文件数量。

## 7.2 Nodes 页面

类似 EXO 的节点展示方式。

V1 只有一个 Local Node。

展示：

- 节点名称。
- Hostname。
- Online 状态。
- 操作系统。
- CPU。
- RAM。
- GPU 列表。
- 模型数量。
- 实例数量。

提供：

```text
[查看详情] [创建实例]
```

## 7.3 Models 页面

展示 Model Catalog 中自动发现的模型。

展示：

- 模型名称。
- 文件名。
- 所属节点。
- 文件路径（相对路径）。
- 文件大小。
- 模型架构。
- 文件格式。

提供：

- 刷新模型列表。
- 创建实例。

模型列表通过后端 API 获取。

用户向 `models/` 放入 GGUF 文件后，页面在下一次自动刷新时应显示新模型。

不要求用户手动添加模型记录。

## 7.4 创建 Instance 页面

创建流程：

1. 选择 Node。
2. 选择 Model。
3. 输入实例名称。
4. 设置 Context Size。
5. 设置 GPU Layers。
6. 设置 Parallel。
7. 设置其他基础参数。
8. 创建。

模型选择列表只展示 Model Catalog 中可用的模型。

## 7.5 Instances 页面

展示：

- Instance Name。
- Model。
- Node。
- Runtime。
- Status。
- PID。
- 创建时间。

提供：

- 查看详情。
- 打开 Chat。
- 销毁实例。

## 7.6 Chat 页面

参考 EXO Chat 的交互体验。

左侧为会话列表，右侧为对话区域。

支持：

- 新建会话。
- 切换会话。
- 删除会话。
- 选择 READY Instance。
- Markdown。
- 代码块。
- SSE 流式输出。
- 停止生成。
- 错误提示。
- 自动保存会话。

历史会话对应的实例被销毁时，保留历史消息，允许选择其他实例继续对话。

---

# 8. 会话管理

使用 IndexedDB + Dexie。

会话保存：

- Session ID。
- Title。
- Node ID。
- Instance ID。
- Model Name。
- Messages。
- 创建时间。
- 更新时间。

每次发送请求提交完整 messages。

会话与 llama.cpp 内部 Slot 解耦。

---

# 9. 实例生命周期

## 9.1 创建流程

```text
Frontend
   |
   | POST /instances
   v
Instance Manager
   |
   | Validate Node
   | Validate Model ID
   | Resolve Model Path
   v
Runtime Adapter
   |
   | Build Command
   v
Process Manager
   |
   | Spawn llama-server
   v
STARTING
   |
   | Health Check
   v
READY
   |
   v
Frontend Update
```

## 9.2 销毁流程

```text
Frontend
   |
   | DELETE /instances/{id}
   v
Instance Manager
   |
   v
STOPPING
   |
   | Reject New Requests
   | Terminate Process
   | Wait For Exit
   | Force Kill If Timeout
   v
STOPPED
```

销毁操作应当幂等。

---

# 10. 配置与持久化

V1 使用 SQLite。

持久化：

- Instance。
- InstanceConfig。
- Instance 状态。
- PID。
- 内部端口。
- 错误信息。
- 生命周期时间。

Model 文件系统是事实来源。

Backend 启动时重新扫描模型目录，恢复 Model Catalog。

前端会话保存到 IndexedDB。

管理后端重启时需要检查之前的 Instance 进程是否仍然存在，并恢复正确状态。

---

# 11. 项目目录完整结构

```text
llama-inference-manager/
|
+--- frontend/
|    +--- src/
|         +--- components/
|         |    +--- layout/
|         |    +--- node/
|         |    +--- model/
|         |    +--- instance/
|         |    +--- chat/
|         |
|         +--- pages/
|         |    +--- Dashboard/
|         |    +--- Nodes/
|         |    +--- Models/
|         |    +--- Instances/
|         |    +--- Chat/
|         |
|         +--- services/
|         +--- stores/
|         +--- database/
|         +--- types/
|         +--- hooks/
|
+--- backend/
|    +--- app/
|         +--- api/
|         |    +--- routes/
|         |         +--- nodes.py
|         |         +--- models.py
|         |         +--- instances.py
|         |         +--- chat.py
|         |
|         +--- core/
|         +--- schemas/
|         +--- services/
|         |    +--- node_manager.py
|         |    +--- model_catalog.py
|         |    +--- instance_manager.py
|         |    +--- gateway.py
|         |
|         +--- runtime/
|         |    +--- base.py
|         |    +--- llama_cpp.py
|         |
|         +--- process/
|         |    +--- supervisor.py
|         |    +--- port_allocator.py
|         |
|         +--- repositories/
|         +--- main.py
|
+--- inference_backends/
|    +--- llama.cpp/
|         +--- ggml/
|         +--- src/
|         +--- tools/
|         +--- include/
|         +--- build/
|
+--- models/
|    +--- .gitkeep
|
+--- config/
|    +--- config.yaml
|    +--- config.example.yaml
|
+--- data/
|    +--- database/
|    +--- logs/
|
+--- docs/
|    +--- architecture.md
|    +--- api.md
|    +--- development.md
|
+--- README.md
+--- .gitignore
```

---

# 12. 技术栈

| 模块 | 技术 |
|---|---|
| Frontend | React |
| Language | TypeScript |
| Build | Vite |
| UI | Tailwind CSS + shadcn/ui |
| State | Zustand |
| Server State | TanStack Query |
| Session Storage | IndexedDB + Dexie |
| Backend | Python |
| API | FastAPI |
| Async HTTP | httpx |
| Process | asyncio subprocess |
| File Watcher | watchdog |
| Database | SQLite |
| ORM | SQLAlchemy |
| System Info | psutil |
| Streaming | SSE |
| Runtime | llama.cpp |

---

# 13. 安全与稳定性要求

1. llama-server 默认监听 127.0.0.1。
2. 前端只访问 Management Backend。
3. 禁止直接执行用户输入的 Shell 命令。
4. 模型路径必须经过允许目录校验。
5. Instance 端口由后端分配。
6. 不允许通过 Gateway 指定任意 URL。
7. 进程退出必须能够更新 Instance 状态。
8. 启动失败必须记录日志和错误原因。
9. 进程终止需要处理超时。
10. SSE 断开时释放上游 HTTP 连接。
11. 不允许模型扫描阻塞主事件循环。
12. 正在复制的模型文件不能被识别为可用模型。
13. 已加载模型文件被删除时，不主动终止对应的运行实例。

---

# 14. 开发里程碑

## Milestone 1：项目骨架

- 初始化前端。
- 初始化后端。
- 创建项目目录。
- 初始化配置系统。
- 初始化 SQLite。
- 配置 llama.cpp 后端路径。

验收：前后端可以启动，后端能够找到 llama-server。

## Milestone 2：Node 和 Model

- 本机节点发现。
- CPU、内存信息。
- GPU 信息。
- GGUF 目录扫描。
- Watchdog 文件监听。
- Model Catalog。
- Models 页面。

验收：

将一个 GGUF 文件放入 `models/`，无需重启后端，前端能够自动展示。

## Milestone 3：Instance 管理

- Instance 数据模型。
- Instance Repository。
- Runtime Adapter。
- Process Manager。
- llama-server 启动。
- 健康检查。
- 实例销毁。
- 实例状态展示。

验收：

能够创建两个独立 llama-server 实例，并分别销毁。

## Milestone 4：Chat 链路

- Instance Gateway。
- Chat Completions API。
- SSE 透传。
- 前端 Chat 页面。
- 实例选择。
- 流式消息展示。

验收：

前端能够向指定 Instance 发送消息并实时接收结果。

## Milestone 5：会话管理

- IndexedDB。
- 新建会话。
- 切换会话。
- 删除会话。
- 自动保存。
- 恢复历史消息。

验收：

刷新前端后会话仍然存在。

## Milestone 6：稳定性

- 进程异常退出检测。
- 启动超时处理。
- 销毁超时处理。
- 后端重启恢复。
- SSE 断连处理。
- 错误提示完善。

---

# 15. 最终验收标准

### A. 推理后端管理

- 项目中存在 `inference_backends/llama.cpp/`。
- Backend 能找到 llama-server 可执行文件。
- 后续可以在 `inference_backends/` 增加其他 Runtime。

### B. 模型自动发现

- 项目中存在 `models/`。
- 支持递归扫描 GGUF。
- 新增模型文件后自动发现。
- 前端自动显示新模型。
- 删除模型后更新可用模型列表。
- 不需要重启 Management Backend。

### C. 节点和实例

- 前端显示 Local Node。
- 可以选择节点。
- 可以选择自动发现的模型。
- 可以创建两个独立实例。
- 可以分别查看和销毁。

### D. Chat

- 选择 READY Instance。
- 创建会话。
- 发送消息。
- 后端根据 Instance ID 转发。
- llama-server 执行推理。
- SSE 实时返回。
- 会话持久化。

### E. 稳定性

- 模型不存在时拒绝创建。
- 启动失败时显示 FAILED。
- 实例未 READY 时拒绝推理。
- 进程异常退出时更新状态。
- 销毁后确认进程退出。

---

# 16. 后续扩展方向

```text
V1: Single Node Instance Manager
    |
    +-- llama.cpp
    +-- Models Catalog
    +-- Instance Manager
    +-- Chat Gateway
    |
    v
V2: Multi-Node Management
    |
    +-- Node Agent
    +-- Remote Instance
    +-- Device Discovery
    |
    v
V3: Multi-Runtime Support
    |
    +-- llama.cpp
    +-- vLLM
    +-- Runtime Adapter
    |
    v
V4: Scheduling
    |
    +-- Resource Scheduling
    +-- Load Balancing
    +-- Instance Placement
    |
    v
V5: Distributed Inference
    |
    +-- Prefill / Decode Separation
    +-- Distributed KV Cache
    +-- Cross-Node Communication
    +-- Advanced Scheduling
```

---

# 17. 最终架构约束

1. Node、Model、Instance、Session 必须是独立的数据实体。
2. Instance 必须有独立 ID，不能使用 PID 作为主键。
3. InstanceConfig 和 InstanceRuntimeInfo 必须分离。
4. Runtime Adapter 必须独立于 Instance Manager。
5. 前端不得直接访问 llama-server。
6. Gateway 必须通过 Instance ID 找到目标 Runtime。
7. 会话数据不得与 llama.cpp 内部 Slot 绑定。
8. Instance 生命周期必须有明确状态机。
9. Node V1 只实现 LocalNodeProvider，但不能将 Node 概念写死在业务逻辑中。
10. 推理后端必须统一放在 `inference_backends/`。
11. 模型文件必须统一由 Model Catalog 管理。
12. `models/` 中的模型文件必须支持自动发现。
13. 模型路径由后端管理，前端只使用 Model ID。
14. 新增 Runtime 不应改变前端的核心 Instance 管理流程。

**V1 的最终目标：建立一套清晰、可靠、可扩展的推理实例管理基础设施，打通 Frontend → Management Backend → llama-server → GGML Backend → Frontend 的完整请求链路，并实现推理后端与模型文件的统一管理。**