#!/usr/bin/env python3
"""Opt-in three-pane browser regression; only a temporary manager is used."""

import argparse
import copy
import json
import os
import socket
import struct
import subprocess
import tempfile
import time
import urllib.request
import zlib
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from verify_ui_v31 import REPO, SETUP


def verify(url, dist, output):
    errors, checks = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda e: errors.append(str(e)))
        base = page.request.get(url + "/api/v1/nodes").json()["nodes"][0]
        uploads = []
        page.on(
            "request",
            lambda r: (
                uploads.append(r.headers.get("content-type"))
                if r.url.endswith("/media/images") and r.method == "POST"
                else None
            ),
        )

        def nodes_route(route):
            local = copy.deepcopy(base)
            local.update(
                name="Local Node",
                hostname="control.demo",
                devices=[
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
                ],
                memory_info={
                    "total": 8 * 1024**3,
                    "used": 3 * 1024**3,
                    "available": 5 * 1024**3,
                    "usage_percent": 37.5,
                },
            )
            records = page.request.get(url + "/api/v1/instances").json()["instances"]
            local["instance_count"] = len(records)
            local["ready_count"] = sum(i["status"] == "ready" for i in records)
            nodes = [local]
            for i in [1, 2]:
                node = copy.deepcopy(local)
                node.update(
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
                nodes.append(node)
            route.fulfill(json={"nodes": nodes})

        page.route(url + "/", lambda r: r.fulfill(path=str(dist / "index.html")))
        page.route(
            "**/assets/**",
            lambda r: r.fulfill(
                path=str(dist / "assets" / r.request.url.rsplit("/", 1)[-1])
            ),
        )
        page.route("**/ascnet.svg", lambda r: r.fulfill(path=str(dist / "ascnet.svg")))
        page.route("**/api/v1/nodes", nodes_route)
        page.route(
            "**/api/v1/workers/rpc-1/logs",
            lambda r: r.fulfill(json={"text": "Fixture RPC ready"}),
        )

        def overflow():
            assert page.evaluate(
                "document.documentElement.scrollWidth <= innerWidth"
            ), "horizontal overflow"
            assert (
                page.locator(".ascnet-workspace").bounding_box()["height"]
                <= page.viewport_size["height"] + 1
            )

        def create(model, name, context=256):
            page.get_by_role("button", name="模型目录", exact=False).click()
            catalog = page.get_by_role("dialog", name="模型目录", exact=True)
            catalog.locator(".model-card").filter(
                has=page.get_by_role("heading", name=model, exact=True)
            ).get_by_role("button", name="创建实例", exact=True).click()
            expect(catalog).to_have_count(0)
            form = page.get_by_role("region", name="创建推理实例", exact=True)
            form.get_by_label("实例名称", exact=True).fill(name)
            form.get_by_label("Context size", exact=True).fill(str(context))
            form.get_by_role("button", name="创建并启动", exact=True).click()
            expect(form).to_have_count(0)
            card = page.locator(".managed-instance").filter(
                has=page.get_by_role("button", name=name, exact=True)
            )
            expect(card.locator(".badge")).to_have_text(
                "失败" if context == 131 else "就绪", timeout=15000
            )
            return card

        try:
            page.goto(url)
            expect(
                page.get_by_role("button", name="查看节点 Local Node", exact=True)
            ).to_be_visible()
            expect(page.get_by_role("complementary", name="会话列表")).to_be_visible()
            expect(page.get_by_role("complementary", name="实例管理")).to_be_visible()
            expect(page.get_by_role("navigation")).to_have_count(0)
            overflow()
            page.screenshot(path=str(output / "workspace-empty.png"), full_page=True)
            checks.append(
                "persistent conversation / node / instance panes; no page navigation bar"
            )
            page.get_by_role("button", name="查看节点 Edge 01", exact=True).click()
            expect(page.locator(".node-card")).to_have_count(1)
            expect(
                page.get_by_role("button", name="删除 RPC", exact=True)
            ).to_be_visible()
            page.get_by_role("button", name="日志", exact=True).click()
            expect(page.get_by_role("dialog").locator("pre")).to_contain_text(
                "Fixture RPC ready"
            )
            page.keyboard.press("Escape")
            page.get_by_role("button", name="新建 RPC", exact=True).click()
            expect(page.get_by_role("dialog")).to_be_visible()
            page.get_by_role("button", name="关闭 RPC 配置", exact=True).click()
            page.get_by_role("button", name="返回节点网络", exact=True).click()
            page.get_by_role("button", name="添加节点", exact=True).click()
            dialog = page.get_by_role("dialog")
            expect(dialog.get_by_label("登录密码", exact=True)).to_be_visible()
            page.keyboard.press("Escape")
            expect(dialog).to_have_count(0)
            checks.append(
                "click node -> isolated node controls, RPC logs/config, password onboarding and return to graph; remote data mocked"
            )
            page.get_by_role("button", name="模型目录", exact=False).click()
            catalog = page.get_by_role("dialog", name="模型目录", exact=True)
            expect(catalog.locator(".model-card")).to_have_count(3)
            catalog.get_by_role("button", name="图文模型", exact=False).click()
            expect(catalog.locator(".model-card")).to_have_count(1)
            catalog.get_by_role("button", name="全部模型", exact=False).click()
            catalog.get_by_label("搜索模型").fill("nothing")
            expect(catalog.get_by_text("没有匹配的模型", exact=True)).to_be_visible()
            catalog.get_by_role("button", name="重置筛选", exact=True).click()
            unsupported = catalog.locator(".model-card").filter(
                has=page.get_by_role("heading", name="unsupported", exact=True)
            )
            expect(unsupported.get_by_role("button", name="创建实例")).to_be_disabled()
            unsupported.locator("summary").click()
            expect(unsupported.get_by_text("未找到可创建实例的主权重")).to_be_visible()
            page.keyboard.press("Escape")
            checks.append(
                "model catalog, type/search filters, invalid asset reasons and disabled creation"
            )
            failed = create("tiny", "Failed allocation", 131)
            expect(failed.locator(".instance-failure-summary")).to_contain_text(
                "960.00 MiB"
            )
            failed.get_by_role(
                "button", name="查看 Failed allocation 详情", exact=True
            ).click()
            dialog = page.get_by_role("dialog")
            expect(dialog.get_by_role("alert")).to_contain_text("KV")
            dialog.get_by_role("alert").locator("summary").click()
            expect(dialog.get_by_role("alert").locator("pre")).to_contain_text(
                "failed to allocate buffer"
            )
            page.keyboard.press("Escape")
            create("tiny", "Local Chat")
            create("vision", "Vision Studio")
            page.get_by_label("筛选实例状态", exact=True).select_option("failed")
            expect(page.locator(".managed-instance")).to_have_count(1)
            page.get_by_label("筛选实例状态", exact=True).select_option("all")
            checks.append(
                "inline creation, real native startup/failure diagnostics, instance status filtering"
            )
            # Configuration remains editable in the instance pane without a modal.
            page.get_by_role("button", name="新建实例", exact=True).click()
            form = page.get_by_role("region", name="创建推理实例", exact=True)
            form.locator("summary").click()
            expect(form.get_by_label("CPU threads", exact=True)).to_be_visible()
            form.get_by_label("CPU threads", exact=True).fill("2")
            models = page.request.get(url + "/api/v1/models").json()["models"]
            text_model = next(m for m in models if m["name"] == "tiny")
            image_model = next(m for m in models if m["name"] == "vision")
            form.get_by_label("模型", exact=True).select_option(image_model["id"])
            form.get_by_label("模型变体", exact=True).select_option(
                image_model["default_variant_id"]
            )
            form.get_by_label("输入类型", exact=True).select_option("text")
            form.get_by_label("模型", exact=True).select_option(text_model["id"])
            expect(form.get_by_label("模型变体", exact=True)).to_have_value(
                text_model["default_variant_id"]
            )
            form.get_by_label("模型", exact=True).select_option(image_model["id"])
            expect(form.get_by_label("输入类型", exact=True)).to_have_value(
                "image_text"
            )
            for _ in range(2):
                page.get_by_role("button", name="模型目录", exact=False).click()
                catalog = page.get_by_role("dialog", name="模型目录", exact=True)
                catalog.locator(".model-card").filter(
                    has=page.get_by_role("heading", name="vision", exact=True)
                ).get_by_role("button", name="创建实例", exact=True).click()
                expect(form.get_by_label("模型", exact=True)).to_have_value(
                    image_model["id"]
                )
                form.get_by_label("模型", exact=True).select_option(text_model["id"])
            form.get_by_role("button", name="取消创建实例", exact=True).click()
            checks.append(
                "advanced parameters, model switches reset stale variant/modality and repeated catalog selection starts the chosen model draft"
            )
            card = page.locator(".managed-instance").filter(
                has=page.get_by_role("button", name="Local Chat", exact=True)
            )
            card.get_by_role("button", name="开始对话", exact=True).click()
            expect(page.get_by_role("region", name="对话工作区")).to_be_visible()
            page.get_by_label("输入消息").fill("hello workspace")
            page.get_by_role("button", name="发送消息", exact=True).click()
            expect(page.locator(".message.assistant .markdown").last).to_contain_text(
                "Hello world"
            )
            expect(
                page.get_by_role("button", name="打开会话 hello workspace", exact=True)
            ).to_be_visible()
            page.get_by_label("输入消息").fill("stall")
            page.get_by_role("button", name="发送消息", exact=True).click()
            expect(page.get_by_role("button", name="停止生成")).to_be_visible()
            page.get_by_role("button", name="返回计算网络", exact=True).click()
            expect(
                page.get_by_role("button", name="查看节点 Edge 01", exact=True)
            ).to_be_visible()
            expect(
                page.get_by_role("button", name="新建会话", exact=True)
            ).to_be_disabled()
            page.get_by_role("button", name="返回当前对话", exact=True).click()
            expect(page.get_by_role("button", name="停止生成")).to_be_visible()
            page.get_by_role("button", name="停止生成").click()
            expect(page.get_by_role("button", name="发送消息")).to_be_visible()
            checks.append(
                "chat stream, live conversation list, generation survives inspecting nodes, stop and session lock"
            )
            page.locator(".managed-instance").filter(
                has=page.get_by_role("button", name="Vision Studio", exact=True)
            ).get_by_role("button", name="开始对话", exact=True).click()

            def chunk(kind, data):
                return (
                    struct.pack("!I", len(data))
                    + kind
                    + data
                    + struct.pack("!I", zlib.crc32(kind + data) & 0xFFFFFFFF)
                )

            png = (
                b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", struct.pack("!IIBBBBB", 32, 32, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress((b"\0" + b"\xff\xd8\x68" * 32) * 32))
                + chunk(b"IEND", b"")
            )
            page.get_by_label("选择图片", exact=True).set_input_files(
                {"name": "fixture.png", "mimeType": "image/png", "buffer": png}
            )
            page.get_by_label("输入消息", exact=True).fill("describe image")
            expect(page.get_by_role("button", name="发送消息")).to_be_enabled(
                timeout=10000
            )
            page.get_by_role("button", name="发送消息").click()
            expect(page.locator(".message.assistant .markdown").last).to_contain_text(
                "Hello world"
            )
            expect(page.locator(".message.user img")).to_have_count(1)
            page.get_by_role("button", name="查看图片", exact=True).click()
            expect(page.get_by_role("dialog", name="图片预览")).to_be_visible()
            page.keyboard.press("Escape")
            page.reload()
            expect(
                page.get_by_role("button", name="查看节点 Local Node", exact=True)
            ).to_be_visible()
            page.get_by_role(
                "button", name="打开会话 describe image", exact=True
            ).click()
            expect(page.locator(".message.user img")).to_have_count(1)
            expect(page.locator(".message.assistant .markdown").last).to_contain_text(
                "Hello world"
            )
            assert page.evaluate(
                "indexedDB.databases().then(d=>d.some(x=>x.name==='llama-inference-manager'))"
            )
            checks.append(
                "binary image upload, image ID chat, preview, original IndexedDB and history restoration"
            )
            page.screenshot(path=str(output / "chat-desktop.png"), full_page=True)
            page.get_by_role("button", name="返回计算网络", exact=True).click()
            page.screenshot(path=str(output / "nodes-desktop.png"), full_page=True)
            for width in [1920, 1280, 1100, 900, 768, 390, 375]:
                page.set_viewport_size(
                    {"width": width, "height": 1000 if width > 640 else 844}
                )
                page.wait_for_timeout(100)
                overflow()
                expect(
                    page.get_by_role("button", name="查看节点 Local Node", exact=True)
                ).to_be_visible()
                if width == 390:
                    page.screenshot(
                        path=str(output / "nodes-mobile.png"), full_page=True
                    )
            page.get_by_role("button", name="显示会话列表", exact=True).click()
            expect(page.get_by_role("complementary", name="会话列表")).to_be_visible()
            assert page.locator(".workspace-center").evaluate("(e)=>e.inert")
            page.get_by_label("搜索会话").fill("describe")
            expect(page.locator(".conversation-item")).to_have_count(1)
            page.get_by_label("搜索会话").fill("")
            page.get_by_role(
                "button", name="打开会话 describe image", exact=True
            ).click()
            expect(page.get_by_role("complementary", name="会话列表")).to_be_hidden()
            expect(page.get_by_role("region", name="对话工作区")).to_be_visible()
            page.screenshot(path=str(output / "chat-mobile.png"), full_page=True)
            page.get_by_role("button", name="显示实例管理", exact=True).click()
            expect(page.get_by_role("complementary", name="实例管理")).to_be_visible()
            page.keyboard.press("Shift+Tab")
            expect(
                page.locator(
                    ".instance-panel-scroll .managed-instance-actions button"
                ).last
            ).to_be_focused()
            page.keyboard.press("Tab")
            expect(page.get_by_role("button", name="关闭实例管理")).to_be_focused()
            page.screenshot(path=str(output / "instances-mobile.png"), full_page=True)
            page.get_by_role("button", name="新建实例", exact=True).click()
            form = page.get_by_role("region", name="创建推理实例", exact=True)
            form.get_by_label("实例名称", exact=True).fill("Preserved draft")
            page.keyboard.press("Escape")
            expect(page.get_by_role("complementary", name="实例管理")).to_be_hidden()
            page.get_by_role("button", name="显示实例管理", exact=True).click()
            expect(form.get_by_label("实例名称", exact=True)).to_have_value(
                "Preserved draft"
            )
            page.screenshot(path=str(output / "create-mobile.png"), full_page=True)
            form.get_by_role("button", name="取消创建实例", exact=True).click()
            page.keyboard.press("Escape")
            page.get_by_role("button", name="显示会话列表", exact=True).click()
            page.set_viewport_size({"width": 1440, "height": 1000})
            assert page.locator(".workspace-center").evaluate("(e)=>!e.inert")
            checks.append(
                "seven additional widths, mobile drawers, keyboard focus/Escape, search, preserved draft and resize restoration"
            )
            page.get_by_role(
                "button", name="打开会话 hello workspace", exact=True
            ).click()
            expect(page.locator(".message.user .markdown").first).to_contain_text(
                "hello workspace"
            )
            page.once("dialog", lambda d: d.accept())
            page.get_by_role(
                "button", name="删除会话 hello workspace", exact=True
            ).click()
            expect(
                page.get_by_role("button", name="打开会话 hello workspace", exact=True)
            ).to_have_count(0)
            expect(page.locator(".chat-welcome")).to_be_visible()
            failed = page.locator(".managed-instance").filter(
                has=page.get_by_role("button", name="Failed allocation", exact=True)
            )
            page.once("dialog", lambda d: d.accept())
            failed.get_by_role(
                "button", name="销毁 Failed allocation", exact=True
            ).click()
            expect(failed).to_have_count(0)
            checks.append(
                "delete active conversation safely and remove destroyed instances from current list"
            )
            assert not errors, errors
            assert uploads == ["image/png"], uploads
            report = {
                "checks": checks,
                "page_errors": errors,
                "image_upload_content_types": uploads,
                "scope": "Actual temporary manager HTTP API and deterministic native runtime; synthetic remote node inventory/logs; no real SSH/RPC or large-model inference.",
            }
            (output / "browser.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2)
            )
            print(json.dumps(report, ensure_ascii=False))
        finally:
            browser.close()


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
        else Path(tempfile.mkdtemp(prefix="ascnet-v32-evidence-"))
    )
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ascnet-v32-workspace-") as temp:
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
