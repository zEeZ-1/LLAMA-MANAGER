#!/usr/bin/env python3
"""Loopback QA: UI password onboarding, placement, RPC controls and memory soak.

Requires the dedicated qa_ssh_server fixture and a separate Playwright QA venv.
"""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time

import httpx
from playwright.sync_api import expect, sync_playwright

from scripts.check_memory import sample


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/tmp/lim-v2-final"))
    parser.add_argument("--url", default="http://127.0.0.1:8013")
    parser.add_argument("--seconds", type=float, default=1800)
    args = parser.parse_args()
    assert args.url.startswith("http://127.0.0.1:")
    endpoints = json.loads((args.root / "ssh-endpoints.json").read_text())
    unit = (
        "llama-manager-"
        + hashlib.sha256(str(args.root / "manager.db").encode()).hexdigest()[:12]
        + ".service"
    )
    manager_pid = int(
        subprocess.check_output(
            ["systemctl", "--user", "show", unit, "-p", "MainPID", "--value"]
        )
    )
    result = {"checks": [], "page_errors": [], "console_errors": []}
    with (
        httpx.Client(
            base_url=args.url + "/api/v1", trust_env=False, timeout=30
        ) as client,
        sync_playwright() as p,
    ):

        def request(method, path, body=None):
            response = client.request(method, path, json=body)
            response.raise_for_status()
            return response.json()

        def job(record):
            for _ in range(1000):
                value = request("GET", "/jobs/" + record["id"])
                if value["state"] == "completed":
                    return value
                assert value["state"] not in {"failed", "interrupted"}, value
                time.sleep(0.1)
            raise AssertionError("QA job timed out")

        for node in request("GET", "/nodes")["nodes"]:
            if node.get("address") == endpoints[2]["address"] and not node.get(
                "rpc_workers"
            ):
                request("DELETE", "/nodes/" + node["id"])

        browser = p.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        page.on("pageerror", lambda e: result["page_errors"].append(str(e)))
        page.on(
            "console",
            lambda e: (
                result["console_errors"].append(e.text) if e.type == "error" else None
            ),
        )
        browser_pid = next(
            proc["id"]
            for proc in browser.new_browser_cdp_session().send(
                "SystemInfo.getProcessInfo"
            )["processInfo"]
            if proc["type"] == "browser"
        )
        roots = [manager_pid, browser_pid]
        page.goto(args.url)
        page.get_by_role("button", name="节点", exact=True).click()
        expect(
            page.get_by_role("heading", name="Local Node", exact=True)
        ).to_be_visible()
        assert "nvgpu)不可用" not in page.locator("body").inner_text()
        expect(page.get_by_text("共享系统内存", exact=False).first).to_be_visible()
        page.get_by_role("button", name="添加节点", exact=True).click()
        dialog = page.get_by_role("dialog")
        record = endpoints[2]
        dialog.get_by_label("节点名称", exact=True).fill("Browser SSH QA")
        dialog.get_by_label("IP 地址", exact=True).fill(record["address"])
        dialog.get_by_label("SSH 用户名", exact=True).fill(record["username"])
        dialog.get_by_label("SSH 端口", exact=True).fill(str(record["port"]))
        dialog.get_by_label("登录密码", exact=True).fill("lim-isolated-qa-password")
        dialog.get_by_role("button", name="检测主机", exact=True).click()
        expect(dialog.locator("code")).to_have_text(
            record["fingerprint"], timeout=15000
        )
        dialog.get_by_label("我已通过可信渠道核对该主机指纹").check()
        dialog.get_by_role("button", name="信任并连接", exact=True).click()
        expect(dialog).not_to_be_visible()
        card = page.locator(".node-card").filter(
            has=page.get_by_role("heading", name="Browser SSH QA", exact=True)
        )
        expect(card.get_by_text("在线", exact=True)).to_be_visible(timeout=20000)
        result["checks"].append(
            "UI IP/password -> fingerprint -> online; password bootstrap without preconfigured key"
        )

        page.get_by_role("button", name="创建实例", exact=True).first.click()
        dialog = page.get_by_role("dialog")
        dialog.get_by_label("模型", exact=True).select_option(
            request("GET", "/models")["models"][0]["id"]
        )
        dialog.get_by_label("实例名称").fill("V2 Browser RPC QA")
        dialog.get_by_label("推理方式").select_option("rpc")
        dialog.get_by_label("Context size").fill("512")
        dialog.get_by_label("CPU threads").fill("2")
        dialog.get_by_label("QA RPC 1", exact=False).check()
        dialog.get_by_label("QA RPC 2", exact=False).check()
        dialog.get_by_label("设备 1 分配权重").fill("2")
        dialog.get_by_role("button", name="前移", exact=True).last.click()
        dialog.get_by_role("button", name="创建并启动", exact=True).click()
        row = page.get_by_role("row").filter(
            has=page.get_by_role("button", name="V2 Browser RPC QA", exact=True)
        )
        expect(row.get_by_text("就绪", exact=True)).to_be_visible(timeout=60000)
        instance = next(
            i
            for i in request("GET", "/instances")["instances"]
            if i["name"] == "V2 Browser RPC QA"
        )
        assert instance["runtime"]["placement"]["tensor_split"] == [1, 2]
        assert len(instance["runtime"]["placement"]["rpc_endpoints"]) == 2
        stream = client.post(
            "/instances/" + instance["id"] + "/chat/completions",
            json={
                "messages": [{"role": "user", "content": "Hello"}],
                "stream": True,
                "max_tokens": 12,
            },
        )
        assert stream.status_code == 200 and "data: [DONE]" in stream.text
        request("DELETE", "/instances/" + instance["id"])
        result["checks"].append(
            "UI multiworker selection, order and ratio -> native inference -> resource release"
        )

        page.get_by_role("button", name="节点", exact=True).click()
        worker_card = page.locator(".node-card").filter(
            has=page.get_by_role("heading", name="QA RPC 1", exact=True)
        )
        expect(
            worker_card.get_by_role("button", name="停止", exact=True)
        ).to_be_enabled(timeout=20000)
        worker_card.get_by_role("button", name="停止", exact=True).click()
        expect(worker_card.get_by_text("已停止", exact=True)).to_be_visible(
            timeout=20000
        )
        worker_card.get_by_role("button", name="启动", exact=True).click()
        expect(worker_card.get_by_text("就绪", exact=True)).to_be_visible(timeout=20000)
        worker_card.get_by_role("button", name="日志", exact=True).click()
        expect(page.get_by_role("dialog").locator("pre")).to_contain_text("RPC")
        page.get_by_role("button", name="关闭日志").click()
        page.screenshot(path=str(args.root / "nodes-desktop.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path=str(args.root / "nodes-mobile.png"), full_page=True)
        assert page.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth"
        )
        page.set_viewport_size({"width": 1440, "height": 1000})
        result["checks"].append("UI RPC stop/start/logs; desktop/mobile node layout")

        baseline = sample(roots)
        # Expand to ten simulated machines; real OpenSSH authentication and pooled commands.
        for record in endpoints[3:]:
            node = request(
                "POST",
                "/nodes",
                {
                    "name": "Pool QA " + record["address"],
                    "address": record["address"],
                    "port": record["port"],
                    "username": record["username"],
                },
            )
            challenge = request("POST", "/nodes/" + node["id"] + "/host-key/scan")
            request(
                "POST",
                "/nodes/" + node["id"] + "/host-key/trust",
                {
                    "challenge_id": challenge["challenge_id"],
                    "fingerprint": record["fingerprint"],
                },
            )
            job(
                request(
                    "POST",
                    "/nodes/" + node["id"] + "/connect",
                    {"password": "lim-isolated-qa-password"},
                )
            )
        result["checks"].append(
            "ten registered SSH nodes; password/key bootstrap and connection pool eviction"
        )
        peak, first, final = sample(roots), sample(roots), None
        started = time.monotonic()
        samples, max_connections = 0, 0
        (args.root / "soak-started.json").write_text(
            json.dumps(
                {
                    "manager_pid": manager_pid,
                    "browser_pid": browser_pid,
                    "seconds": args.seconds,
                    "baseline": baseline,
                },
                indent=2,
            )
        )
        print("Browser UI checks passed; starting memory soak", flush=True)
        while time.monotonic() - started < args.seconds:
            current = sample(roots)
            if current["rss_bytes"] > peak["rss_bytes"]:
                peak = current
            final = current
            health = request("GET", "/health")
            assert health["managed_lifecycle"]
            max_connections = max(max_connections, health["ssh_connections"])
            assert health["ssh_connections"] <= 4
            samples += 1
            page.wait_for_timeout(2000)
        result["memory"] = {
            "duration_seconds": time.monotonic() - started,
            "samples": samples,
            "roots": roots,
            "baseline": baseline,
            "first": first,
            "final": final,
            "peak": peak,
            "max_ssh_connections": max_connections,
            "budget_bytes": 1024**3,
            "passed": peak["rss_bytes"] < 1024**3,
        }
        assert result["memory"]["passed"]
        assert not result["page_errors"] and not result["console_errors"], result
        (args.root / "browser-soak.json").write_text(json.dumps(result, indent=2))
        browser.close()
        print(
            json.dumps({k: v for k, v in result.items() if k != "memory"}), flush=True
        )
        print(
            "Peak management+browser RSS MiB: "
            + str(round(peak["rss_bytes"] / 1024**2, 2)),
            flush=True,
        )


if __name__ == "__main__":
    main()
