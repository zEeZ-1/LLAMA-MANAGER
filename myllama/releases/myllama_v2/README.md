# myllama_v2

三台 Jetson Orin Nano 的 llama.cpp 发布包。底座基于上游 11 月 17 日的提交
`9214bdb554544c35e4a9e2c2e8b75d8cfee330ab`，使用 aarch64、CUDA 13.2、SM87 和 RPC。
默认加载 v1 已验证的 Orin MMVQ 优化，使用原版 CUDA MMQ 和 FlashAttention。

## 启动配置

| 配置 | 模型与用途 | 设备顺序 | 4K TTFT | Prefill | 4K + 128 总耗时 |
|---|---|---|---:|---:|---:|
| `35b-prefill` | 35B IQ3_M + MTP，首 token 优先 | .12 → .13 → .11 | 9.79 s | 418.7 token/s | 18.41 s |
| `35b-full` | 35B IQ3_M + MTP，完整请求优先 | .12 → .11 → .13 | 10.42 s | 393.7 token/s | 17.51 s |
| `14b-dspark` | Qwen3-14B Q4_K_M 和 14B DSpark | .12 → .11 → .13 | 见 14B 测速记录 |  |  |

35B 数据来自此前相同三机实测。无提示缓存，prefill 是三轮 4096-token 热测中位数，
完整请求各测一轮。35B 共用 context 5120、batch 4096、ubatch 1024、单并发、Q8_0 KV，
MTP 最多 2 个候选和草稿 ubatch 256。两组完整实测参数和原始阶段数据在 `benchmarks/35b/`。
14B 保留 v1 的启动配置和测速记录。

在 .13 上启动，默认使用 .12 和 .11 已注册的 RPC worker 端口 `50052`。

```bash
./serve.sh
./serve.sh 35b-full
./serve.sh 14b-dspark
./serve.sh 35b-prefill --dry-run
```

`MYLLAMA_MODEL_DIR`、`MYLLAMA_HTTP_HOST`、`MYLLAMA_HTTP_PORT`、
`MYLLAMA_RPC_SERVERS` 和 `MYLLAMA_CUDA_LIB_DIR` 可以覆盖对应路径及地址。
RPC 列表先写 .12，再写 .11。额外的 llama-server 参数可写在预设后面，例如
`./serve.sh 35b-full -- --ubatch-size 512`。查看引擎选项用 `./serve.sh 35b-full -- --help`。

独立开 worker 时，在对应 Jetson 前台执行 `./worker.sh 192.168.50.12` 或
`./worker.sh 192.168.50.11`。已有正式 worker 在监听时直接复用。
做隔离实验时给 worker 设置 `MYLLAMA_RPC_PORT=50190`，
同时把主机的 `MYLLAMA_RPC_SERVERS` 指向这两个实验端口。实验结束退出对应前台进程。

## 实验开关

用 `MYLLAMA_EXPERIMENT` 选择一个实验。默认值 `none` 使用发布配置。
对照 CUDA 算子时主机和对应 worker 选择同一开关，`mtp-kv-only` 只在主机加载。

| 开关 | 内容 |
|---|---|
| `native` | 使用原版算子作对照 |
| `l2-prefetch`、`async-ca`、`async-cg` | L2 提示和两种异步权重预取实验 |
| `mmq-layout64`、`mmq-layout64-l2` | layout64 MMQ，以及实测过的布局与 L2 预取组合 |
| `mmq-j24` | IQ3_S gate/down 的 tile 默认设为 24。此前用 `35b-full --ubatch-size 512` 验证 |
| `iq3s-fusion` | 单独的 CUDA 库，包含 IQ3_S gate/up 融合。设 `GGML_CUDA_DISABLE_IQ3_S_FUSION=1` 作同库对照 |
| `mtp-kv-only` | Qwen3.5 MoE 单层 MTP 的 KV-only 实验 |
| `q6-row4` | 35B 单 token Q6_K 输出头实验 |

layout64/L2 组合的 4K prefill 收益约 0.4%，J24 在 ubatch 512 的对照中有约 0.4 秒收益。
IQ3_S 融合单算子约慢 25%，KV-only 在大逻辑 batch 的对照里约省 60 毫秒。
实验结果依赖模型、设备和输入。权重预取等实验采用各自测试过的加载顺序。

`experiments/expert-parallel/` 保留跨机 MoE 单层原型、真实路由、计划和 A/B 数据。
它是独立基准，主要推理入口使用 RPC 层切分。FP16 FlashAttention 独立探针、
Q6 packed 实验和分阶段诊断工具也随包存档。
编译方案保存在对应实验目录和 `source/`，历史构建脚本保留当时的源码路径及依赖要求。

独立基准使用包内运行时，可先设置
`LD_LIBRARY_PATH="$PWD/bin:/usr/local/cuda-13.2/lib64"`。跨机专家基准的参数依次是
GGUF 文件、随包的 `expert-rpc-*.bin` 计划、实验 RPC 地址和重复次数。

新包的默认 35B 入口已用现有两台正式 worker 跑通 4096 输入加 8 输出，MTP 接受
4 个草稿 token。校验日志在 `benchmarks/35b/v2-smoke.log`。

## 权重和校验

本机发布目录包含 35B、14B 和 14B DSpark 三个 GGUF 硬链接，共享现有模型的磁盘数据。
可迁移压缩包包含运行时、脚本、实验、源码和结果。解压到其他主机后，把权重放到
`models/` 或用 `MYLLAMA_MODEL_DIR` 指向已有权重。

```bash
sha256sum -c SHA256SUMS
sha256sum -c MODELS.sha256
```
