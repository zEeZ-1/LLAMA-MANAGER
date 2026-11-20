import uuid
from pathlib import Path
from ..core.errors import ManagerError


class CredentialStore:
    def __init__(self, cfg, store, transport):
        self.cfg, self.store, self.transport = cfg, store, transport
        self.records = {}
        transport.credential_resolver = self.resolve

    async def initialize(self):
        self.records = {r["id"]: r for r in await self.store.all("credentials")}

    def resolve(self, identifier):
        if not identifier:
            return None
        record = self.records.get(identifier)
        if not record:
            raise ManagerError("credential_missing", "指定密钥不存在")
        path = Path(record["path"])
        if not path.is_file() or path.is_symlink():
            raise ManagerError("credential_missing", "密钥文件已丢失或路径发生变化")
        return path

    def public(self, record):
        return {k: v for k, v in record.items() if k != "path"}

    def available_keys(self):
        root = Path.home() / ".ssh"
        if not root.exists():
            return []
        return [
            p.name
            for p in root.iterdir()
            if p.is_file()
            and not p.is_symlink()
            and p.name.startswith("id_")
            and p.suffix not in {".pub", ".pem"}
        ][:20]

    async def create(self, body):
        identifier = str(uuid.uuid4())
        if body.type == "existing":
            if body.key_name not in self.available_keys():
                raise ManagerError("credential_invalid", "仅可选择列出的本机 SSH 私钥")
            path = Path.home() / ".ssh" / body.key_name
        else:
            path = self.cfg.ssh.private_directory / ("key-" + identifier)
            code, out, error = await self.transport.run(
                [
                    "ssh-keygen",
                    "-q",
                    "-t",
                    "ed25519",
                    "-N",
                    "",
                    "-C",
                    "llama-manager:" + self.store.controller_id,
                    "-f",
                    str(path),
                ]
            )
            if code:
                raise ManagerError("key_generation_failed", "无法生成平台专用 SSH 密钥")
            path.chmod(0o600)
        record = dict(id=identifier, label=body.label, type=body.type, path=str(path))
        await self.store.put("credentials", record)
        self.records[identifier] = record
        return record

    async def generated(self):
        existing = next(
            (r for r in self.records.values() if r["type"] == "generated"), None
        )
        if existing:
            return existing
        from ..schemas.nodes import AddCredential

        return await self.create(AddCredential())

    async def public_key(self, identifier):
        path = self.resolve(identifier)
        code, out, error = await self.transport.run(
            ["ssh-keygen", "-y", "-f", str(path)]
        )
        if code:
            raise ManagerError(
                "credential_invalid", "密钥不可用；加密私钥请先通过本机 agent 解锁"
            )
        return out.strip() + " llama-manager:" + self.store.controller_id
