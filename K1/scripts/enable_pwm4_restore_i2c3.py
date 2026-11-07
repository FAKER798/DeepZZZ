#!/usr/bin/env python3
from __future__ import annotations

from enable_gpio50_pwm8 import (
    DTB_PATH,
    DtbPatchError,
    ensure_phandle,
    parse_dtb,
    patch_node_status,
    property_blob,
    find_or_add_string,
    insert_struct_blob,
    update_prop_exact,
)

import argparse
import shutil
import struct
import time
from pathlib import Path


TARGET_NODE = "/soc/pwm@d401b000"
TARGET_PINCTRL_NODE = "/soc/pinctrl@d401e000/pwm4_1_grp"
I2C3_NODE = "/soc/i2c@f0614000"


def patch_dtb(data: bytearray) -> None:
    props, node_end_offsets = parse_dtb(data)
    patch_node_status(data, props, I2C3_NODE, enabled=True)
    props, node_end_offsets = parse_dtb(data)
    patch_node_status(data, props, TARGET_NODE, enabled=True)
    props, node_end_offsets = parse_dtb(data)
    phandle = ensure_phandle(data, props, node_end_offsets, TARGET_PINCTRL_NODE)
    props, node_end_offsets = parse_dtb(data)

    additions = []
    if (TARGET_NODE, "pinctrl-names") not in props:
        additions.append(property_blob(find_or_add_string(data, "pinctrl-names"), b"default\0"))
    if (TARGET_NODE, "pinctrl-0") not in props:
        additions.append(property_blob(find_or_add_string(data, "pinctrl-0"), struct.pack(">I", phandle)))
    if additions:
        if TARGET_NODE not in node_end_offsets:
            raise DtbPatchError(f"node not found: {TARGET_NODE}")
        insert_struct_blob(data, node_end_offsets[TARGET_NODE], b"".join(additions))
        props, _ = parse_dtb(data)

    update_prop_exact(data, props, TARGET_NODE, "pinctrl-names", b"default\0")
    update_prop_exact(data, props, TARGET_NODE, "pinctrl-0", struct.pack(">I", phandle))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Enable PWM4 and restore AP I2C3 in the MUSE-Pi-Pro boot DTB.")
    parser.add_argument("--dtb", type=Path, default=DTB_PATH)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not args.dtb.exists():
        raise SystemExit(f"DTB not found: {args.dtb}")

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    backup = args.dtb.with_name(f"{args.dtb.name}.bak-{timestamp}")
    shutil.copy2(args.dtb, backup)

    data = bytearray(args.dtb.read_bytes())
    patch_dtb(data)
    temp = args.dtb.with_name(f".{args.dtb.name}.tmp")
    temp.write_bytes(data)
    temp.replace(args.dtb)

    print(f"backup={backup}")
    print(f"restored={I2C3_NODE}")
    print(f"enabled={TARGET_NODE}")
    print(f"pinctrl={TARGET_PINCTRL_NODE}")
    print("reboot required")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
