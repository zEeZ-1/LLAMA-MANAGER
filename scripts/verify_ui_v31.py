#!/usr/bin/env python3
"""Opt-in browser regression against a temporary manager and deterministic runtime.

Requires the project development venv and Playwright in the invoking interpreter.
Never connects to the production API or performs real SSH / RPC operations.
"""

import argparse
import copy
import json
import os
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

REPO = Path(__file__).resolve().parents[1]


def verify(url, dist, output):
    ROOT, DIST, URL = output, dist, url
    errors, checks, network = [], [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on(
            "request",
            lambda r: network.append(
                {
                    "url": r.url.split("?")[0],
                    "method": r.method,
                    "content_type": r.headers.get("content-type"),
                    "bytes": r.post_data_buffer.__len__() if r.post_data_buffer else 0,
                }
            ),
        )
        base = page.request.get(URL + "/api/v1/nodes").json()["nodes"][0]

        def nodes_route(route):
            if route.request.method != "GET":
                return route.continue_()
            local = copy.deepcopy(base)
            local["name"] = "Local Node"
            local["hostname"] = "control.demo"
            local["devices"] = [
                {
                    "id": "CUDA0",
                    "name": "NVIDIA Orin",
                    "backend": "CUDA",
                    "backend_index": 0,
                    "memory_kind": "shared",
                    "memory_total": None,
                    "memory_free": None,
                    "properties": {"utilization_percent": None},
                    "compute_state": "ready",
                    "driver": None,
                }
            ]
            local["memory_info"] = {
                "total": 8 * 1024**3,
                "used": 3 * 1024**3,
                "available": 5 * 1024**3,
                "usage_percent": 37.5,
            }
            records = page.request.get(URL + "/api/v1/instances").json()["instances"]
            local["instance_count"] = len(records)
            local["ready_count"] = sum(i["status"] == "ready" for i in records)
            nodes = [local]
            for i in [1, 2]:
                n = copy.deepcopy(local)
                n.update(
                    id=f"edge-{i}",
                    name=f"Edge 0{i}",
                    hostname=f"edge-0{i}",
                    role="rpc_worker",
                    address=f"192.0.2.{10 + i}",
                    ssh={"port": 22, "username": "demo", "credential_id": "demo"},
                    identity={},
                    auth_state=None,
                    inventory={},
                    last_error=None,
                    model_count=0,
                    instance_count=0,
                    ready_count=0,
                    manager_memory_bytes=None,
                    telemetry_stale=False,
                    rpc_discovery={"state": "complete", "count": 1},
                    rpc_workers=[
                        {
                            "id": f"rpc-{i}",
                            "node_id": f"edge-{i}",
                            "device_ids": ["CUDA0"],
                            "endpoint": {"host": f"192.0.2.{10 + i}", "port": 50052},
                            "management_mode": "managed",
                            "controller_id": "demo",
                            "service_name": "demo-rpc.service",
                            "state": "ready",
                            "release_id": "demo-release",
                            "protocol_version": "10.0.0",
                            "process_identity": None,
                            "claim": None,
                            "rss_bytes": 25 * 1024**2,
                            "origin": "created",
                            "usable": True,
                            "client_count": 0,
                            "can_delete": True,
                            "can_start": True,
                        }
                    ],
                )
                n["memory_info"]["used"] = (2 + i) * 1024**3
                n["memory_info"]["available"] = (6 - i) * 1024**3
                n["memory_info"]["usage_percent"] = (2 + i) / 8 * 100
                nodes.append(n)
            route.fulfill(json={"nodes": nodes})

        page.route(URL + "/", lambda r: r.fulfill(path=str(DIST / "index.html")))
        page.route(
            "**/assets/**",
            lambda r: r.fulfill(
                path=str(DIST / "assets" / r.request.url.rsplit("/", 1)[-1])
            ),
        )
        page.route("**/ascnet.svg", lambda r: r.fulfill(path=str(DIST / "ascnet.svg")))
        page.route("**/api/v1/nodes", nodes_route)

        def nav(name):
            page.get_by_role("navigation").get_by_role(
                "button", name=name, exact=True
            ).click()

        def overflow():
            assert page.evaluate(
                "document.documentElement.scrollWidth <= innerWidth"
            ), page.evaluate(
                "({width:innerWidth,scroll:document.documentElement.scrollWidth})"
            )

        def create(model, name, context=256):
            nav("模型")
            card = page.locator(".model-card").filter(
                has=page.get_by_role("heading", name=model, exact=True)
            )
            card.get_by_role("button", name="创建实例", exact=True).click()
            dialog = page.get_by_role("dialog")
            dialog.get_by_label("实例名称", exact=True).fill(name)
            dialog.get_by_label("Context size", exact=True).fill(str(context))
            dialog.get_by_role("button", name="创建并启动", exact=True).click()
            expect(dialog).to_have_count(0)
            card = page.locator(".inference-card").filter(
                has=page.get_by_role("button", name=name, exact=True)
            )
            expect(card.locator(".badge")).to_have_text(
                "失败" if context == 131 else "就绪", timeout=15000
            )
            return card

        try:
            page.goto(URL)
            expect(page.get_by_text("3 节点在线")).to_be_visible()
            overflow()
            assert page.title().startswith("AscNet")
            nav("模型")
            expect(page.locator(".model-card")).to_have_count(3)
            page.get_by_role("button", name="图文模型", exact=False).click()
            expect(page.locator(".model-card")).to_have_count(1)
            page.get_by_role("button", name="文本模型", exact=False).click()
            expect(page.locator(".model-card")).to_have_count(1)
            page.get_by_role("button", name="全部模型", exact=False).click()
            page.get_by_label("搜索模型").fill("does-not-exist")
            expect(page.get_by_text("没有匹配的模型", exact=True)).to_be_visible()
            page.get_by_role("button", name="重置筛选", exact=True).click()
            unavailable = page.locator(".model-card").filter(
                has=page.get_by_role("heading", name="unsupported", exact=True)
            )
            expect(unavailable.get_by_role("button", name="创建实例")).to_be_disabled()
            unavailable.locator("summary").click()
            expect(unavailable.get_by_text("未找到可创建实例的主权重")).to_be_visible()
            checks.append(
                "model type filters, search/no results, unavailable package details"
            )
            failed = create("tiny", "Failed allocation", 131)
            expect(failed.locator(".instance-failure-summary")).to_contain_text(
                "960.00 MiB"
            )
            failed.get_by_role("button", name="Failed allocation", exact=True).click()
            dialog = page.get_by_role("dialog")
            expect(dialog.get_by_role("alert")).to_contain_text("KV")
            dialog.get_by_role("alert").locator("summary").click()
            expect(dialog.get_by_role("alert").locator("pre")).to_contain_text(
                "failed to allocate buffer"
            )
            page.screenshot(path=str(ROOT / "failure-detail.png"), full_page=True)
            dialog.get_by_role("button", name="关闭详情").click()
            checks.append(
                "real HTTP creation and subprocess failure surfaces causal diagnostic and raw excerpt"
            )
            create("tiny", "Local Chat")
            create("vision", "Vision Studio")
            nav("实例")
            page.locator(".filter-tabs").get_by_role(
                "button", name="失败", exact=False
            ).click()
            expect(page.locator(".inference-card")).to_have_count(1)
            page.locator(".filter-tabs").get_by_role(
                "button", name="全部", exact=False
            ).click()
            expect(page.locator(".inference-card")).to_have_count(3)
            text_id = next(
                i["id"]
                for i in page.request.get(URL + "/api/v1/instances").json()["instances"]
                if i["name"] == "Local Chat"
            )
            vision_id = next(
                i["id"]
                for i in page.request.get(URL + "/api/v1/instances").json()["instances"]
                if i["name"] == "Vision Studio"
            )
            nav("对话")
            page.get_by_label("选择就绪实例").select_option(text_id)
            page.get_by_label("输入消息").fill("hello ui")
            page.get_by_role("button", name="发送消息", exact=True).click()
            expect(page.locator(".message.assistant .markdown").last).to_contain_text(
                "Hello world"
            )
            expect(page.get_by_role("button", name="发送消息")).to_be_visible()
            checks.append(
                "real HTTP chat -> gateway -> deterministic native subprocess SSE"
            )
            page.get_by_label("输入消息").fill("stall")
            page.get_by_role("button", name="发送消息").click()
            expect(page.get_by_role("button", name="停止生成")).to_be_visible()
            page.get_by_role("button", name="停止生成").click()
            expect(page.get_by_role("button", name="发送消息")).to_be_visible()
            checks.append("stop generation and preserve partial message")
            page.get_by_role("button", name="新建会话", exact=True).click()
            page.get_by_label("选择就绪实例").select_option(vision_id)
            # Supply a valid encoded image created by the Python standard library.
            import struct
            import zlib

            def chunk(kind, data):
                return (
                    struct.pack("!I", len(data))
                    + kind
                    + data
                    + struct.pack("!I", zlib.crc32(kind + data) & 0xFFFFFFFF)
                )

            png = (
                b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", struct.pack("!IIBBBBB", 16, 16, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress((b"\0" + b"\xff\xd8\x68" * 16) * 16))
                + chunk(b"IEND", b"")
            )
            page.get_by_label("选择图片", exact=True).set_input_files(
                {"name": "ui-fixture.png", "mimeType": "image/png", "buffer": png}
            )
            page.get_by_label("输入消息").fill("describe this image")
            expect(page.get_by_role("button", name="发送消息")).to_be_enabled(
                timeout=10000
            )
            page.get_by_role("button", name="发送消息").click()
            expect(page.locator(".message.assistant .markdown").last).to_contain_text(
                "Hello world"
            )
            expect(page.locator(".message.user img")).to_have_count(1)
            page.locator(".message.user").get_by_role(
                "button", name="查看图片", exact=True
            ).click()
            expect(page.get_by_role("dialog", name="图片预览")).to_be_visible()
            page.get_by_role("button", name="关闭图片").click()
            checks.append(
                "binary image upload, IndexedDB blob, thumbnail/preview and image-id chat via fake native runtime"
            )
            page.reload()
            nav("对话")
            expect(page.locator(".message.user img")).to_have_count(1)
            expect(page.locator(".message.assistant .markdown").last).to_contain_text(
                "Hello world"
            )
            assert page.evaluate(
                "indexedDB.databases().then(d=>d.some(x=>x.name==='llama-inference-manager'))"
            )
            checks.append(
                "existing database name retained, image conversation survives reload"
            )
            # Desktop screenshots include test-only synthetic remote node telemetry.
            for label in ["总览", "模型", "实例", "节点", "对话"]:
                nav(label)
                page.wait_for_timeout(200)
                overflow()
                page.screenshot(path=str(ROOT / f"desktop-{label}.png"), full_page=True)
            nav("节点")
            page.get_by_role("button", name="添加节点", exact=True).click()
            expect(page.get_by_role("dialog")).to_be_visible()
            overflow()
            page.keyboard.press("Escape")
            expect(page.get_by_role("dialog")).to_have_count(0)
            checks.append(
                "node cards, RPC actions and password onboarding dialog preserved (remote telemetry mocked, no SSH side effects)"
            )
            for width in [1920, 1024, 768, 390, 375]:
                page.set_viewport_size(
                    {"width": width, "height": 1000 if width > 640 else 844}
                )
                for label in ["总览", "节点", "模型", "实例", "对话"]:
                    nav(label)
                    page.wait_for_timeout(100)
                    overflow()
                    if width == 390:
                        page.screenshot(
                            path=str(ROOT / f"mobile-{label}.png"), full_page=True
                        )
            checks.append("all five pages fit 1920/1440/1024/768/390/375px viewports")
            nav("对话")
            page.get_by_role("button", name="显示会话列表", exact=True).click()
            expect(page.get_by_role("complementary", name="会话列表")).to_be_visible()
            assert page.locator(".conversation").evaluate("(e)=>e.inert")
            page.keyboard.press("Escape")
            expect(page.get_by_role("complementary", name="会话列表")).to_have_count(0)
            page.get_by_role("button", name="显示计算资源", exact=True).click()
            expect(page.get_by_role("complementary", name="计算资源")).to_be_visible()
            page.screenshot(
                path=str(ROOT / "mobile-resource-drawer.png"), full_page=True
            )
            page.keyboard.press("Shift+Tab")
            expect(page.locator(".cluster-manage")).to_be_focused()
            page.keyboard.press("Tab")
            expect(page.get_by_role("button", name="关闭计算资源")).to_be_focused()
            page.get_by_role("button", name="关闭计算资源").click()
            expect(page.get_by_role("complementary", name="计算资源")).to_have_count(0)
            page.get_by_role("button", name="显示会话列表", exact=True).click()
            page.set_viewport_size({"width": 1024, "height": 900})
            expect(page.locator(".conversation")).to_have_js_property("inert", False)
            page.get_by_role("button", name="关闭会话列表", exact=True).click()
            page.set_viewport_size({"width": 375, "height": 844})
            checks.append(
                "mobile drawers open/close, focus containment, Escape, inert background and resize to desktop"
            )
            nav("实例")
            failed = page.locator(".inference-card").filter(
                has=page.get_by_role("button", name="Failed allocation", exact=True)
            )
            page.once("dialog", lambda d: d.accept())
            failed.get_by_role(
                "button", name="销毁 Failed allocation", exact=True
            ).click()
            expect(failed).to_have_count(0)
            checks.append(
                "destroyed instance disappears from current list after DELETE"
            )
            nav("模型")
            page.locator(".model-card").filter(
                has=page.get_by_role("heading", name="vision", exact=True)
            ).get_by_role("button", name="创建实例", exact=True).click()
            overflow()
            bounds = page.get_by_role("dialog").bounding_box()
            assert bounds and bounds["y"] >= 0 and bounds["y"] + bounds["height"] <= 845
            page.screenshot(path=str(ROOT / "mobile-create.png"), full_page=False)
            page.get_by_role("dialog").get_by_role(
                "button", name="创建并启动", exact=True
            ).scroll_into_view_if_needed()
            expect(
                page.get_by_role("dialog").get_by_role(
                    "button", name="创建并启动", exact=True
                )
            ).to_be_in_viewport()
            page.get_by_role("dialog").get_by_role(
                "button", name="关闭", exact=True
            ).click()
            assert not errors, errors
            report = {
                "checks": checks,
                "page_errors": errors,
                "requests": len(network),
                "image_upload_content_types": [
                    r["content_type"]
                    for r in network
                    if r["url"].endswith("/media/images") and r["method"] == "POST"
                ],
                "scope": "Isolated actual manager HTTP API, deterministic native subprocess; remote node GET inventory synthesized with TEST-NET addresses; no real SSH/RPC or hardware inference exercised.",
            }
            (ROOT / "browser.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2)
            )
            print(json.dumps(report, ensure_ascii=False))
        finally:
            browser.close()


SETUP = """from pathlib import Path
import sys, yaml
from backend.tests.conftest import cfg, gguf
root=Path(sys.argv[1]); settings=cfg.__wrapped__(root)
settings.server.port=int(sys.argv[2]); settings.instances.startup_timeout_seconds=6
settings.limits.max_request_bytes=4*1024*1024
settings.models.scan_interval_seconds=1; settings.models.stability_seconds=.1
vision=settings.models.directories[0]/'vision'
gguf(vision/'main.gguf')
gguf(vision/'mmproj.gguf',metadata={'general.architecture':'clip','clip.has_vision_encoder':True})
unsupported=settings.models.directories[0]/'unsupported';unsupported.mkdir()
(unsupported/'model.safetensors').write_text('not a model')
(root/'config.yaml').write_text(yaml.safe_dump(settings.model_dump(mode='json')))
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=REPO / "frontend/dist")
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--backend-python", type=Path, default=REPO / ".venv/bin/python"
    )
    args = parser.parse_args()
    dist = args.dist.resolve()
    if not (dist / "index.html").is_file():
        parser.error("Build the frontend first, or provide --dist.")
    output = (
        args.output.resolve()
        if args.output
        else Path(tempfile.mkdtemp(prefix="ascnet-v31-evidence-"))
    )
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ascnet-v31-workspace-") as temp:
        workspace = Path(temp)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        subprocess.run(
            [str(args.backend_python), "-c", SETUP, temp, str(port)],
            cwd=REPO,
            check=True,
        )
        env = os.environ | {"LIM_CONFIG": str(workspace / "config.yaml")}
        # The synthetic fixture server is the only process this runner owns.
        with (workspace / "manager.log").open("w") as log:
            server = subprocess.Popen(
                [str(args.backend_python), "-m", "backend.app", "--direct"],
                cwd=REPO,
                env=env,
                stdout=log,
                stderr=log,
            )
            try:
                url = f"http://127.0.0.1:{port}"
                deadline = time.monotonic() + 15
                while True:
                    try:
                        with urllib.request.urlopen(
                            url + "/api/v1/health", timeout=1
                        ) as response:
                            assert json.load(response)["managed_lifecycle"] is False
                        break
                    except (OSError, TimeoutError):
                        if time.monotonic() > deadline or server.poll() is not None:
                            raise RuntimeError("Isolated manager failed to start")
                        time.sleep(0.1)
                verify(url, dist, output)
            finally:
                server.terminate()
                try:
                    server.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait()
    print(f"Browser evidence: {output}")


if __name__ == "__main__":
    main()
