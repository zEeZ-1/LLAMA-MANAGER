import asyncio
from contextlib import asynccontextmanager
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from starlette.responses import JSONResponse
from .api.remote import install_routes
from .repositories.manager import ManagerStore
from .remote.ssh_transport import SshTransport
from .remote.credentials import CredentialStore
from .services.job_manager import JobManager
from .services.remote_manager import RemoteManager
from .services.deployment_service import DeploymentService
from .services.resource_manager import ResourceManager
from .core.managed_service import supervised
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
        remote = jobs = ssh = None
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
                store = ManagerStore(cfg.storage.database)
                await store.initialize()
                ssh = SshTransport(cfg)
                credentials = CredentialStore(cfg, store, ssh)
                await credentials.initialize()
                jobs = JobManager(store, cfg)
                await jobs.initialize()
                remote = RemoteManager(cfg, store, ssh, credentials, jobs)
                await remote.initialize()
                (
                    app.state.store,
                    app.state.remote,
                    app.state.credentials,
                    app.state.jobs,
                ) = store, remote, credentials, jobs
                app.state.deployments = DeploymentService(remote)
                await catalog.initialize()
                providers = {"local": LocalNodeProvider()}
                supervisor = ProcessSupervisor(cfg)
                adapters = {"llama.cpp": LlamaCppAdapter(cfg, supervisor, client)}
                resources = ResourceManager(remote, providers["local"])
                app.state.resources = resources
                manager = InstanceManager(
                    cfg, catalog, repository, adapters, providers, resources
                )
                await manager.initialize()
                app.state.catalog, app.state.manager, app.state.providers = (
                    catalog,
                    manager,
                    providers,
                )
                app.state.gateway = Gateway(cfg, manager, client)
                yield
            finally:
                try:
                    if remote:
                        await remote.close()
                    if jobs:
                        await jobs.close()
                    if manager:
                        await manager.close()
                finally:
                    if ssh:
                        await ssh.close()
                    await catalog.close()
                    lock.close()

    app = FastAPI(
        title="llama.cpp Inference Manager", version="0.2.0", lifespan=lifespan
    )
    app.add_middleware(RequestLimits, max_bytes=cfg.limits.max_request_bytes)
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "testserver", cfg.server.host],
    )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Do not echo request inputs: connect/deploy bodies can contain one-time secrets.
        return JSONResponse(
            {
                "detail": [
                    {k: v for k, v in error.items() if k in {"loc", "msg", "type"}}
                    for error in exc.errors()
                ]
            },
            status_code=422,
        )

    @app.get("/api/v1/health")
    async def health():
        return {
            "status": "ok",
            "version": "0.2.0",
            "managed_lifecycle": supervised(),
            "ssh_connections": len(app.state.remote.ssh.masters),
            "runtime_available": cfg.runtime.llama_cpp.server_binary.is_file(),
            "active_chats": app.state.gateway.active,
            "memory_budget_bytes": 1024**3,
            "catalog_last_scan": app.state.catalog.last_scan,
            "catalog_rejected_files": len(app.state.catalog.errors),
        }

    async def node_detail(node_id):
        if node_id == "local":
            node = await app.state.providers["local"].get()
        else:
            node = app.state.remote.public_node(app.state.remote.node(node_id))
        node["model_count"] = len(app.state.catalog.list_models(node_id))
        worker_ids = {
            w["id"]
            for w in app.state.remote.workers.values()
            if w["node_id"] == node_id
        }
        records = [
            r
            for r in app.state.manager.instances.values()
            if r["status"] != "stopped"
            and (
                r["node_id"] == node_id
                or worker_ids.intersection(
                    r["runtime"].get("placement", {}).get("worker_ids", [])
                )
            )
        ]
        node["instance_count"] = len(records)
        node["ready_count"] = sum(r["status"] == "ready" for r in records)
        claims = {c["worker_id"]: c for c in await app.state.store.claims()}
        for worker in node.get("rpc_workers", []):
            worker["claim"] = claims.get(worker["id"])
        for device in node["devices"]:
            ready = next(
                (
                    w
                    for w in node.get("rpc_workers", [])
                    if device["id"] in w["device_ids"]
                    and w["state"] == "ready"
                    and w.get("protocol_version")
                ),
                None,
            )
            if ready:
                device["compute_state"] = "ready"
                device["last_compute_check_at"] = ready.get("last_compute_check_at")
        return node

    @app.get("/api/v1/nodes")
    async def nodes():
        identifiers = [
            "local",
            *[
                n["id"]
                for n in app.state.remote.nodes.values()
                if not n.get("archived_at")
            ],
        ]
        return {"nodes": await asyncio.gather(*(node_detail(i) for i in identifiers))}

    @app.get("/api/v1/nodes/{node_id}")
    async def node(node_id: str):
        return await node_detail(node_id)

    install_routes(app)

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
    async def instances(include_stopped: bool = False):
        return {
            "instances": [
                app.state.manager.public(r)
                for r in reversed(app.state.manager.instances.values())
                if include_stopped or r["status"] != "stopped"
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
