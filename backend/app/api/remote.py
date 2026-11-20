from ..schemas.nodes import (
    AddNode,
    EditNode,
    TrustHost,
    ConnectNode,
    AddCredential,
    WorkerConfig,
    DeployWorker,
    JobOperation,
    RemoveWorker,
)


def install_routes(app):
    def remote():
        return app.state.remote

    @app.post("/api/v1/nodes", status_code=201)
    async def add_node(body: AddNode):
        return await remote().add_node(body)

    @app.patch("/api/v1/nodes/{identifier}")
    async def edit_node(identifier: str, body: EditNode):
        return await remote().edit_node(identifier, body)

    @app.delete("/api/v1/nodes/{identifier}")
    async def delete_node(identifier: str):
        return await remote().archive_node(identifier)

    @app.post("/api/v1/nodes/{identifier}/host-key/scan")
    async def scan(identifier: str):
        return await remote().scan_host(identifier)

    @app.post("/api/v1/nodes/{identifier}/host-key/trust")
    async def trust(identifier: str, body: TrustHost):
        return await remote().trust_host(identifier, body)

    @app.post("/api/v1/nodes/{identifier}/connect", status_code=202)
    async def connect(identifier: str, body: ConnectNode):
        remote().node(identifier)
        return await remote().connect(
            identifier, body.password.get_secret_value() if body.password else None
        )

    @app.post("/api/v1/nodes/{identifier}/refresh", status_code=202)
    async def refresh(identifier: str):
        return await remote().refresh(identifier)

    @app.get("/api/v1/nodes/{identifier}/telemetry")
    async def telemetry(identifier: str):
        return remote().public_node(remote().node(identifier)).get("telemetry", {})

    @app.get("/api/v1/nodes/{identifier}/workers")
    async def workers(identifier: str):
        remote().node(identifier)
        claims = {c["worker_id"]: c for c in await app.state.store.claims()}
        return {
            "workers": [
                {**remote().public_worker(w), "claim": claims.get(w["id"])}
                for w in remote().node_workers(identifier)
            ]
        }

    @app.post("/api/v1/nodes/{identifier}/workers", status_code=201)
    async def add_worker(identifier: str, body: WorkerConfig):
        return await remote().add_worker(identifier, body)

    @app.get("/api/v1/releases")
    async def releases():
        return {
            "releases": [
                remote().releases.public(r) for r in remote().releases.records()
            ]
        }

    @app.patch("/api/v1/workers/{identifier}")
    async def edit_worker(identifier: str, body: WorkerConfig):
        return await remote().edit_worker(identifier, body)

    @app.delete("/api/v1/workers/{identifier}", status_code=202)
    async def remove_worker(identifier: str, body: RemoveWorker):
        return await remote().discovery.remove(identifier, body)

    @app.post("/api/v1/workers/{identifier}/deploy", status_code=202)
    async def deploy(identifier: str, body: DeployWorker):
        return await app.state.deployments.deploy(identifier, body)

    @app.post("/api/v1/workers/{identifier}/adopt", status_code=202)
    async def adopt(identifier: str, body: DeployWorker):
        return await app.state.deployments.adopt(identifier, body)

    @app.post("/api/v1/workers/{identifier}/start", status_code=202)
    async def start(identifier: str, body: JobOperation):
        return await remote().operation(identifier, "start", body.idempotency_key)

    @app.post("/api/v1/workers/{identifier}/stop", status_code=202)
    async def stop(identifier: str, body: JobOperation):
        return await remote().operation(identifier, "stop", body.idempotency_key)

    @app.post("/api/v1/workers/{identifier}/cache/clean", status_code=202)
    async def clean(identifier: str, body: JobOperation):
        return await remote().operation(identifier, "clean", body.idempotency_key)

    @app.post("/api/v1/workers/{identifier}/reconcile", status_code=202)
    async def reconcile(identifier: str):
        return await app.state.resources.reconcile(identifier)

    @app.get("/api/v1/workers/{identifier}/logs")
    async def logs(identifier: str):
        return await remote().logs(identifier)

    @app.get("/api/v1/jobs")
    async def jobs():
        return {"jobs": list(reversed(app.state.jobs.records.values()))}

    @app.get("/api/v1/jobs/{identifier}")
    async def job(identifier: str):
        return app.state.jobs.get(identifier)

    @app.post("/api/v1/jobs/{identifier}/cancel")
    async def cancel(identifier: str):
        return await app.state.jobs.cancel(identifier)

    @app.get("/api/v1/credentials")
    async def credentials():
        service = app.state.credentials
        return {
            "credentials": [service.public(r) for r in service.records.values()],
            "available_keys": service.available_keys(),
        }

    @app.post("/api/v1/credentials", status_code=201)
    async def credential(body: AddCredential):
        return app.state.credentials.public(await app.state.credentials.create(body))
