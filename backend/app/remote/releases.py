import asyncio
import hashlib
from pathlib import PurePosixPath
import re
import tarfile
import yaml
from ..core.errors import ManagerError


class ReleaseCatalog:
    def __init__(self, cfg):
        self.cfg = cfg

    def records(self):
        if not self.cfg.rpc.deployment_manifest.exists():
            return []
        try:
            content = yaml.safe_load(self.cfg.rpc.deployment_manifest.read_text()) or {}
            records = content.get("releases", [])
            if not isinstance(records, list):
                raise ValueError("releases must be a list")
            seen = set()
            for record in records:
                if (
                    not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", record["id"])
                    or record["id"] in seen
                ):
                    raise ValueError("invalid or duplicate release id")
                seen.add(record["id"])
                if not re.fullmatch(
                    r"[a-f0-9]{64}", record["sha256"]
                ) or not re.fullmatch(
                    r"[a-f0-9]{64}", record["files"]["bin/ggml-rpc-server"]
                ):
                    raise ValueError("missing or invalid SHA256")
                if not re.fullmatch(r"[0-9]+\.[0-9]+", record.get("glibc_min", "0.0")):
                    raise ValueError("invalid glibc minimum")
                if not isinstance(record["archive"], str):
                    raise ValueError("invalid archive")
            return records
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            yaml.YAMLError,
        ) as exc:
            raise ManagerError(
                "manifest_invalid", "RPC 发布清单格式无效，请检查本地配置"
            ) from exc

    @staticmethod
    def public(record):
        return {
            k: v
            for k, v in record.items()
            if k not in {"archive", "files", "cuda_library_dirs"}
        }

    async def get(self, identifier, node):
        record = next((r for r in self.records() if r["id"] == identifier), None)
        if not record:
            raise ManagerError("release_missing", "控制端没有所选 RPC 发布包")
        inv = node.get("snapshot", {})
        if record.get("architecture") != inv.get("architecture"):
            raise ManagerError("unsupported_platform", "发布包架构与远端机器不一致")
        if (
            record.get("commit") != self.cfg.rpc.expected_commit
            or record.get("protocol_version") != self.cfg.rpc.protocol_version
        ):
            raise ManagerError(
                "version_mismatch", "发布包与控制端验证的 commit / 协议版本不一致"
            )
        glibc = re.search(r"(\d+)\.(\d+)", inv.get("inventory", {}).get("glibc") or "")
        required = record.get("glibc_min", "0.0")
        if not glibc or tuple(map(int, glibc.groups())) < tuple(
            map(int, required.split("."))
        ):
            raise ManagerError(
                "unsupported_platform", "远端 glibc 低于发布包要求或无法确认"
            )
        if record.get("platform") == "jetson" and not inv.get("inventory", {}).get(
            "l4t"
        ):
            raise ManagerError(
                "unsupported_platform", "此发布包仅验证用于 NVIDIA Jetson"
            )
        if not inv.get("devices"):
            raise ManagerError(
                "device_unavailable", "没有识别到可供此 CUDA 发布包使用的 GPU"
            )
        archive = (self.cfg.rpc.packages_directory / record["archive"]).resolve()
        if (
            not archive.is_relative_to(self.cfg.rpc.packages_directory)
            or not archive.is_file()
        ):
            raise ManagerError("release_missing", "发布包文件缺失或不在允许的目录中")

        def validate():
            with archive.open("rb") as f:
                digest = hashlib.file_digest(f, "sha256").hexdigest()
            if digest != record["sha256"]:
                raise ManagerError("checksum_mismatch", "RPC 发布包 SHA256 不匹配")
            total = 0
            with tarfile.open(archive, "r:gz") as tar:
                names = set()
                for index, member in enumerate(tar):
                    if index >= 500:
                        raise ManagerError("package_invalid", "发布包文件数量超限")
                    path = PurePosixPath(member.name)
                    if (
                        path.is_absolute()
                        or ".." in path.parts
                        or str(path) != member.name.rstrip("/")
                        or not (member.isfile() or member.isdir())
                    ):
                        raise ManagerError(
                            "package_invalid", "发布包包含不允许的路径、链接或特殊文件"
                        )
                    total += member.size
                    if total > 512 * 1024**2:
                        raise ManagerError("package_invalid", "发布包解压大小超限")
                    if member.name in names:
                        raise ManagerError("package_invalid", "发布包存在重复路径")
                    names.add(member.name)
                    expected = record["files"].get(member.name)
                    if member.isfile() and path.parts[0] == "bin" and not expected:
                        raise ManagerError(
                            "package_invalid", "发布包中的运行文件缺少校验摘要"
                        )
                    if member.isfile() and expected:
                        with tar.extractfile(member) as source:
                            if (
                                hashlib.file_digest(source, "sha256").hexdigest()
                                != expected
                            ):
                                raise ManagerError(
                                    "checksum_mismatch", "发布包内文件摘要不匹配"
                                )
                if "bin/ggml-rpc-server" not in names:
                    raise ManagerError("package_invalid", "发布包缺少 RPC 可执行文件")

        try:
            await asyncio.to_thread(validate)
        except (OSError, tarfile.TarError) as exc:
            raise ManagerError("package_invalid", "发布包无法读取或解压") from exc
        return {**record, "archive_path": archive}
