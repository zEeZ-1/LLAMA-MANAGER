#!/usr/bin/env python3
"""Browser acceptance against qa_ssh_server's isolated loopback machines only."""

import argparse
import json
from pathlib import Path
import subprocess
import time
import hashlib

from playwright.sync_api import expect, sync_playwright
from scripts.check_memory import sample


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/tmp/lim-rpc-discovery-qa"))
    parser.add_argument("--url", default="http://127.0.0.1:8017")
    parser.add_argument("--dist", type=Path, default=Path("/tmp/lim-discovery-ui-next"))
    parser.add_argument("--seconds", type=float, default=60)
    args = parser.parse_args()
    assert args.url.startswith("http://127.0.0.1:") and args.root.is_relative_to("/tmp")
    endpoint = json.loads((args.root / "ssh-endpoints.json").read_text())[2]
    deny = args.root / "machines/node-4/deny-key-install"
    deny.touch()
    result = {
        "checks": [],
        "page_errors": [],
        "scope": "loopback test SSH machines; real OpenSSH and native CPU RPC; browser uses candidate static build",
    }
    unit = (
        "llama-manager-"
        + hashlib.sha256(str(args.root / "manager.db").encode()).hexdigest()[:12]
        + ".service"
    )
    manager = int(
        subprocess.check_output(
            ["systemctl", "--user", "show", unit, "-p", "MainPID", "--value"]
        )
    )
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda e: result["page_errors"].append(str(e)))
        page.route(
            args.url + "/",
            lambda route: route.fulfill(path=str(args.dist / "index.html")),
        )
        page.route(
            "**/assets/**",
            lambda route: route.fulfill(
                path=str(args.dist / "assets" / route.request.url.rsplit("/", 1)[-1])
            ),
        )
        page.goto(args.url)
        page.get_by_role("button", name="节点", exact=True).click()
        page.get_by_role("button", name="添加节点", exact=True).click()
        dialog = page.get_by_role("dialog")
        dialog.get_by_label("节点名称", exact=True).fill("Browser Discovery QA")
        dialog.get_by_label("IP 地址", exact=True).fill(endpoint["address"])
        dialog.get_by_label("SSH 用户名", exact=True).fill(endpoint["username"])
        dialog.get_by_label("SSH 端口", exact=True).fill(str(endpoint["port"]))
        dialog.get_by_label("登录密码", exact=True).fill("lim-isolated-qa-password")
        dialog.get_by_role("button", name="检测主机", exact=True).click()
        expect(dialog.locator("code")).to_have_text(
            endpoint["fingerprint"], timeout=10000
        )
        dialog.get_by_label("我已通过可信渠道核对该主机指纹").check()
        dialog.get_by_role("button", name="信任并连接", exact=True).click()
        expect(dialog).not_to_be_visible(timeout=20000)
        card = page.locator(".node-card").filter(
            has=page.get_by_role("heading", name="Browser Discovery QA", exact=True)
        )
        expect(
            card.get_by_text("已检测到 1 个运行中的 RPC", exact=False)
        ).to_be_visible(timeout=15000)
        expect(card.get_by_text("密码认证成功", exact=False)).to_be_visible()
        expect(card.get_by_text("就绪", exact=True)).to_be_visible()
        result["checks"].append(
            "password validation automatically displays existing RPC; optional key-install failure leaves connection and RPC usable with a warning"
        )
        deny.unlink()
        card.get_by_role("button", name="连接", exact=True).click()
        dialog = page.get_by_role("dialog")
        dialog.get_by_label("登录密码", exact=True).fill("lim-isolated-qa-password")
        dialog.get_by_role("button", name="连接节点", exact=True).click()
        expect(dialog).not_to_be_visible(timeout=20000)
        expect(card.get_by_text("密码认证成功", exact=False)).not_to_be_visible(
            timeout=15000
        )
        result["checks"].append(
            "password reconnection repairs key bootstrap without losing discovered RPC"
        )
        card.get_by_role("button", name="新建 RPC", exact=True).click()
        dialog = page.get_by_role("dialog")
        port = int(dialog.get_by_label("RPC 端口", exact=True).input_value())
        assert port != 19062
        dialog.get_by_role("button", name="下一步：部署", exact=True).click()
        dialog = page.get_by_role("dialog")
        expect(dialog.get_by_label("RPC 发布包", exact=True)).to_have_value(
            "9214bdb-aarch64-jetson-v2", timeout=10000
        )
        dialog.get_by_role("button", name="部署并启动", exact=True).click()
        expect(dialog).not_to_be_visible(timeout=180000)
        created = card.locator(".worker-box").filter(
            has_text=endpoint["address"] + ":" + str(port)
        )
        expect(created.get_by_text("就绪", exact=True)).to_be_visible(timeout=15000)
        result["checks"].append(
            "new RPC chooses a free port, deploys with live progress and appears alongside discovered service"
        )
        page.get_by_role("button", name="创建实例", exact=True).first.click()
        dialog = page.get_by_role("dialog")
        dialog.get_by_label("推理方式", exact=True).select_option("rpc")
        old = dialog.get_by_label("Browser Discovery QA · RPC 19062", exact=False)
        new = dialog.get_by_label(
            "Browser Discovery QA · RPC " + str(port), exact=False
        )
        old.check()
        new.check()
        expect(old).not_to_be_checked()
        expect(new).to_be_checked()
        dialog.get_by_role("button", name="关闭", exact=True).click()
        result["checks"].append(
            "placement exposes both RPC endpoints and replaces the previous selection for the same GPU"
        )
        browser_pid = next(
            v["id"]
            for v in browser.new_browser_cdp_session().send(
                "SystemInfo.getProcessInfo"
            )["processInfo"]
            if v["type"] == "browser"
        )
        peak = sample([manager, browser_pid])
        start = time.monotonic()
        samples = 0
        while time.monotonic() - start < args.seconds:
            current = sample([manager, browser_pid])
            samples += 1
            if current["rss_bytes"] > peak["rss_bytes"]:
                peak = current
            page.wait_for_timeout(1000)
        result["memory"] = {
            "duration_seconds": time.monotonic() - start,
            "samples": samples,
            "budget_bytes": 1024**3,
            "passed": peak["rss_bytes"] < 1024**3,
            "peak": peak,
        }
        assert result["memory"]["passed"]
        page.screenshot(
            path=str(args.root / "discovery-nodes-desktop.png"), full_page=True
        )
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth"
        )
        page.screenshot(
            path=str(args.root / "discovery-nodes-mobile.png"), full_page=True
        )
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.on("dialog", lambda d: d.accept())
        created.get_by_role("button", name="删除 RPC", exact=True).click()
        expect(created).not_to_be_visible(timeout=20000)
        existing = card.locator(".worker-box").filter(
            has_text=endpoint["address"] + ":19062"
        )
        expect(existing.get_by_text("就绪", exact=True)).to_be_visible()
        existing.get_by_role("button", name="删除 RPC", exact=True).click()
        expect(existing).not_to_be_visible(timeout=20000)
        expect(card.get_by_text("未检测到运行中的 RPC", exact=False)).to_be_visible(
            timeout=15000
        )
        result["checks"].append(
            "UI delete separately removes created RPC and discovered external RPC; node returns to no-running-RPC state"
        )
        assert not result["page_errors"]
        (args.root / "discovery-browser.json").write_text(json.dumps(result, indent=2))
        print(
            json.dumps(
                {k: v for k, v in result.items() if k != "memory"}, ensure_ascii=False
            ),
            flush=True,
        )
        print("peak RSS MiB", round(peak["rss_bytes"] / 1024**2, 2), flush=True)
        browser.close()


if __name__ == "__main__":
    main()
