from pathlib import Path
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
            raise ValueError("V1 inference instances must listen on 127.0.0.1")
        return self


class Limits(BaseModel):
    max_request_bytes: int = Field(2 * 1024**2, ge=1024, le=8 * 1024**2)
    max_concurrent_chats: int = Field(8, ge=1, le=32)
    max_response_bytes: int = Field(4 * 1024**2, ge=1024, le=16 * 1024**2)
    max_log_bytes: int = Field(10 * 1024**2, ge=65536)


class Settings(BaseModel):
    server: Server = Field(default_factory=Server)
    runtime: Runtime = Field(default_factory=Runtime)
    models: Models = Field(default_factory=Models)
    storage: Storage = Field(default_factory=Storage)
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
        self.storage.database = path(self.storage.database)
        self.storage.logs = path(self.storage.logs)
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
