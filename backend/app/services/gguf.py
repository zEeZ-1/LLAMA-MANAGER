"""Read bounded GGUF metadata without importing numpy, torch, or model weights."""

import math
import struct
from pathlib import Path

# (elements per block, bytes per block), ggml types used by llama.cpp.
TYPES = {
    0: (1, 4),
    1: (1, 2),
    2: (32, 18),
    3: (32, 20),
    6: (32, 22),
    7: (32, 24),
    8: (32, 34),
    9: (32, 40),
    10: (256, 84),
    11: (256, 110),
    12: (256, 144),
    13: (256, 176),
    14: (256, 210),
    15: (256, 292),
    16: (256, 66),
    17: (256, 74),
    18: (256, 98),
    19: (256, 50),
    20: (32, 18),
    21: (256, 110),
    22: (256, 82),
    23: (256, 136),
    24: (1, 1),
    25: (1, 2),
    26: (1, 4),
    27: (1, 8),
    28: (1, 8),
    29: (256, 56),
    30: (1, 2),
    34: (256, 54),
    35: (256, 66),
    39: (32, 17),
    40: (64, 36),
    41: (128, 18),
    42: (64, 18),
}
SCALAR = {
    0: "B",
    1: "b",
    2: "H",
    3: "h",
    4: "I",
    5: "i",
    6: "f",
    7: "?",
    10: "Q",
    11: "q",
    12: "d",
}


def read_metadata(path: Path) -> dict:
    size = path.stat().st_size
    with path.open("rb") as f:
        operations = 0

        def read(fmt):
            n = struct.calcsize("<" + fmt)
            data = f.read(n)
            if len(data) != n:
                raise ValueError("incomplete GGUF header")
            return struct.unpack("<" + fmt, data)[0]

        def skip(n):
            if n < 0 or f.tell() + n > size:
                raise ValueError("incomplete GGUF data")
            f.seek(n, 1)

        def string(keep=False):
            n = read("Q")
            if keep and n <= 4096:
                data = f.read(n)
                if len(data) != n:
                    raise ValueError("incomplete GGUF string")
                return data.decode("utf-8", errors="replace")
            skip(n)
            return None

        def value(kind, keep=False, depth=0):
            nonlocal operations
            operations += 1
            if operations > 1000000 or depth > 2:
                raise ValueError("GGUF metadata exceeds parsing budget")
            if kind in SCALAR:
                v = read(SCALAR[kind])
                return v if keep else None
            if kind == 8:
                return string(keep)
            if kind == 9:
                subtype, n = read("I"), read("Q")
                if subtype in SCALAR:
                    skip(struct.calcsize("<" + SCALAR[subtype]) * n)
                elif subtype == 8 and n <= 1000000:
                    for _ in range(n):
                        value(subtype, depth=depth + 1)
                else:
                    raise ValueError("unsupported or oversized GGUF array")
                return None
            raise ValueError("unknown GGUF metadata type")

        if f.read(4) != b"GGUF" or read("I") not in (2, 3):
            raise ValueError("not a supported GGUF v2/v3 file")
        tensors, pairs = read("Q"), read("Q")
        if not 0 < tensors <= 100000 or pairs > 100000:
            raise ValueError("invalid GGUF counts")
        metadata = {}
        for _ in range(pairs):
            key, kind = string(True), read("I")
            keep = bool(
                len(metadata) < 16
                and key
                and (
                    key
                    in {
                        "general.name",
                        "general.architecture",
                        "general.file_type",
                        "general.quantization_version",
                        "general.alignment",
                        "split.count",
                    }
                    or key.endswith(".context_length")
                )
            )
            v = value(kind, keep)
            if keep and v is not None:
                metadata[key] = v
        extent = 0
        for _ in range(tensors):
            string()
            dimensions = read("I")
            if not 1 <= dimensions <= 4:
                raise ValueError("invalid GGUF tensor dimensions")
            count = math.prod(read("Q") for _ in range(dimensions))
            kind, offset = read("I"), read("Q")
            if kind not in TYPES or count <= 0:
                raise ValueError(f"unsupported GGML tensor type {kind}")
            block, nbytes = TYPES[kind]
            extent = max(extent, offset + ((count + block - 1) // block) * nbytes)
        alignment = metadata.get("general.alignment", 32)
        if not isinstance(alignment, int) or not 1 <= alignment <= 65536:
            raise ValueError("invalid GGUF alignment")
        data_start = (f.tell() + alignment - 1) // alignment * alignment
        if data_start + extent > size:
            raise ValueError(
                "GGUF tensor data is incomplete (file may still be copying)"
            )
        if metadata.get("split.count", 1) != 1:
            raise ValueError("V1 requires a single-file GGUF; merge split shards first")
        return metadata
