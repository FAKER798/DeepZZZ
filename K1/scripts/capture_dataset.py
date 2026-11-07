#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
import urllib.request


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Capture lowres dataset samples through the running K1 web API.")
    parser.add_argument("--base-url", default="http://127.0.0.1:7861")
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--interval", type=float, default=0.0, help="seconds between captures")
    parser.add_argument("--light", choices=["auto", "day", "night_ir", "low_light"], default="auto")
    parser.add_argument("--confirm", action="store_true", help="confirm each capture immediately")
    return parser


def post_json(url: str, payload: bytes = b"{}") -> dict:
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    args = build_parser().parse_args()
    count = max(args.count, 1)
    base_url = args.base_url.rstrip("/")
    capture_body = b"{}" if args.light == "auto" else json.dumps({"labels": {"light": args.light}}).encode("utf-8")
    for index in range(count):
        payload = post_json(f"{base_url}/api/dataset/capture", capture_body)
        if not payload.get("ok"):
            raise RuntimeError(payload.get("error") or "capture failed")
        result = payload["result"]
        if args.confirm:
            confirm_payload = post_json(
                f"{base_url}/api/dataset/confirm",
                json.dumps({"id": result["id"]}).encode("utf-8"),
            )
            if not confirm_payload.get("ok"):
                raise RuntimeError(confirm_payload.get("error") or "confirm failed")
            result = confirm_payload["result"]
            print(f"{result['id']} confirmed raw={result['raw_url']} meta={result['meta_url']}")
        else:
            print(f"{result['id']} pending raw={result['raw_path']} meta={result['meta_path']}")
        if index + 1 < count and args.interval > 0:
            time.sleep(args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
