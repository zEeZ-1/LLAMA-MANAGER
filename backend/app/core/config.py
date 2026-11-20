from pathlib import Path
import ipaddress
import os
import yaml
from pydantic import BaseModel, Field, model_validator

ROOT = Path(__file__).resolve().parents[3]


class Server(BaseModel):
    host: str = "127.0.0.1"
    port: int = Field(8000, ge=1, le=65535)


class LlamaCpp(BaseModel):
    root: Path = Path("inference_backends/llama.cpp")
    server_binary: Path = Path("inference_backends/llama.cpp/build/bin/llama-server")


class Runtime(BaseModel):
    default_backend: str = "llama.cpp"
    llama_cpp: LlamaCpp = Field(default_factory=LlamaCpp)


class Models(BaseModel):
    directories: list[Path] = Field(
        default_factory=lambda: [Path("models")], min_length=1
    )
    recursive: bool = True
    watch: bool = True
    scan_interval_seconds: float = Field(30, ge=0.1)
    stability_seconds: float = Field(1, ge=0.05)


class Storage(BaseModel):
    database: Path = Path("data/database/manager.db")
    logs: Path = Path("data/logs")
    media: Path = Path("data/media")


class Images(BaseModel):
    upload_bytes: int = Field(8 * 1024**2, ge=1024, le=16 * 1024**2)
    normalized_bytes: int = Field(4 * 1024**2, ge=1024, le=8 * 1024**2)
    max_pixels: int = Field(16000000, ge=1, le=16000000)
    max_side: int = Field(8192, ge=1, le=8192)
    normalized_side: int = Field(2048, ge=64, le=2048)
    storage_bytes: int = Field(512 * 1024**2, ge=16 * 1024**2)
    ttl_seconds: float = Field(86400, ge=1)


class PortRange(BaseModel):
    start: int = Field(18000, ge=1024, le=65535)
    end: int = Field(18999, ge=1024, le=65535)

    @model_validator(mode="after")
    def ordered(self):
        if self.end < self.start:
            raise ValueError("port range end must be >= start")
        return self


class Instances(BaseModel):
    host: str = "127.0.0.1"
    port_range: PortRange = Field(default_factory=PortRange)
    startup_timeout_seconds: float = Field(180, gt=0)
    shutdown_timeout_seconds: float = Field(10, gt=0)
    max_active: int = Field(16, ge=1, le=128)

    @model_validator(mode="after")
    def loopback(self):
        if self.host != "127.0.0.1":
            raise ValueError("Inference instances must listen on 127.0.0.1")
        return self


class Limits(BaseModel):
    max_request_bytes: int = Field(2 * 1024**2, ge=1024, le=8 * 1024**2)
    max_concurrent_chats: int = Field(8, ge=1, le=32)
    max_response_bytes: int = Field(4 * 1024**2, ge=1024, le=16 * 1024**2)
    max_log_bytes: int = Field(10 * 1024**2, ge=65536)


class Nodes(BaseModel):
    max_registered: int = Field(10, ge=1, le=100)
    allowed_networks: list[str] = Field(
        default_factory=lambda: ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]
    )
    inventory_ttl_seconds: float = Field(3600, ge=1)
    telemetry_interval_seconds: float = Field(30, ge=1)
    telemetry_stale_after_seconds: float = Field(90, ge=1)

    @model_validator(mode="after")
    def networks(self):
        for network in self.allowed_networks:
            if ipaddress.ip_network(network).version != 4:
                raise ValueError("V2 node networks must be IPv4 CIDRs")
        return self


class Ssh(BaseModel):
    max_connections: int = Field(4, ge=1, le=8)
    max_parallel_commands: int = Field(2, ge=1, le=4)
    max_pending_commands: int = Field(20, ge=1, le=100)
    connect_timeout_seconds: int = Field(5, ge=1, le=30)
    command_timeout_seconds: float = Field(15, ge=1, le=120)
    control_persist_seconds: float = Field(45, ge=1, le=300)
    max_output_bytes: int = Field(65536, ge=1024, le=262144)
    private_directory: Path = Path("data/ssh")


class Rpc(BaseModel):
    default_port: int = Field(50052, ge=1024, le=65535)
    deployment_manifest: Path = Path("config/rpc_releases.yaml")
    packages_directory: Path = Path("inference_backends/rpc_packages")
    probe_timeout_seconds: float = Field(3, gt=0, le=30)
    startup_timeout_seconds: float = Field(60, gt=0)
    distributed_instance_startup_timeout_seconds: float = Field(900, gt=0)
    expected_commit: str = "9214bdb554544c35e4a9e2c2e8b75d8cfee330ab"
    protocol_version: str = "10.0.0"


class Jobs(BaseModel):
    max_concurrent_deployments: int = Field(1, ge=1, le=2)
    credential_ttl_seconds: float = Field(300, ge=10, le=900)
    transfer_timeout_seconds: float = Field(1200, gt=0)


class Settings(BaseModel):
    nodes: Nodes = Field(default_factory=Nodes)
    ssh: Ssh = Field(default_factory=Ssh)
    rpc: Rpc = Field(default_factory=Rpc)
    jobs: Jobs = Field(default_factory=Jobs)
    server: Server = Field(default_factory=Server)
    runtime: Runtime = Field(default_factory=Runtime)
    models: Models = Field(default_factory=Models)
    storage: Storage = Field(default_factory=Storage)
    images: Images = Field(default_factory=Images)
    instances: Instances = Field(default_factory=Instances)
    limits: Limits = Field(default_factory=Limits)

    def resolve(self, root: Path = ROOT):
        def path(p):
            return (root / p.expanduser()).resolve()

        self.runtime.llama_cpp.root = path(self.runtime.llama_cpp.root)
        self.runtime.llama_cpp.server_binary = path(
            self.runtime.llama_cpp.server_binary
        )
        self.models.directories = list(
            dict.fromkeys(path(p) for p in self.models.directories)
        )
        for index, model_root in enumerate(self.models.directories):
            if any(
                model_root.is_relative_to(other) or other.is_relative_to(model_root)
                for other in self.models.directories[:index]
            ):
                raise ValueError("Model scan roots must not overlap")
        self.storage.database = path(self.storage.database)
        self.storage.logs = path(self.storage.logs)
        self.storage.media = path(self.storage.media)
        self.ssh.private_directory = path(self.ssh.private_directory)
        self.rpc.deployment_manifest = path(self.rpc.deployment_manifest)
        self.rpc.packages_directory = path(self.rpc.packages_directory)
        return self


def load_settings() -> Settings:
    filename = Path(os.getenv("LIM_CONFIG", ROOT / "config/config.yaml"))
    if not filename.is_absolute():
        filename = ROOT / filename
    raw = yaml.safe_load(filename.read_text()) if filename.exists() else {}
    cfg = Settings.model_validate(raw or {})
    if value := os.getenv("LIM_LLAMA_SERVER"):
        cfg.runtime.llama_cpp.server_binary = Path(value)
    if value := os.getenv("LIM_MODEL_DIRS"):
        cfg.models.directories = [Path(p) for p in value.split(os.pathsep) if p]
    if value := os.getenv("LIM_DATABASE"):
        cfg.storage.database = Path(value)
    if value := os.getenv("LIM_LOG_DIR"):
        cfg.storage.logs = Path(value)
    return cfg.resolve()
