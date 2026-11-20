# 推理运行时

llama.cpp 的源码、构建产物及指向本机源码的软链接不提交到管理平台仓库。

使用 `python3 scripts/setup_runtime.py` 获取固定提交；已有构建可用 `--source /path/to/llama.cpp` 复用，或通过 `LIM_LLAMA_SERVER` 配置二进制路径。

V1 验收版本：`9214bdb554544c35e4a9e2c2e8b75d8cfee330ab`。管理端的 Runtime Adapter 位于 `backend/app/runtime/`，不修改上游源码。
