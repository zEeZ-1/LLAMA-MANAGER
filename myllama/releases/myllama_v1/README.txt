myllama_v1 | 2026-11-22

三台 Jetson Orin Nano (aarch64 / SM87)，CUDA 13.2。主机 .13，RPC worker
在 .12、.11。Qwen3-14B Q4_K_M 按层切分为 14,14,13。主机 GPU 运行
DSpark Q4_K_M 草稿，每轮最多 6 个候选。三台均加载 MMVQ 优化库。

启动时把 bin/ 和 worker.sh 放到各远端的同一目录，分别执行
./worker.sh 192.168.50.12 和 ./worker.sh 192.168.50.11，再在主机执行
./serve.sh。默认 RPC 端口 50162，HTTP 为 127.0.0.1:18080。
可用 MYLLAMA_RPC_SERVERS、MYLLAMA_RPC_PORT、MYLLAMA_HTTP_PORT 覆盖端口。
权重只需要在主机上，models/ 使用现有权重的硬链接。

启动脚本不调整 GPU 频率。历史测速在 MAXN_SUPER、三台 GPU 锁定
1.02 GHz、temperature=0、无提示缓存下完成。英文问答两次为
15.71 / 16.31 token/s，中文问答为 11.54 / 11.74 token/s。
锁频和动态调频的原始记录保存在 benchmarks/。

sha256sum -c SHA256SUMS 可校验文件。source/ 保存 MMVQ 源码和原构建
配方，后者引用 manifest.json 里的上游提交和原构建目录。
启动使用包内的二进制、共享库及系统 CUDA 运行时。
