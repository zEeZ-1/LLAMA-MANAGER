from collections import defaultdict
import json
from pathlib import Path
import re
from statistics import median
import struct

import numpy as np


LAYERS = 40
EXPERTS = 256
WIDTH = 2048
RATE_BYTES_S = 1_000_000_000 / 8
HERE = Path(__file__).resolve().parent


def routes(case):
    by_layer = defaultdict(list)
    for path in sorted(HERE.glob(f'expert-routes-u1024-{case}-*.bin')):
        data = path.read_bytes()
        position = 0
        while position < len(data):
            layer, tokens, picks = struct.unpack_from('<III', data, position)
            position += 12
            if not 0 <= layer < LAYERS or (tokens, picks) != (1024, 8):
                raise ValueError(f'invalid router data in {path}')
            rows = np.frombuffer(data, dtype=np.uint8, count=tokens*picks, offset=position)
            by_layer[layer].append(rows.reshape(tokens, picks))
            position += rows.size
    if set(by_layer) != set(range(LAYERS)) or any(len(batches) != 4 for batches in by_layer.values()):
        raise ValueError(f'{case}: expected four router records for each layer, got '
                         f'{ {layer: len(batches) for layer, batches in by_layer.items()} }')
    return {layer: np.concatenate(batches) for layer, batches in by_layer.items()}


def affinity(rows):
    counts = np.bincount(rows.ravel(), minlength=EXPERTS)
    col1, col2 = np.triu_indices(rows.shape[1], 1)
    pair_id = rows[:, col1].astype(np.int32)*EXPERTS + rows[:, col2]
    pair = np.bincount(pair_id.ravel(), minlength=EXPERTS**2).reshape(EXPERTS, EXPERTS)
    return counts, pair + pair.T


def greedy_placement(counts, pairs, ranks, penalty):
    remaining = np.bincount(np.arange(EXPERTS) % ranks, minlength=ranks)
    affinity_to_rank = np.zeros((EXPERTS, ranks), dtype=np.int64)
    work = np.zeros(ranks, dtype=np.int64)
    expected = counts.sum()/ranks
    mapping = np.empty(EXPERTS, dtype=np.uint8)
    for expert in sorted(range(EXPERTS), key=lambda e: (-counts[e], e)):
        score = affinity_to_rank[expert] / max(1, counts[expert])
        score -= penalty * work / expected
        score[remaining == 0] = -np.inf
        rank = int(np.argmax(score))
        mapping[expert] = rank
        work[rank] += counts[expert]
        remaining[rank] -= 1
        affinity_to_rank[:, rank] += pairs[:, expert]
    return mapping


def measures(rows, mapping, ranks):
    placements = mapping[rows]
    hit = np.array([(placements == rank).any(axis=1).sum() for rank in range(ranks)])
    work = np.bincount(placements.ravel(), minlength=ranks)
    micro_peaks = [
        np.bincount(group.ravel(), minlength=ranks).max()/group.size
        for group in np.split(placements, 4)
    ]
    return hit, work, micro_peaks


def best_partition(rows, ranks):
    ids = np.arange(EXPERTS, dtype=np.uint8)
    counts, pairs = affinity(rows)
    candidates = [ids % ranks, ids.astype(np.int32)*ranks//EXPERTS]
    candidates.extend(greedy_placement(counts, pairs, ranks, penalty)
                      for penalty in (0.5, 1, 2, 4, 8))

    def score(mapping):
        hits, work, _ = measures(rows, mapping, ranks)
        return max(0, work.max()/work.sum() - (1/ranks+0.07)), hits.sum()-hits.max()

    return min(candidates, key=score)


def replay(training, evaluation, ranks, optimized):
    by_layer = []
    for layer, rows in evaluation.items():
        mapping = best_partition(training[layer], ranks) if optimized else np.arange(EXPERTS) % ranks
        train_hits, _, _ = measures(training[layer], mapping, ranks)
        owner = int(np.argmax(train_hits))
        hits, counts, micro_peaks = measures(rows, mapping, ranks)
        by_layer.append({
            'layer': layer, 'owner': owner,
            'unique_experts_1024': [int(np.unique(chunk).size) for chunk in np.split(rows, 4)],
            'remote_copies': int(hits.sum()-hits[owner]),
            'optimistic_remote_copies': int(hits.sum()-hits.max()),
            'peak_rank_share': float(counts.max()/counts.sum()),
            'microbatch_peak_rank_share': micro_peaks,
        })
    remote = sum(r['remote_copies'] for r in by_layer)
    ideal_owner = sum(r['optimistic_remote_copies'] for r in by_layer)
    tokens = LAYERS * 4096
    width_fp16 = WIDTH*2
    payload = remote * width_fp16 * 2
    return {
        'ranks': ranks,
        'optimized': optimized,
        'remote_copies_per_token_per_layer': remote/tokens,
        'optimistic_remote_copies_per_token_per_layer': ideal_owner/tokens,
        'median_peak_rank_share': median(r['peak_rank_share'] for r in by_layer),
        'median_microbatch_peak_rank_share': median(x for r in by_layer for x in r['microbatch_peak_rank_share']),
        'median_unique_experts_per_1024_layer': median(x for r in by_layer for x in r['unique_experts_1024']),
        'fp16_dispatch_combine_mib': payload/2**20,
        'fp16_mean_serial_owner_link_ms_per_layer': 1000*payload/LAYERS/RATE_BYTES_S,
        'fp16_ideal_full_duplex_owner_link_ms_per_layer': 1000*payload/LAYERS/RATE_BYTES_S/2,
        'fp16_serial_owner_link_seconds': payload/RATE_BYTES_S,
        'fp16_total_transmit_capacity_seconds': payload/(ranks*RATE_BYTES_S),
        'layers': by_layer,
    }


def observed_mmq():
    costs = defaultdict(lambda: np.zeros(4))
    for path in HERE.glob('op-events-prefill-u1024-*.tsv'):
        chunk = -1
        for line in path.read_text().splitlines():
            group, _, weight, start, end = line.split('\t')
            if group == 'graph':
                chunk += 1
            elif group == 'MoE.MMQ':
                layer = int(re.match(r'blk\.(\d+)\.', weight)[1])
                costs[layer][chunk] += float(end)-float(start)
    fallback = np.median(list(costs.values()), axis=0)
    return {layer: costs.get(layer, fallback) for layer in range(LAYERS)}


def sparse_orders(rows):
    counts, pairs = affinity(rows)
    hot_first = np.argsort(-counts, kind='stable')
    yield hot_first
    yield hot_first[::-1]
    present = np.zeros((EXPERTS, len(rows)), dtype=bool)
    present[rows, np.arange(len(rows))[:, None]] = True
    for seed in (hot_first[0], hot_first[64], hot_first[128]):
        order = [int(seed)]
        visited = present[seed].copy()
        for _ in range(23):
            new_visits = np.count_nonzero(present & ~visited, axis=1)
            score = counts/(new_visits + 1)
            score += pairs[seed] / max(1, counts[seed])
            score[order] = -1
            expert = int(np.argmax(score))
            order.append(expert)
            visited |= present[expert]
        yield order


def sparse_cost(rows, mask, mmq_ms, element_bytes, adaptive):
    hit = mask[rows].reshape(4, 1024, 8)
    share = hit.sum(axis=(1, 2))/(1024*8)
    tokens = hit.any(axis=-1).sum(axis=-1)
    wire_ms = tokens*WIDTH*element_bytes*2/RATE_BYTES_S*1000
    # Overlap local experts with dispatch, helper compute and combine.
    critical_ms = np.maximum(mmq_ms*(1-share), mmq_ms*share + wire_ms)
    if adaptive:
        use_helper = critical_ms < mmq_ms
        critical_ms = np.where(use_helper, critical_ms, mmq_ms)
        share *= use_helper
        tokens *= use_helper
    return critical_ms.sum(), share.mean(), tokens.sum()


def sparse_replay(training, evaluation, costs, element_bytes, adaptive=False):
    results = []
    for layer, rows in training.items():
        mmq_ms = costs[layer]
        best = mmq_ms.sum()
        winner = np.zeros(EXPERTS, dtype=bool)
        for order in sparse_orders(rows):
            mask = np.zeros(EXPERTS, dtype=bool)
            for expert in order[:4 if adaptive else 24]:
                mask[expert] = True
                time, _, _ = sparse_cost(rows, mask, mmq_ms, element_bytes, adaptive)
                if time < best:
                    winner = mask.copy()
                    best = time
        predicted, share, tokens = sparse_cost(evaluation[layer], winner, mmq_ms, element_bytes, adaptive)
        results.append({
            'layer': layer, 'moved_experts': int(winner.sum()),
            'work_share': float(share), 'remote_tokens': int(tokens),
            'mmq_ms': float(mmq_ms.sum()), 'predicted_ms': float(predicted),
        })
    return {
        'activation_bytes_per_element': element_bytes,
        'adaptive_with_local_replicas': adaptive,
        'baseline_mmq_ms': sum(row['mmq_ms'] for row in results),
        'predicted_change_ms': sum(row['predicted_ms']-row['mmq_ms'] for row in results),
        'participating_layers': sum(row['moved_experts'] > 0 for row in results),
        'mean_remote_expert_work_share': sum(row['work_share'] for row in results)/LAYERS,
        'layers': results,
    }


if __name__ == '__main__':
    data = {name: routes(name) for name in ('needle', 'docs')}
    pairs = (('needle', 'docs'), ('docs', 'needle'))
    balanced = [
        {'fit': fit, 'evaluate': evaluate, **replay(data[fit], data[evaluate], ranks, optimized)}
        for fit, evaluate in pairs
        for ranks in (2, 3)
        for optimized in (False, True)
    ]
    costs = observed_mmq()
    sparse = [
        {'fit': fit, 'evaluate': evaluate, **sparse_replay(data[fit], data[evaluate], costs, width, adaptive)}
        for fit, evaluate in pairs
        for width in (4, 2)
        for adaptive in (False, True)
    ]
    print(json.dumps({'balanced': balanced, 'sparse': sparse}, indent=2))
