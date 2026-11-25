from collections import Counter, defaultdict
import json
from pathlib import Path
import sys


def union(intervals):
    stop = total = 0.0
    for low, high in sorted(intervals):
        total += max(0.0, high - max(low, stop))
        stop = max(stop, high)
    return total


def summarize(path):
    graph_times = []
    groups = defaultdict(list)
    counts = Counter()
    weight_ms = Counter()
    offset = 0.0
    for line in path.read_text().splitlines():
        group, kind, name, low, high = line.split('\t')
        low, high = float(low), float(high)
        if group == 'graph':
            offset = sum(graph_times)
            graph_times.append(high)
            continue
        if not graph_times or high < low or low < -0.1 or high > graph_times[-1] + 0.1:
            raise ValueError(f'invalid graph interval in {path}: {line}')
        groups[group].append((offset + max(low, 0.0), offset + min(high, graph_times[-1])))
        counts[group] += 1
        if group == 'MoE.MMQ':
            role = name.rsplit('.', 2)[-2] if name.endswith('.weight') else name
            weight_ms[f'{kind}/{role}'] += high-low

    if len(graph_times) != 4:
        raise ValueError(f'{path}: expected four traced microbatches, got {len(graph_times)}')
    if not counts['MoE.MMQ']:
        raise ValueError(f'{path}: MoE MMQ hook recorded no calls')
    total = sum(graph_times)
    spans = [
        {'operation': name, 'calls': counts[name], 'milliseconds': union(segments),
         'percent_of_gpu_graph': union(segments)*100/total}
        for name, segments in groups.items()
    ]
    return {
        'file': path.name,
        'graph_ms': total,
        'microbatch_ms': graph_times,
        'other_graph_ms': total - union([span for segments in groups.values() for span in segments]),
        'routed_weight_ms': dict(weight_ms.most_common()),
        'operations': sorted(spans, key=lambda span: -span['milliseconds']),
    }


if __name__ == '__main__':
    print(json.dumps([summarize(Path(path)) for path in sys.argv[1:]], indent=2))
