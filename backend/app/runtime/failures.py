"""Bounded, public diagnostics from native stderr; never return a whole log."""

import re

ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
ERROR_LINE = re.compile(
    r"(?:\bE\s|\berror\b|\bfailed\b|\bexception\b|\bassertion\b|out of memory)",
    re.I,
)


def public_text(text):
    text = ANSI.sub("", text)
    text = "".join(c for c in text if c in "\n\t" or c.isprintable())
    text = re.sub(r"data:image/[^;\s]+;base64,[A-Za-z0-9+/=]+", "[image omitted]", text)
    text = re.sub(
        r"(?i)((?:authorization\s*:\s*bearer|--api-key|--password|api_key\s*[=:]|password\s*[=:])\s*)[^\s,;]+",
        r"\1[redacted]",
        text,
    )
    # Keep useful filenames without exposing local absolute directories.
    text = re.sub(r"(?<![\w:])/(?:[^/\s'\"<>]+/)+([^/\s'\"<>;,:)\]]+)", r"\1", text)
    return text


def describe_failure(text, fallback, code="runtime_exited", exit_code=None):
    text = public_text(text[-32768:])
    lines = [line[:800] for line in text.splitlines() if ERROR_LINE.search(line)]
    message = public_text(fallback)[:1000]
    lower = text.lower()
    allocation = re.search(
        r"endpoint:\s*([^,\s]+),\s*device:\s*\d+,\s*size:\s*([\d.]+\s*\w+)\s*->\s*failed",
        text,
        re.I,
    )
    memory_failure = allocation or re.search(
        r"out of memory|cudaMalloc[^\n]*failed|failed to allocate|unable to allocate|cannot allocate memory|std::bad_alloc",
        text,
        re.I,
    )
    if memory_failure:
        code = "runtime_memory_allocation_failed"
        target = f"远端 {allocation[1]}" if allocation else "运行时"
        size = f"，申请 {allocation[2]}" if allocation else ""
        if not size and (size_match := re.search(r"buffer of size\s+(\d+)", text)):
            size = f"，申请 {int(size_match[1]) / 1024**2:.1f} MiB"
        phase = "（KV 缓存）" if "kv cache" in lower else ""
        message = f"{target}内存分配失败{size}{phase}。"
    elif "address already in use" in lower:
        code, message = "runtime_port_in_use", "推理服务启动失败：监听端口已被占用。"
    elif "cuda error" in lower or "ggml_assert" in lower:
        code = "runtime_compute_failed"
        message = "推理运行时计算失败：" + (lines[0] if lines else message)
    elif lines:
        # The earliest error is usually the cause; the final lines are cleanup wrappers.
        message = lines[0]
    excerpt = "\n".join(lines[:10])[:4000]
    result = {"code": code, "message": message[:1000], "log_excerpt": excerpt}
    if exit_code is not None:
        result["exit_code"] = exit_code
        if not lines:
            result["message"] = f"{message}（退出码 {exit_code}）"[:1000]
    return result
