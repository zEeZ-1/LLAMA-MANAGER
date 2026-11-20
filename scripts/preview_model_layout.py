#!/usr/bin/env python3
"""Preview loose GGUF organization or register an explicitly completed move.

Never moves model files. --register-plan requires the manager to be stopped.
"""

from contextlib import closing
import argparse
import asyncio
import json
from pathlib import Path
import sys
import re

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.core.config import load_settings
from backend.app.core.lock import ManagerLock
from backend.app.repositories.manager import ManagerStore
from backend.app.services.gguf import read_metadata
from backend.app.services.model_catalog import ModelCatalog
from backend.app.services.model_packages import fingerprint, identifier


def preview(cfg):
    records = []
    for root in cfg.models.directories:
        if not root.exists():
            continue
        mains = []
        projectors = []
        for p in sorted(
            p for p in root.iterdir() if p.is_file() and p.suffix.lower() == ".gguf"
        ):
            if p.is_symlink():
                continue
            try:
                md = read_metadata(p, allow_split=True)
            except ValueError:
                continue
            (projectors if md.get("general.architecture") == "clip" else mains).append(
                p
            )
        for p in mains:
            folder = re.sub(r"-\d{5}-of-\d{5}$", "", p.stem)
            records.append(
                dict(
                    root=str(root),
                    source=p.name,
                    target=f"{folder}/{p.name}",
                    fingerprint=fingerprint(p),
                    old_id=identifier(f"local:{root}:{p.name}"),
                )
            )
        if len(mains) == len(projectors) == 1:
            p = projectors[0]
            folder = Path(records[-1]["target"]).parent
            records.append(
                dict(
                    root=str(root),
                    source=p.name,
                    target=str(folder / p.name),
                    fingerprint=fingerprint(p),
                    old_id=None,
                )
            )
    return {
        "version": 1,
        "note": "Preview only. Review targets, move files explicitly, then register this plan while the manager is stopped.",
        "moves": records,
    }


async def register(cfg, plan):
    if plan.get("version") != 1 or not isinstance(plan.get("moves"), list):
        raise ValueError("Invalid layout plan")
    allowed = {str(p) for p in cfg.models.directories}
    for m in plan["moves"]:
        if m["root"] not in allowed:
            raise ValueError("Plan root is not allowed")
        root = Path(m["root"])
        relative = Path(m["target"])
        source = Path(m["source"])
        if (
            relative.is_absolute()
            or len(relative.parts) != 2
            or ".." in relative.parts
            or len(source.parts) != 1
        ):
            raise ValueError("Invalid package target")
        target = root / relative
        if target.is_symlink() or not target.resolve(strict=True).is_relative_to(
            root / relative.parts[0]
        ):
            raise ValueError("Invalid asset location")
        if fingerprint(target).split(":")[:4] != m["fingerprint"].split(":")[:4]:
            raise ValueError("Moved asset identity differs; aliases not registered")
        expected = identifier(f"local:{root}:{source}")
        if m.get("old_id") not in {None, expected}:
            raise ValueError("Invalid old model ID")
    lock = ManagerLock(cfg.storage.database.with_suffix(".manager.lock"))
    catalog = ModelCatalog(cfg.models)
    try:
        store = ManagerStore(cfg.storage.database)
        await store.initialize()
        await catalog.scan_all()
        await asyncio.sleep(cfg.models.stability_seconds + 0.01)
        await catalog.scan_all()
        pending = []
        for m in plan["moves"]:
            if not m.get("old_id"):
                continue
            target = str((Path(m["root"]) / m["target"]).resolve())
            matches = [
                (model, v)
                for model in catalog.models.values()
                for v in model["variants"]
                if any(s["path"] == target for s in v["shards"])
            ]
            if len(matches) != 1:
                raise ValueError("Moved file has no unique package variant")
            model, variant = matches[0]
            pending.append(
                dict(id=m["old_id"], package_id=model["id"], variant_id=variant["id"])
            )
        # One transaction: a partial alias plan is never published.
        async with store.write_lock:
            with closing(store.connect()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                for item in pending:
                    connection.execute(
                        "INSERT OR REPLACE INTO model_aliases VALUES (?,?)",
                        (item["id"], json.dumps(item)),
                    )
        return {"registered": len(pending), "model_files_modified": False}
    finally:
        lock.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--register-plan", type=Path)
    args = parser.parse_args()
    cfg = load_settings()
    result = (
        asyncio.run(register(cfg, json.loads(args.register_plan.read_text())))
        if args.register_plan
        else preview(cfg)
    )
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
