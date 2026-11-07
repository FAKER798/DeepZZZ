#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import struct
import time
from pathlib import Path


DTB_PATH = Path("/boot/spacemit/6.6.63/k1-x_MUSE-Pi-Pro.dtb")
TARGET_NODE = "/soc/pwm@d4020000"
TARGET_PINCTRL_NODE = "/soc/pinctrl@d401e000/pwm8_1_grp"
CONFLICT_I2C_NODE = "/soc/i2c@f0614000"
FDT_BEGIN_NODE = 1
FDT_END_NODE = 2
FDT_PROP = 3
FDT_NOP = 4
FDT_END = 9


class DtbPatchError(RuntimeError):
    pass


def align4(value: int) -> int:
    return (value + 3) & ~3


def read_u32(data: bytes | bytearray, offset: int) -> int:
    return struct.unpack_from(">I", data, offset)[0]


def cstring(data: bytes | bytearray, offset: int) -> bytes:
    end = data.index(0, offset)
    return bytes(data[offset:end])


def write_u32(data: bytearray, offset: int, value: int) -> None:
    struct.pack_into(">I", data, offset, value)


def get_string_table(data: bytearray) -> tuple[int, int]:
    off_dt_strings = read_u32(data, 12)
    size_dt_strings = read_u32(data, 32)
    return off_dt_strings, size_dt_strings


def get_string(data: bytearray, nameoff: int) -> str:
    off_dt_strings, size_dt_strings = get_string_table(data)
    prop_name_offset = off_dt_strings + nameoff
    if prop_name_offset >= off_dt_strings + size_dt_strings:
        raise DtbPatchError("property name offset is out of range")
    return cstring(data, prop_name_offset).decode("ascii")


def find_or_add_string(data: bytearray, value: str) -> int:
    encoded = value.encode("ascii") + b"\0"
    off_dt_strings, size_dt_strings = get_string_table(data)
    table = bytes(data[off_dt_strings : off_dt_strings + size_dt_strings])
    pos = table.find(encoded)
    if pos >= 0:
        return pos
    data[off_dt_strings + size_dt_strings : off_dt_strings + size_dt_strings] = encoded
    write_u32(data, 4, read_u32(data, 4) + len(encoded))
    write_u32(data, 32, size_dt_strings + len(encoded))
    return size_dt_strings


def property_blob(nameoff: int, value: bytes) -> bytes:
    padding = b"\0" * (align4(len(value)) - len(value))
    return struct.pack(">III", FDT_PROP, len(value), nameoff) + value + padding


def insert_struct_blob(data: bytearray, offset: int, blob: bytes) -> None:
    data[offset:offset] = blob
    write_u32(data, 4, read_u32(data, 4) + len(blob))
    write_u32(data, 12, read_u32(data, 12) + len(blob))
    write_u32(data, 36, read_u32(data, 36) + len(blob))


def parse_dtb(data: bytearray) -> tuple[dict[tuple[str, str], tuple[int, int]], dict[str, int]]:
    if read_u32(data, 0) != 0xD00DFEED:
        raise DtbPatchError("not a flattened device tree blob")

    off_dt_struct = read_u32(data, 8)
    offset = off_dt_struct
    stack: list[str] = []
    props: dict[tuple[str, str], tuple[int, int]] = {}
    node_end_offsets: dict[str, int] = {}

    while True:
        token_offset = offset
        token = read_u32(data, offset)
        offset += 4
        if token == FDT_BEGIN_NODE:
            name = cstring(data, offset).decode("ascii")
            offset = align4(offset + len(name) + 1)
            if name:
                stack.append(name)
        elif token == FDT_END_NODE:
            node_path = "/" + "/".join(stack)
            node_end_offsets[node_path] = token_offset
            if stack:
                stack.pop()
        elif token == FDT_PROP:
            prop_len = read_u32(data, offset)
            nameoff = read_u32(data, offset + 4)
            value_offset = offset + 8
            prop_name = get_string(data, nameoff)
            node_path = "/" + "/".join(stack)
            props[(node_path, prop_name)] = (value_offset, prop_len)
            offset = align4(value_offset + prop_len)
        elif token == FDT_NOP:
            continue
        elif token == FDT_END:
            break
        else:
            raise DtbPatchError(f"unknown FDT token {token} at 0x{offset - 4:x}")
    return props, node_end_offsets


def patch_dtb(data: bytearray) -> None:
    props, node_end_offsets = parse_dtb(data)
    patch_node_status(data, props, CONFLICT_I2C_NODE, enabled=False)
    props, node_end_offsets = parse_dtb(data)
    patch_status(data, props)
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


def patch_status(data: bytearray, props: dict[tuple[str, str], tuple[int, int]]) -> None:
    patch_node_status(data, props, TARGET_NODE, enabled=True)


def patch_node_status(
    data: bytearray,
    props: dict[tuple[str, str], tuple[int, int]],
    node: str,
    enabled: bool,
) -> None:
    desired = b"okay\0" if enabled else b"disabled\0"
    key = (node, "status")
    if key not in props:
        raise DtbPatchError(f"property not found: {node}/status")
    value_offset, prop_len = props[key]
    old_value = bytes(data[value_offset : value_offset + prop_len])
    if old_value == desired or old_value.startswith(desired):
        return
    if prop_len == len(desired):
        data[value_offset : value_offset + prop_len] = desired
        return
    prop_offset = value_offset - 12
    old_total = align4(12 + prop_len)
    nameoff = read_u32(data, value_offset - 4)
    replacement = property_blob(nameoff, desired)
    data[prop_offset : prop_offset + old_total] = replacement
    size_delta = len(replacement) - old_total
    write_u32(data, 4, read_u32(data, 4) + size_delta)
    write_u32(data, 12, read_u32(data, 12) + size_delta)
    write_u32(data, 36, read_u32(data, 36) + size_delta)


def find_phandle(data: bytearray, props: dict[tuple[str, str], tuple[int, int]], node: str) -> int:
    for prop in ("phandle", "linux,phandle"):
        key = (node, prop)
        if key not in props:
            continue
        value_offset, prop_len = props[key]
        if prop_len >= 4:
            return read_u32(data, value_offset)
    raise DtbPatchError(f"phandle not found: {node}")


def ensure_phandle(
    data: bytearray,
    props: dict[tuple[str, str], tuple[int, int]],
    node_end_offsets: dict[str, int],
    node: str,
) -> int:
    try:
        return find_phandle(data, props, node)
    except DtbPatchError:
        pass
    if node not in node_end_offsets:
        raise DtbPatchError(f"node not found: {node}")
    phandle = max_existing_phandle(data, props) + 1
    blob = property_blob(find_or_add_string(data, "phandle"), struct.pack(">I", phandle))
    insert_struct_blob(data, node_end_offsets[node], blob)
    return phandle


def max_existing_phandle(data: bytearray, props: dict[tuple[str, str], tuple[int, int]]) -> int:
    maximum = 0
    for (_, prop), (value_offset, prop_len) in props.items():
        if prop not in {"phandle", "linux,phandle"} or prop_len < 4:
            continue
        maximum = max(maximum, read_u32(data, value_offset))
    return maximum


def update_prop_exact(
    data: bytearray,
    props: dict[tuple[str, str], tuple[int, int]],
    node: str,
    prop: str,
    value: bytes,
) -> None:
    key = (node, prop)
    if key not in props:
        raise DtbPatchError(f"property not found after insert: {node}/{prop}")
    value_offset, prop_len = props[key]
    if prop_len != len(value):
        raise DtbPatchError(f"cannot update {node}/{prop}: length {prop_len} != {len(value)}")
    data[value_offset : value_offset + prop_len] = value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Enable AP_I2C3_SCL_3V3 PWM8 in the MUSE-Pi-Pro boot DTB.")
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
    print(f"disabled={CONFLICT_I2C_NODE}")
    print(f"enabled={TARGET_NODE}")
    print(f"pinctrl={TARGET_PINCTRL_NODE}")
    print("reboot required")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
