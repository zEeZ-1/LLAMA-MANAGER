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
