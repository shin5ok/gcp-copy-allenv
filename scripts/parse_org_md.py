#!/usr/bin/env python3
"""
org/ORG.md を SoT として読み、setup_org.sh が eval 可能な
bash 代入文を stdout に出力する。

stdlib のみで動く。`uv run` 不要。

Usage:
    python3 scripts/parse_org_md.py [path/to/ORG.md]

Output 変数 (bash):
    HOST_PROJECT, SVC1_PROJECT, SVC3_PROJECT
    NETWORK, REGION, ZONE
    SUBNET_SVC1, SUBNET_SVC1_CIDR
    SUBNET_SVC3, SUBNET_SVC3_CIDR
    ROUTER, NAT
    DEBIAN_IMAGE_FAMILY, DEBIAN_IMAGE_PROJECT
    UBUNTU_IMAGE_FAMILY, UBUNTU_IMAGE_PROJECT
    VMS_SVC1=( "name|mt|ip" ... )
    VMS_SVC3=( "name|mt|ip" ... )
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


SECTION_RE = re.compile(r"^(#{2,3})\s+([0-9]+(?:\.[0-9]+)?)\.\s+(.+?)\s*$")
TABLE_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$")
SEPARATOR_RE = re.compile(r"^\s*\|\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|\s*$")
IMAGE_PATH_RE = re.compile(
    r"projects/(?P<proj>[a-z0-9-]+)/global/images/family/(?P<family>[A-Za-z0-9._-]+)"
)


def die(msg: str) -> "NoReturn":  # type: ignore[name-defined]
    print(f"parse_org_md: ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def strip_cell(s: str) -> str:
    s = s.strip()
    if s.startswith("`") and s.endswith("`") and len(s) >= 2:
        s = s[1:-1]
    return s.strip()


def split_row(line: str) -> list[str]:
    m = TABLE_ROW_RE.match(line)
    if not m:
        return []
    inner = m.group(1)
    cells = [strip_cell(c) for c in inner.split("|")]
    return cells


def parse_sections(text: str) -> dict[str, dict]:
    """Group lines by section key ("1", "2", "2.1", ...)."""
    sections: dict[str, dict] = {}
    current_key = ""
    for line in text.splitlines():
        m = SECTION_RE.match(line)
        if m:
            current_key = m.group(2)
            sections.setdefault(current_key, {"title": m.group(3), "lines": []})
            continue
        if current_key:
            sections[current_key]["lines"].append(line)
    return sections


def extract_tables(lines: list[str]) -> list[list[list[str]]]:
    """各 markdown table のデータ行 (header / separator を除く) を返す。
    separator 行 (`| :--- | :--- |`) を「ここから先はデータ行」マーカーにし、
    空行でテーブル終了とみなす。
    """
    tables: list[list[list[str]]] = []
    current: list[list[str]] = []
    in_data = False
    for line in lines:
        if SEPARATOR_RE.match(line):
            in_data = True
            current = []
            continue
        cells = split_row(line)
        if cells:
            if in_data:
                current.append(cells)
            # else: ヘッダ行 — 捨てる
        else:
            if current:
                tables.append(current)
            current = []
            in_data = False
    if current:
        tables.append(current)
    return tables


def first_table_of(section: dict) -> list[list[str]]:
    tables = extract_tables(section["lines"])
    if not tables:
        die(f"section {section.get('_key', '?')}: テーブルが見つからない")
    return tables[0]


def sh_quote(v: str) -> str:
    return "'" + v.replace("'", "'\\''") + "'"


def emit_scalar(name: str, value: str) -> str:
    return f"{name}={sh_quote(value)}"


def emit_array(name: str, values: list[str]) -> str:
    out = [f"{name}=("]
    for v in values:
        out.append(f"  {sh_quote(v)}")
    out.append(")")
    return "\n".join(out)


def find_bullet(lines: list[str], label_keywords: list[str]) -> str:
    """- **<label>**: `<value>` の形式から value を返す。label_keywords の
    どれかが label に含まれる行を探す。"""
    pat = re.compile(r"^\s*-\s*\*\*([^*]+)\*\*\s*[:：]\s*(.+?)\s*$")
    for line in lines:
        m = pat.match(line)
        if not m:
            continue
        label = m.group(1).strip()
        if any(k in label for k in label_keywords):
            value = m.group(2).strip()
            # strip backticks and trailing parens (e.g. "asia-northeast1 (東京)")
            value = re.sub(r"\s*\([^)]*\)\s*$", "", value)
            value = strip_cell(value)
            return value
    die(f"bullet が見つからない (keywords={label_keywords})")


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] not in ("-h", "--help"):
        path = Path(sys.argv[1])
    else:
        path = Path("org/ORG.md")
    if not path.is_file():
        die(f"ORG.md が見つからない: {path}")

    text = path.read_text(encoding="utf-8")
    sections = parse_sections(text)
    for k, v in sections.items():
        v["_key"] = k

    # ----- Section 1: プロジェクト構造 -----
    if "1" not in sections:
        die("Section 1 (プロジェクト構造) が見つからない")
    proj_rows = first_table_of(sections["1"])
    host = svc1 = svc3 = ""
    for row in proj_rows:
        if len(row) < 2:
            continue
        role, pid = row[0], row[1]
        if "Host" in role:
            host = pid
        elif "Service Project 1" in role:
            svc1 = pid
        elif "Service Project 3" in role:
            svc3 = pid
    if not (host and svc1 and svc3):
        die(
            f"Section 1 のプロジェクト ID を全て抽出できなかった "
            f"(host={host!r} svc1={svc1!r} svc3={svc3!r})"
        )

    # ----- Section 2: ネットワーク -----
    if "2" not in sections:
        die("Section 2 (ネットワーク) が見つからない")
    sec2_lines = sections["2"]["lines"]
    network = find_bullet(sec2_lines, ["VPC", "ネットワーク名"])
    region = find_bullet(sec2_lines, ["リージョン"])

    # ----- Section 2.1: サブネット -----
    if "2.1" not in sections:
        die("Section 2.1 (サブネット) が見つからない")
    sub_rows = first_table_of(sections["2.1"])
    subnet_svc1 = subnet_svc1_cidr = ""
    subnet_svc3 = subnet_svc3_cidr = ""
    for row in sub_rows:
        if len(row) < 3:
            continue
        sname, cidr, target = row[0], row[1], row[2]
        if target == svc1:
            subnet_svc1, subnet_svc1_cidr = sname, cidr
        elif target == svc3:
            subnet_svc3, subnet_svc3_cidr = sname, cidr
    if not (subnet_svc1 and subnet_svc3):
        die(f"Section 2.1 から SVC1/SVC3 サブネットを抽出できなかった")

    # ----- Section 2.2: NAT / Router -----
    if "2.2" not in sections:
        die("Section 2.2 (Cloud NAT) が見つからない")
    nat_rows = first_table_of(sections["2.2"])
    router = nat = ""
    for row in nat_rows:
        if len(row) < 2:
            continue
        type_label, rname = row[0], row[1]
        if "Cloud Router" in type_label:
            router = rname
        elif "Cloud NAT" in type_label:
            nat = rname
    if not (router and nat):
        die(f"Section 2.2 から Router/NAT を抽出できなかった (router={router!r} nat={nat!r})")

    # ----- Section 3: zone bullet -----
    if "3" not in sections:
        die("Section 3 (VM) が見つからない")
    zone = find_bullet(sections["3"]["lines"], ["ゾーン", "Zone"])

    # ----- Section 3.1 / 3.2: VM 表と image family -----
    def parse_vm_section(key: str) -> tuple[list[str], str, str]:
        if key not in sections:
            die(f"Section {key} が見つからない")
        lines = sections[key]["lines"]
        m = IMAGE_PATH_RE.search("\n".join(lines))
        if not m:
            die(f"Section {key} から OS イメージ projects/.../family/... が抽出できない")
        img_project = m.group("proj")
        img_family = m.group("family")
        rows = first_table_of(sections[key])
        vms: list[str] = []
        for row in rows:
            if len(row) < 6:
                continue
            name, mt = row[0], row[1]
            ip = row[5]
            if not name:
                continue
            vms.append(f"{name}|{mt}|{ip}")
        if not vms:
            die(f"Section {key} の VM 表から行を抽出できなかった")
        return vms, img_family, img_project

    vms_svc1, debian_family, debian_project = parse_vm_section("3.1")
    vms_svc3, ubuntu_family, ubuntu_project = parse_vm_section("3.2")

    # ----- 出力 -----
    out: list[str] = []
    out.append("# generated by scripts/parse_org_md.py — do not edit; edit org/ORG.md")
    out.append(emit_scalar("HOST_PROJECT", host))
    out.append(emit_scalar("SVC1_PROJECT", svc1))
    out.append(emit_scalar("SVC3_PROJECT", svc3))
    out.append(emit_scalar("NETWORK", network))
    out.append(emit_scalar("REGION", region))
    out.append(emit_scalar("ZONE", zone))
    out.append(emit_scalar("SUBNET_SVC1", subnet_svc1))
    out.append(emit_scalar("SUBNET_SVC1_CIDR", subnet_svc1_cidr))
    out.append(emit_scalar("SUBNET_SVC3", subnet_svc3))
    out.append(emit_scalar("SUBNET_SVC3_CIDR", subnet_svc3_cidr))
    out.append(emit_scalar("ROUTER", router))
    out.append(emit_scalar("NAT", nat))
    out.append(emit_scalar("DEBIAN_IMAGE_FAMILY", debian_family))
    out.append(emit_scalar("DEBIAN_IMAGE_PROJECT", debian_project))
    out.append(emit_scalar("UBUNTU_IMAGE_FAMILY", ubuntu_family))
    out.append(emit_scalar("UBUNTU_IMAGE_PROJECT", ubuntu_project))
    out.append(emit_array("VMS_SVC1", vms_svc1))
    out.append(emit_array("VMS_SVC3", vms_svc3))
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
