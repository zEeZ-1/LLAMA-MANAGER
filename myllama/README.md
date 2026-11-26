# myllama 自主优化推理后端

存放基于 llama.cpp 自主修改、调优的推理后端版本，并通过本仓库的 GitHub Releases 分发运行包。后端版本 `myllama_v1` / `myllama_v2` / `myllama_v3` 与 AscNet 应用版本独立。

从 `/home/nvidia/llama-deploy/releases` 导入既有发布版本。发布目录内的源码、脚本、说明、测速记录和校验清单保持原样。v2、v3 的现有运行包及其 SHA256 原样保留；v1 补充不含权重的运行包。

## 版本

| 版本 | 内容 | 发布下载 |
| --- | --- | --- |
| [myllama_v1](releases/myllama_v1/README.txt) | Orin MMVQ 优化，Qwen3-14B Q4_K_M + DSpark，三机 RPC 层切分 | [运行包与 SHA256](https://github.com/zEeZ-1/LLAMA-MANAGER/releases/tag/myllama_v1) |
| [myllama_v2](releases/myllama_v2/README.md) | 在 v1 基础上增加 35B IQ3_M + MTP 预设及算子、预取、专家并行等实验归档 | [运行包与 SHA256](https://github.com/zEeZ-1/LLAMA-MANAGER/releases/tag/myllama_v2) |
| [myllama_v3](releases/myllama_v3/README.md) | Qwen3.5-35B-A3B-IQ3_M，默认关闭 MTP、五块流水预填充，可选内置 MTP；引擎补丁与定制 CUDA 算子 | [运行包与 SHA256](https://github.com/zEeZ-1/LLAMA-MANAGER/releases/tag/myllama_v3) |

三个版本的底座提交均为 `9214bdb554544c35e4a9e2c2e8b75d8cfee330ab`；准确的完整提交、配置及历史测试数据见各版本 `manifest.json`。运行平台为 **Linux aarch64 / Jetson Orin Nano SM87 / CUDA 13.2**（v1、v2 另标注 L4T R39.2.1），不作为其他 CPU / CUDA / JetPack 平台的通用发布包。

机器可读的版本、资产 URL、大小和 SHA256 见 [catalog.json](catalog.json)。测速数字是原发布目录中的历史记录，此次导入没有重跑推理性能测试。

## 目录和分发

```text
myllama/
├── README.md
├── catalog.json
├── package_release.py
└── releases/
    ├── myllama_v1/
    ├── myllama_v2/
    ├── myllama_v3/
    ├── myllama_v1-runtime.tar.gz          # 本机 / Release 附件，Git 忽略
    ├── myllama_v1-runtime.tar.gz.sha256
    ├── myllama_v2-runtime.tar.gz          # 本机 / Release 附件，Git 忽略
    ├── myllama_v2-runtime.tar.gz.sha256
    ├── myllama_v3-runtime.tar.gz          # 本机 / Release 附件，Git 忽略
    └── myllama_v3-runtime.tar.gz.sha256
```

Git 跟踪源码、启动 / 构建脚本、实验与测速记录、许可证、manifest 和校验清单。原生程序、共享库、实验二进制及第三方源码压缩包放在对应运行包中，以 GitHub Release 附件分发。运行包超过普通 Git 单文件限制时仍能正常发布，日常 clone 不会下载这些二进制。分发方式参见 [GitHub 大文件说明](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github)。

模型权重不提交到 Git，也不包含在运行包内。本机 `releases/*/models/` 保留原权重的硬链接，不重复占用大模型的磁盘数据；在其他机器部署时另行提供匹配权重。`myllama_v1/SHA256SUMS` 包含权重校验项，`myllama_v2/MODELS.sha256` 和 `myllama_v3/models/SHA256SUMS` 单独记录权重校验值；模型校验文件进入 Git 和运行包。

## 取得运行包

从项目根目录执行，以最新 v3 为例：

```bash
gh release download myllama_v3 --repo zEeZ-1/LLAMA-MANAGER \
  --pattern myllama_v3-runtime.tar.gz --dir myllama/releases
(cd myllama/releases && sha256sum -c myllama_v3-runtime.tar.gz.sha256)
tar -xzf myllama/releases/myllama_v3-runtime.tar.gz -C myllama/releases
(cd myllama/releases/myllama_v3 && sha256sum -c SHA256SUMS)
```

v1、v2 将版本名替换为相应版本。尚未提供权重时，可运行 `sha256sum -c --ignore-missing SHA256SUMS` 验证已有运行时文件；补齐模型后再执行完整校验。

v3 用 `MYLLAMA_MODEL` 指定 GGUF 的完整路径；其 MTP、默认流水及端口说明见版本 README。v2 可用 `MYLLAMA_MODEL_DIR` 指定已有模型目录，或把匹配权重放到包内 `models/`。v1 通过包内 `models/` 读取权重。启动、RPC 地址、端口和实验开关遵循各版本原说明；首次部署应显式设置适合目标环境的 RPC 地址。历史构建脚本保留原源码路径和依赖要求，并不等同于开箱即用的全量上游源码树。

## 后续发布

为新版本建立独立目录，例如 `myllama/releases/myllama_v4/`，准备源码、运行时、许可证、说明、`manifest.json` 和 `SHA256SUMS`。manifest 的 `name`（v1、v2）或 `release`（v3）必须与目录名一致。已有版本按发布快照保存，变更使用新版本名。

```bash
python3 myllama/package_release.py myllama/releases/myllama_v4 --verify-only
python3 myllama/package_release.py myllama/releases/myllama_v4
```

打包器先校验运行时文件，跳过模型校验项；生成不含模型权重的运行包与 SHA256，保留 `models/SHA256SUMS` 元数据，并保留程序权限及包内共享库链接。已存在同名归档时拒绝覆盖。随后更新 `catalog.json` 和此版本表，提交并 push 文本文件及新标签，再发布附件：

```bash
gh release create myllama_v4 --repo zEeZ-1/LLAMA-MANAGER \
  --verify-tag --latest=false --title 'myllama_v4' \
  --notes-file myllama/releases/myllama_v4/README.md \
  myllama/releases/myllama_v4-runtime.tar.gz \
  myllama/releases/myllama_v4-runtime.tar.gz.sha256
```

此目录用于后端版本存储与发布。AscNet 的运行时选择仍通过现有配置完成，导入不会更改当前服务配置或启动模型。
