"""Shared local/SSH telemetry formatting; no GPU runtime is imported."""

import hashlib
import copy
import time
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat()


def metric(value, unit, source, status=None, reason=None):
    return dict(
        value=value,
        unit=unit,
        status=status or ("ok" if value is not None else "unsupported"),
        source=source,
        sampled_at=now(),
        stale=False,
        reason=reason,
    )


def parse_inventory(text):
    fields, gpu_lines = {}, []
    for line in text.splitlines():
        if line.startswith("gpu\t"):
            gpu_lines.append(line[4:])
        elif "\t" in line:
            key, value = line.split("\t", 1)
            fields[key] = value.strip().replace("\x00", "")[:4096]
    fields["gpus"] = gpu_lines[:16]
    return fields


def number(value, scale=1):
    try:
        return float(value) * scale
    except (ValueError, TypeError):
        return None


def snapshot(raw, node_id, previous=None):
    stamp = now()
    if raw.get("inventory_mode") == "dynamic" and previous:
        cached = previous.get("inventory", {})
        raw = {
            **{
                k: previous.get(k)
                for k in ("hostname", "architecture", "os", "hardware_model")
            },
            **{
                k: cached.get(k)
                for k in ("kernel", "glibc", "cuda_version", "l4t", "linger")
            },
            "cpu_name": previous["cpu_info"]["name"],
            "cpu_count": previous["cpu_info"]["logical_count"],
            "machine_id_hash": cached.get("machine_id_hash"),
            **raw,
        }

    def integer(key):
        value = number(raw.get(key))
        return int(value) if value is not None else None

    total, available = integer("memory_total"), integer("memory_available")
    cpu_total, cpu_idle = integer("cpu_total"), integer("cpu_idle")
    cpu_usage = None
    if previous and cpu_total is not None and cpu_idle is not None:
        delta = cpu_total - previous.get("_cpu_total", cpu_total)
        if delta > 0:
            cpu_usage = round(
                100 * (1 - (cpu_idle - previous.get("_cpu_idle", cpu_idle)) / delta), 1
            )
    hardware = raw.get("hardware_model") or ""
    jetson = any(
        word in hardware.lower() for word in ("jetson", "orin", "tegra")
    ) or bool(raw.get("l4t"))
    devices = []
    for line in raw.get("gpus", []):
        parts = [v.strip() for v in line.split(",")]
        if len(parts) < 5:
            continue
        index, name, mt, mf, util = parts[:5]
        if not index.isdigit():
            continue
        shared = jetson and (
            "orin" in name.lower() or "nvgpu" in name.lower() or "tegra" in name.lower()
        )
        memtotal, memfree = number(mt, 1024**2), number(mf, 1024**2)
        utilization = number(util)
        if utilization is None:
            utilization = number(raw.get("gpu_usage"))
        driver = "nvgpu" if "nvgpu" in name.lower() else raw.get("gpu_driver") or None
        friendly = (
            "NVIDIA Orin · 集成 GPU"
            if shared and "orin" in name.lower()
            else name.replace(" (nvgpu)", "")
        )
        devices.append(
            dict(
                id=f"{node_id}:cuda:{index}",
                node_id=node_id,
                index=int(index),
                backend_index=int(index),
                name=friendly,
                type="gpu",
                backend="cuda",
                driver=driver,
                compute_state="unverified",
                memory_kind="shared"
                if shared
                else ("dedicated" if memtotal is not None else "unknown"),
                memory_total=int(memtotal)
                if memtotal is not None and not shared
                else None,
                memory_free=int(memfree)
                if memfree is not None and not shared
                else None,
                properties={"utilization_percent": utilization},
                metrics={
                    "memory_total": metric(
                        memtotal if not shared else None,
                        "bytes",
                        "nvidia-smi",
                        reason="共享系统内存" if shared else None,
                    ),
                    "utilization": metric(
                        utilization,
                        "percent",
                        "sysfs"
                        if number(raw.get("gpu_usage")) is not None
                        else "nvidia-smi",
                    ),
                },
            )
        )
    if jetson and not devices:
        devices.append(
            dict(
                id=f"{node_id}:cuda:0",
                node_id=node_id,
                index=0,
                backend_index=0,
                name="NVIDIA Jetson · 集成 GPU",
                type="gpu",
                backend="cuda",
                driver="nvgpu",
                compute_state="unverified",
                memory_kind="shared",
                memory_total=None,
                memory_free=None,
                properties={"utilization_percent": number(raw.get("gpu_usage"))},
                metrics={
                    "utilization": metric(
                        number(raw.get("gpu_usage")), "percent", "sysfs"
                    )
                },
            )
        )
    used = total - available if total is not None and available is not None else None
    usage = round(used / total * 100, 1) if used is not None and total else None
    cpu_name = raw.get("cpu_name") or "型号未提供"
    if cpu_name in {"aarch64", "arm64", "x86_64"}:
        cpu_name = "型号未提供"
    return dict(
        id=node_id,
        name="Local Node" if node_id == "local" else raw.get("hostname", "Remote Node"),
        node_type="local" if node_id == "local" else "ssh",
        role="controller" if node_id == "local" else "rpc_worker",
        hostname=raw.get("hostname", ""),
        status="online",
        connection_state="online",
        os=raw.get("os", "Linux"),
        architecture=raw.get("architecture", ""),
        hardware_model=hardware or None,
        cpu_info={
            "name": cpu_name,
            "logical_count": integer("cpu_count"),
            "usage_percent": cpu_usage,
        },
        memory_info={
            "total": total,
            "available": available,
            "used": used,
            "usage_percent": usage,
        },
        devices=devices,
        inventory={
            "hardware_model": hardware or None,
            "kernel": raw.get("kernel"),
            "glibc": raw.get("glibc"),
            "cuda_version": raw.get("cuda_version") or None,
            "l4t": raw.get("l4t") or None,
            "linger": raw.get("linger"),
            "machine_id_hash": hashlib.sha256(raw["machine_id"].encode()).hexdigest()
            if raw.get("machine_id")
            else raw.get("machine_id_hash"),
        },
        telemetry={
            "cpu_usage": metric(
                cpu_usage,
                "percent",
                "/proc/stat",
                status="unknown" if cpu_usage is None else "ok",
            ),
            "memory_total": metric(total, "bytes", "/proc/meminfo"),
            "memory_available": metric(available, "bytes", "/proc/meminfo"),
            "swap_used": metric(integer("swap_used"), "bytes", "/proc/meminfo"),
        },
        sampled_at=stamp,
        updated_at=stamp,
        _cpu_total=cpu_total or 0,
        _cpu_idle=cpu_idle or 0,
        _sample_monotonic=time.monotonic(),
    )


def public_snapshot(value, stale_after=90):
    result = copy.deepcopy({k: v for k, v in value.items() if not k.startswith("_")})
    stale = time.monotonic() - value.get("_sample_monotonic", 0) > stale_after
    result["telemetry_stale"] = stale
    result["telemetry"] = {
        k: {**v, "stale": stale} for k, v in value.get("telemetry", {}).items()
    }
    for device in result.get("devices", []):
        device["compute_state"] = "unverified"
        for item in device.get("metrics", {}).values():
            item["stale"] = stale
    return result
