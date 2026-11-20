import asyncio
import json
import anyio
import httpx
from fastapi import HTTPException, Request
from starlette.responses import Response, StreamingResponse


class Gateway:
    def __init__(self, settings, manager, client, media=None):
        self.settings, self.manager, self.client = settings, manager, client
        self.active = 0
        self.media = media

    async def _read_bounded(self, response, limit):
        body = bytearray()
        async for chunk in response.aiter_bytes(16384):
            if len(body) + len(chunk) > limit:
                raise HTTPException(
                    502, "Upstream response exceeded configured byte limit"
                )
            body.extend(chunk)
        return bytes(body)

    async def _until_disconnect(self, operation, request):
        async def disconnected():
            while not await request.is_disconnected():
                await asyncio.sleep(0.1)

        work = asyncio.create_task(operation)
        watcher = asyncio.create_task(disconnected())
        delivered = False
        try:
            done, _ = await asyncio.wait(
                {work, watcher}, return_when=asyncio.FIRST_COMPLETED
            )
            if watcher in done:
                raise HTTPException(499, "Client disconnected")
            result = work.result()
            delivered = True
            return result
        finally:
            with anyio.CancelScope(shield=True):
                watcher.cancel()
                if not work.done():
                    work.cancel()
                results = await asyncio.gather(work, watcher, return_exceptions=True)
                if not delivered and isinstance(results[0], httpx.Response):
                    await results[0].aclose()

    async def forward(self, instance_id, payload, request: Request):
        if not self.media:
            return await self._forward(instance_id, payload, request)
        context = self.media.prepare(payload, self.manager.get(instance_id))
        body = await context.__aenter__()
        try:
            response = await self._forward(instance_id, payload, request, body)
        except BaseException:
            with anyio.CancelScope(shield=True):
                await context.__aexit__(None, None, None)
            raise
        if isinstance(response, StreamingResponse):
            iterator = response.body_iterator

            async def pinned():
                try:
                    async for chunk in iterator:
                        yield chunk
                finally:
                    with anyio.CancelScope(shield=True):
                        await iterator.aclose()
                        await context.__aexit__(None, None, None)

            response.body_iterator = pinned()
        else:
            await context.__aexit__(None, None, None)
        return response

    async def _forward(self, instance_id, payload, request: Request, prepared=None):
        instance = self.manager.get(instance_id)
        adapter = self.manager.adapters[instance["runtime_type"]]
        if instance["status"] != "ready" or not adapter.get_status(instance):
            raise HTTPException(409, "Instance is not READY")
        if self.active >= self.settings.limits.max_concurrent_chats:
            raise HTTPException(429, "Concurrent chat limit reached; try again later")
        self.active += 1
        upstream = None
        try:
            body = (
                prepared
                if prepared is not None
                else payload.model_dump(exclude_none=True)
            )
            body["model"] = instance["id"]
            upstream = await self._until_disconnect(
                self.client.send(
                    self.client.build_request(
                        "POST",
                        instance["runtime"]["endpoint"] + "/v1/chat/completions",
                        json=body,
                    ),
                    stream=True,
                ),
                request,
            )
            if upstream.status_code >= 400:
                detail = (await self._read_bounded(upstream, 65536)).decode(
                    errors="replace"
                )
                if upstream.status_code in {400, 413, 415, 422} and instance.get(
                    "capabilities", {}
                ).get("image_input"):
                    try:
                        message = (
                            json.loads(detail)
                            .get("error", {})
                            .get("message", "图片或上下文参数不合法")
                        )
                    except (ValueError, AttributeError):
                        message = "图片或上下文参数不合法"
                    raise HTTPException(
                        422,
                        {
                            "code": "image_request_invalid",
                            "message": str(message)[:1000],
                        },
                    )
                raise HTTPException(
                    502,
                    f"llama-server returned {upstream.status_code}: {detail[:2000]}",
                )
            if not payload.stream:
                data = await self._until_disconnect(
                    self._read_bounded(
                        upstream, self.settings.limits.max_response_bytes
                    ),
                    request,
                )
                await upstream.aclose()
                self.active -= 1
                return Response(data, media_type="application/json")
        except BaseException as exc:
            with anyio.CancelScope(shield=True):
                if upstream:
                    await upstream.aclose()
                self.active -= 1
            if isinstance(exc, httpx.HTTPError):
                raise HTTPException(
                    502, "llama-server connection failed or timed out"
                ) from exc
            raise

        async def disconnected():
            while not await request.is_disconnected():
                await asyncio.sleep(0.1)

        async def chunks():
            watcher = asyncio.create_task(disconnected())
            pending = None
            total = 0
            try:
                iterator = upstream.aiter_bytes().__aiter__()
                while True:
                    pending = asyncio.create_task(anext(iterator))
                    done, _ = await asyncio.wait(
                        {pending, watcher}, return_when=asyncio.FIRST_COMPLETED
                    )
                    if watcher in done:
                        break
                    try:
                        chunk = pending.result()
                    except StopAsyncIteration:
                        break
                    total += len(chunk)
                    if total > self.settings.limits.max_response_bytes:
                        raise ValueError("Response exceeded byte limit")
                    yield chunk
            except (httpx.HTTPError, ValueError) as exc:
                yield (
                    "\n\ndata: "
                    + json.dumps({"error": {"message": str(exc)}})
                    + "\n\ndata: [DONE]\n\n"
                ).encode()
            finally:
                with anyio.CancelScope(shield=True):
                    watcher.cancel()
                    if pending and not pending.done():
                        pending.cancel()
                    await asyncio.gather(
                        watcher, *([pending] if pending else []), return_exceptions=True
                    )
                    await upstream.aclose()
                    self.active -= 1

        return StreamingResponse(
            chunks(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
