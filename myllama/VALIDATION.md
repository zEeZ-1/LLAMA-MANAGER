# myllama 发布目录导入记录

日期：2026-11-25。来源为本机 `llama-deploy/releases` 的既有 `myllama_v1`、`myllama_v2` 与 v2 原运行包。

- 原发布目录保留；项目内导入副本的非模型文件逐字节 SHA256 对比一致，共享库链接目标一致。原日志和补丁的空白也保留，通过目录属性关闭存档的空白风格检查。
- 本机 5 个 GGUF 路径通过硬链接纳入新目录，确认与对应来源为同一文件，不额外复制权重数据。权重不进入 Git 或 GitHub Release 运行包，此次未重新读取全部权重做模型哈希校验。
- 按原 `SHA256SUMS` 验证运行时文件：v1 29 项通过，跳过其中 2 项模型权重；v2 122 项通过。
- v1 补建运行包，50 个归档条目；v2 沿用原压缩包，163 个归档条目。归档逐项检查路径、共享库链接、可执行权限与文件 SHA256，均与本机导入快照一致。v2 原包包含空 `models/` 目录，两个包均不含模型权重。
- 所有导入 shell 脚本通过 `bash -n`。
- 新打包器的 4 项测试通过：无权重归档与解压回验、保留共享库链接和执行权限、拒绝覆盖已有发布包、拒绝越界链接并清理失败归档、运行时文件变更的哈希识别。
- 新打包器和测试文件通过 Ruff 检查。未修改导入的历史构建配方；未重跑模型或历史性能基准。

运行包大小与 SHA256 收录在 `catalog.json`，归档本身与模型目录由 `.gitignore` 排除。源码和说明进入 Git，运行包及其校验文件作为 `myllama_v1` / `myllama_v2` GitHub Release 附件分发。

复核命令：

```bash
python3 myllama/package_release.py myllama/releases/myllama_v1 --verify-only
python3 myllama/package_release.py myllama/releases/myllama_v2 --verify-only
.venv/bin/python -m pytest -q myllama/test_packaging.py
```
