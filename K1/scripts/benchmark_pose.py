#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np  # noqa: E402

from ai.runtime import CPU_PROVIDER, NPU_PROVIDER, create_session  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Benchmark a YOLO pose ONNX model on CPU/NPU providers.")
    parser.add_argument("--model", default="/home/z/.brdk_models/pose/yolov8n-pose-k1-320-int8.onnx")
    parser.add_argument("--provider", default=NPU_PROVIDER)
    parser.add_argument("--no-fallback", dest="fallback", action="store_false", default=True)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--compare-cpu", action="store_true")
    return parser


def benchmark(model: str, provider: str, fallback: bool, warmup: int, runs: int) -> dict[str, object]:
    session = create_session(model, provider=provider, fallback=fallback)
    input_meta = session.get_inputs()[0]
    output_names = [output.name for output in session.get_outputs()]
    shape = [1 if not isinstance(dim, int) else dim for dim in input_meta.shape]
    if input_meta.type != "tensor(float)":
        raise RuntimeError(f"unsupported input type: {input_meta.type}")
    sample = np.zeros(shape, dtype=np.float32)

    for _ in range(max(warmup, 0)):
        session.run(output_names, {input_meta.name: sample})

    elapsed = []
    for _ in range(max(runs, 1)):
        started = time.perf_counter()
        session.run(output_names, {input_meta.name: sample})
        elapsed.append((time.perf_counter() - started) * 1000.0)

    return {
        "model": model,
        "requested_provider": provider,
        "actual_providers": session.get_providers(),
        "input": {"name": input_meta.name, "type": input_meta.type, "shape": input_meta.shape},
        "runs": len(elapsed),
        "avg_ms": round(sum(elapsed) / len(elapsed), 2),
        "min_ms": round(min(elapsed), 2),
        "max_ms": round(max(elapsed), 2),
    }


def print_result(result: dict[str, object]) -> None:
    print(f"model: {result['model']}")
    print(f"requested_provider: {result['requested_provider']}")
    print(f"actual_providers: {result['actual_providers']}")
    print(f"input: {result['input']}")
    print(f"runs: {result['runs']}")
    print(f"avg_ms: {result['avg_ms']}")
    print(f"min_ms: {result['min_ms']}")
    print(f"max_ms: {result['max_ms']}")


def main() -> int:
    args = build_parser().parse_args()
    print_result(benchmark(args.model, args.provider, args.fallback, args.warmup, args.runs))
    if args.compare_cpu and args.provider != CPU_PROVIDER:
        print("")
        print_result(benchmark(args.model, CPU_PROVIDER, False, args.warmup, args.runs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
