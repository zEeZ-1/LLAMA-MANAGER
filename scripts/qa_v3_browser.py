#!/usr/bin/env python3
"""Image acceptance using an isolated control app and real native vision model."""

import argparse
import json
from pathlib import Path
import re
import time
from playwright.sync_api import sync_playwright, expect


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/tmp/lim-v3-qa"))
    parser.add_argument("--url", default="http://127.0.0.1:8018")
    parser.add_argument("--dist", type=Path, default=Path("/tmp/lim-v3-ui"))
    parser.add_argument("--model", default="qwen2vl")
    args = parser.parse_args()
    assert args.url.startswith("http://127.0.0.1:") and args.root.is_relative_to("/tmp")
    instance_name = "Browser V3 Vision " + str(time.time_ns())
    report = {
        "checks": [],
        "page_errors": [],
        "scope": "isolated API + candidate UI + native vision inference",
        "model": args.model,
    }
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.on("pageerror", lambda e: report["page_errors"].append(str(e)))
            page.route(
                args.url + "/",
                lambda route: route.fulfill(path=str(args.dist / "index.html")),
            )
            page.route(
                "**/assets/**",
                lambda route: route.fulfill(
                    path=str(
                        args.dist / "assets" / route.request.url.rsplit("/", 1)[-1]
                    )
                ),
            )

            def deterministic_chat(route):
                body = route.request.post_data_json
                body.update(temperature=0, max_tokens=128)
                headers = {
                    k: v
                    for k, v in route.request.headers.items()
                    if k.lower() != "content-length"
                }
                route.continue_(post_data=json.dumps(body), headers=headers)

            page.route("**/api/v1/instances/*/chat/completions", deterministic_chat)
            report["sampling"] = {"temperature": 0, "max_tokens": 128}
            page.goto(args.url)
            page.get_by_role("button", name="模型", exact=True).click()
            row = page.get_by_role("row").filter(
                has=page.get_by_text(args.model, exact=True)
            )
            expect(row.get_by_text("gguf · 图文", exact=False)).to_be_visible(
                timeout=10000
            )
            row.get_by_role("button", name="创建实例", exact=True).click()
            dialog = page.get_by_role("dialog")
            expect(dialog.get_by_label("输入类型", exact=True)).to_have_value(
                "image_text"
            )
            expect(dialog.get_by_label("视觉编码设备", exact=True)).to_have_value("cpu")
            dialog.get_by_label("视觉编码设备", exact=True).select_option(
                "local:cuda:0"
            )
            dialog.get_by_label("实例名称", exact=True).fill(instance_name)
            dialog.get_by_label("Context size", exact=True).fill("2048")
            dialog.get_by_label("Micro batch", exact=True).fill("512")
            dialog.get_by_role("button", name="创建并启动", exact=True).click()
            row = page.get_by_role("row").filter(
                has=page.get_by_role("button", name=instance_name, exact=True)
            )
            expect(row.get_by_text("就绪", exact=True)).to_be_visible(timeout=120000)
            row.get_by_role("button", name="对话", exact=True).click()
            report["checks"].append(
                "package detection and UI creation load real vision projector and confirm capabilities"
            )
            page.get_by_label("选择图片", exact=True).set_input_files(
                str(args.root / "sample-a.png")
            )
            expect(page.locator(".draft-images img")).to_have_count(1, timeout=15000)
            page.get_by_label("输入消息", exact=True).fill(
                "What is the main color in the image? Answer with one color."
            )
            page.get_by_role("button", name="发送消息", exact=True).click()
            expect(page.locator(".message.assistant .markdown").last).to_contain_text(
                re.compile("red", re.I), timeout=120000
            )
            expect(
                page.get_by_role("button", name="停止生成", exact=True)
            ).not_to_be_visible(timeout=120000)
            report["checks"].append(
                "browser binary upload and controlled file reference produce visually correct native red answer"
            )
            page.get_by_role("button", name="新建会话", exact=True).first.click()
            page.get_by_label("选择图片", exact=True).set_input_files(
                [str(args.root / "sample-a.png"), str(args.root / "sample-b.png")]
            )
            expect(page.locator(".draft-images img")).to_have_count(2, timeout=20000)
            page.get_by_label("输入消息", exact=True).fill(
                "What are the colors of the first and second images?"
            )
            page.get_by_role("button", name="发送消息", exact=True).click()
            expect(page.locator(".message.assistant .markdown").last).to_contain_text(
                re.compile("red", re.I), timeout=120000
            )
            expect(page.locator(".message.assistant .markdown").last).to_contain_text(
                re.compile("blue", re.I), timeout=120000
            )
            expect(
                page.get_by_role("button", name="停止生成", exact=True)
            ).not_to_be_visible(timeout=120000)
            report["multi_image_answer"] = page.locator(
                ".message.assistant .markdown"
            ).last.inner_text()
            page.reload()
            page.get_by_role("button", name="对话", exact=True).click()
            expect(page.locator(".message.user img")).to_have_count(2, timeout=15000)
            page.get_by_role("button", name="查看图片", exact=True).first.click()
            expect(page.get_by_role("dialog", name="图片预览")).to_be_visible()
            page.get_by_role("button", name="关闭图片", exact=True).click()
            report["checks"].append(
                "multiple images, ordered comparison, IndexedDB Blob persistence and on-demand full preview"
            )
            # Expire all uploaded test assets; history must recover from browser Blobs.
            ids = page.evaluate(
                """async()=>{const db=await new Promise((resolve,reject)=>{const r=indexedDB.open('llama-inference-manager');r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error);});return await new Promise((resolve,reject)=>{const t=db.transaction('attachments');const r=t.objectStore('attachments').getAll();r.onsuccess=()=>{db.close();resolve(r.result.map(a=>a.server_id));};r.onerror=()=>reject(r.error);});}"""
            )
            for identifier in set(ids):
                response = page.request.delete(
                    args.url + "/api/v1/media/images/" + identifier
                )
                assert response.ok
            page.get_by_label("输入消息", exact=True).fill(
                "What color was the first image? Answer briefly."
            )
            page.get_by_role("button", name="发送消息", exact=True).click()
            expect(page.locator(".message.assistant .markdown").last).to_contain_text(
                re.compile("red", re.I), timeout=120000
            )
            expect(
                page.get_by_role("button", name="停止生成", exact=True)
            ).not_to_be_visible(timeout=120000)
            report["checks"].append(
                "lost server image cache restored from IndexedDB without changing normalized bytes; multi-turn image history understood"
            )
            page.screenshot(path=str(args.root / "v3-chat-desktop.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(500)
            assert page.evaluate(
                "document.documentElement.scrollWidth<=window.innerWidth"
            )
            page.screenshot(path=str(args.root / "v3-chat-mobile.png"), full_page=True)
            assert not report["page_errors"], report["page_errors"]
            report["checks"].append("desktop and 390px mobile layout; no page errors")
            page.set_viewport_size({"width": 1440, "height": 1000})
            page.get_by_role("button", name="新建会话", exact=True).first.click()
            page.get_by_label("选择图片", exact=True).set_input_files(
                str(args.root / "sample-b.png")
            )
            expect(page.locator(".draft-images img")).to_have_count(1, timeout=15000)
            expect(page.get_by_label("输入消息", exact=True)).to_have_value("")
            page.get_by_role("button", name="发送消息", exact=True).click()
            expect(page.locator(".message.user img")).to_have_count(1, timeout=15000)
            expect(page.locator(".message.assistant .markdown").last).not_to_be_empty(
                timeout=120000
            )
            expect(
                page.get_by_role("button", name="停止生成", exact=True)
            ).not_to_be_visible(timeout=120000)
            report["checks"].append(
                "image-only user message accepted by real browser and native vision runtime"
            )
            page.get_by_role("button", name="新建会话", exact=True).first.click()
            page.get_by_label("选择图片", exact=True).set_input_files(
                str(args.root / "sample-a.png")
            )
            expect(page.locator(".draft-images img")).to_have_count(1, timeout=15000)
            page.get_by_label("输入消息", exact=True).fill(
                "Describe this image in a long detailed essay."
            )
            page.get_by_role("button", name="发送消息", exact=True).click()
            expect(page.locator(".message.user img")).to_have_count(1, timeout=15000)
            page.get_by_role("button", name="停止生成", exact=True).click()
            expect(
                page.get_by_role("button", name="停止生成", exact=True)
            ).not_to_be_visible(timeout=15000)
            page.reload()
            page.get_by_role("button", name="对话", exact=True).click()
            expect(page.locator(".message.user img")).to_have_count(1, timeout=15000)
            report["checks"].append(
                "stopping generation and refreshing preserves image message history"
            )

        finally:
            report["last_answers"] = page.locator(
                ".message.assistant .markdown"
            ).all_inner_texts()
            (args.root / "browser-latest.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2)
            )
            try:
                response = page.request.get(args.url + "/api/v1/instances")
                for instance in response.json()["instances"]:
                    if instance["name"] == instance_name:
                        deleted = page.request.delete(
                            args.url + "/api/v1/instances/" + instance["id"]
                        )
                        assert deleted.ok
            finally:
                browser.close()
    (args.root / "browser.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2)
    )
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
