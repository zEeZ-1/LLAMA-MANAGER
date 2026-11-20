#!/usr/bin/env python3
"""Optional browser QA: pip install playwright; playwright install chromium.
Run only against a test workspace with a small, explicitly selected model.
"""

import argparse
import json
import time
import subprocess
from pathlib import Path
from playwright.sync_api import sync_playwright, expect


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--model-id", required=True)
    parser.add_argument(
        "--manager-pid",
        type=int,
        help="also measure backend + browser RSS during generation",
    )
    parser.add_argument("--memory-seconds", type=float, default=4)
    parser.add_argument("--output", type=Path, default=Path("/tmp/lim-browser-results"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    result = {"checks": [], "page_errors": []}
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        context = browser.new_context(
            viewport={"width": 1440, "height": 1000}, device_scale_factor=1
        )
        page = context.new_page()
        page.on("pageerror", lambda error: result["page_errors"].append(str(error)))
        session = browser.new_browser_cdp_session()
        processes = session.send("SystemInfo.getProcessInfo")["processInfo"]
        browser_pid = next(
            proc["id"] for proc in processes if proc["type"] == "browser"
        )
        (args.output / "browser-pid").write_text(str(browser_pid))
        instance_id = None
        qa_name = f"Browser QA {time.time_ns()}"
        try:
            page.goto(args.url)
            expect(page.get_by_role("heading", name="工作空间总览")).to_be_visible()
            page.get_by_role("button", name="节点", exact=True).click()
            expect(
                page.get_by_role("heading", name="Local Node", exact=True)
            ).to_be_visible()
            page.get_by_role("button", name="查看详情", exact=True).first.click()
            expect(page.get_by_text("操作系统", exact=True)).to_be_visible()
            result["checks"].append("dashboard and node details")
            page.get_by_role("button", name="模型", exact=True).click()
            expect(page.get_by_role("heading", name="模型目录")).to_be_visible()
            page.get_by_role("button", name="刷新目录", exact=True).click()
            page.get_by_role("button", name="创建实例", exact=True).first.click()
            dialog = page.get_by_role("dialog")
            dialog.get_by_label("模型", exact=True).select_option(args.model_id)
            dialog.get_by_label("实例名称").fill(qa_name)
            dialog.get_by_label("Context size").fill("512")
            dialog.get_by_label("CPU threads").fill("2")
            dialog.get_by_role("button", name="创建并启动").click()
            expect(
                page.get_by_role("heading", name="推理实例", exact=True)
            ).to_be_visible()
            row = page.get_by_role("row").filter(
                has=page.get_by_role("button", name=qa_name, exact=True)
            )
            expect(row.get_by_text("就绪", exact=True)).to_be_visible(timeout=60000)
            records = page.request.get(args.url + "/api/v1/instances").json()[
                "instances"
            ]
            instance_id = next(
                i["id"]
                for i in records
                if i["name"] == qa_name and i["status"] == "ready"
            )
            row.get_by_role("button", name=f"查看 {qa_name} 详情和日志").click()
            expect(
                page.get_by_role("dialog").get_by_text("运行日志", exact=False)
            ).to_be_visible()
            expect(page.get_by_role("dialog").locator("pre")).to_contain_text("llama")
            page.get_by_role("button", name="关闭详情").click()
            result["checks"].append(
                "model refresh, create instance, ready and runtime log"
            )
            row.get_by_role("button", name="对话", exact=True).click()
            page.get_by_label("输入消息").fill("Tell me a story about a cat.")
            page.get_by_role("button", name="发送消息").click()
            expect(page.locator(".message.assistant")).to_be_visible(timeout=30000)
            expect(page.get_by_role("button", name="停止生成")).to_be_visible(
                timeout=30000
            )
            # Keep the browser open for external process-tree memory measurement during generation.
            (args.output / "streaming").write_text("ready")
            if args.manager_pid:
                root = Path(__file__).resolve().parents[1]
                subprocess.run(
                    [
                        str(root / ".venv/bin/python"),
                        str(root / "scripts/check_memory.py"),
                        "--pid",
                        str(args.manager_pid),
                        "--browser-pid",
                        str(browser_pid),
                        "--seconds",
                        str(args.memory_seconds),
                        "--output",
                        str(args.output / "memory.json"),
                    ],
                    check=True,
                )
            else:
                time.sleep(5)
            if page.get_by_role("button", name="停止生成").count():
                page.get_by_role("button", name="停止生成").click()
            expect(page.get_by_role("button", name="发送消息")).to_be_visible(
                timeout=30000
            )
            expect(page.locator(".message.assistant .markdown")).not_to_be_empty()
            page.screenshot(path=str(args.output / "chat-desktop.png"), full_page=True)
            result["checks"].append("streaming chat and stop generation")
            original_text = page.locator(".message.user .markdown").inner_text()
            page.reload()
            page.get_by_role("button", name="对话", exact=True).click()
            expect(page.locator(".message.user .markdown")).to_have_text(
                original_text, timeout=10000
            )
            result["checks"].append("IndexedDB session persistence after reload")
            page.request.delete(args.url + "/api/v1/instances/" + instance_id)
            expect(
                page.get_by_role("option", name="原实例不可用，请切换")
            ).to_be_attached(timeout=10000)
            options = (
                page.get_by_label("选择就绪实例")
                .locator("option")
                .evaluate_all(
                    "(els) => els.map(e=>({value:e.value,text:e.textContent}))"
                )
            )
            replacement = next(
                (
                    o["value"]
                    for o in options
                    if o["value"] and o["value"] != instance_id
                ),
                None,
            )
            if replacement:
                page.get_by_label("选择就绪实例").select_option(replacement)
                expect(page.locator(".message.user .markdown")).to_have_text(
                    original_text
                )
                result["checks"].append(
                    "retain history and select another ready instance"
                )
            page.set_viewport_size({"width": 390, "height": 844})
            expect(page.get_by_label("输入消息")).to_be_visible()
            assert page.evaluate(
                "document.documentElement.scrollWidth <= innerWidth"
            ), "mobile page overflows horizontally"
            page.screenshot(path=str(args.output / "chat-mobile.png"), full_page=True)
            result["checks"].append("390px mobile layout")
            page.on("dialog", lambda dialog: dialog.accept())
            page.get_by_role(
                "button", name="删除会话 Tell me a story about a cat."
            ).click()
            expect(page.locator(".session-item")).to_have_count(0)
            result["checks"].append("delete persisted session")
            assert not result["page_errors"], result["page_errors"]
        finally:
            page.screenshot(path=str(args.output / "last-state.png"), full_page=True)
            if instance_id:
                page.request.delete(args.url + "/api/v1/instances/" + instance_id)
            (args.output / "results.json").write_text(
                json.dumps(result, indent=2, ensure_ascii=False) + "\n"
            )
            print(json.dumps(result, ensure_ascii=False))
            browser.close()


if __name__ == "__main__":
    main()
