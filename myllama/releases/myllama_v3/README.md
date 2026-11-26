# myllama_v3

面向三台 Jetson Orin Nano 的 llama.cpp 运行包，包含 aarch64 / SM87 主控与 RPC 二进制、CUDA 算子库和启动脚本。默认模型为 Qwen3.5-35B-A3B-IQ3_M，默认关闭 MTP，使用五块流水预填充。设置 `MYLLAMA_MTP=1` 可启用 GGUF 内置 MTP，每步最多提议 2 个 token，草稿 ubatch 为 256。模型文件单独存放。

## 启动

将同一归档解压到三台设备。在目标设备上使用 Jetson CUDA 13.2 运行库、cuBLAS 以及本机驱动。若 CUDA 库目录不同，设置 MYLLAMA_CUDA_LIB_DIR。三台设备先各进入 myllama_v3 目录。

~~~bash
# .12
./worker.sh 192.168.50.12

# .11
./worker.sh 192.168.50.11

# .13
./serve.sh
~~~

主控默认在 127.0.0.1:18236 提供服务，远端各使用 50236。模型可放入包的 models/，或放在主控的 ~/models/Qwen3.5-35B-A3B-IQ3_M/。可用 MYLLAMA_MODEL 指定 GGUF 的完整路径。检查模型文件时在其所在目录执行 sha256sum -c 并传入包中 models/SHA256SUMS 的绝对路径。

~~~bash
./serve.sh --dry-run
MYLLAMA_MTP=1 ./serve.sh
sha256sum -c SHA256SUMS
~~~

默认启用单请求、context 5120、batch 4096、ubatch 832、Q8_0 KV 和 FlashAttention。MTP 模式将主模型 40 层按 14/15/11 分配到 .12/.13/.11，embedding 在 .12，最后一层、输出和 MTP 头集中在 .11。开启 MTP 时，运行中的长提示采用能为草稿保留中间状态的兼容调度。关闭 MTP 后，启动脚本自动选用已验证的 12/14/14 分片和五块流水。可用 MYLLAMA_TENSOR_SPLIT 显式覆盖分片。

MYLLAMA_RPC_SERVERS 为两个逗号分隔的 RPC 地址；MYLLAMA_RPC_PORT 控制 worker 端口；MYLLAMA_HTTP_HOST 与 MYLLAMA_HTTP_PORT 控制主控监听地址和端口。脚本设置相对于发布目录的库路径，使用随包提供的 MMQ、MMVQ、L2 预取、GDN 和卷积转置实现。

## 三机实测

单并发、三台 MAXN_SUPER、千兆以太网。测试期间临时暂停 GDM。以下开关对照都固定主模型 14/15/11 与相同算子，使用贪心生成 64 个 token，各类交替测三次并取中位数。TTFT 从客户端 POST 计到第一条携带生成 token 的 SSE 事件，生成速度按首个事件之后发出的 token 数与时间计算。每条请求的提示缓存命中均为 0。

| 输入 | MTP 首 token | 关闭首 token | MTP 生成 | 关闭生成 | 草稿接受率 |
|---|---:|---:|---:|---:|---:|
| 128 日志 | 0.708 s | 0.642 s | 13.46 tok/s | 10.89 tok/s | 77.6% |
| 1024 日志 | 2.653 s | 2.346 s | 12.81 tok/s | 10.72 tok/s | 72.5% |
| 4096 日志 | 9.272 s | 5.055 s | 13.15 tok/s | 10.43 tok/s | 79.2% |
| 4096 代码 | 9.591 s | 5.261 s | 14.23 tok/s | 10.38 tok/s | 88.9% |

服务就绪后首条请求直接提交原始 UTF-8 代码，4096 输入、64 输出。MTP 首 token 为 9.858 s。额外的 4096 代码输入、128 输出单次总耗时，MTP 为 18.015 s，关闭为 17.417 s。开关两组共 14 对贪心请求，其中 5 对的完整输出 token ID 相同。逐对差异见 JSON 汇总。

上一版 4096 输入 44/44 次低于 5 s 的记录使用 MTP 关闭、12/14/14 的分片。包中 previous-mtp-off.json 保留了该口径的数据。

要复测开关对照，在主控启动每种配置后，从另一终端运行对应的测量命令。切换配置前停止主控服务。

~~~bash
MYLLAMA_MTP=1 ./serve.sh
python3 benchmarks/measure.py --mode mtp-on --output on.jsonl
MYLLAMA_MTP=0 MYLLAMA_TENSOR_SPLIT=13.5,15,13.5 ./serve.sh
python3 benchmarks/measure.py --mode mtp-off --output off.jsonl
~~~

[逐次指标](benchmarks/mtp-speeds.csv) · [原始请求结果](benchmarks/mtp-requests.jsonl) · [汇总](benchmarks/mtp-summary.json) · [构建和运行信息](manifest.json)

引擎源码以提交 9214bdb554544c35e4a9e2c2e8b75d8cfee330ab 为基础，在该提交运行 git apply source/myllama_v3.patch 可恢复 v3 引擎改动。source/ 另附集成 CUDA 库的两个定制算子源码，归档直接提供实测使用的动态库。
