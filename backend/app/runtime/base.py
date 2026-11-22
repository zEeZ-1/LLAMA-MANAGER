from abc import ABC, abstractmethod


class RuntimeAdapter(ABC):
    @abstractmethod
    async def start(self, instance, model): ...
    @abstractmethod
    async def stop(self, instance): ...
    @abstractmethod
    async def health_check(self, instance): ...
    @abstractmethod
    def get_status(self, instance): ...

    async def cleanup_exited(self, instance):
        pass

    async def maintain(self, instance):
        pass

    async def failure_details(
        self, instance, fallback, code="runtime_exited", exit_code=None
    ):
        return {"code": code, "message": fallback, "log_excerpt": ""}
