from urllib.parse import urlparse
from starlette.responses import JSONResponse


class BodyTooLarge(Exception):
    pass


class RequestLimits:
    def __init__(self, app, max_bytes, image_bytes=None):
        self.app, self.max_bytes = app, max_bytes
        self.image_bytes = image_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])
        limit = (
            self.image_bytes
            if self.image_bytes
            and scope["method"] == "POST"
            and scope["path"] == "/api/v1/media/images"
            else self.max_bytes
        )
        if scope["method"] in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = headers.get(b"origin", b"").decode()
            host = headers.get(b"host", b"").decode()
            allowed_dev = {"http://127.0.0.1:5173", "http://localhost:5173"}
            if origin and urlparse(origin).netloc != host and origin not in allowed_dev:
                return await JSONResponse(
                    {"detail": "Cross-origin mutation rejected"}, 403
                )(scope, receive, send)
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            length = self.max_bytes + 1
        if length > limit:
            return await JSONResponse({"detail": "Request body too large"}, 413)(
                scope, receive, send
            )
        consumed = 0

        async def bounded_receive():
            nonlocal consumed
            message = await receive()
            if message["type"] == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > limit:
                    raise BodyTooLarge()
            return message

        async def bounded_send(message):
            if consumed > limit and message["type"] == "http.response.start":
                message["status"] = 413
            await send(message)

        try:
            await self.app(scope, bounded_receive, bounded_send)
        except BodyTooLarge:
            await JSONResponse({"detail": "Request body too large"}, 413)(
                scope, receive, send
            )
