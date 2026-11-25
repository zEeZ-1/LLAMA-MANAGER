#!/usr/bin/env python3
"""Compare greedy target and native MTP outputs across matched benchmark runs."""

import argparse
from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path
import re


PROMPT_START = re.compile(r"new prompt, .*task\.n_tokens = (\d+)")
DRAFT_PICK = re.compile(r"draft candidate\s+0, pos\s+(\d+):\s+(-?\d+) ")
GENERATION_END = re.compile(r"\|\s*eval time\s*=.* /\s+(\d+) tokens")
CACHE_ONLY = "qwen35moe MTP cache-only graph active"


def run_records(path, prefix):
    groups = defaultdict(list)
    for line in path.read_text().splitlines():
        request = json.loads(line)
        label, _, workload = request["label"].partition("/")
        if label == prefix:
            groups[(workload, request["trial"], request["input"], request["output"])].append(request)
    return groups


def draft_runs(file):
    runs = []
    current = None
    marker = False
    if not file.is_file():
        return runs, marker
    with file.open() as log:
        for line in log:
            marker |= CACHE_ONLY in line
            prompt = PROMPT_START.search(line)
            if prompt:
                current = int(prompt[1]), []
            pick = DRAFT_PICK.search(line)
            if pick and current:
                current[1].append((int(pick[1]), int(pick[2])))
            completed = GENERATION_END.search(line)
            if completed and current:
                length, picks = current
                if picks:
                    runs.append((length, int(completed[1]), picks))
                current = None
    return runs, marker


def fingerprint(value):
    return sha256(json.dumps(value).encode()).hexdigest()


def compare_draft(baseline, candidate):
    old = defaultdict(list)
    new = defaultdict(list)
    for length, output, picks in baseline:
        old[length, output].append(picks)
    for length, output, picks in candidate:
        new[length, output].append(picks)
    matches = []
    for length, output in sorted(old.keys() & new.keys()):
        left = old[length, output]
        right = new[length, output]
        comparable = min(len(left), len(right))
        for old_picks, new_picks in zip(left[-comparable:], right[-comparable:]):
            matches.append({
                "input": length,
                "output": output,
                "match": old_picks == new_picks,
                "baseline_fingerprint": fingerprint(old_picks),
                "candidate_fingerprint": fingerprint(new_picks),
                "baseline_count": len(old_picks),
                "candidate_count": len(new_picks),
            })
    return {
        "baseline_requests": len(baseline),
        "candidate_requests": len(candidate),
        "match": all(row["match"] for row in matches) if matches else None,
        "matched_requests": matches,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline")
    parser.add_argument("candidate")
    parser.add_argument("--directory", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--baseline-log", type=Path)
    parser.add_argument("--candidate-log", type=Path)
    parser.add_argument("--require-draft-picks", action="store_true")
    args = parser.parse_args()
    request_file = args.directory / "requests.jsonl"
    baseline = run_records(request_file, args.baseline)
    candidate = run_records(request_file, args.candidate)
    records = []
    for key in sorted(baseline.keys() & candidate.keys()):
        workload, trial, length, output = key
        # Use the latest completed repetition for a given label and trial.
        old, new = baseline[key][-1], candidate[key][-1]
        old_stats, new_stats = old["timings"], new["timings"]
        metrics = {
            name: [old_stats.get(name), new_stats.get(name)]
            for name in ("draft_n", "draft_n_accepted")
        }
        records.append({
            "workload": workload,
            "trial": trial,
            "input": length,
            "output": output,
            "token_hash_match": old["token_hash"] == new["token_hash"],
            "draft_counts_match": all(a == b for a, b in metrics.values()),
            "draft_counts_baseline_candidate": metrics,
            "ttft_s_baseline_candidate": [old["ttft_s"], new["ttft_s"]],
            "prefill_tps_baseline_candidate": [old["prefill_tps"], new["prefill_tps"]],
        })
    old_picks, old_marker = draft_runs(args.baseline_log or args.directory / f"{args.baseline}.log")
    new_picks, new_marker = draft_runs(args.candidate_log or args.directory / f"{args.candidate}.log")
    old_unmatched = sorted(baseline.keys() - candidate.keys())
    new_unmatched = sorted(candidate.keys() - baseline.keys())
    report = {
        "baseline": args.baseline,
        "candidate": args.candidate,
        "baseline_cache_only_marker": old_marker,
        "candidate_cache_only_marker": new_marker,
        "greedy_native_draft_candidate_zero": compare_draft(old_picks, new_picks),
        "matched_requests": records,
        "unmatched_baseline": old_unmatched,
        "unmatched_candidate": new_unmatched,
    }
    print(json.dumps(report, indent=2))
    draft_match = report["greedy_native_draft_candidate_zero"]["match"]
    return int(not records or any(
        not row["token_hash_match"] or not row["draft_counts_match"] for row in records
    ) or draft_match is False or (args.require_draft_picks and draft_match is None))


if __name__ == "__main__":
    raise SystemExit(main())
