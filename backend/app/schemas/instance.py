from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

CacheType = Literal[
    "f32", "f16", "bf16", "q8_0", "q4_0", "q4_1", "iq4_nl", "q5_0", "q5_1"
]


class PlacementDevice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_id: str = Field(min_length=1, max_length=128)
    worker_id: str | None = Field(None, max_length=64)
    weight: float = Field(1, gt=0, le=100000)


class Placement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["local", "rpc"] = "local"
    devices: list[PlacementDevice] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def unique_devices(self):
        if len({d.device_id for d in self.devices}) != len(self.devices):
            raise ValueError("Devices may only be selected once")
        if self.mode == "local" and self.devices:
            raise ValueError("Explicit device placement requires rpc mode")
        return self


class InstanceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    context_size: int = Field(4096, ge=128, le=1048576)
    batch_size: int = Field(512, ge=1, le=8192)
    ubatch_size: int = Field(128, ge=1, le=8192)
    parallel: int = Field(1, ge=1, le=32)
    threads: int = Field(4, ge=1, le=1024)
    n_gpu_layers: int = Field(0, ge=0, le=999)
    cache_type_k: CacheType = "f16"
    cache_type_v: CacheType = "f16"
    placement: Placement = Field(default_factory=Placement)
    extra_options: dict[Literal["flash_attn", "mlock", "no_mmap"], bool] = Field(
        default_factory=dict
    )

    @model_validator(mode="after")
    def compatible(self):
        if self.placement.mode == "rpc" and self.n_gpu_layers == 0:
            raise ValueError("RPC GPU mode requires n_gpu_layers > 0")
        if self.ubatch_size > self.batch_size:
            raise ValueError("ubatch_size must not exceed batch_size")
        if self.context_size < self.parallel * 128:
            raise ValueError("each parallel slot requires at least 128 context tokens")
        return self


class CreateInstance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100, pattern=r"\S")
    node_id: str = "local"
    model_id: str = Field(min_length=1, max_length=100)
    backend: Literal["llama.cpp"] = "llama.cpp"
    config: InstanceConfig = Field(default_factory=InstanceConfig)


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["system", "user", "assistant"]
    content: str = Field(max_length=500000)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    messages: list[ChatMessage] = Field(min_length=1, max_length=500)
    stream: bool = True
    model: str | None = Field(None, max_length=200)
    temperature: float = Field(0.7, ge=0, le=2)
    top_p: float = Field(0.95, gt=0, le=1)
    max_tokens: int = Field(2048, ge=1, le=32768)
    stop: list[str] | None = Field(None, max_length=8)
