"""Read-only RPC v10 probe. Never connect to a worker leased by an instance."""

import asyncio
import struct
from ..core.errors import ManagerError


async def probe(host, port, timeout=3, expected="10.0.0"):
    writer = None
    try:
        async with asyncio.timeout(timeout):
            reader, writer = await asyncio.open_connection(host, port)

            async def command(code, payload=b"", size=None):
                writer.write(bytes([code]) + struct.pack("<Q", len(payload)) + payload)
                await writer.drain()
                n = struct.unpack("<Q", await reader.readexactly(8))[0]
                if n > 4096 or (size is not None and n != size):
                    raise ManagerError(
                        "protocol_invalid", "RPC 返回了不支持的数据包长度"
                    )
                return await reader.readexactly(n)

            hello = await command(14, bytes(24), 28)
            version = ".".join(str(v) for v in hello[:3])
            if version != expected:
                raise ManagerError(
                    "version_mismatch", f"RPC 协议 {version} 与要求的 {expected} 不一致"
                )
            count = struct.unpack("<I", await command(15, size=4))[0]
            if not 1 <= count <= 16:
                raise ManagerError("device_unavailable", "RPC 未提供受支持的设备数量")
            devices = []
            for index in range(count):
                free, total = struct.unpack(
                    "<QQ", await command(11, struct.pack("<I", index), 16)
                )
                devices.append(
                    {"index": index, "memory_free": free, "memory_total": total}
                )
            return {"protocol_version": version, "devices": devices}
    except (OSError, asyncio.IncompleteReadError, TimeoutError) as exc:
        raise ManagerError(
            "rpc_unreachable", "RPC 连接、握手或读取失败；请检查服务与端口"
        ) from exc
    finally:
        if writer:
            writer.close()
            await writer.wait_closed()
