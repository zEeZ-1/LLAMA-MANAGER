import asyncio
from contextlib import asynccontextmanager
import httpx
from fastapi import FastAPI, HTTPException, Request
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.staticfiles import StaticFiles
from .core.config import ROOT, Settings, load_settings
from .core.http_limits import RequestLimits
from .core.lock import ManagerLock
from .repositories.instances import InstanceRepository
from .process.supervisor import ProcessSupervisor
from .runtime.llama_cpp import LlamaCppAdapter
from .schemas.instance import ChatRequest, CreateInstance
from .services.model_catalog import ModelCatalog
from .services.instance_manager import InstanceManager
from .services.node_manager import LocalNodeProvider
from .services.gateway import Gateway


def create_app(settings: Settings | None = None):
    cfg = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app):
        lock = ManagerLock(cfg.storage.database.with_suffix(".manager.lock"))
        catalog = ModelCatalog(cfg.models)
        manager = None
        async with httpx.AsyncClient(
            trust_env=False,
            timeout=httpx.Timeout(300, connect=5, pool=5),
            limits=httpx.Limits(
                max_connections=cfg.limits.max_concurrent_chats
                + cfg.instances.max_active,
                max_keepalive_connections=8,
            ),
        ) as client:
            try:
                repository = InstanceRepository(cfg.storage.database)
                await repository.initialize()
                await catalog.initialize()
                providers = {"local": LocalNodeProvider()}
                supervisor = ProcessSupervisor(cfg)
                adapters = {"llama.cpp": LlamaCppAdapter(cfg, supervisor, client)}
                manager = InstanceManager(cfg, catalog, repository, adapters, providers)
                await manager.initialize()
                app.state.catalog, app.state.manager, app.state.providers = (
                    catalog,
                    manager,
                    providers,
                )
                app.state.gateway = Gateway(cfg, manager, client)
                yield
            finally:
                if manager:
                    await manager.close()
                await catalog.close()
                lock.close()

    app = FastAPI(
        title="llama.cpp Inference Manager", version="0.1.0", lifespan=lifespan
    )
    app.add_middleware(RequestLimits, max_bytes=cfg.limits.max_request_bytes)
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "testserver", cfg.server.host],
    )

    @app.get("/api/v1/health")
    async def health():
        return {
            "status": "ok",
            "runtime_available": cfg.runtime.llama_cpp.server_binary.is_file(),
            "active_chats": app.state.gateway.active,
            "memory_budget_bytes": 1024**3,
            "catalog_last_scan": app.state.catalog.last_scan,
            "catalog_rejected_files": len(app.state.catalog.errors),
        }

    async def node_detail(node_id):
        if node_id not in app.state.providers:
            raise HTTPException(404, "Node not found")
        node = await app.state.providers[node_id].get()
        node["model_count"] = len(app.state.catalog.list_models(node_id))
        records = [
            r for r in app.state.manager.instances.values() if r["node_id"] == node_id
        ]
        node["instance_count"] = len(records)
        node["ready_count"] = sum(r["status"] == "ready" for r in records)
        return node

    @app.get("/api/v1/nodes")
    async def nodes():
        return {
            "nodes": await asyncio.gather(
                *(node_detail(i) for i in app.state.providers)
            )
        }

    @app.get("/api/v1/nodes/{node_id}")
    async def node(node_id: str):
        return await node_detail(node_id)

    @app.get("/api/v1/models")
    async def models(node_id: str | None = None):
        return {
            "models": app.state.catalog.list_models(node_id),
            "last_scan": app.state.catalog.last_scan,
            "rejected_files": len(app.state.catalog.errors),
        }

    @app.post("/api/v1/models/refresh", status_code=202)
    async def refresh():
        app.state.catalog.refresh()
        return {"status": "scheduled"}

    @app.get("/api/v1/models/{model_id}")
    async def model(model_id: str):
        try:
            return app.state.catalog.public(app.state.catalog.get_model(model_id))
        except (ValueError, OSError) as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/v1/instances")
    async def instances():
        return {
            "instances": [
                app.state.manager.public(r)
                for r in reversed(app.state.manager.instances.values())
            ]
        }

    @app.post("/api/v1/instances", status_code=202)
    async def create_instance(body: CreateInstance):
        return app.state.manager.public(await app.state.manager.create(body))

    @app.get("/api/v1/instances/{instance_id}")
    async def instance(instance_id: str):
        return app.state.manager.public(app.state.manager.get(instance_id))

    @app.delete("/api/v1/instances/{instance_id}")
    async def destroy(instance_id: str):
        return app.state.manager.public(await app.state.manager.destroy(instance_id))

    @app.get("/api/v1/instances/{instance_id}/logs")
    async def logs(instance_id: str):
        app.state.manager.get(instance_id)

        def read_tail():
            path = cfg.storage.logs / f"{instance_id}.log"
            if not path.exists():
                return "No runtime log yet."
            with path.open("rb") as f:
                f.seek(max(0, path.stat().st_size - 32768))
                return f.read(32768).decode(errors="replace")

        return {"text": await asyncio.to_thread(read_tail)}

    @app.post("/api/v1/instances/{instance_id}/chat/completions")
    async def completions(instance_id: str, body: ChatRequest, request: Request):
        return await app.state.gateway.forward(instance_id, body, request)

    @app.api_route(
        "/api/{rest:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"]
    )
    async def unknown_api(rest: str):
        raise HTTPException(404, "API route not found")

    dist = ROOT / "frontend/dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    return app


app = create_app()
