"""Bounded directory detectors. They inspect headers, never load model weights."""

import hashlib
import json
import re
from pathlib import Path

TEXT_ARCHES = {
    "llama",
    "qwen2",
    "qwen3",
    "qwen3moe",
    "qwen35",
    "gemma",
    "gemma2",
    "gemma3",
    "gemma3n",
    "phi3",
    "phi2",
    "mistral",
    "deepseek2",
    "deepseek",
    "smollm",
    "smollm3",
    "gpt2",
    "gptneox",
    "bert",
    "t5",
    "internlm2",
    "minicpm",
    "minicpm3",
    "qwen2vl",
    "qwen3vl",
    "qwen3vlmoe",
}
VISION_ARCHES = {"qwen2vl", "qwen3vl", "qwen3vlmoe", "qwen35"}
EMBEDDING_ARCHES = {"bert", "nomic-bert", "nomic-bert-moe", "jina-bert-v2", "t5encoder"}


def identifier(value):
    return hashlib.sha256(value.encode()).hexdigest()[:24]


def fingerprint(path):
    s = path.stat()
    return f"{s.st_dev}:{s.st_ino}:{s.st_size}:{s.st_mtime_ns}:{s.st_ctime_ns}"


def files_in(directory):
    result = []

    def visit(parent, depth):
        for p in sorted(parent.iterdir()):
            if p.name.startswith(".") or p.name.endswith(
                (".part", ".tmp", ".download")
            ):
                continue
            if p.is_dir() and not p.is_symlink():
                if depth < 3:
                    visit(p, depth + 1)
                else:
                    raise ValueError("Model assets exceed the maximum directory depth")
                continue
            if p.suffix.lower() not in {".gguf", ".json", ".safetensors"}:
                continue
            resolved = p.resolve(strict=True)
            if not resolved.is_relative_to(directory) or not resolved.is_file():
                raise ValueError("Asset symlink escapes its model package")
            result.append(p)
            if len(result) > 1024:
                raise ValueError("Model package exceeds 1024 asset files")

    visit(directory, 0)
    return result


def read_json(path):
    if path.stat().st_size > 1024**2:
        raise ValueError("Model JSON exceeds 1 MiB")
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError("Model JSON must be an object")
    return data


def detect(root, directory, paths, revision, headers):
    pid = identifier(f"package:{root}:{directory.relative_to(root)}")
    model = dict(
        id=pid,
        name=directory.name,
        relative_directory=str(directory.relative_to(root)),
        relative_path=str(directory.relative_to(root)),
        filename=directory.name,
        format="unknown",
        architecture=None,
        node_id="local",
        metadata={},
        kind="unknown",
        input_modalities=[],
        output_modalities=["text"],
        runtime_support="unknown",
        availability="invalid",
        revision=revision,
        variants=[],
        projectors=[],
        size_bytes=sum(p.stat().st_size for p in paths),
        detection=dict(source="metadata", reasons=[], verified=False),
    )
    assets = {str(p.relative_to(directory)): p for p in paths}
    manifest = (
        read_json(directory / "model.json")
        if directory / "model.json" in paths
        else None
    )
    if manifest:
        if (
            set(manifest)
            - {"schema_version", "name", "format", "variants", "default_variant"}
            or manifest.get("schema_version") != 1
        ):
            raise ValueError("Invalid model.json schema")
        if not isinstance(manifest.get("name", directory.name), str):
            raise ValueError("Invalid model name")
        model["name"] = manifest.get("name", directory.name)[:200]
    mains = []
    for relative, p in assets.items():
        if p.suffix.lower() != ".gguf":
            continue
        md = headers[p]
        role = (
            "projector"
            if md.get("general.architecture") == "clip" or "clip.projector_type" in md
            else "weights"
        )
        entry = dict(
            id=identifier(f"{pid}:{relative}"),
            name=md.get("general.name") or p.stem,
            relative_path=relative,
            size_bytes=p.stat().st_size,
            metadata=md,
            path=str(p.resolve()),
            fingerprint=fingerprint(p),
        )
        if role == "projector":
            entry["vision"] = md.get("clip.has_vision_encoder") is True or bool(
                md.get("clip.vision.projector_type")
            )
            entry["audio"] = md.get("clip.has_audio_encoder") is True
            model["projectors"].append(entry)
        else:
            mains.append(entry)
    if mains:
        model["format"] = "gguf"
        architectures = {m["metadata"].get("general.architecture") for m in mains}
        if len(architectures) != 1:
            raise ValueError("Different model architectures share one package")
        names = {
            re.sub(
                r"[-_. ](?:I?Q\d\w*|F16|BF16|F32)$",
                "",
                str(m["metadata"].get("general.name", "")),
                flags=re.I,
            )
            for m in mains
        }
        if len(names) > 1:
            raise ValueError(
                "Different model families share one package; use separate directories"
            )
        model["architecture"] = next(iter(architectures))
        model["metadata"] = mains[0]["metadata"]
        grouped = {}
        for m in mains:
            count = m["metadata"].get("split.count", 1)
            if count > 1:
                match = re.fullmatch(
                    r"(.*)-\d{5}-of-\d{5}\.gguf", m["relative_path"], re.I
                )
                if not match:
                    raise ValueError("Unrecognized GGUF shard filename")
                key = match[1]
            else:
                key = m["relative_path"]
            grouped.setdefault(key, []).append(m)
        for key, group in grouped.items():
            count = group[0]["metadata"].get("split.count", 1)
            group.sort(key=lambda m: m["metadata"].get("split.no", 0))
            total = group[0]["metadata"].get("split.tensors.count")
            if total is not None and (
                any(m["metadata"].get("split.tensors.count") != total for m in group)
                or sum(m["metadata"]["gguf.tensor_count"] for m in group) != total
            ):
                model["availability"] = "incomplete"
                model["detection"]["reasons"].append("GGUF 分片张量总数不一致")
                continue
            if (
                count != len(group)
                or any(m["metadata"].get("split.count", 1) != count for m in group)
                or (
                    count > 1
                    and [m["metadata"].get("split.no") for m in group]
                    != list(range(count))
                )
            ):
                model["availability"] = "incomplete"
                model["detection"]["reasons"].append("GGUF 分片缺失、重复或数量不一致")
                continue
            entry = group[0].copy()
            entry["id"] = identifier(f"{pid}:variant:{key}")
            entry["name"] = Path(key).stem
            entry["size_bytes"] = sum(m["size_bytes"] for m in group)
            entry["shards"] = group
            entry["projector_id"] = None
            model["variants"].append(entry)
        if model["availability"] == "incomplete":
            return model
        projectors = [p for p in model["projectors"] if p["vision"]]
        for variant in model["variants"]:
            if len(projectors) == 1:
                variant["projector_id"] = projectors[0]["id"]
        if manifest:
            if manifest.get("format", "gguf") != "gguf":
                raise ValueError("Manifest format conflicts with file signatures")
            choices = manifest.get("variants")
            if choices is not None:
                if not isinstance(choices, list) or not 1 <= len(choices) <= 64:
                    raise ValueError("Invalid manifest variants")
                selected = []
                for choice in choices:
                    if not isinstance(choice, dict) or set(choice) - {
                        "name",
                        "entry",
                        "projector",
                    }:
                        raise ValueError("Invalid manifest variant")
                    v = next(
                        (
                            v.copy()
                            for v in model["variants"]
                            if v["relative_path"] == choice.get("entry")
                        ),
                        None,
                    )
                    if v is None:
                        raise ValueError("Manifest entry is not a model variant")
                    if "name" in choice:
                        if not isinstance(choice["name"], str):
                            raise ValueError("Invalid variant name")
                        v["name"] = choice["name"][:100]
                    if "projector" in choice:
                        projector = next(
                            (
                                p
                                for p in projectors
                                if p["relative_path"] == choice["projector"]
                            ),
                            None,
                        )
                        if projector is None:
                            raise ValueError(
                                "Manifest projector is not a verified vision asset"
                            )
                        v["projector_id"] = projector["id"]
                    selected.append(v)
                if len({v["id"] for v in selected}) != len(selected):
                    raise ValueError("Duplicate manifest entry")
                model["variants"] = selected
        paired = any(v["projector_id"] for v in model["variants"])
        model["kind"] = (
            "image_text"
            if paired
            else ("text" if model["architecture"] in TEXT_ARCHES else "unknown")
        )
        model["input_modalities"] = ["text", "image"] if paired else ["text"]
        model["runtime_support"] = (
            "candidate" if model["architecture"] in TEXT_ARCHES else "unknown"
        )
        model["availability"] = "available"
        if model["projectors"] and not paired:
            model["availability"] = "needs_selection"
            model["detection"]["reasons"].append(
                "需要选择唯一兼容的视觉投影，音频投影不能用于图片"
            )
        if model["architecture"] in VISION_ARCHES and not paired:
            model["kind"] = "image_text"
            model["availability"] = "incomplete"
            model["detection"]["reasons"].append("视觉模型缺少兼容投影文件")
        if model["runtime_support"] == "unknown":
            model["availability"] = "unsupported"
            model["detection"]["reasons"].append("当前运行时尚未验证此模型架构")
        if any(p.get("audio") for p in model["projectors"]):
            model["input_modalities"].append("audio")
            if not paired:
                model.update(
                    kind="other",
                    runtime_support="unsupported",
                    availability="unsupported",
                )
                model["detection"]["reasons"].append(
                    "已识别音频投影；V3 仅支持图片和文本对话"
                )
        if model["architecture"] in EMBEDDING_ARCHES:
            model.update(
                kind="other",
                runtime_support="unsupported",
                availability="unsupported",
                output_modalities=["embedding"],
            )
            model["detection"]["reasons"].append(
                "已识别嵌入模型；当前没有嵌入服务适配器"
            )
        default = manifest.get("default_variant") if manifest else None
        if default:
            selected = next(
                (v for v in model["variants"] if v["name"] == default), None
            )
            if selected is None:
                raise ValueError("Unknown default_variant")
            model["default_variant_id"] = selected["id"]
        elif len(model["variants"]) == 1:
            model["default_variant_id"] = model["variants"][0]["id"]
    elif any(p.suffix == ".safetensors" for p in paths) and "config.json" in assets:
        config = read_json(assets["config.json"])
        model.update(
            format="hf_safetensors",
            architecture=config.get("model_type"),
            runtime_support="unsupported",
            availability="unsupported",
        )
        visual = "vision_config" in config
        model["kind"] = "image_text" if visual else "text"
        model["input_modalities"] = ["text", "image"] if visual else ["text"]
        model["detection"]["reasons"].append("已识别 safetensors；尚无对应运行时适配器")
        if model["architecture"] in EMBEDDING_ARCHES:
            model.update(kind="other", output_modalities=["embedding"])
    else:
        model["detection"]["reasons"].append("未找到可创建实例的主权重")
    return model


def public_asset(asset):
    return {
        k: v
        for k, v in asset.items()
        if k not in {"path", "fingerprint", "shards", "metadata"}
    }


def public_package(model):
    data = {
        k: v
        for k, v in model.items()
        if not k.startswith("_")
        and k not in {"path", "fingerprint", "shards", "projector_path"}
    }
    data["variants"] = [public_asset(v) for v in model["variants"]]
    data["projectors"] = [public_asset(p) for p in model["projectors"]]
    return data
