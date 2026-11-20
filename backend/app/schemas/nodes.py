from ipaddress import IPv4Address
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AddNode(StrictModel):
    name: str = Field(min_length=1, max_length=100, pattern=r"\S")
    address: IPv4Address
    username: str = Field(pattern=r"^[a-z_][a-z0-9_-]{0,31}\$?$")
    port: int = Field(22, ge=1, le=65535)
    credential_id: str | None = Field(None, max_length=64)


class EditNode(StrictModel):
    name: str | None = Field(None, min_length=1, max_length=100, pattern=r"\S")
    address: IPv4Address | None = None
    username: str | None = Field(None, pattern=r"^[a-z_][a-z0-9_-]{0,31}\$?$")
    port: int | None = Field(None, ge=1, le=65535)
    credential_id: str | None = Field(None, max_length=64)


class TrustHost(StrictModel):
    challenge_id: str = Field(max_length=64)
    fingerprint: str = Field(max_length=100)
    replace: bool = False
    replace_machine: bool = False


class ConnectNode(StrictModel):
    password: SecretStr | None = Field(None, min_length=1, max_length=4096)


class AddCredential(StrictModel):
    label: str = Field("平台专用密钥", min_length=1, max_length=100)
    type: Literal["generated", "existing"] = "generated"
    key_name: str | None = Field(None, pattern=r"^[A-Za-z0-9_.-]{1,80}$")


class WorkerConfig(StrictModel):
    port: int | None = Field(None, ge=1024, le=65535)
    device_ids: list[str] = Field(default_factory=list, max_length=16)
    service_name: str | None = Field(
        None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.@-]{0,100}\.service$"
    )
    mode: Literal["managed", "observed"] = "managed"

    @model_validator(mode="after")
    def valid_devices_and_service(self):
        if not self.device_ids or len(self.device_ids) != len(set(self.device_ids)):
            raise ValueError("Devices must be nonempty and unique")
        if self.mode == "observed" and not self.service_name:
            raise ValueError("An observed worker requires a service name")
        return self


class DeployWorker(StrictModel):
    release_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
    enable_linger: bool = False
    sudo_password: SecretStr | None = Field(None, min_length=1, max_length=4096)
    start: bool = True
    autostart: bool = False


class JobOperation(StrictModel):
    idempotency_key: str | None = Field(None, max_length=100)


class RemoveWorker(StrictModel):
    process_identity: dict | None = None
    force: bool = False
