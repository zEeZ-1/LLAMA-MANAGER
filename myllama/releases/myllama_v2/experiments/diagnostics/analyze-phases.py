#!/usr/bin/env python3
"""Join application timing spans and request records using CLOCK_MONOTONIC."""

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
import json
from pathlib import Path


@dataclass(frozen=True)
class Span:
    tid: int
    id: int
    parent: int
    stage: str
    role: str
    spec: str
    context: str
    tokens: int
    position: int
    source: str
    destination: str
    size: int
    detail: str
    start: int
    end: int

    @classmethod
    def read(cls, row):
        tid, id_, parent, stage, role, spec, context, tokens, position, src, dst, size, detail, start, end = row.split("\t")
        return cls(int(tid), int(id_), int(parent), stage, role, spec, context, int(tokens),
                   int(position), src, dst, int(size), detail, int(start), int(end))

    @property
    def duration(self):
        return self.end - self.start


def ms(nanoseconds):
    return round(nanoseconds / 1_000_000, 6)


def union(intervals):
    occupied = 0
    right = 0
    for left, end in sorted(intervals):
        if end > right:
            occupied += end - max(left, right)
            right = end
    return occupied


def trace_inputs(directory):
    requests = []
    errors = Counter()
    request_path = directory / "request-spans.jsonl"
    if request_path.is_file():
        for row in request_path.read_text().splitlines():
            try:
                request = json.loads(row)
                if 0 < request["start_ns"] <= request["end_ns"]:
                    requests.append(request)
            except (json.JSONDecodeError, KeyError, TypeError):
                errors[request_path.name] += 1
    origins = defaultdict(set)
    labels = {request.get("phase_label") for request in requests} - {None}
    paths = set(directory.glob("phase-*.tsv"))
    for label in labels:
        paths.update(directory.glob(f"{label}-*.tsv"))
    for path in sorted(paths):
        if "-worker-" in path.name:
            continue
        for row in path.read_text().splitlines():
            if not row:
                continue
            try:
                span = Span.read(row)
                if span.start < span.end:
                    origins[span].add(path.name)
            except ValueError:
                errors[path.name] += 1
    return sorted(requests, key=lambda request: request["start_ns"]), origins, errors


def pair_worker_gets(node, worker_spans, controller_spans, first_token):
    worker_gets = [(idx, span) for idx, span in enumerate(worker_spans) if span.stage == "tensor_get"]
    controller_gets = sorted(
        (span for span in controller_spans if span.stage == "tensor_get" and node in span.source),
        key=lambda span: span.start,
    )
    result = {"worker_get_count": len(worker_gets), "controller_get_count": len(controller_gets)}
    remote_keys = [(span.detail, span.size) for _, span in worker_gets]
    local_keys = [(span.detail, span.size) for span in controller_gets]
    if not local_keys:
        return {**result, "status": "no controller GET spans"}
    if remote_keys != local_keys:
        return {**result, "status": "GET tensor order/name/size mismatch"}

    phases = defaultdict(list)
    for (idx, worker), controller in zip(worker_gets, controller_gets):
        previous = worker_spans[idx - 1] if idx else None
        if not previous or previous.tid != worker.tid or previous.stage != "backend_sync":
            return {**result, "status": "missing worker sync before GET"}
        graph = worker_spans[idx - 2] if idx > 1 else None
        submit = graph.duration if graph and graph.tid == worker.tid and graph.stage == "graph_submit" else 0
        phase = "prefill" if controller.end <= first_token else "decode"
        phases[phase].append((controller, previous.duration, worker.duration, submit))
    return {
        **result,
        "status": "paired",
        "by_phase": {
            key: {
                "count": len(rows),
                "bytes": sum(controller.size for controller, _, _, _ in rows),
                "controller_get_wait_ms": ms(sum(controller.duration for controller, _, _, _ in rows)),
                "worker_backend_sync_ms": ms(sum(sync for _, sync, _, _ in rows)),
                "worker_tensor_get_ms": ms(sum(get for _, _, get, _ in rows)),
                "residual_host_transport_ms": ms(sum(controller.duration - sync - get for controller, sync, get, _ in rows)),
                "worker_preceding_graph_submit_ms": ms(sum(submit for _, _, _, submit in rows)),
            }
            for key, rows in sorted(phases.items())
        },
    }


def worker_stats(directory, request, files, controller_spans):
    results = {}
    for node, (start, end) in request.get("remote_probe_ranges", {}).items():
        path = directory / f"{request['phase_label']}-worker-{node}-phase.tsv"
        if not path.is_file():
            results[node] = {"status": "missing file"}
            continue
        if path not in files:
            files[path] = path.read_bytes()
        if end > len(files[path]):
            results[node] = {"status": "incomplete file"}
            continue
        spans = []
        invalid_rows = 0
        for row in files[path][start:end].decode(errors="replace").splitlines():
            try:
                span = Span.read(row)
                if span.end > span.start:
                    spans.append(span)
            except ValueError:
                invalid_rows += 1
        roots = [span for span in spans if not span.parent]
        results[node] = {
            "status": "ok",
            "span_count": len(spans),
            "invalid_rows": invalid_rows,
            "root_union_ms": ms(union((span.start, span.end) for span in roots)),
            "root_calls": group_stats(roots, stage_key),
            "by_stage": group_stats(spans, stage_key),
            "matched_gets": pair_worker_gets(node, spans, controller_spans, request["first_ns"]),
        }
    return results


def is_rpc(backend):
    return backend.upper().startswith("RPC")


def stage_key(span):
    parts = [span.stage]
    if span.role != "-":
        parts.append(span.role)
    if span.spec != "-":
        parts.append(span.spec)
    if span.source != "-" or span.destination != "-":
        parts.append(f"{span.source} -> {span.destination}")
    return " / ".join(parts)


def phase_key(span, request):
    if span.stage in ("llama_process", "spec_process") and span.position >= 0:
        return "prefill" if span.position < request["input"] else "decode"
    if span.stage in ("spec_draft", "spec_accept"):
        return "decode"
    return "before_client_first" if span.start < request["first_ns"] else "after_client_first"


def group_stats(items, group, exclusives=None):
    count = Counter()
    inclusive = Counter()
    exclusive = Counter()
    intervals = defaultdict(list)
    for span in items:
        key = group(span)
        count[key] += 1
        inclusive[key] += span.duration
        if exclusives is not None:
            exclusive[key] += exclusives[span]
        intervals[key].append((span.start, span.end))
    stats = {}
    for key in sorted(inclusive, key=lambda label: (-inclusive[label], label)):
        metrics = {"count": count[key], "inclusive_sum_ms": ms(inclusive[key]),
                   "inclusive_union_ms": ms(union(intervals[key]))}
        if exclusives is not None:
            metrics["exclusive_thread_sum_ms"] = ms(exclusive[key])
        stats[key] = metrics
    return stats


def transfer_kind(span):
    if span.stage.startswith("tensor_get") and is_rpc(span.source):
        return "get"
    if span.stage.startswith("tensor_set") and is_rpc(span.destination):
        return "set"
    if span.stage.startswith("tensor_copy") and (is_rpc(span.source) or is_rpc(span.destination)):
        return "copy"
    return None


def transfer_stats(items):
    groups = defaultdict(list)
    for span in items:
        groups[f"{transfer_kind(span)} / {span.source} -> {span.destination}"].append(span)
    return {
        "count": len(items),
        "bytes": sum(span.size for span in items),
        "api_union_ms": ms(union((span.start, span.end) for span in items)),
        "by_direction": {
            key: {
                "count": len(spans),
                "bytes": sum(span.size for span in spans),
                "api_union_ms": ms(union((span.start, span.end) for span in spans)),
            }
            for key, spans in sorted(groups.items())
        },
    }


def rpc_stats(spans, parents, exclusive):
    transfers = {span: transfer_kind(span) for span in spans if transfer_kind(span)}
    # Dedup API nesting. COPY is the requested payload; GET plus SET can each
    # move that payload when a copy between endpoints stages through the host.
    nested_same_kind = set()
    nested_transfers = set()
    for span, kind in transfers.items():
        parent = parents.get(span)
        while parent is not None:
            if parent in transfers:
                nested_transfers.add(span)
                if transfers[parent] == kind:
                    nested_same_kind.add(parent)
            parent = parents.get(parent)
    logical = [span for span in transfers if span not in nested_transfers]
    get_set = [span for span, kind in transfers.items()
               if kind in ("get", "set") and span not in nested_same_kind]
    relay = [span for span in get_set if span in nested_transfers]
    graph = [span for span in spans if span.stage == "graph_submit" and is_rpc(span.destination)]
    waits = [span for span in spans
             if span.stage in ("backend_sync", "event_sync") and is_rpc(span.source)]
    return {
        "logical_outermost_payload": transfer_stats(logical),
        "observed_get_set_payload": transfer_stats(get_set),
        "nested_get_set_payload": transfer_stats(relay),
        "graph_submission_calls": group_stats(graph, stage_key, exclusive),
        "backend_wait_calls": group_stats(waits, stage_key, exclusive),
    }


def summarize_request(request, spans, origins):
    spans = sorted(spans, key=lambda span: (span.start, -span.end))
    index = {(span.tid, span.id): span for span in spans}
    parents = {}
    children = defaultdict(list)
    roots = []
    missing_parents = 0
    for span in spans:
        candidate = index.get((span.tid, span.parent))
        if candidate and candidate.id < span.id and candidate.start <= span.start <= span.end <= candidate.end:
            parents[span] = candidate
            children[candidate].append(span)
        else:
            roots.append(span)
            missing_parents += bool(span.parent)

    exclusive = {
        span: span.duration - union((child.start, child.end) for child in children[span])
        for span in spans
    }
    phases = {}
    by_phase = defaultdict(list)
    for span in spans:
        parent = parents.get(span)
        phases[span] = phases[parent] if parent in phases else phase_key(span, request)
        by_phase[phases[span]].append(span)

    stats = {
        key: request[key]
        for key in ("label", "trial", "phase_label", "input", "output", "traced", "ttft_s", "wall_s",
                    "prefill_tps", "decode_tps", "stream_decode_tps", "timings", "token_hash")
        if key in request
    }
    stats["request_window_ms"] = ms(request["end_ns"] - request["start_ns"])
    stats["observed_root_union_ms"] = ms(union((span.start, span.end) for span in roots))
    stats["root_calls"] = group_stats(roots, stage_key, exclusive)
    stats["phase_breakdown"] = {
        key: {
            "all_span_count": len(selected),
            "root_span_union_ms": ms(union((span.start, span.end) for span in selected if span not in parents)),
            "by_stage": group_stats(selected, stage_key, exclusive),
            "rpc": rpc_stats(selected, parents, exclusive),
        }
        for key, selected in sorted(by_phase.items())
    }
    stats["rpc"] = rpc_stats(spans, parents, exclusive)
    stats["span_count"] = len(spans)
    stats["missing_parents"] = missing_parents
    stats["trace_files"] = sorted({path for span in spans for path in origins[span]})
    return stats


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, nargs="?", default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    requests, origins, errors = trace_inputs(args.directory)
    by_request = defaultdict(list)
    pending = iter(sorted(origins, key=lambda span: span.start))
    span = next(pending, None)
    for idx, request in enumerate(requests):
        while span and span.start < request["end_ns"]:
            if request["start_ns"] <= span.start and span.end <= request["end_ns"]:
                by_request[idx].append(span)
            span = next(pending, None)
    summaries = []
    worker_files = {}
    for idx, request in enumerate(requests):
        summary = summarize_request(request, by_request[idx], origins)
        if "remote_probe_ranges" in request:
            summary["worker_devices"] = worker_stats(args.directory, request, worker_files, by_request[idx])
        summaries.append(summary)
    report = {
        "measurement": {
            "clock": "Local requests and spans use CLOCK_MONOTONIC. Worker ranges match requests by file byte offsets. A request window can include client tokenization.",
            "exclusive": "Parent duration minus the union of direct child intervals, summed per traced thread. Graph/copy APIs can enqueue asynchronous work.",
            "phase": "Outermost target/model process is classified by position relative to prompt length, with explicit draft/accept decode phases. Other roots use client first-token time.",
            "rpc_bytes": "Outermost transfer bytes are logical. Leaf RPC GET/SET are observed payload sizes and can include both legs of a relayed tensor. Packet/framing bytes and unobserved calls are excluded.",
            "rpc_time": "Transfer API durations include queueing or GPU synchronization and can overlap other groups. RPC wait and graph submission are reported separately.",
            "worker_gets": "Per-node GET calls match by request byte range, order, tensor name and size. Worker sync and D2H are subtracted from the controller API duration. Residual includes transport, scheduling, and host work, and preceding worker graph submit can overlap it.",
        },
        "request_count": len(requests),
        "matched_span_count": sum(map(len, by_request.values())),
        "unmatched_span_count": len(origins) - sum(map(len, by_request.values())),
        "invalid_rows": errors,
        "requests": summaries,
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
