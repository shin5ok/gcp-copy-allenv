#!/usr/bin/env python3
"""GCP プロジェクトまるごとコピーオーケストレータ (sync_env.py)。

設計の柱:
- ORG プロジェクトに対する書き込みは絶対に行わない（コードレベルで強制）。
- すべての外部コマンドは side="src" | "dst" | "local" タグ付きで実行され、
  src 操作は read-only パターンに限定される（書き込み動詞は実行前に拒否）。
- impersonate_sa は推奨だが必須ではない。未指定の場合はローカル認証
  （gcloud のアクティブアカウント / ADC）にフォールバックする。src の書込権を
  持っていれば事前チェックで警告 + 続行確認を求める。
- ログは実行ごとに logs/<timestamp>/{org,dst}.log に分離、日本語で記録。
"""
import argparse
import sys
import os
import re
import yaml
import logging
import subprocess
import json
import shutil
import shlex
import tempfile
import time
import datetime
import threading
import concurrent.futures
from typing import Dict, List, Optional, Any, Tuple

# ---------------------------------------------------------------------------
# ORG 保護: src 側操作で許可するコマンドパターン
# ---------------------------------------------------------------------------
# src 操作で許可される動詞（read-only のみ）。完全一致ではなく単語境界マッチ。
_READ_ONLY_VERBS = (
    "describe", "list", "show", "get", "search-all-resources",
    "bulk-export",  # bulk-export はローカルに HCL を書き出すだけで src は変更しない
    "get-iam-policy",
)

# src 操作で必ず存在してはいけない書き込み動詞（フェイルセーフのデニーリスト）。
_WRITE_VERBS = (
    "create", "delete", "update", "add", "remove", "set",
    "enable", "disable", "attach", "detach", "stop", "start", "reset",
    "suspend", "resume",
    "apply", "destroy", "mk", "cp", "rm", "rsync", "mv",
    "import", "patch", "replace",
)

# Mock モード時に「分かっている」と判定するコマンド先頭パターン。
# これに該当しないコマンドは fail-closed（即時エラー）にする。
_MOCK_KNOWN_PATTERNS = (
    "gcloud asset search-all-resources",
    "gcloud beta resource-config bulk-export",
    "gcloud compute instances list",
    "gcloud compute instances describe",
    "gcloud compute instances create",
    "gcloud compute disks describe",
    "gcloud compute networks list",
    "gcloud compute networks describe",
    "gcloud compute networks create",
    "gcloud compute networks subnets list",
    "gcloud compute networks subnets describe",
    "gcloud compute networks subnets create",
    "gcloud storage buckets describe",
    "gcloud storage buckets create",
    "gcloud compute instances stop",
    "gcloud compute instances start",
    "gcloud compute instances suspend",
    "gcloud compute instances resume",
    "gcloud compute instances detach-disk",
    "gcloud compute instances attach-disk",
    "gcloud compute disks delete",
    "gcloud compute disks create",
    "gcloud compute snapshots list",
    "gcloud storage buckets list",
    "gcloud storage rsync",
    "gcloud storage cp",
    "gcloud compute addresses create",
    "gcloud compute addresses describe",
    "gcloud compute firewall-rules list",
    "gcloud compute firewall-rules describe",
    "gcloud compute firewall-rules create",
    "gcloud compute network-firewall-policies list",
    "gcloud compute network-firewall-policies describe",
    "gcloud compute network-firewall-policies create",
    "gcloud compute network-firewall-policies rules describe",
    "gcloud compute network-firewall-policies rules create",
    "gcloud compute network-firewall-policies associations create",
    "gcloud access-context-manager perimeters describe",
    "gcloud access-context-manager perimeters update",
    "gcloud services enable",
    "bq ls",
    "bq show",
    "bq mk",
    "bq cp",
    "terraform init",
    "terraform plan",
    "terraform apply",
)


# ---------------------------------------------------------------------------
# CAI アセット → 複製担当ステップのカバレッジマップ (ISSUE-01)
# ---------------------------------------------------------------------------
# 値はそのアセットを dst に再現する担当 step の名前。None は「意図的に対象外」
# （理由をコメントで明示）。step_cai_scan の末尾で、src の実 assetType 集合と
# このマップを突合せ、未登録の種別を WARNING で列挙する。
# 新ステップを追加した・bulk-export 対応範囲が変わった場合は必ずここを更新する。
_ASSET_COVERAGE: Dict[str, Optional[str]] = {
    # --- compute (network) ---
    "compute.googleapis.com/Network":         "gce_restore",          # _replicate_host_networks
    "compute.googleapis.com/Subnetwork":      "gce_restore",          # _replicate_host_networks
    "compute.googleapis.com/Firewall":        "network_firewall",
    "compute.googleapis.com/FirewallPolicy":  "network_firewall",
    "compute.googleapis.com/Router":          None,                    # ISSUE-03 未対応
    "compute.googleapis.com/Route":           None,                    # ISSUE-07 未対応（大半は自動生成）
    "compute.googleapis.com/Address":         "terraform_apply",       # bulk-export 出力、_strip_reserved_ip で IP は剥がす
    # --- compute (workload) ---
    "compute.googleapis.com/Instance":        "gce_restore",
    "compute.googleapis.com/Disk":            "gce_restore",
    "compute.googleapis.com/Snapshot":        "gce_restore",           # src snapshot から復元するので作成不要
    "compute.googleapis.com/Image":           None,                    # snapshot 由来。dst では使わない
    "compute.googleapis.com/InstanceSettings": None,                   # プロジェクト既定。複製不要
    "compute.googleapis.com/ResourcePolicy":  None,                    # ISSUE-08 未対応
    "compute.googleapis.com/Project":         None,                    # メタ情報。create_projects.py が担当
    # --- storage / bigquery ---
    "storage.googleapis.com/Bucket":          "data_sync",             # terraform で作成、data_sync で内容コピー
    "bigquery.googleapis.com/Dataset":        "data_sync",
    "bigquery.googleapis.com/Table":          "data_sync",
    # --- iam ---
    "iam.googleapis.com/Role":                "terraform_apply",       # bulk-export が custom role を出力
    "iam.googleapis.com/ServiceAccount":      "terraform_apply",       # bulk-export 出力
    "iam.googleapis.com/ServiceAccountKey":   None,                    # 静的キー方針外。bootstrap で SA 借用に統一
    # --- logging ---
    "logging.googleapis.com/LogSink":         "terraform_apply",       # ISSUE-11: カスタムシンクは要監視
    "logging.googleapis.com/LogBucket":       "terraform_apply",
    # --- vmmigration ---
    "vmmigration.googleapis.com/ImageImport":  None,                   # 一過性。完了後は不要
    "vmmigration.googleapis.com/TargetProject": None,                  # vmware/scripts/vmdk_run.py が担当
    # --- service usage / project meta ---
    "serviceusage.googleapis.com/Service":             None,           # create_projects.py / _ensure_dst_prereq_apis
    "cloudresourcemanager.googleapis.com/Project":     None,           # create_projects.py
    "cloudresourcemanager.googleapis.com/Lien":        None,           # 削除保護用メタ。複製不要
    "cloudbilling.googleapis.com/ProjectBillingInfo":  None,           # create_projects.py の billing link
    # --- osconfig (任意機能、運用継続には不要) ---
    "osconfig.googleapis.com/OSPolicyAssignment":       None,
    "osconfig.googleapis.com/OSPolicyAssignmentReport": None,
}

# 専用ステップが dst へリソースを複製するため、bulk-export 出力に無くても想定内
# （手動対応不要）。DIFF.md からは除外し件数だけ集計する。
_AUTO_HANDLED_STEPS = frozenset({"gce_restore", "network_firewall", "data_sync"})


def _needs_manual_recreate(atype: str, coverage_step: Optional[str]) -> bool:
    """DIFF.md に載せるべき（手動で dst 作成/調整が要る）欠落かを判定する。

    - `_ASSET_COVERAGE` 未登録: 複製漏れの可能性 → 要手動。
    - coverage_step is None（意図的対象外）: 不要。
    - gce_restore / network_firewall / data_sync: 専用ステップが複製 → 不要。
    - terraform_apply / bulk_export 等: bulk-export が出すはずが欠落 → 要手動。
    """
    if atype not in _ASSET_COVERAGE:
        return True
    if coverage_step is None:
        return False
    return coverage_step not in _AUTO_HANDLED_STEPS


def fw_rule_scope_flag(scope_flag: str) -> str:
    """ポリシー scope flag を rules / associations サブコマンド用に変換する。

    `network-firewall-policies` の list/describe/create はポリシー自体の scope を
    `--global` / `--region=R` で指定するが、`rules ...` と `associations create`
    サブコマンドは `--global-firewall-policy` / `--firewall-policy-region=R` を
    要求する（gcloud CLI 仕様）。
    """
    if scope_flag == "--global":
        return "--global-firewall-policy"
    if scope_flag.startswith("--region="):
        return "--firewall-policy-region=" + scope_flag.split("=", 1)[1]
    return scope_flag


def fw_policy_rule_layer4(rule: Dict[str, Any]) -> str:
    """FW policy rule の match.layer4Configs を gcloud --layer4-configs 文字列に変換する (ISSUE-02)。

    - 各 layer4Config は ipProtocol と任意の ports[]。
    - gcloud は `<proto>:<port>` をカンマ区切りで複数指定する形式
      (例: tcp:80,tcp:443,udp:53)。
    - ports が複数ある場合は ports 数だけ展開する。
    - layer4Configs が空 / 無指定なら `all`（IPv4/IPv6 全プロトコル）。
    """
    cfgs = rule.get('match', {}).get('layer4Configs') or [{"ipProtocol": "all"}]
    parts: List[str] = []
    for c in cfgs:
        proto = c.get('ipProtocol', 'all')
        ports = c.get('ports') or []
        if ports:
            for p in ports:
                parts.append(f"{proto}:{p}")
        else:
            parts.append(proto)
    return ",".join(parts) if parts else "all"


def fw_policy_rule_secure_tags(rule: Dict[str, Any]) -> List[str]:
    """rule が参照する全 secure tag name (`tagValues/<id>` など) を返す。

    `match.srcSecureTags` と `targetSecureTags` の両方を対象にする。
    """
    match = rule.get('match', {}) or {}
    names = [t.get('name') for t in match.get('srcSecureTags') or [] if t.get('name')]
    names += [t.get('name') for t in rule.get('targetSecureTags') or [] if t.get('name')]
    return names


def fw_policy_rule_flags(
    rule: Dict[str, Any], proj_id_map: Dict[str, str],
    secure_tag_map: Optional[Dict[str, str]] = None,
) -> List[str]:
    """FW policy rule dict を gcloud `rules create` 用の追加フラグリストに変換する。

    呼び出し側が prefix (`rules create <priority> --firewall-policy=... --action=... --direction=...
    --layer4-configs=...`) を作り、その後ろに append する想定。

    REST API の FirewallPolicyRule スキーマ全フィールドに対応:
      match 内 (リスト/文字列):
        srcIpRanges / destIpRanges / srcRegionCodes / destRegionCodes /
        srcThreatIntelligences / destThreatIntelligences /
        srcAddressGroups / destAddressGroups / srcFqdns / destFqdns /
        srcNetworks / srcSecureTags
      match 内 (enum → 文字列):
        srcNetworkScope (--src-network-context)
      ルール直下:
        targetSecureTags / targetServiceAccounts / disabled / enableLogging /
        description / securityProfileGroup / tlsInspect
    SA email 中の src プロジェクト ID は proj_id_map で dst へ置換する。

    secure tag (`tagValues/<id>`) は ORG スコープの permanent ID で別 ORG には存在しない。
    `secure_tag_map` (src tagValues/... → dst tagValues/...) で dst の値へ変換する。
    map に無いタグは無視する（呼び出し側が事前にスキップ判定する想定）。
    `secure_tag_map=None` の場合は変換せずそのまま（同一 ORG コピー時の後方互換）。
    """
    flags: List[str] = []
    match = rule.get('match', {}) or {}

    def _join_or_skip(key: str, flag: str, src: Dict[str, Any]):
        vals = src.get(key) or []
        if vals:
            flags.append(f"{flag}={','.join(str(v) for v in vals)}")

    # --- match 内リストフィールド (JSON key → gcloud flag) ---
    _join_or_skip('srcIpRanges',            '--src-ip-ranges', match)
    _join_or_skip('destIpRanges',           '--dest-ip-ranges', match)
    _join_or_skip('srcRegionCodes',         '--src-region-codes', match)
    _join_or_skip('destRegionCodes',        '--dest-region-codes', match)
    _join_or_skip('srcThreatIntelligences',  '--src-threat-intelligence', match)
    _join_or_skip('destThreatIntelligences', '--dest-threat-intelligence', match)
    _join_or_skip('srcAddressGroups',        '--src-address-groups', match)
    _join_or_skip('destAddressGroups',       '--dest-address-groups', match)
    _join_or_skip('srcFqdns',               '--src-fqdns', match)
    _join_or_skip('destFqdns',              '--dest-fqdns', match)
    _join_or_skip('srcNetworks',            '--src-networks', match)

    # srcNetworkScope は enum 文字列 (INTERNET / NON_INTERNET / VPC_NETWORKS / INTRA_VPC)
    # gcloud では --src-network-context に対応
    src_net_scope = match.get('srcNetworkScope')
    if src_net_scope:
        flags.append(f"--src-network-context={src_net_scope}")

    # secure tag は name (`tagValues/...` フル形式) で渡す。別 ORG コピー時は
    # secure_tag_map で dst 側の値に変換する（map=None なら変換せずそのまま）。
    def _remap_tags(names: List[str]) -> List[str]:
        if secure_tag_map is None:
            return names
        return [secure_tag_map[n] for n in names if n in secure_tag_map]

    src_tags = _remap_tags(
        [t.get('name') for t in match.get('srcSecureTags') or [] if t.get('name')]
    )
    if src_tags:
        flags.append(f"--src-secure-tags={','.join(src_tags)}")

    tgt_tags = _remap_tags(
        [t.get('name') for t in rule.get('targetSecureTags') or [] if t.get('name')]
    )
    if tgt_tags:
        flags.append(f"--target-secure-tags={','.join(tgt_tags)}")

    # target SA email 中の src project ID を dst に書き換え
    tgt_sas: List[str] = []
    for sa in rule.get('targetServiceAccounts') or []:
        s = sa
        for src_proj, dst_proj in proj_id_map.items():
            s = s.replace(src_proj, dst_proj)
        tgt_sas.append(s)
    if tgt_sas:
        flags.append(f"--target-service-accounts={','.join(tgt_sas)}")

    if rule.get('disabled'):
        flags.append("--disabled")

    if rule.get('enableLogging'):
        flags.append("--enable-logging")

    desc = rule.get('description')
    if desc:
        flags.append(f"--description={shlex.quote(desc)}")

    # securityProfileGroup / tlsInspect (Cloud NGFW L7 inspection)
    spg = rule.get('securityProfileGroup')
    if spg:
        flags.append(f"--security-profile-group={spg}")

    if rule.get('tlsInspect'):
        flags.append("--tls-inspect")

    return flags


def _parse_gcloud_describe_json(raw: Optional[str]) -> Dict[str, Any]:
    """`gcloud ... describe --format=json` の出力を dict として安全に返す。

    一部の gcloud SDK バージョン / リソース種では describe が単体オブジェクトでは
    なく 1 要素配列 `[{...}]` で返ってくる（compute network-firewall-policies で
    実機確認、AttributeError: 'list' object has no attribute 'get' を誘発）。
    空文字 / JSON 解析失敗 / 想定外の型のいずれも空 dict にフォールバックする。
    """
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
    except Exception:
        return {}
    if isinstance(obj, list):
        # 1 要素配列形式を吸収。複数 / 空配列は「不明」として空 dict 扱い。
        if len(obj) == 1 and isinstance(obj[0], dict):
            return obj[0]
        return {}
    if isinstance(obj, dict):
        return obj
    return {}


def diff_coverage(asset_types: List[str]) -> Tuple[List[str], List[str]]:
    """(uncovered, covered_but_unimplemented) を返す。

    - uncovered: _ASSET_COVERAGE に存在しない assetType（= 知識ベースに無い）
    - covered_but_unimplemented: マップ上 None = 「意図的対象外」だが
      ISSUE 等で「将来対応予定」とコメントされたものを別途警告したい場合に使用。
      現状は None = 全て対象外扱いとし、空リストを返す（拡張余地）。
    """
    covered = set(_ASSET_COVERAGE.keys())
    uncovered = sorted({t for t in asset_types if t and t not in covered})
    return uncovered, []


# ---------------------------------------------------------------------------
# CAI vs bulk-export tf 差分解析
# ---------------------------------------------------------------------------
# CAI assetType → bulk-export が出力しうる terraform resource type 群。
# bulk-export は config-connector 経由でリソースを HCL 化する。複数候補がある
# 場合は最初に見つかった一致を採用（例: Address は global/regional で別 type）。
_CAI_TO_TF_RESOURCE: Dict[str, Tuple[str, ...]] = {
    "compute.googleapis.com/Network":         ("google_compute_network",),
    "compute.googleapis.com/Subnetwork":      ("google_compute_subnetwork",),
    "compute.googleapis.com/Firewall":        ("google_compute_firewall",),
    "compute.googleapis.com/FirewallPolicy":  ("google_compute_network_firewall_policy",
                                               "google_compute_firewall_policy"),
    "compute.googleapis.com/Router":          ("google_compute_router",),
    "compute.googleapis.com/Route":           ("google_compute_route",),
    "compute.googleapis.com/Address":         ("google_compute_address",
                                               "google_compute_global_address"),
    "compute.googleapis.com/Instance":        ("google_compute_instance",),
    "compute.googleapis.com/Disk":            ("google_compute_disk",
                                               "google_compute_region_disk"),
    "compute.googleapis.com/Snapshot":        ("google_compute_snapshot",),
    "compute.googleapis.com/Image":           ("google_compute_image",),
    "compute.googleapis.com/ResourcePolicy":  ("google_compute_resource_policy",),
    "storage.googleapis.com/Bucket":          ("google_storage_bucket",),
    "bigquery.googleapis.com/Dataset":        ("google_bigquery_dataset",),
    "bigquery.googleapis.com/Table":          ("google_bigquery_table",),
    "iam.googleapis.com/Role":                ("google_project_iam_custom_role",),
    "iam.googleapis.com/ServiceAccount":      ("google_service_account",),
    "serviceusage.googleapis.com/Service":    ("google_project_service",),
    "logging.googleapis.com/LogSink":         ("google_logging_project_sink",),
    "logging.googleapis.com/LogBucket":       ("google_logging_project_bucket_config",),
}


# CAI full name の正規表現:  //<service>/projects/<proj>/[<scope>/<region>/]<kind>/<name>
# 例: //compute.googleapis.com/projects/X/regions/asia-northeast1/subnetworks/subnet-svc1
#     //storage.googleapis.com/<bucket-name>
#     //serviceusage.googleapis.com/projects/<num>/services/<api>
_CAI_NAME_RE = re.compile(
    r"^//(?P<service>[a-z0-9.\-]+)/(?P<tail>.+)$"
)


def parse_cai_resources(path: str) -> List[Dict[str, str]]:
    """CAI 出力 (YAML 風) をパースしてリソースリストを返す。

    各レコードは `---` で区切られ、`assetType:` `name:` 等のキーを 1 行に持つ。
    本パーサは PyYAML を使わず簡易行スキャンで対応する（CAI 出力は flat なため十分）。
    Returns:
        [{asset_type, name, short_name, location, project, display_name}, ...]
    """
    records: List[Dict[str, str]] = []
    if not os.path.isfile(path):
        return records

    current: Dict[str, str] = {}

    def _flush() -> None:
        if not current:
            return
        if current.get("asset_type"):
            full = current.get("name", "")
            current["short_name"] = full.rsplit("/", 1)[-1] if full else ""
            records.append(dict(current))
        current.clear()

    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            s = line.rstrip("\n")
            if s.strip() == "---":
                _flush()
                continue
            if not s or s.startswith(" ") or s.startswith("\t") or s.startswith("-"):
                continue
            if ":" not in s:
                continue
            k, v = s.split(":", 1)
            k = k.strip()
            v = v.strip()
            if k == "assetType":
                current["asset_type"] = v
            elif k == "name":
                current["name"] = v
            elif k == "location":
                current["location"] = v
            elif k == "project":
                current["project"] = v
            elif k == "displayName":
                current["display_name"] = v
    _flush()
    return records


# terraform .tf 内の resource ブロック検出。`name = "..."` 属性も拾う。
_TF_RESOURCE_RE = re.compile(
    r'resource\s+"([a-zA-Z0-9_]+)"\s+"([a-zA-Z0-9_\-]+)"\s*\{'
)
_TF_NAME_ATTR_RE = re.compile(r'^\s*name\s*=\s*"([^"]+)"\s*$', re.MULTILINE)


def parse_tf_resources(tf_dir: str) -> Dict[str, List[str]]:
    """terraform/<project>/*.tf から {resource_type: [name_attr, ...]} を抽出。

    name 属性が無いリソースは Terraform ラベル（2 番目の `"..."`) をフォールバック。
    bulk-export の慣行に従い 1 ファイル 1 リソース型を前提とせず、すべての .tf を走査。
    """
    out: Dict[str, List[str]] = {}
    if not os.path.isdir(tf_dir):
        return out
    for fn in sorted(os.listdir(tf_dir)):
        if not fn.endswith(".tf"):
            continue
        path = os.path.join(tf_dir, fn)
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        # ブロック単位に走査: 各 `resource "T" "L" {` の次の `}` までを本体とみなす
        for m in _TF_RESOURCE_RE.finditer(text):
            rtype, label = m.group(1), m.group(2)
            body_start = m.end()
            # ざっくり `}` の最初の出現を本体終端とする（bulk-export の出力は
            # ネスト浅く、name 属性は通常先頭近傍にあるため誤検出は限定的）
            body_end = text.find("\nresource ", body_start)
            body = text[body_start: body_end if body_end != -1 else len(text)]
            nm = _TF_NAME_ATTR_RE.search(body)
            name_val = nm.group(1) if nm else label
            out.setdefault(rtype, []).append(name_val)
    return out


def gcloud_recreate_command(
    asset_type: str, short_name: str, location: str,
    dst_project: str, full_name: str,
) -> List[str]:
    """欠落リソースを dst に作るための gcloud コマンド列を生成する。

    生成方針: dst 側で作成する `gcloud ... create` 系のみを返す（必要な値は
    <PLACEHOLDER> で示す）。src 側の describe / list など read 操作は DIFF.md の
    ノイズになるため含めない。完全な引数を網羅できない種別は再作成方針を示す
    コメント行のみを返す。短い 1 ライナを目指す。
    """
    src_proj = ""
    m = re.match(r"^//[^/]+/projects/([^/]+)/", full_name)
    if m:
        src_proj = m.group(1)

    loc = location or "global"
    sn = short_name or "<RESOURCE_NAME>"
    dst_flag = f"--project={dst_project}" if dst_project else "--project=<DST_PROJECT>"

    if asset_type == "compute.googleapis.com/Network":
        return [
            f"gcloud compute networks create {sn} {dst_flag} --subnet-mode=custom",
        ]
    if asset_type == "compute.googleapis.com/Subnetwork":
        return [
            f"gcloud compute networks subnets create {sn} {dst_flag} "
            f"--region={loc} --network=<NETWORK> --range=<CIDR>",
        ]
    if asset_type == "compute.googleapis.com/Firewall":
        return [
            f"gcloud compute firewall-rules create {sn} {dst_flag} "
            f"--network=<NETWORK> --direction=<INGRESS|EGRESS> --action=<ALLOW|DENY> "
            f"--rules=<PROTO:PORT,...>",
        ]
    if asset_type == "compute.googleapis.com/FirewallPolicy":
        return [
            f"gcloud compute network-firewall-policies create {sn} --global {dst_flag} "
            f"--description=<DESC>",
        ]
    if asset_type == "compute.googleapis.com/Router":
        return [
            f"gcloud compute routers create {sn} {dst_flag} --region={loc} "
            f"--network=<NETWORK> --asn=<ASN>",
        ]
    if asset_type == "compute.googleapis.com/Route":
        return [
            f"gcloud compute routes create {sn} {dst_flag} "
            f"--network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>",
        ]
    if asset_type == "compute.googleapis.com/Address":
        region_flag = f"--region={loc}" if loc and loc != "global" else "--global"
        return [
            f"gcloud compute addresses create {sn} {dst_flag} {region_flag}",
        ]
    if asset_type == "compute.googleapis.com/Instance":
        return [
            f"gcloud compute instances create {sn} {dst_flag} "
            f"--zone={loc} --machine-type=<MACHINE_TYPE> "
            f"--source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore) が担当",
        ]
    if asset_type == "compute.googleapis.com/Disk":
        return [
            f"gcloud compute disks create {sn} {dst_flag} "
            f"--zone={loc} --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore)",
        ]
    if asset_type == "compute.googleapis.com/Snapshot":
        return [
            f"# snapshot は src 側からの参照で復元する設計のため dst 作成は不要 "
            f"(Step 5 gce_restore が source-snapshot として直接使用)",
        ]
    if asset_type == "compute.googleapis.com/Image":
        return [
            f"# image は使用しない方針（snapshot 由来）。必要なら "
            f"gcloud compute images create {sn} {dst_flag} --source-snapshot=<SNAPSHOT>",
        ]
    if asset_type == "compute.googleapis.com/ResourcePolicy":
        return [
            f"gcloud compute resource-policies create snapshot-schedule {sn} {dst_flag} "
            f"--region={loc} --max-retention-days=<N> --daily-schedule --start-time=<HH:MM>",
        ]
    if asset_type == "storage.googleapis.com/Bucket":
        return [
            f"gcloud storage buckets create gs://<DST_BUCKET_NAME> "
            f"{dst_flag} --location={loc}  # 名前は rename_rules.gcs を適用すること",
        ]
    if asset_type == "bigquery.googleapis.com/Dataset":
        return [
            f"bq --project_id={dst_project or '<DST>'} mk --location={loc} "
            f"--dataset {dst_project or '<DST>'}:{sn}",
        ]
    if asset_type == "bigquery.googleapis.com/Table":
        # CAI の full name 形式: //bigquery.googleapis.com/projects/<p>/datasets/<d>/tables/<t>
        ds = ""
        mm = re.search(r"/datasets/([^/]+)/tables/([^/]+)$", full_name)
        if mm:
            ds, sn = mm.group(1), mm.group(2)
        return [
            f"bq --project_id={dst_project or '<DST>'} cp "
            f"{src_proj or '<SRC>'}:{ds or '<DATASET>'}.{sn} "
            f"{dst_project or '<DST>'}:{ds or '<DATASET>'}.{sn}  "
            f"# 通常は Step 6 (data_sync) が担当",
        ]
    if asset_type == "iam.googleapis.com/Role":
        # full name: //iam.googleapis.com/projects/<p>/roles/<roleId>
        return [
            f"gcloud iam roles create {sn} {dst_flag} "
            f"--title=<TITLE> --permissions=<PERM1,PERM2,...> --stage=GA",
        ]
    if asset_type == "iam.googleapis.com/ServiceAccount":
        # full name: //iam.googleapis.com/projects/<p>/serviceAccounts/<email>
        # short_name は email 全体。create の引数は accountId（email の @ より前）。
        account_id = sn.split("@", 1)[0] if "@" in sn else sn
        return [
            f"gcloud iam service-accounts create {account_id} {dst_flag} "
            f"--display-name=<DISPLAY_NAME>",
        ]
    if asset_type == "serviceusage.googleapis.com/Service":
        return [
            f"gcloud services enable {sn} {dst_flag}",
        ]
    if asset_type == "logging.googleapis.com/LogSink":
        return [
            f"gcloud logging sinks create {sn} <DESTINATION> {dst_flag} "
            f"--log-filter='<FILTER>'",
        ]
    if asset_type == "logging.googleapis.com/LogBucket":
        # full name: //logging.googleapis.com/projects/<p>/locations/<loc>/buckets/<id>
        return [
            f"gcloud logging buckets create {sn} --location={loc} {dst_flag} "
            f"--retention-days=<N>",
        ]
    # generic fallback
    return [
        f"# {asset_type} は自動補完対象外。手動でドキュメント参照のうえ dst で再作成してください。",
    ]


def analyze_cai_tf_diff(
    cai_path: str, tf_dirs: List[str],
    src_project: str, dst_project: str,
) -> Dict[str, Any]:
    """CAI と terraform 出力を突合し、欠落リソースとリカバリコマンドを返す。

    Args:
        cai_path:  cai_export/cai_resources_<src>.txt
        tf_dirs:   走査する terraform ディレクトリ群（raw 優先 / active fallback など）。
                   先頭から順に資料を統合し、いずれかに resource が見つかれば「カバー済み」。
        src_project: src プロジェクト ID（ログ表示用）
        dst_project: dst プロジェクト ID（生成コマンドに埋め込む）

    Returns:
        {
            'src_project': str,
            'dst_project': str,
            'cai_total':   int,
            'tf_total':    int,
            'covered':     int,
            'missing':     [ {asset_type, short_name, full_name, location,
                              tf_resource_type, coverage_step, reason, commands}, ...],
            'unknown_types': [str, ...],
        }
    """
    cai_records = parse_cai_resources(cai_path)
    tf_resources: Dict[str, set] = {}
    for d in tf_dirs:
        for rtype, names in parse_tf_resources(d).items():
            tf_resources.setdefault(rtype, set()).update(names)

    missing: List[Dict[str, Any]] = []
    unknown_types: set = set()
    covered = 0
    auto_handled = 0  # 専用ステップ複製 / 意図的対象外（DIFF.md からは除外、件数のみ）

    for r in cai_records:
        atype = r.get("asset_type", "")
        short = r.get("short_name", "")
        full = r.get("name", "")
        loc = r.get("location", "")
        coverage_step = _ASSET_COVERAGE.get(atype, "<unknown>")
        tf_types = _CAI_TO_TF_RESOURCE.get(atype, ())

        # tf 側で同名のリソースが見つかればカバー済み
        is_covered = False
        if tf_types:
            for t in tf_types:
                if short and short in tf_resources.get(t, set()):
                    is_covered = True
                    break

        if is_covered:
            covered += 1
            continue

        if atype not in _ASSET_COVERAGE:
            unknown_types.add(atype)
            reason = "_ASSET_COVERAGE に未登録（複製漏れの可能性）"
        elif coverage_step is None:
            reason = "意図的に対象外（マップで None 指定）"
        elif coverage_step == "bulk_export" or tf_types:
            reason = (
                f"bulk-export が出力しなかった (期待 TF 型: {'/'.join(tf_types) or '不明'})"
            )
        else:
            reason = f"別ステップ '{coverage_step}' が複製を担当（bulk-export 出力対象外）"

        # DIFF.md は手動で dst 作成/調整が要るものだけに絞る（量が多すぎるため）。
        # 専用ステップ複製分・意図的対象外は件数だけ数えてスキップする。
        if not _needs_manual_recreate(atype, coverage_step):
            auto_handled += 1
            continue

        missing.append({
            "asset_type": atype,
            "short_name": short,
            "full_name": full,
            "location": loc,
            "tf_resource_type": "/".join(tf_types) if tf_types else None,
            "coverage_step": coverage_step,
            "reason": reason,
            "commands": gcloud_recreate_command(atype, short, loc, dst_project, full),
        })

    return {
        "src_project": src_project,
        "dst_project": dst_project,
        "cai_total": len(cai_records),
        "tf_total": sum(len(v) for v in tf_resources.values()),
        "covered": covered,
        "auto_handled": auto_handled,
        "missing": missing,
        "unknown_types": sorted(unknown_types),
    }


def format_diff_report(reports: List[Dict[str, Any]]) -> str:
    """analyze_cai_tf_diff の結果群を Markdown レポートに整形する。

    DIFF.md と stdout の両方で同じテキストを使い、ログには `\n`.split() で行ごと書く。
    """
    lines: List[str] = []
    lines.append("# CAI ↔ Terraform bulk-export 差分レポート")
    lines.append("")
    lines.append("Cloud Asset Inventory（CAI）が観測した src 側リソースのうち、")
    lines.append("bulk-export / terraform で **自動再現されず、手動で dst 作成・調整が必要なもの** だけを")
    lines.append("プロジェクトごとに列挙し、dst 側に再現するための gcloud コマンドを併記します。")
    lines.append("（read 操作の describe / list は省き、作成系コマンドのみ掲載）")
    lines.append("")
    lines.append("掲載対象（要手動対応）:")
    lines.append("- 「未登録」: `_ASSET_COVERAGE` に無い assetType（複製漏れの可能性）。")
    lines.append("- 「bulk-export が出力しなかった」: terraform_apply 担当のはずが TF 出力に無い。")
    lines.append("")
    lines.append("非掲載（自動処理 / 対象外。件数のみ集計）:")
    lines.append("- 専用ステップ（Step 4.5 network_firewall / Step 5 gce_restore / Step 6 data_sync）が複製。")
    lines.append("- `_ASSET_COVERAGE` で None 指定の意図的対象外（実害なし）。")
    lines.append("")

    grand_total = 0
    for r in reports:
        sp = r["src_project"]
        dp = r["dst_project"] or "<未設定>"
        lines.append(f"## プロジェクト: `{sp}` → `{dp}`")
        lines.append("")
        lines.append(
            f"- CAI 検出リソース: **{r['cai_total']}** 件"
            f" / TF 出力リソース: **{r['tf_total']}** 件"
            f" / 一致: **{r['covered']}** 件"
            f" / 要手動対応: **{len(r['missing'])}** 件"
            f" / 自動処理・対象外: **{r.get('auto_handled', 0)}** 件"
        )
        if r["unknown_types"]:
            lines.append(
                f"- 未登録 assetType: " + ", ".join(f"`{t}`" for t in r["unknown_types"])
            )
        lines.append("")
        if not r["missing"]:
            lines.append("要手動対応の欠落なし。 ✓")
            lines.append("")
            continue

        # 種別ごとにグルーピングして読みやすくする
        by_type: Dict[str, List[Dict[str, Any]]] = {}
        for m in r["missing"]:
            by_type.setdefault(m["asset_type"], []).append(m)
        for atype in sorted(by_type.keys()):
            items = by_type[atype]
            lines.append(f"### `{atype}` （{len(items)} 件）")
            lines.append("")
            for m in items:
                grand_total += 1
                lines.append(
                    f"#### `{m['short_name']}` "
                    f"(location=`{m['location'] or 'global'}`)"
                )
                lines.append("")
                lines.append(f"- full name: `{m['full_name']}`")
                cov = m.get("coverage_step")
                cov_disp = cov if cov is not None else "意図的対象外 (None)"
                lines.append(f"- 担当ステップ: `{cov_disp}`")
                lines.append(f"- 期待 TF 型: `{m['tf_resource_type'] or 'なし'}`")
                lines.append(f"- 判定理由: {m['reason']}")
                lines.append("- 推奨コマンド:")
                lines.append("  ```bash")
                for c in m["commands"]:
                    lines.append(f"  {c}")
                lines.append("  ```")
                lines.append("")
    lines.append("---")
    lines.append(f"合計（要手動対応）: **{grand_total}** 件")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# SA プリフライト: 借用 SA に必要な代表権限（有効ステップごと）
# ---------------------------------------------------------------------------
# test-iam-permissions で検査する代表的な権限。全リソース種は網羅しないが、
# 「Viewer/Editor 相当のロールが付いていない」ケースを実行前に検出するための
# 最小セット。baseline は常に付与し、有効ステップ分を union する。
_SRC_BASELINE_PERMS = ("resourcemanager.projects.get",)
_SRC_PERMS_BY_STEP = {
    "cai_scan":         ("cloudasset.assets.searchAllResources",),
    "gce_snapshot":     ("compute.instances.list", "compute.snapshots.list"),
    "bulk_export":      ("cloudasset.assets.searchAllResources",
                         "compute.instances.list", "storage.buckets.list"),
    "network_firewall": ("compute.firewalls.list", "compute.networkFirewallPolicies.list"),
    "gce_restore":      ("compute.instances.list", "compute.snapshots.list"),
    "data_sync":        ("storage.buckets.list", "bigquery.datasets.get"),
}
_DST_BASELINE_PERMS = ("resourcemanager.projects.get",)
_DST_PERMS_BY_STEP = {
    "terraform_apply":  ("compute.instances.create", "storage.buckets.create"),
    "network_firewall": ("compute.firewalls.create", "compute.networkFirewallPolicies.create"),
    "gce_restore":      ("compute.instances.start", "compute.instances.stop",
                         "compute.disks.create", "compute.disks.delete",
                         "compute.instances.attachDisk", "compute.instances.detachDisk"),
    "data_sync":        ("storage.objects.create",
                         "bigquery.datasets.create", "bigquery.tables.create"),
}

# `*_impersonate_service_account` 未指定でローカル認証 (gcloud のアクティブ
# アカウント / ADC) を src に対して使う場合、現在の認証主体が src に書込相当の
# 権限を持っていないかを事前確認するための代表セット。Editor / Owner / 各種
# Admin ロール相当を検知できれば足りるため、リソース種を網羅する必要はない。
# is_src_read_only(cmd) の最終防衛線は別途あるが、認証主体の最小権限化を促す
# ための「実行前警告 + 続行確認」のトリガーとして使う。
_SRC_DANGEROUS_PERMS = (
    "resourcemanager.projects.setIamPolicy",
    "resourcemanager.projects.update",
    "resourcemanager.projects.delete",
    "compute.instances.create",
    "compute.instances.delete",
    "compute.instances.setMetadata",
    "compute.disks.create",
    "compute.disks.delete",
    "compute.networks.create",
    "compute.networks.delete",
    "compute.firewalls.create",
    "compute.firewalls.delete",
    "storage.buckets.create",
    "storage.buckets.delete",
    "storage.objects.create",
    "storage.objects.delete",
    "bigquery.datasets.create",
    "bigquery.datasets.delete",
    "bigquery.tables.create",
    "bigquery.tables.delete",
    "iam.serviceAccounts.create",
    "iam.serviceAccounts.delete",
    "iam.serviceAccountKeys.create",
    "serviceusage.services.enable",
    "serviceusage.services.disable",
)


def is_src_read_only(cmd: str) -> bool:
    """src 側に対して安全（read-only）なコマンドかを判定する。

    判定ロジック:
    - コマンド中に書き込み動詞（_WRITE_VERBS）が単語境界で出現したら NG。
      ただしフラグ値（例: `--format=value(creationTimestamp)`）に動詞が
      含まれるケースを誤検知しないよう、`--` で始まる引数は除外して判定する。
    """
    # `--xxx=yyy` および `--xxx yyy` の値部分は除外して動詞検査する
    tokens = []
    for tok in cmd.split():
        if tok.startswith("--"):
            continue
        if tok.startswith("-"):
            continue
        tokens.append(tok)
    body = " ".join(tokens)
    for verb in _WRITE_VERBS:
        if re.search(rf"\b{re.escape(verb)}\b", body):
            return False
    return True


def is_known_mock_command(cmd: str) -> bool:
    """コマンドが Mock 対応済みかを判定する。

    `bq --project_id=xxx cp ...` のようにツール名と動詞の間にフラグが入る場合も
    マッチさせるため、フラグ（`--` 始まり）を除いたトークン列でも判定する。
    """
    stripped = cmd.strip()
    if any(stripped.startswith(p) for p in _MOCK_KNOWN_PATTERNS):
        return True
    tokens = [t for t in stripped.split() if not t.startswith("--")]
    normalized = " ".join(tokens)
    return any(normalized.startswith(p) for p in _MOCK_KNOWN_PATTERNS)


# ---------------------------------------------------------------------------
# ログ
# ---------------------------------------------------------------------------
class _ThreadTagFilter(logging.Filter):
    """ログレコードに現在のスレッド名をタグとして付与する。

    並列実行時にどのスレッドのログかが追えるようにする。メインスレッドは "main"。
    """
    def filter(self, record: logging.LogRecord) -> bool:
        tname = threading.current_thread().name
        record.thread_tag = "main" if tname == "MainThread" else tname
        return True


def setup_run_dir(base_log_dir: str) -> str:
    """実行ごとの logs/<timestamp>/ ディレクトリを作って返す。"""
    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = os.path.join(base_log_dir, timestamp)
    os.makedirs(run_dir, exist_ok=True)
    return run_dir


def setup_logger(name: str, filepath: str, verbose: bool) -> logging.Logger:
    """日本語ログ用 logger を作成。verbose=True で DEBUG レベルも記録。"""
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.handlers.clear()
    logger.propagate = False
    logger.addFilter(_ThreadTagFilter())

    fmt = logging.Formatter(
        '[%(asctime)s] [%(levelname)s] [%(thread_tag)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )

    fh = logging.FileHandler(filepath, mode='w', encoding='utf-8')
    fh.setFormatter(fmt)
    fh.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.addHandler(fh)

    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    ch.setLevel(logging.INFO)  # コンソールは INFO 以上のみ（DEBUG はファイルだけに）
    logger.addHandler(ch)

    return logger


def log_stage_header(logger: logging.Logger, step_no: int, title: str, count: Optional[int] = None):
    bar = "━" * 60
    logger.info("")
    logger.info(bar)
    if count is not None:
        logger.info(f" ステップ {step_no}: {title}  （対象 {count} 件）")
    else:
        logger.info(f" ステップ {step_no}: {title}")
    logger.info(bar)


_ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')


def _first_meaningful_line(stderr: Optional[str], stdout: Optional[str], limit: int = 200) -> str:
    """サマリー用に、stderr/stdout から最も情報量の多い1行を抽出する。

    WARNING / impersonation 警告 / 装飾用の空行や枠線は飛ばし、
    "Error:" を含む行があれば優先する。なければ最初の非空行を返す。
    """
    for src in (stderr, stdout):
        if not src:
            continue
        text = _ANSI_RE.sub('', src)
        lines = [ln.strip(' │╷╵') for ln in text.splitlines() if ln.strip(' │╷╵')]
        # Error: を含む行を優先
        for ln in lines:
            if 'Error' in ln and 'WARNING' not in ln:
                return ln[:limit]
        # それ以外は WARNING / impersonation を除いた最初の行
        for ln in lines:
            if ln.upper().startswith('WARNING'):
                continue
            if 'impersonation' in ln.lower():
                continue
            return ln[:limit]
    return "(理由不明)"


# ---------------------------------------------------------------------------
# 統計（スレッドセーフ）
# ---------------------------------------------------------------------------
class StageStats:
    def __init__(self):
        self.lock = threading.Lock()
        self.executed = 0   # 実行成功（write 系を実際に実行した）
        self.read = 0       # 読み取り成功
        self.skipped = 0    # 既存のためスキップ
        self.failed = 0
        self.mocked = 0
        self.failures: List[tuple] = []  # [(desc, reason_one_line), ...]

    def incr(self, kind: str):
        with self.lock:
            if kind == "executed":
                self.executed += 1
            elif kind == "read":
                self.read += 1
            elif kind == "skipped":
                self.skipped += 1
            elif kind == "failed":
                self.failed += 1
            elif kind == "mocked":
                self.mocked += 1

    def add_failure(self, desc: str, reason: str):
        with self.lock:
            self.failures.append((desc or "(no desc)", reason or "(no reason)"))


# ---------------------------------------------------------------------------
# プロジェクト設定の妥当性検査（ORG 保護の最終防衛線）
# ---------------------------------------------------------------------------
def validate_config(config: Dict[str, Any]) -> List[str]:
    """config.yaml の整合性を厳格に検証し、エラー文字列のリストを返す（空なら安全）。

    検証項目:
    - project_mapping の存在
    - host_project と service_projects の src/dst が埋まっている
    - src と dst が同一でないこと
    - dst 側 ID が src 側 ID と重複していないこと（ORG を上書きしないため）
    - dst が複数の src にマップされていないこと

    `*_impersonate_service_account` の未指定は **ここではエラーにしない**。
    未指定の場合は CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT を設定せずに
    ローカル認証（gcloud にログイン中のユーザー / ADC）にフォールバックする。
    その場合 src プロジェクトに対する書き込み相当の権限を持っていないかを
    `check_service_accounts` が事前確認し、持っていれば実行前に警告 + 続行確認する。
    """
    errors: List[str] = []
    mapping = config.get('project_mapping')
    if not mapping or not isinstance(mapping, dict):
        return ["project_mapping が定義されていません"]

    entries = []
    host = mapping.get('host_project')
    if not isinstance(host, dict):
        errors.append("project_mapping.host_project が定義されていません")
    else:
        entries.append(("host_project", host))

    services = mapping.get('service_projects', [])
    if not isinstance(services, list) or len(services) == 0:
        errors.append("project_mapping.service_projects が空、または定義されていません")
    else:
        for i, svc in enumerate(services):
            entries.append((f"service_projects[{i}]", svc))

    src_ids = set()
    dst_to_src: Dict[str, str] = {}
    for label, ent in entries:
        if not isinstance(ent, dict):
            errors.append(f"{label}: 辞書ではありません")
            continue
        src = ent.get('src')
        dst = ent.get('dst')
        # impersonate SA は **必須にしない**。未指定はローカル認証フォールバックを許容し、
        # check_service_accounts 側で src 書込権の有無を確認 + 警告 + 続行確認する。
        if not src:
            errors.append(f"{label}: src が未指定")
        if not dst:
            errors.append(f"{label}: dst が未指定")
        if src and dst and src == dst:
            errors.append(f"{label}: src と dst が同一です ({src})。ORG への書き込みになります")
        if src:
            src_ids.add(src)
        if dst and src and dst in dst_to_src and dst_to_src[dst] != src:
            errors.append(f"重複: dst '{dst}' に複数の src がマップされています")
        if dst and src:
            dst_to_src[dst] = src

    # dst が src の ID と衝突していないか
    for label, ent in entries:
        if isinstance(ent, dict):
            dst = ent.get('dst')
            if dst and dst in src_ids:
                errors.append(f"{label}: dst '{dst}' は他の src と同じ ID です（ORG を上書きするリスク）")

    return errors


def validate_steps_config(config: Dict[str, Any]) -> List[str]:
    """有効化された各ステップの必須設定を検証し、エラー文字列のリストを返す（空なら安全）。

    `make plan` / `make run` の **実行前** に、「ステップが enabled なのに設定不足で
    必ず失敗する / 黙ってスキップされてしまう」状態を fail-fast で検出する。
    ORG 保護（`validate_config`）とは別レイヤの『設定不備』検査。

    方針: 自動補完で曖昧に握り潰さず、不足は明示エラーにして実行前に気付かせる。
    新しいステップの必須項目が増えたらここに追加する。
    """
    errors: List[str] = []
    steps = config.get('steps', {})
    if not isinstance(steps, dict):
        return ["steps が定義されていません（辞書である必要があります）"]

    def enabled(name: str) -> bool:
        s = steps.get(name, {})
        return isinstance(s, dict) and bool(s.get('enabled', False))

    def sval(d: Dict[str, Any], key: str) -> str:
        return str((d.get(key) if isinstance(d, dict) else '') or '').strip()

    # --- Step 7: VPC Service Controls ---
    # access-context-manager は --project を持たないため billing_project が無いと
    # ローカル gcloud config の無関係プロジェクトを quota に使い SERVICE_DISABLED で失敗する。
    # 自動補完しない方針なので、enabled なら 3 つとも明示必須。
    if enabled('vpc_sc'):
        vc = steps.get('vpc_sc', {})
        for key, desc in (
            ('access_policy', 'アクセスポリシー番号'),
            ('perimeter', 'ペリメタ名'),
            ('billing_project', 'quota/billing project（自動補完しない・明示必須）'),
        ):
            if not sval(vc, key):
                errors.append(
                    f"steps.vpc_sc.{key} が未設定です（{desc}）。"
                    f"vpc_sc.enabled=true では必須。不要なら steps.vpc_sc.enabled=false に"
                )

    # --- rename_rules.gcs（bulk_export / data_sync が依存）---
    # method 不正だと bucket リネームが no-op になり src と同名 → dst で名前衝突して
    # terraform apply / rsync が失敗する。suffix/prefix で value 空も同様に同名衝突。
    if enabled('bulk_export') or enabled('data_sync'):
        gcs = (config.get('rename_rules', {}) or {}).get('gcs', {}) or {}
        method = sval(gcs, 'method') or 'suffix'
        valid_methods = ('suffix', 'prefix', 'custom')
        if method not in valid_methods:
            errors.append(
                f"rename_rules.gcs.method='{method}' は不正です。"
                f"{list(valid_methods)} のいずれかにしてください"
            )
        elif method in ('suffix', 'prefix') and not sval(gcs, 'value'):
            errors.append(
                f"rename_rules.gcs.value が空です（method={method}）。"
                f"GCS バケット名が src と同名になり衝突します。固定文字列か 'auto' を指定"
            )

    # --- gce_snapshot.max_age_days ---
    if enabled('gce_snapshot'):
        mad = steps.get('gce_snapshot', {}).get('max_age_days', 30)
        try:
            ok = int(mad) > 0
        except (TypeError, ValueError):
            ok = False
        if not ok:
            errors.append(
                f"steps.gce_snapshot.max_age_days='{mad}' は正の整数にしてください"
            )

    return errors


# ---------------------------------------------------------------------------
# オーケストレータ本体
# ---------------------------------------------------------------------------
class MigrationOrchestrator:
    def __init__(
        self,
        config_path: str,
        dry_run_override: Optional[bool] = None,
        verbose_override: Optional[bool] = None,
        mock_override: Optional[bool] = None,
    ):
        self.config_path = config_path
        self.config: Dict[str, Any] = {}
        self.org_logger: Optional[logging.Logger] = None
        self.dst_logger: Optional[logging.Logger] = None
        self.dry_run = True
        self.verbose = True
        self.mock = False
        self.parallel_jobs = 1
        self.run_dir: str = ""
        self.dry_run_override = dry_run_override
        self.verbose_override = verbose_override
        self.mock_override = mock_override
        self.stats = StageStats()
        self.start_t = time.time()
        # src プロジェクト番号 → dst プロジェクト番号 の対応（customize で番号置換に使用）
        self.proj_num_map: Dict[str, str] = {}

    # ----- 設定 -----
    def load_config(self):
        if not os.path.exists(self.config_path):
            print(f"エラー: 設定ファイル {self.config_path} が見つかりません。", file=sys.stderr)
            sys.exit(1)
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                self.config = yaml.safe_load(f)
        except Exception as e:
            print(f"エラー: 設定ファイルの解析に失敗: {e}", file=sys.stderr)
            sys.exit(1)

        global_cfg = self.config.get('global', {})
        self.dry_run = global_cfg.get('dry_run', True)
        self.verbose = global_cfg.get('verbose_logging', True)
        self.mock = global_cfg.get('mock', False)
        self.parallel_jobs = max(1, int(global_cfg.get('parallel_jobs', 1)))

        if self.dry_run_override is not None:
            self.dry_run = self.dry_run_override
        if self.verbose_override is not None:
            self.verbose = self.verbose_override
        if self.mock_override is not None:
            self.mock = self.mock_override

        # 厳格バリデーション（ORG 保護の最終防衛線 + ステップ設定の不備チェック）。
        # make plan / make run の実行前に、ORG 上書きリスクと「enabled なのに設定不足で
        # 必ず失敗する」状態の両方を fail-fast で弾く。
        errors = validate_config(self.config)
        step_errors = validate_steps_config(self.config)
        if errors or step_errors:
            print("=" * 60, file=sys.stderr)
            print(" config.yaml にエラーがあります。処理を中止します:", file=sys.stderr)
            for e in errors:
                print(f"  - [ORG 保護] {e}", file=sys.stderr)
            for e in step_errors:
                print(f"  - [設定不備] {e}", file=sys.stderr)
            print("=" * 60, file=sys.stderr)
            sys.exit(1)

        # ログ初期化（実行ごとの dir を作る）
        base_log_dir = global_cfg.get('log_dir', './logs')
        self.run_dir = setup_run_dir(base_log_dir)
        org_log_name = global_cfg.get('org_log_file', 'org.log')
        dst_log_name = global_cfg.get('dst_log_file', 'dst.log')
        self.org_logger = setup_logger('org', os.path.join(self.run_dir, org_log_name), self.verbose)
        self.dst_logger = setup_logger('dst', os.path.join(self.run_dir, dst_log_name), self.verbose)

    # ----- 前提コンポーネントの確認 -----
    def check_prerequisites(self):
        """実行に必要な外部 CLI / コンポーネントの存在を確認する。

        有効化されているステップが必要とするツールだけを検査し、未インストールなら
        dry-run（make plan）を含め**実行前に**停止する。これにより、Step の途中で
        `not found` (exit 127) になって中途半端な状態になるのを防ぐ。
        Mock モードでは実コマンドを叩かないためスキップする。

        ステップ別の必須ツール:
        - gcloud          … src/dst を操作するほぼ全ステップ
        - config-connector … bulk_export (Step 3) の `gcloud ... bulk-export` が依存
        - terraform       … terraform_apply (Step 4)
        - bq              … data_sync (Step 6) の BigQuery 同期
        """
        if self.mock:
            self.org_logger.info("  [前提チェック] Mock モードのため外部コンポーネントのチェックをスキップ")
            return

        steps = self.config.get('steps', {})

        def enabled(name: str) -> bool:
            return steps.get(name, {}).get('enabled', False)

        gcloud_steps = ("cai_scan", "gce_snapshot", "bulk_export", "gce_restore", "data_sync", "vpc_sc")

        # (ツール名, 必要か, 不足時の説明)
        required = [
            (
                "gcloud",
                any(enabled(s) for s in gcloud_steps),
                "Google Cloud CLI。インストール: https://cloud.google.com/sdk/docs/install",
            ),
            (
                "config-connector",
                enabled("bulk_export"),
                "Config Connector。`gcloud beta resource-config bulk-export` に必須。"
                "インストール: `gcloud components install config-connector`",
            ),
            (
                "terraform",
                enabled("terraform_apply"),
                "Terraform CLI。インストール: https://developer.hashicorp.com/terraform/install",
            ),
            (
                "bq",
                enabled("data_sync"),
                "BigQuery CLI（Google Cloud CLI に同梱）。"
                "インストール: https://cloud.google.com/sdk/docs/install",
            ),
        ]

        missing: List[str] = []
        ok: List[str] = []
        for tool, needed, hint in required:
            if not needed:
                continue
            if shutil.which(tool) is None:
                missing.append(f"{tool} … {hint}")
            else:
                ok.append(tool)

        if missing:
            print("=" * 60, file=sys.stderr)
            print(" [前提チェック] 必須コンポーネントが見つかりません。処理を中止します:", file=sys.stderr)
            for m in missing:
                print(f"  - {m}", file=sys.stderr)
            print("=" * 60, file=sys.stderr)
            sys.exit(1)

        if ok:
            self.org_logger.info(f"  [前提チェック] OK: {', '.join(ok)}")

    # ----- 借用 SA の事前検証 -----
    def _sa_preflight_run(self, cmd: str) -> tuple:
        """SA プリフライト専用の read-only コマンド実行ラッパ。

        (returncode, stdout, stderr) を返す。run_command を使わない理由:
        - run_command は side="dst" を dry-run でスキップしてしまう（SA 検証は
          dry-run でも必ず実行したい）。
        - verbose 時に stdout をログ出力するため、print-access-token の
          アクセストークンが漏れてしまう。
        ここで実行するのは print-access-token のみで read-only。
        """
        try:
            res = subprocess.run(
                cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, env=os.environ.copy(),
            )
            return res.returncode, res.stdout, res.stderr
        except Exception as e:
            return 1, "", str(e)

    def _test_iam_permissions(self, token: str, project: str, perms: set):
        """resourcemanager の testIamPermissions REST を呼び、付与済み権限集合を返す。

        検証できなかった場合（対象 API 未有効・ネットワーク不通など）は None を返し、
        呼び出し側で「権限不足」とは区別して扱う（借用は確認済みのため警告に留める）。
        gcloud に projects:testIamPermissions の CLI が無いため REST を直接叩く。
        """
        import urllib.request
        import urllib.error
        url = (
            "https://cloudresourcemanager.googleapis.com/v1/"
            f"projects/{project}:testIamPermissions"
        )
        body = json.dumps({"permissions": sorted(perms)}).encode("utf-8")
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return set(data.get("permissions", []))
        except Exception:
            return None

    # ----- ローカル認証（ADC / gcloud アクティブアカウント）ヘルパ -----
    def _local_access_token(self) -> Optional[str]:
        """ローカル認証のアクセストークンを取得する（impersonation なし）。

        `subprocess` から `gcloud ...` を呼ぶ際、CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT を
        設定しなければ gcloud は **アクティブな gcloud アカウント** を使う。`gcloud auth
        print-access-token`（フラグ無し）はそのトークンを返すので、テスト権限の主体として
        同等に扱える。失敗時は None。
        """
        rc, out, _err = self._sa_preflight_run("gcloud auth print-access-token")
        if rc != 0 or not out.strip():
            return None
        return out.strip()

    def _local_active_account(self) -> Optional[str]:
        """`gcloud config get-value account` でアクティブアカウント名を返す。失敗時は None。"""
        rc, out, _err = self._sa_preflight_run(
            "gcloud config get-value account --quiet"
        )
        if rc != 0:
            return None
        acct = out.strip()
        return acct or None

    def _confirm_adc_src_write_or_abort(self, warnings: List[str]) -> None:
        """ローカル認証が src に書込権を持つ場合の警告 + 続行確認。

        - warnings が空ならノーオペ。
        - 環境変数 `COPY_ALL_ENV_AUTO_APPROVE=1` で確認スキップ（CI/非対話用）。
        - 非対話セッション（stdin が tty でない）かつ AUTO_APPROVE 未指定ならエラー終了。
        - 対話なら `[y/N]` を求め、y/yes 以外は中断。
        """
        if not warnings:
            return

        print("=" * 60, file=sys.stderr)
        print(
            " [SA事前チェック] 警告: 借用 SA 未指定のためローカル認証（gcloud / ADC）を使用します。",
            file=sys.stderr,
        )
        print(
            " 現在の認証主体は以下の src プロジェクトに対して書き込み相当の権限を持っています:",
            file=sys.stderr,
        )
        for w in warnings:
            print(f"  - {w}", file=sys.stderr)
        print(
            " ORG 保護: コマンド単位の書込動詞拒否ガード (is_src_read_only) は継続適用しますが、",
            file=sys.stderr,
        )
        print(
            " 認証主体の最小権限化（読み取り専用 SA の借用）を推奨します。",
            file=sys.stderr,
        )
        print(
            " 続行をスキップしたい場合は config の `*_impersonate_service_account` を設定してください。",
            file=sys.stderr,
        )
        print("=" * 60, file=sys.stderr)

        if os.environ.get("COPY_ALL_ENV_AUTO_APPROVE") == "1":
            self.org_logger.warning(
                "  [SA事前チェック] COPY_ALL_ENV_AUTO_APPROVE=1 により続行確認を自動承認"
            )
            return

        if not sys.stdin.isatty():
            print(
                " 非対話セッションのため自動続行できません。"
                " 続行する場合は環境変数 COPY_ALL_ENV_AUTO_APPROVE=1 を設定してください。",
                file=sys.stderr,
            )
            sys.exit(1)

        try:
            ans = input("続行しますか？ [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            ans = ""
        if ans not in ("y", "yes"):
            print(" [SA事前チェック] ユーザー操作により中断しました。", file=sys.stderr)
            sys.exit(1)
        self.org_logger.warning(
            "  [SA事前チェック] ユーザー承認によりローカル認証で続行します"
        )

    def check_service_accounts(self):
        """impersonate 対象 SA の実在性・借用可否・代表権限を実行前に検証する。

        - dry-run / 本番のどちらでも実行する（mock モードではスキップ）。
        - 各 SA についてアクセストークン発行を試み、SA の実在と実行ユーザーの
          Token Creator 権限（roles/iam.serviceAccountTokenCreator）を確認する。
          ここで失敗したら即停止（fail-fast）。
        - 続けて借用トークンで testIamPermissions(REST) を呼び、有効ステップが必要と
          する代表権限（src=読取 / dst=書込）の有無を確認する。権限が不足していたら
          即停止。ただし API 未有効等で「検証できなかった」場合は警告に留め継続する
          （借用は確認済みのため）。検証する権限は代表値であり全リソース種を網羅しない。
        - `*_impersonate_service_account` 未指定のプロジェクトはローカル認証
          （gcloud のアクティブアカウント / ADC）にフォールバックする。その場合:
          - src 側: 代表的な書込権 (_SRC_DANGEROUS_PERMS) を testIamPermissions で確認し、
            granted があれば警告を集めて最後に続行確認する（is_src_read_only ガードは別途継続）。
          - dst 側: 必要権限の不足を WARNING ログだけ出し、続行する（実書込は run 時に
            分かる / 借用未指定のフォールバックなので fail-fast にはしない）。
        """
        if self.mock:
            self.org_logger.info("  [SA事前チェック] Mock モードのため SA 検証をスキップ")
            return

        steps = self.config.get('steps', {})

        def enabled(name: str) -> bool:
            return steps.get(name, {}).get('enabled', False)

        def required_perms(perms_by_step: dict, baseline: tuple) -> set:
            perms = set(baseline)
            for step, plist in perms_by_step.items():
                if enabled(step):
                    perms.update(plist)
            return perms

        # 検証対象を 2 系統に分割: 借用 SA 検証用 と ローカル認証フォールバック用。
        impersonate_targets: List[Tuple[str, str, str, set]] = []
        adc_src_projects: List[str] = []
        adc_dst_projects: List[str] = []
        src_perms = required_perms(_SRC_PERMS_BY_STEP, _SRC_BASELINE_PERMS)
        dst_perms = required_perms(_DST_PERMS_BY_STEP, _DST_BASELINE_PERMS)
        for src_proj, dst_proj, src_sa, dst_sa in self._iter_project_pairs():
            if src_sa:
                impersonate_targets.append((src_sa, src_proj, "src", src_perms))
            else:
                adc_src_projects.append(src_proj)
            if dst_sa:
                impersonate_targets.append((dst_sa, dst_proj, "dst", dst_perms))
            else:
                adc_dst_projects.append(dst_proj)

        errors: List[str] = []
        checked_sas = set()
        adc_src_warnings: List[str] = []
        sa_lock = threading.Lock()

        def check_one(item):
            sa, project, side, perms = item
            label = f"{side} SA '{sa}' (project={project})"

            # 1) 実在＋借用可否（アクセストークン発行）。stdout=token はログに出さない。
            rc, token, err = self._sa_preflight_run(
                f"gcloud auth print-access-token --impersonate-service-account={sa}"
            )
            if rc != 0 or not token.strip():
                reason = err.strip().splitlines()[-1] if err.strip() else "原因不明"
                with sa_lock:
                    errors.append(
                        f"{label}: 借用（impersonate）できません。SA が存在しないか、"
                        f"実行ユーザーに roles/iam.serviceAccountTokenCreator がありません。"
                        f" 詳細: {reason[:300]}"
                    )
                return

            # 2) 権限（借用トークンで testIamPermissions REST を実行）。
            granted = self._test_iam_permissions(token.strip(), project, perms)
            if granted is None:
                self.org_logger.warning(
                    f"  [SA事前チェック] {label}: 権限を検証できませんでした"
                    f"（対象 API 未有効などの可能性）。借用は確認済みのため継続します。"
                )
                with sa_lock:
                    checked_sas.add(sa)
                return
            missing = sorted(perms - granted)
            if missing:
                with sa_lock:
                    errors.append(
                        f"{label}: 必要権限が不足しています: {', '.join(missing)}"
                    )
                return

            with sa_lock:
                checked_sas.add(sa)

        self._parallel_for_each(impersonate_targets, check_one, "sa-preflight")

        # --- ローカル認証フォールバック: src 書込権 + dst 必要権限の事前確認 ---
        adc_token: Optional[str] = None
        adc_account: Optional[str] = None
        if adc_src_projects or adc_dst_projects:
            adc_token = self._local_access_token()
            adc_account = self._local_active_account()
            label_account = adc_account or "(active account 不明)"
            self.org_logger.warning(
                f"  [SA事前チェック] 借用 SA 未指定のためローカル認証を使用します"
                f"（認証主体={label_account}）。"
                f" 対象: src={len(adc_src_projects)} / dst={len(adc_dst_projects)}"
            )
            if not adc_token:
                errors.append(
                    "借用 SA 未指定でローカル認証を試みましたが、"
                    "`gcloud auth print-access-token` でトークンを取得できませんでした。"
                    " `gcloud auth login` を実行するか、"
                    " `*_impersonate_service_account` を設定してください。"
                )
            else:
                # src 側: 代表的な書込権を持っていれば警告対象に追加
                for proj in sorted(set(adc_src_projects)):
                    granted = self._test_iam_permissions(
                        adc_token, proj, set(_SRC_DANGEROUS_PERMS)
                    )
                    if granted is None:
                        self.org_logger.info(
                            f"  [SA事前チェック] src '{proj}' のローカル認証権限を"
                            f"検証できませんでした（cloudresourcemanager API 未有効等の可能性）。"
                            f" 書込権チェックをスキップして継続します。"
                        )
                        continue
                    if granted:
                        adc_src_warnings.append(
                            f"src '{proj}' (認証主体={label_account}): "
                            f"{', '.join(sorted(granted))}"
                        )
                # dst 側: 必要権限が不足していても WARNING に留める（fail-fast しない）
                for proj in sorted(set(adc_dst_projects)):
                    granted = self._test_iam_permissions(adc_token, proj, set(dst_perms))
                    if granted is None:
                        self.org_logger.info(
                            f"  [SA事前チェック] dst '{proj}' のローカル認証権限を"
                            f"検証できませんでした（API 未有効等の可能性）。継続します。"
                        )
                        continue
                    missing = sorted(set(dst_perms) - granted)
                    if missing:
                        shown = ", ".join(missing[:8])
                        more = " ..." if len(missing) > 8 else ""
                        self.org_logger.warning(
                            f"  [SA事前チェック] dst '{proj}' でローカル認証主体に"
                            f"不足権限の可能性: {shown}{more}"
                            f"（認証主体={label_account}）"
                        )

        if errors:
            has_dst_impersonate_failure = any(
                ("dst SA" in e) and ("借用（impersonate）できません" in e) for e in errors
            )
            print("=" * 60, file=sys.stderr)
            print(" [SA事前チェック] サービスアカウントに問題があります。処理を中止します:", file=sys.stderr)
            for e in errors:
                print(f"  - {e}", file=sys.stderr)
            print("=" * 60, file=sys.stderr)
            if has_dst_impersonate_failure:
                print(" 対処: dst SA がまだ作成されていない可能性があります。", file=sys.stderr)
                print("   まず dry-run で内容確認:  make bootstrap-plan", file=sys.stderr)
                print("   実際に作成/付与:         make bootstrap", file=sys.stderr)
                print("   個別:  make bootstrap-dst-sa / bootstrap-cross-project / bootstrap-shared-vpc", file=sys.stderr)
                print("=" * 60, file=sys.stderr)
            sys.exit(1)

        # ローカル認証 src 書込権の警告 + 続行確認（warnings 空ならノーオペ）
        self._confirm_adc_src_write_or_abort(adc_src_warnings)

        adc_summary = ""
        if adc_src_projects or adc_dst_projects:
            adc_summary = (
                f" / ローカル認証フォールバック src={len(adc_src_projects)} "
                f"dst={len(adc_dst_projects)}"
            )
        self.org_logger.info(
            f"  [SA事前チェック] OK: 借用 SA {len(checked_sas)} 個を検証"
            f"{adc_summary}（検証は代表的な権限のみ）"
        )

    # ----- 安全に外部コマンドを実行 -----
    def run_command(
        self,
        cmd: str,
        side: str,
        logger: logging.Logger,
        desc: str = "",
        explanation: str = "",
        allow_fail: bool = False,
        cwd: Optional[str] = None,
        impersonate_sa: Optional[str] = None,
        retries: int = 0,
        expect_not_found_ok: bool = False,
    ) -> Optional[str]:
        """外部コマンドを安全に実行する。

        Args:
            side: "src" (ORG = read-only 必須) / "dst" (コピー先) / "local" (terraform 等)
            impersonate_sa: 借用 SA。未指定の場合はローカル認証（gcloud のアクティブ
                            アカウント / ADC）にフォールバックする。side="src" でも未指定可。
                            ただし src の書込権を持っていれば事前チェック側で警告 + 続行確認する。
            retries: 失敗時の追加リトライ回数（config-connector 等のフレーキー対策）。
                     リトライ中の失敗は失敗カウントに含めない。
            expect_not_found_ok: True の場合、stdout/stderr に "Not found" を含む失敗は
                                 「存在しない」を意味する正常系として扱い、ERROR/失敗カウントせず None を返す。
                                 bq show / gcloud ... describe 等の存在確認に使う。
        """
        tag = f"[{desc}] " if desc else ""

        # === ORG 保護 ===
        if side not in ("src", "dst", "local"):
            logger.error(f"{tag}[ORG 保護] 無効な side='{side}' です")
            sys.exit(1)

        # src 側はコマンド文字列レベルで書き込み動詞を必ず拒否する（最終防衛線）。
        # impersonate_sa の有無に関わらず、ここで弾く。
        if side == "src":
            if not is_src_read_only(cmd):
                logger.error(
                    f"{tag}[ORG 保護] src 操作で書き込み動詞が検出されたため拒否しました。"
                    f" コマンド: {cmd}"
                )
                sys.exit(1)

        # 説明（日本語の補足）
        if explanation:
            logger.info(f"{tag}[実行内容] {explanation}")

        env = os.environ.copy()
        if impersonate_sa:
            env['CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT'] = impersonate_sa
            tag = f"{tag}[借用SA: {impersonate_sa}] "

        # === Mock モード ===
        if self.mock:
            if not is_known_mock_command(cmd):
                logger.error(
                    f"{tag}[Mock] 未対応コマンドのため安全のため停止します: {cmd}"
                )
                sys.exit(1)
            mock_res = self._simulate_command(cmd, logger, tag)
            self.stats.incr("mocked")
            return mock_res

        # === Dry-run ===
        # side="src" は read-only と検証済なので dry-run でも実行してよい。
        # それ以外（dst/local の write 系）は dry-run ではスキップ。
        if self.dry_run and side != "src":
            logger.info(f"{tag}[DRY RUN] 予定: {cmd}  (cwd={cwd or '.'})")
            return ""

        if self.verbose:
            logger.info(f"{tag}実行: {cmd}  (cwd={cwd or '.'})")
        else:
            logger.info(f"{tag}実行中…")

        attempt = 0
        while True:
            try:
                result = subprocess.run(
                    cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, cwd=cwd, env=env,
                )
                if result.returncode != 0:
                    # まだリトライ余地があれば、失敗カウントせず再試行
                    if attempt < retries:
                        attempt += 1
                        logger.warning(
                            f"{tag}一時失敗 (exit={result.returncode})。再試行 {attempt}/{retries}"
                        )
                        time.sleep(min(5 * attempt, 30))
                        continue
                    combined = f"{result.stdout or ''}\n{result.stderr or ''}"
                    if expect_not_found_ok and "Not found" in combined:
                        logger.info(f"{tag}存在しません（Not Found）")
                        return None
                    logger.error(f"{tag}✗ 失敗 (exit={result.returncode})")
                    if result.stderr and result.stderr.strip():
                        logger.error(f"      理由(stderr): {result.stderr.strip()[:2000]}")
                    if result.stdout and result.stdout.strip():
                        logger.error(f"      理由(stdout): {result.stdout.strip()[:2000]}")
                    self.stats.incr("failed")
                    self.stats.add_failure(desc, _first_meaningful_line(result.stderr, result.stdout))
                    if not allow_fail:
                        sys.exit(result.returncode)
                    return None
                else:
                    if side == "src":
                        self.stats.incr("read")
                    else:
                        self.stats.incr("executed")
                    if result.stdout and self.verbose:
                        logger.debug(f"{tag}[STDOUT]\n{result.stdout.strip()}")
                    return result.stdout.strip()
            except Exception as e:
                if attempt < retries:
                    attempt += 1
                    logger.warning(f"{tag}例外発生: {e}。再試行 {attempt}/{retries}")
                    time.sleep(min(5 * attempt, 30))
                    continue
                logger.error(f"{tag}例外発生: {e}")
                self.stats.incr("failed")
                self.stats.add_failure(desc, f"例外: {e}")
                if not allow_fail:
                    sys.exit(1)
                return None

    # ----- Mock シミュレータ（fail-closed 化済み） -----
    def _simulate_command(self, cmd: str, logger: logging.Logger, tag: str) -> Optional[str]:
        from datetime import datetime, timezone

        proj_match = re.search(r'--project=([^\s]+)', cmd) or re.search(r'--project_id=([^\s]+)', cmd)
        proj_id = proj_match.group(1) if proj_match else "unknown-project"

        if cmd.strip().startswith("gcloud compute instances list"):
            logger.info(f"{tag}[MOCK] VM 一覧をシミュレート ({proj_id})")
            return json.dumps([
                {
                    "name": "org-svc1-deb-e2-mic-01",
                    "zone": f"projects/{proj_id}/zones/asia-northeast1-a",
                    "disks": [{"boot": True, "source": f"projects/{proj_id}/zones/asia-northeast1-a/disks/org-svc1-deb-e2-mic-01"}],
                },
                {
                    "name": "org-svc1-deb-n2-std2-02",
                    "zone": f"projects/{proj_id}/zones/asia-northeast1-b",
                    "disks": [{"boot": True, "source": f"projects/{proj_id}/zones/asia-northeast1-b/disks/org-svc1-deb-n2-std2-02"}],
                },
            ])

        if cmd.strip().startswith("gcloud compute snapshots list"):
            logger.info(f"{tag}[MOCK] スナップショット一覧をシミュレート ({proj_id})")
            now = datetime.now(timezone.utc).isoformat()
            return json.dumps([
                {"name": "migration-snap-org-svc1-deb-e2-mic-01",
                 "sourceDisk": f"projects/{proj_id}/zones/asia-northeast1-a/disks/org-svc1-deb-e2-mic-01",
                 "creationTimestamp": now},
                {"name": "migration-snap-org-svc1-deb-n2-std2-02",
                 "sourceDisk": f"projects/{proj_id}/zones/asia-northeast1-b/disks/org-svc1-deb-n2-std2-02",
                 "creationTimestamp": now},
            ])

        if cmd.strip().startswith("gcloud compute networks subnets list"):
            logger.info(f"{tag}[MOCK] サブネット一覧をシミュレート ({proj_id})")
            return json.dumps([
                {"name": "subnet-svc1", "region": f"projects/{proj_id}/regions/asia-northeast1",
                 "ipCidrRange": "10.100.1.0/24",
                 "network": f"projects/{proj_id}/global/networks/shared-vpc"},
            ])

        if cmd.strip().startswith("gcloud compute networks list"):
            logger.info(f"{tag}[MOCK] VPC 一覧をシミュレート ({proj_id})")
            return json.dumps([
                {"name": "default", "autoCreateSubnetworks": True},
                {"name": "shared-vpc", "autoCreateSubnetworks": False},
            ])

        if cmd.strip().startswith("gcloud compute firewall-rules list"):
            logger.info(f"{tag}[MOCK] ファイアウォールルール一覧をシミュレート ({proj_id})")
            return json.dumps([
                {"name": "allow-shared-iap-ssh", "direction": "INGRESS", "priority": 1000,
                 "network": f"projects/{proj_id}/global/networks/shared-vpc",
                 "allowed": [{"IPProtocol": "tcp", "ports": ["22"]}],
                 "sourceRanges": ["35.235.240.0/20"], "disabled": False},
                {"name": "all-for-incredibuild", "direction": "INGRESS", "priority": 1000,
                 "network": f"projects/{proj_id}/global/networks/shared-vpc",
                 "allowed": [{"IPProtocol": "all"}],
                 "sourceRanges": ["10.0.0.0/8"], "disabled": False},
                {"name": "ssh", "direction": "INGRESS", "priority": 1000,
                 "network": f"projects/{proj_id}/global/networks/shared-vpc",
                 "allowed": [{"IPProtocol": "tcp", "ports": ["22"]}],
                 "sourceRanges": ["10.0.0.0/8"], "disabled": False},
                {"name": "rdp", "direction": "INGRESS", "priority": 1000,
                 "network": f"projects/{proj_id}/global/networks/shared-vpc",
                 "allowed": [{"IPProtocol": "tcp", "ports": ["3389"]}],
                 "sourceRanges": ["0.0.0.0/0"], "disabled": False},
            ])

        if cmd.strip().startswith("gcloud compute network-firewall-policies list"):
            logger.info(f"{tag}[MOCK] ネットワークファイアウォールポリシー一覧をシミュレート ({proj_id})")
            # --global の場合のみダミー policy を 1 つ返し、regional は空にする
            if "--global" in cmd:
                return json.dumps([{"name": "shared-policy"}])
            return json.dumps([])

        if (
            cmd.strip().startswith("gcloud compute network-firewall-policies describe")
            and "--format=json" in cmd
        ):
            logger.info(f"{tag}[MOCK] ネットワークファイアウォールポリシー describe をシミュレート")
            # describe の出力に rules / associations を埋め込む（実 CLI と同じ構造）
            return json.dumps({
                "name": "shared-policy",
                "rules": [
                    {
                        "priority": 1000, "action": "allow", "direction": "INGRESS",
                        "match": {
                            "srcIpRanges": ["10.0.0.0/8"],
                            "layer4Configs": [
                                {"ipProtocol": "tcp", "ports": ["80", "443"]},
                                {"ipProtocol": "udp", "ports": ["53"]},
                            ],
                        },
                        "targetServiceAccounts": [
                            "app-sa@<SRC_HOST_PROJECT_ID>.iam.gserviceaccount.com",
                        ],
                        "enableLogging": True,
                        "description": "web ingress (mock)",
                    },
                ],
                "associations": [],
            })

        if cmd.strip().startswith("gcloud storage buckets list"):
            logger.info(f"{tag}[MOCK] バケット一覧をシミュレート ({proj_id})")
            return json.dumps([
                {"name": "org-bucket-shared-data", "location": "US"},
                {"name": "org-assets-bucket", "location": "US"},
            ])

        if cmd.strip().startswith("bq ls"):
            logger.info(f"{tag}[MOCK] BigQuery 一覧をシミュレート ({proj_id})")
            if "raw_logs" in cmd or "raw_dataset" in cmd:
                return json.dumps([
                    {"tableReference": {"tableId": "app_events"}},
                    {"tableReference": {"tableId": "user_logs"}},
                ])
            return json.dumps([
                {"datasetReference": {"datasetId": "raw_logs"}, "location": "asia-northeast1"},
                {"datasetReference": {"datasetId": "raw_dataset"}, "location": "asia-northeast1"},
            ])

        if cmd.strip().startswith("bq show"):
            logger.info(f"{tag}[MOCK] BigQuery show をシミュレート")
            return json.dumps({"location": "asia-northeast1"})

        # 残りのパターン（_MOCK_KNOWN_PATTERNS に含まれるもの）はすべて成功扱い
        logger.info(f"{tag}[MOCK] コマンド成功をシミュレート: {cmd.split()[0]} {cmd.split()[1] if len(cmd.split()) > 1 else ''}")
        return "Success"

    # ----- Mock 時のダミー TF ファイル書き出し -----
    def _write_dummy_tf_files(self, proj_dir: str, proj_id: str):
        self.org_logger.info(f"  [MOCK] ダミー TF を書き出し: {proj_dir}")
        vm_hcl = f"""
resource "google_compute_instance" "mock_vm" {{
  name         = "org-svc1-deb-e2-mic-01"
  project      = "{proj_id}"
  zone         = "asia-northeast1-a"
  boot_disk {{
    auto_delete = true
    device_name = "persistent-disk-0"
    initialize_params {{
      image = "debian-12"
    }}
    source = "https://www.googleapis.com/compute/v1/projects/{proj_id}/zones/asia-northeast1-a/disks/org-svc1-deb-e2-mic-01"
  }}
  network_interface {{
    network = "https://www.googleapis.com/compute/v1/projects/mock-host/global/networks/shared-vpc"
  }}
}}
"""
        bucket_hcl = f"""
resource "google_storage_bucket" "mock_bucket" {{
  name     = "org-bucket-shared-data"
  project  = "{proj_id}"
  location = "US"
}}
"""
        try:
            with open(os.path.join(proj_dir, "google_compute_instance.tf"), "w", encoding="utf-8") as f:
                f.write(vm_hcl)
            with open(os.path.join(proj_dir, "google_storage_bucket.tf"), "w", encoding="utf-8") as f:
                f.write(bucket_hcl)
        except Exception as e:
            self.org_logger.error(f"  [MOCK] ダミー TF 書き出し失敗: {e}")

    # ----- 実行制御 -----
    def execute(self):
        self.load_config()
        self.check_prerequisites()
        self.check_service_accounts()

        self.org_logger.info("=" * 60)
        self.org_logger.info(" copy-all-env  移行オーケストレータ  開始")
        self.org_logger.info("=" * 60)
        self.org_logger.info("【ORG 保護】src 操作は read-only に強制、書き込み動詞は実行前に拒否されます。")
        self.org_logger.info("           impersonate_sa は推奨（未指定はローカル認証フォールバック、")
        self.org_logger.info("           src 書込権ありなら事前確認）。")
        self.org_logger.info(f"  dry_run = {self.dry_run}")
        self.org_logger.info(f"  mock    = {self.mock}")
        self.org_logger.info(f"  verbose = {self.verbose}")
        self.org_logger.info(f"  parallel_jobs = {self.parallel_jobs}")
        self.org_logger.info(f"  ログ出力先 = {self.run_dir}")
        self.dst_logger.info("=" * 60)
        self.dst_logger.info(" copy-all-env  dst 側操作ログ  開始")
        self.dst_logger.info("=" * 60)

        steps = self.config.get('steps', {})
        try:
            if steps.get('cai_scan', {}).get('enabled', False):
                self.step_cai_scan()
            if steps.get('gce_snapshot', {}).get('enabled', False):
                self.step_gce_snapshot()
            if steps.get('bulk_export', {}).get('enabled', False):
                self.step_bulk_export()
            if steps.get('terraform_apply', {}).get('enabled', False):
                self.step_terraform_apply()
            if steps.get('network_firewall', {}).get('enabled', True):
                self.step_network_firewall()
            if steps.get('gce_restore', {}).get('enabled', False):
                self.step_gce_restore()
            if steps.get('data_sync', {}).get('enabled', False):
                self.step_data_sync()
            # VPC SC は最後。先に dst をペリメタへ封じ込めると後続の
            # terraform/gce/gcs/bq 操作が境界で弾かれる恐れがあるため、
            # 全移行が終わってから既存ペリメタへ dst プロジェクトを追加する。
            if steps.get('vpc_sc', {}).get('enabled', False):
                self.step_vpc_sc()
            # 最後に CAI vs bulk-export tf 差分レポートを出力。
            # cai_scan も bulk_export も dry_run 中（src 側 read-only）に
            # 実コマンドが走るため、`make plan` 直後の DIFF.md 生成に使える。
            if (
                steps.get('cai_scan', {}).get('enabled', False)
                and steps.get('bulk_export', {}).get('enabled', False)
            ):
                self._emit_cai_tf_diff()
        finally:
            self._print_summary()

    def _emit_cai_tf_diff(self):
        """CAI 出力と terraform/raw|active の .tf を突合し、欠落リソースを列挙する。

        各 src プロジェクトについて:
            1. cai_export/cai_resources_<src>.txt を analyze_cai_tf_diff() に渡し、
            2. 欠落リソース + 推奨 gcloud コマンドを log と DIFF.md（リポジトリ直下）に出力。
        log は org_logger（INFO） を経由するため stdout にも自動で流れる。
        """
        log_stage_header(self.org_logger, 99, "CAI ↔ TF 差分レポート", 0)
        cai_cfg = self.config.get('steps', {}).get('cai_scan', {})
        bulk_cfg = self.config.get('steps', {}).get('bulk_export', {})
        cai_dir = cai_cfg.get('output_dir', './cai_export')
        tf_base = bulk_cfg.get('output_dir', './terraform')
        proj_map = self._build_proj_id_map()

        reports: List[Dict[str, Any]] = []
        for src_proj, _sa in self._iter_src_projects():
            cai_path = os.path.join(cai_dir, f"cai_resources_{src_proj}.txt")
            if not os.path.isfile(cai_path):
                self.org_logger.warning(
                    f"  CAI 出力が無いためスキップ: {cai_path}"
                )
                continue
            # raw を優先（bulk-export 直後の生 HCL）→ active も補助参照
            tf_dirs = [
                os.path.join(tf_base, "raw", src_proj),
                os.path.join(tf_base, "active", src_proj),
            ]
            report = analyze_cai_tf_diff(
                cai_path=cai_path,
                tf_dirs=tf_dirs,
                src_project=src_proj,
                dst_project=proj_map.get(src_proj, ""),
            )
            reports.append(report)
            self.org_logger.info(
                f"  {src_proj}: CAI {report['cai_total']} 件 / "
                f"TF {report['tf_total']} 件 / 一致 {report['covered']} 件 / "
                f"要手動 {len(report['missing'])} 件 / "
                f"自動・対象外 {report.get('auto_handled', 0)} 件"
            )

        if not reports:
            self.org_logger.info("  解析対象なし（CAI 出力が見つかりません）。")
            return

        # 標準出力 / org.log にも詳細を流す
        text = format_diff_report(reports)
        for line in text.splitlines():
            self.org_logger.info(line)

        # DIFF.md をリポジトリ直下に出力（実行 cwd 基準）。
        # ファイル書き込みは dry_run でも実行する（src への書き込みは発生しない）。
        diff_path = os.path.abspath("DIFF.md")
        try:
            with open(diff_path, "w", encoding="utf-8") as f:
                f.write(text)
            self.org_logger.info(f"  ✓ 差分レポートを書き出しました: {diff_path}")
        except OSError as e:
            self.org_logger.error(f"  DIFF.md の書き出しに失敗: {e}")

    def _print_summary(self):
        elapsed = time.time() - self.start_t
        minutes = int(elapsed // 60)
        seconds = int(elapsed % 60)
        bar = "━" * 60
        for logger in (self.org_logger, self.dst_logger):
            if not logger:
                continue
            logger.info("")
            logger.info(bar)
            logger.info(" サマリー")
            logger.info(bar)
            logger.info(f"  実行時間 : {minutes}分{seconds}秒")
            logger.info(f"  読取成功 : {self.stats.read} 件")
            logger.info(f"  書込成功 : {self.stats.executed} 件")
            logger.info(f"  スキップ : {self.stats.skipped} 件")
            logger.info(f"  失敗     : {self.stats.failed} 件")
            logger.info(f"  Mock実行 : {self.stats.mocked} 件")
            logger.info(f"  ログ     : {self.run_dir}")
            if self.stats.failures:
                logger.info("  失敗詳細:")
                for desc, reason in self.stats.failures:
                    logger.info(f"    - [{desc}] {reason}")
            logger.info(bar)

    # ----- マッピングからプロジェクト一覧を作るヘルパ -----
    def _iter_src_projects(self):
        """(src_proj_id, src_sa) を順に返す。"""
        mapping = self.config.get('project_mapping', {})
        host = mapping.get('host_project', {})
        if host.get('src'):
            yield host['src'], host.get('src_impersonate_service_account')
        for svc in mapping.get('service_projects', []):
            if svc.get('src'):
                yield svc['src'], svc.get('src_impersonate_service_account')

    def _iter_project_pairs(self):
        """(src, dst, src_sa, dst_sa) を順に返す。"""
        mapping = self.config.get('project_mapping', {})
        host = mapping.get('host_project', {})
        if host.get('src') and host.get('dst'):
            yield (host['src'], host['dst'],
                   host.get('src_impersonate_service_account'),
                   host.get('dst_impersonate_service_account'))
        for svc in mapping.get('service_projects', []):
            if svc.get('src') and svc.get('dst'):
                yield (svc['src'], svc['dst'],
                       svc.get('src_impersonate_service_account'),
                       svc.get('dst_impersonate_service_account'))

    def _parallel_for_each(self, items: List[Any], worker, thread_prefix: str):
        """items の各要素に worker(item) を並列実行。parallel_jobs=1 のときは直列。"""
        if self.parallel_jobs <= 1 or len(items) <= 1:
            for item in items:
                worker(item)
            return
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=self.parallel_jobs, thread_name_prefix=thread_prefix
        ) as executor:
            futures = [executor.submit(worker, item) for item in items]
            for f in concurrent.futures.as_completed(futures):
                f.result()  # 例外はここで再 raise される

    # ============================================================
    # Step 1: CAI Scan
    # ============================================================
    def step_cai_scan(self):
        projects = list(self._iter_src_projects())
        log_stage_header(self.org_logger, 1, "CAI スキャン (src read-only)", len(projects))

        cai_cfg = self.config.get('steps', {}).get('cai_scan', {})
        output_dir = cai_cfg.get('output_dir', './cai_export')
        fail_on_uncovered = bool(cai_cfg.get('fail_on_uncovered', False))
        if not self.dry_run and not self.mock:
            os.makedirs(output_dir, exist_ok=True)

        def worker(item):
            proj_id, sa = item
            self.org_logger.info(f"  → src '{proj_id}' をスキャン")
            output_file = os.path.join(output_dir, f"cai_resources_{proj_id}.txt")
            cmd = f"gcloud asset search-all-resources --scope=projects/{proj_id} > {output_file}"
            self.run_command(
                cmd, side="src", logger=self.org_logger,
                desc=f"CAI {proj_id}",
                explanation=f"プロジェクト {proj_id} の全リソースを CAI で探索",
                impersonate_sa=sa, allow_fail=True,
            )

        self._parallel_for_each(projects, worker, "cai-scan")

        # === カバレッジ突合せ (ISSUE-01): 漏れの可視化 ============================
        # CAI 出力 (YAML 風テキスト) から assetType を抽出し、_ASSET_COVERAGE と突合。
        # 未登録 / None マッピングのアセットを WARNING で列挙する。
        self._report_cai_coverage(projects, output_dir, fail_on_uncovered)

        self.org_logger.info("  ✓ Step 1 完了")

    def _parse_cai_asset_types(self, output_dir: str, projects: List[Any]) -> Dict[str, int]:
        """CAI 出力テキストから assetType の出現回数を集計する。

        ファイル形式: `assetType: <full.type>` という行が各リソースごとに 1 行ある。
        mock/dry-run でファイルが無い場合は空 dict を返す（呼び出し側で no-op になる）。
        """
        counts: Dict[str, int] = {}
        for proj_id, _sa in projects:
            path = os.path.join(output_dir, f"cai_resources_{proj_id}.txt")
            if not os.path.isfile(path):
                continue
            try:
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        if line.startswith("assetType:"):
                            t = line.split(":", 1)[1].strip()
                            counts[t] = counts.get(t, 0) + 1
            except Exception as e:
                self.org_logger.warning(f"  CAI 出力の解析失敗 {path}: {e}")
        return counts

    def _report_cai_coverage(
        self, projects: List[Any], output_dir: str, fail_on_uncovered: bool,
    ) -> None:
        """assetType 集計 → _ASSET_COVERAGE と突合 → ログ出力。"""
        counts = self._parse_cai_asset_types(output_dir, projects)
        if not counts:
            self.org_logger.info("  [カバレッジ] CAI 出力が無いため突合せをスキップ")
            return

        uncovered, _ = diff_coverage(list(counts.keys()))
        intentionally_skipped = [
            t for t in counts if t in _ASSET_COVERAGE and _ASSET_COVERAGE[t] is None
        ]

        self.org_logger.info(
            f"  [カバレッジ] CAI 検出 {len(counts)} 種 / 既知 "
            f"{len(counts) - len(uncovered)} / 未登録 {len(uncovered)} / 意図的対象外 "
            f"{len(intentionally_skipped)}"
        )

        if uncovered:
            self.org_logger.warning(
                "  ⚠ 未登録の assetType（_ASSET_COVERAGE 追加が必要 - 複製漏れの可能性）:"
            )
            for t in uncovered:
                self.org_logger.warning(f"      - {t} ×{counts[t]}")

        if intentionally_skipped:
            self.org_logger.info("  ℹ 意図的に対象外（_ASSET_COVERAGE で None 指定）:")
            for t in sorted(intentionally_skipped):
                self.org_logger.info(f"      - {t} ×{counts[t]}")

        if uncovered and fail_on_uncovered:
            self.org_logger.error(
                f"  fail_on_uncovered=true のため未登録アセット {len(uncovered)} 種で停止"
            )
            sys.exit(1)

    # ============================================================
    # Step 2: GCE Snapshot 検証
    # ============================================================
    def step_gce_snapshot(self):
        projects = list(self._iter_src_projects())
        log_stage_header(self.org_logger, 2, "GCE スナップショット検証 (src read-only)", len(projects))

        max_age_days = self.config.get('steps', {}).get('gce_snapshot', {}).get('max_age_days', 30)
        errors: List[str] = []
        err_lock = threading.Lock()

        def worker(item):
            proj_id, sa = item
            self.org_logger.info(f"  → src '{proj_id}' の VM とスナップショットを照合")
            vm_json = self.run_command(
                f"gcloud compute instances list --project={proj_id} --format=json",
                side="src", logger=self.org_logger,
                desc=f"VM list {proj_id}",
                explanation=f"{proj_id} の VM 一覧を取得",
                impersonate_sa=sa, allow_fail=True,
            )
            if not vm_json:
                self.org_logger.info(f"    {proj_id}: VM が無いか取得失敗")
                return
            try:
                vms = json.loads(vm_json)
            except Exception as e:
                with err_lock:
                    errors.append(f"{proj_id}: VM JSON 解析失敗: {e}")
                return
            if not vms:
                return

            snap_json = self.run_command(
                f"gcloud compute snapshots list --project={proj_id} --format=json",
                side="src", logger=self.org_logger,
                desc=f"Snap list {proj_id}",
                explanation=f"{proj_id} のスナップショット一覧を取得",
                impersonate_sa=sa, allow_fail=True,
            )
            try:
                snapshots = json.loads(snap_json) if snap_json else []
            except Exception as e:
                with err_lock:
                    errors.append(f"{proj_id}: snapshot JSON 解析失敗: {e}")
                return

            for vm in vms:
                vm_name = vm.get('name')
                boot_disk = next((d for d in vm.get('disks', []) if d.get('boot')), None)
                if not boot_disk:
                    self.org_logger.warning(f"  ! VM {vm_name} には boot disk がない → スキップ")
                    continue
                disk_name = boot_disk.get('source', '').split('/')[-1]
                if self._has_valid_snapshot(snapshots, disk_name, max_age_days):
                    self.org_logger.info(f"    ✓ {vm_name}: 有効スナップショット OK")
                else:
                    with err_lock:
                        errors.append(
                            f"{proj_id} VM {vm_name}(disk={disk_name}): "
                            f"直近 {max_age_days} 日以内の有効スナップショットがない"
                        )

        self._parallel_for_each(projects, worker, "snap-check")

        if errors:
            self.org_logger.error("✗ 必要なスナップショットが揃っていません:")
            for e in errors:
                self.org_logger.error(f"    - {e}")
            self.org_logger.error("ORG 環境で手動でスナップショットを作成してから再実行してください。")
            sys.exit(1)
        self.org_logger.info("  ✓ Step 2 完了")

    def _has_valid_snapshot(self, snapshots: List[Dict], disk_name: str, max_age_days: int) -> bool:
        for snap in snapshots:
            snap_disk = snap.get('sourceDisk', '').split('/')[-1]
            if snap_disk != disk_name:
                continue
            ts = snap.get('creationTimestamp')
            if not ts:
                continue
            try:
                created = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
                age = datetime.datetime.now(datetime.timezone.utc) - created
                if age <= datetime.timedelta(days=max_age_days):
                    return True
            except Exception:
                pass
        return False

    # ============================================================
    # Step 3: Bulk Export + HCL Customize
    # ============================================================
    def step_bulk_export(self):
        projects = list(self._iter_src_projects())
        log_stage_header(self.org_logger, 3, "Terraform エクスポートと HCL カスタマイズ", len(projects))

        bulk_cfg = self.config.get('steps', {}).get('bulk_export', {})
        output_dir_base = bulk_cfg.get('output_dir', './terraform')
        raw_dir = os.path.join(output_dir_base, 'raw')
        active_dir = os.path.join(output_dir_base, 'active')

        # `make run` 時のみ skip。`make plan` (dry-run) と mock では従来どおり実行し、
        # active が無い・空の場合は安全側で実行する。
        if bulk_cfg.get('skip_on_run') and not self.dry_run and not self.mock:
            has_active = os.path.isdir(active_dir) and any(
                any(f.endswith('.tf') for f in os.listdir(os.path.join(active_dir, d)))
                for d in os.listdir(active_dir)
                if os.path.isdir(os.path.join(active_dir, d))
            )
            if has_active:
                # active/<src>/.dst_project と現 config の dst を突き合わせ、
                # dst プロジェクトが変わっていれば古い番号/ID 置換結果が残った
                # active を再利用するわけにはいかない（apply で 403 になる）。
                # その場合は raw が残っていれば bulk-export だけ省略し、
                # customize_hcl を必ず再実行する。
                proj_map = self._build_proj_id_map()
                stale_projects: List[str] = []
                for src_name in os.listdir(active_dir):
                    d = os.path.join(active_dir, src_name)
                    if not os.path.isdir(d):
                        continue
                    expected_dst = proj_map.get(src_name)
                    if not expected_dst:
                        continue
                    marker = os.path.join(d, ".dst_project")
                    cur = ""
                    if os.path.exists(marker):
                        try:
                            cur = open(marker, encoding="utf-8").read().strip()
                        except OSError:
                            cur = ""
                    if cur != expected_dst:
                        stale_projects.append(f"{src_name}({cur or '不明'}→{expected_dst})")

                if stale_projects:
                    self.org_logger.warning(
                        "  skip_on_run=true だが dst プロジェクトが変わっています: "
                        + ", ".join(stale_projects)
                    )
                    if os.path.isdir(raw_dir):
                        self.org_logger.info(
                            f"  raw を再利用し customize_hcl のみ再実行: {raw_dir}"
                        )
                        self._build_project_number_map()
                        self.customize_hcl(raw_dir, active_dir)
                        self.org_logger.info("  ✓ Step 3 完了（customize のみ再実行）")
                        return
                    self.org_logger.warning(
                        f"  raw も無いため bulk-export から通常実行: {raw_dir}"
                    )
                else:
                    self.org_logger.info(
                        f"  skip_on_run=true: bulk-export と customize をスキップし、既存 {active_dir} を再利用"
                    )
                    # apply 時に proj_num_map が空だと数字置換できないため、ここで構築する。
                    self._build_project_number_map()
                    self.org_logger.info("  ✓ Step 3 完了（スキップ）")
                    return
            else:
                self.org_logger.warning(
                    f"  skip_on_run=true だが {active_dir} に .tf が無いため、安全側で通常実行"
                )

        # raw 全体を作り直す。過去の mock ダミーや別 config の export 残骸
        # （現 config に無い孤児プロジェクト dir 等）が混ざり、customize/terraform
        # を汚すのを防ぐ。raw は毎回 bulk-export で再生成される派生物。
        # bulk-export は dry_run でも実コマンドが走るため（src は変更しない読み取り系）、
        # 出力先ディレクトリは dry_run でも先に作っておかないと
        # `--path` 先が存在せず "is not a directory" で失敗する。
        if not self.dry_run and os.path.isdir(raw_dir):
            self.org_logger.info(f"  既存の raw を作り直し: {raw_dir}")
            shutil.rmtree(raw_dir, ignore_errors=True)
        os.makedirs(raw_dir, exist_ok=True)
        os.makedirs(active_dir, exist_ok=True)

        def bulk_export_worker(item):
            proj_id, sa = item
            self.org_logger.info(f"  → src '{proj_id}' をエクスポート")
            proj_raw_dir = os.path.join(raw_dir, proj_id)
            os.makedirs(proj_raw_dir, exist_ok=True)

            if self.mock and not self.dry_run:
                self._write_dummy_tf_files(proj_raw_dir, proj_id)
                return
            # --quiet: bulk-export は対話プロンプト（continue? 等）を出すため、
            # 非対話の subprocess 実行で EOF 中断（exit 1）になるのを防ぐ。
            cmd = (
                f"gcloud beta resource-config bulk-export "
                f"--project={proj_id} --resource-format=terraform "
                f"--path={proj_raw_dir} --quiet"
            )
            self.run_command(
                cmd, side="src", logger=self.org_logger,
                desc=f"Bulk Export {proj_id}",
                explanation=f"{proj_id} のリソース定義を Terraform HCL としてエクスポート",
                impersonate_sa=sa, allow_fail=True,
                retries=3,  # config-connector は時々フレーキーに失敗するため再試行
            )

        self._parallel_for_each(projects, bulk_export_worker, "bulk-export")

        # プロジェクト番号マップを構築（customize で number 置換に使う）。
        # bulk-export は project = "<番号>" や "<番号>-compute@developer" を出力するが、
        # 番号は ID 置換では変わらないため、別途 src番号 → dst番号 に置換する必要がある。
        # （未置換だと google_project_service が src を操作したり、VM が src の
        #  既定 compute SA を参照して actAs 失敗になる）
        if not self.dry_run and not self.mock:
            self._build_project_number_map()

        self.customize_hcl(raw_dir, active_dir)
        self.org_logger.info("  ✓ Step 3 完了")

    def _get_project_number(self, project: str, impersonate_sa: Optional[str] = None) -> Optional[str]:
        """gcloud projects describe でプロジェクト番号を取得（read-only）。"""
        env = os.environ.copy()
        if impersonate_sa:
            env['CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT'] = impersonate_sa
        try:
            res = subprocess.run(
                f"gcloud projects describe {project} --format='value(projectNumber)'",
                shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, env=env,
            )
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
        except Exception:
            pass
        return None

    def _build_project_number_map(self):
        """src/dst の各プロジェクト番号を取得し proj_num_map(src番号→dst番号) を作る。

        describe は read-only。借用 SA 経由だと対象プロジェクトの CRM API 無効で
        失敗するため、実行ユーザー権限（terraform と同じ local 認証）で取得する。
        """
        self.proj_num_map = {}
        for src, dst, src_sa, dst_sa in self._iter_project_pairs():
            sn = self._get_project_number(src)
            dn = self._get_project_number(dst)
            if sn and dn:
                self.proj_num_map[sn] = dn
                self.org_logger.info(f"  プロジェクト番号マップ: {sn} → {dn} ({src} → {dst})")
            else:
                self.org_logger.warning(
                    f"  プロジェクト番号を取得できませんでした（{src}={sn} / {dst}={dn}）。"
                    f"番号置換をスキップします。"
                )

    def _build_proj_id_map(self) -> Dict[str, str]:
        """src プロジェクト ID → dst プロジェクト ID の対応を config から作る。"""
        mapping = self.config.get('project_mapping', {})
        proj_map: Dict[str, str] = {}
        host = mapping.get('host_project', {})
        if host.get('src') and host.get('dst'):
            proj_map[host['src']] = host['dst']
        for svc in mapping.get('service_projects', []):
            if svc.get('src') and svc.get('dst'):
                proj_map[svc['src']] = svc['dst']
        return proj_map

    def _build_dst_sa_map(self) -> Dict[str, str]:
        """src プロジェクト ID → dst impersonate SA メールアドレス の対応を作る。

        active/<src>/ ディレクトリに置く provider.tf の impersonate_service_account
        を解決するため、step_terraform_apply から参照する。
        """
        out: Dict[str, str] = {}
        for src, _dst, _src_sa, dst_sa in self._iter_project_pairs():
            if src and dst_sa:
                out[src] = dst_sa
        return out

    def _resolve_gcs_rename_value(self, rename_gcs: Dict) -> str:
        """rename_rules.gcs.value を解決する。

        value が 'auto'（大文字小文字無視）のときは日付ベースの一意な値を生成する。
        - suffix:  "-dst-MMDDHHMM"
        - prefix:  "dst-MMDDHHMM-"
        生成値は output_dir 配下の .gcs_rename_value に永続化し、`make plan` と
        `make run`、および skip_on_run で別プロセスにまたがっても **同じ値**を使う。
        ファイルが既にあればそれを再利用する（移行ごとに変えたい場合は削除する）。
        非 auto の場合は設定値をそのまま返す。
        """
        raw = (rename_gcs.get('value') or '')
        if raw.strip().lower() != 'auto':
            return raw
        method = rename_gcs.get('method', 'suffix')
        out_base = self.config.get('steps', {}).get('bulk_export', {}).get('output_dir', './terraform')
        marker = os.path.join(out_base, '.gcs_rename_value')

        # 既存の生成値があれば再利用（plan→run / skip_on_run の整合）。
        try:
            with open(marker, encoding='utf-8') as f:
                cached = f.read().strip()
            if cached:
                return cached
        except OSError:
            pass

        import datetime
        stamp = datetime.datetime.now().strftime('%m%d%H%M')
        token = f"dst-{stamp}"
        value = f"-{token}" if method == 'suffix' else f"{token}-"

        if not self.mock:
            try:
                os.makedirs(out_base, exist_ok=True)
                with open(marker, 'w', encoding='utf-8') as f:
                    f.write(value)
                self.org_logger.info(f"  GCS リネーム値を自動生成: '{value}'（{marker} に保存）")
            except OSError as e:
                self.org_logger.warning(f"  GCS リネーム値の保存に失敗（継続）: {e}")
        else:
            self.org_logger.info(f"  GCS リネーム値を自動生成（mock・非永続）: '{value}'")
        return value

    def _build_network_label_map(
        self, raw_dir: str, proj_map: Dict[str, str]
    ) -> Dict[tuple, str]:
        """raw 配下の全 .tf を走査し、google_compute_network を
        {(dst_project, network_name): resource_label} で返す。
        後段の URL → terraform 参照書き換えで使う。
        """
        result: Dict[tuple, str] = {}
        if not os.path.isdir(raw_dir):
            return result
        # resource "google_compute_network" "<label>" { ... project = "<p>" ... name = "<n>" ... }
        block_re = re.compile(
            r'resource\s+"google_compute_network"\s+"([A-Za-z_][\w-]*)"\s*\{([^}]*)\}',
            re.DOTALL,
        )
        for root, _dirs, files in os.walk(raw_dir):
            for file in files:
                if not file.endswith('.tf'):
                    continue
                try:
                    with open(os.path.join(root, file), encoding='utf-8') as f:
                        content = f.read()
                except OSError:
                    continue
                if 'google_compute_network' not in content:
                    continue
                for m in block_re.finditer(content):
                    label = m.group(1)
                    body = m.group(2)
                    pm = re.search(r'\bproject\s*=\s*"([^"]+)"', body)
                    nm = re.search(r'\bname\s*=\s*"([^"]+)"', body)
                    if not pm or not nm:
                        continue
                    src_proj = pm.group(1)
                    dst_proj = proj_map.get(src_proj, src_proj)
                    result[(dst_proj, nm.group(1))] = label
        return result

    def _rewrite_network_refs(self, content: str, net_map: Dict[tuple, str]) -> str:
        """同一プロジェクト内の network URL を terraform 参照 (self_link) に変換する。

        - ファイルが属する project を判定するため `project = "..."` を読む
          （customize の ID 置換後なので dst プロジェクト ID になっている）。
        - そのプロジェクト直下の `.../projects/<proj>/global/networks/<name>` 文字列を
          `google_compute_network.<label>.self_link` に置換する。
        - 他プロジェクトの network URL（クロスプロジェクト Shared VPC 参照など）は
          別 root module に居るためここでは触らない。
        """
        if 'networks/' not in content or 'google_compute_network' in content and 'resource ' in content:
            # 自プロジェクトの project = を取得（複数あっても同一前提）
            pass
        pm = re.search(r'\bproject\s*=\s*"([^"]+)"', content)
        if not pm:
            return content
        proj = pm.group(1)
        url_pat = re.compile(
            r'"https://www\.googleapis\.com/compute/v1/projects/'
            + re.escape(proj)
            + r'/global/networks/([A-Za-z0-9_.-]+)"'
        )

        def repl(m: re.Match) -> str:
            label = net_map.get((proj, m.group(1)))
            if not label:
                return m.group(0)
            return f"google_compute_network.{label}.self_link"

        return url_pat.sub(repl, content)

    # ----- HCL のカスタマイズ（バグ修正版） -----
    def customize_hcl(self, raw_dir: str, active_dir: str):
        self.org_logger.info(f"  HCL カスタマイズ: {raw_dir} → {active_dir}")

        proj_map = self._build_proj_id_map()
        # 同プロジェクト内 network 参照を terraform 参照に変えるためのマップ
        # {(dst_project, network_name): resource_label}
        net_label_map = self._build_network_label_map(raw_dir, proj_map)

        rename_gcs = self.config.get('rename_rules', {}).get('gcs', {})
        gcs_method = rename_gcs.get('method')
        gcs_val = self._resolve_gcs_rename_value(rename_gcs)
        gcs_overrides = rename_gcs.get('overrides', {}) or {}

        if not os.path.isdir(raw_dir):
            self.org_logger.warning(f"  raw_dir が存在しないため HCL カスタマイズをスキップ: {raw_dir}")
            return

        # active は raw から再生成する派生ディレクトリ。ただし terraform.tfstate /
        # .terraform / lock は作成済みリソースの記録なので保持し、再 apply を冪等にする
        # （state を消すと既存リソースが "already exists" で衝突する）。
        # - 現 export に無い孤児プロジェクト dir は丸ごと削除（別 config / mock 残骸対策）
        # - 既存プロジェクト dir は .tf のみ削除し、state 等は残す
        if not self.dry_run and os.path.isdir(active_dir):
            raw_projects = set(os.listdir(raw_dir))
            for name in os.listdir(active_dir):
                d = os.path.join(active_dir, name)
                if os.path.isdir(d):
                    if name not in raw_projects:
                        self.org_logger.info(f"  孤児プロジェクト dir を削除: {d}")
                        shutil.rmtree(d, ignore_errors=True)
                    else:
                        for r, _dirs, fs in os.walk(d):
                            for f in fs:
                                if f.endswith('.tf'):
                                    os.remove(os.path.join(r, f))
                else:
                    os.remove(d)

        for root, _, files in os.walk(raw_dir):
            for file in files:
                if not file.endswith('.tf'):
                    continue
                raw_path = os.path.join(root, file)
                rel = os.path.relpath(raw_path, raw_dir)
                # Terraform は 1 つのディレクトリ直下の .tf しか読まない（再帰しない）。
                # bulk-export はプロジェクト配下に深いサブディレクトリを作るため、
                # プロジェクトごとに 1 つの平坦な Terraform ルート（active/<project>/）へ
                # 集約する。サブパスは "__" で連結してファイル名衝突を防ぐ。
                parts = rel.split(os.sep)
                if len(parts) <= 1:
                    active_rel = rel
                else:
                    active_rel = os.path.join(parts[0], "__".join(parts[1:]))
                active_path = os.path.join(active_dir, active_rel)
                if not self.dry_run:
                    os.makedirs(os.path.dirname(active_path), exist_ok=True)

                self.org_logger.info(f"    処理中: {rel} → {active_rel}")
                if self.dry_run:
                    continue

                try:
                    with open(raw_path, 'r', encoding='utf-8') as f:
                        content = f.read()

                    # 1. プロジェクト ID 置換（単語境界で安全に置換）。
                    #    src ID を長い順に処理し、ある src ID が他 src ID の prefix
                    #    だった場合の連鎖置換を防ぐ。
                    for src in sorted(proj_map.keys(), key=len, reverse=True):
                        dst = proj_map[src]
                        content = re.sub(
                            rf'(?<![A-Za-z0-9_-]){re.escape(src)}(?![A-Za-z0-9_-])',
                            dst, content,
                        )

                    # 1.2. プロジェクト番号置換（src番号 → dst番号）。
                    #    project = "<番号>" や "<番号>-compute@developer" など、ID 置換
                    #    では変わらない番号参照を dst のものに置き換える。桁境界でマッチ。
                    for snum in sorted(self.proj_num_map.keys(), key=len, reverse=True):
                        content = re.sub(
                            rf'(?<!\d){re.escape(snum)}(?!\d)',
                            self.proj_num_map[snum], content,
                        )

                    # 1.5. Terraform のリソース/データ名（2 番目のラベル）を有効化。
                    #    bulk-export はプロジェクト番号由来で数字始まりのラベルを
                    #    生成することがあり、そのままでは構文エラーになる。
                    content = self._sanitize_resource_names(content, rel)

                    # 1.7. 同プロジェクト内の network URL を terraform 参照に変換。
                    #    bulk-export は network = "https://.../networks/<name>" の
                    #    ハードコード URL を出すため、firewall/subnetwork からの
                    #    暗黙の depends_on が効かず apply 順で network より先に
                    #    firewall を作ろうとして 404 になる。同 root module 内に
                    #    定義された google_compute_network を参照表記に書き換え、
                    #    Terraform に依存関係を理解させる。
                    content = self._rewrite_network_refs(content, net_label_map)

                    # 2. google_storage_bucket リソース「ブロック内」の name のみリネーム。
                    #    以前のバグ: ファイル単位で全 name を書き換えていたため VM/FW 等も
                    #    suffix が付与されてしまっていた。
                    content = self._rename_bucket_names_in_blocks(
                        content, gcs_method, gcs_val, gcs_overrides,
                    )

                    # 3. VM の boot_disk.source 行を削除（スナップショット復元前提）
                    content = self._strip_boot_disk_source(content, rel)

                    # 3.5. 予約 IP（compute address）の固定 IP 指定を外し自動採番に。
                    content = self._strip_reserved_ip(content)

                    # 3.6. 組織ポリシー（uniformBucketLevelAccess 強制）に合わせ、
                    #    バケットの uniform_bucket_level_access を true に統一。
                    content = self._enforce_uniform_bucket_access(content)

                    # 4. Google 管理のデフォルト SA など、Terraform で作成不能な
                    #    リソースはスキップ（account_id が GCP 命名規則違反のもの）。
                    skip_reason = self._skip_reason_for_file(content)
                    if skip_reason:
                        self.org_logger.info(f"      スキップ（{skip_reason}）: {active_rel}")
                        continue

                    # 5. シェル変数 ${VAR} / Terraform ディレクティブ %{...} が
                    #    起動スクリプト等の文字列に含まれると Terraform が補間として
                    #    誤解釈するためエスケープ（最後に適用）。
                    content = self._escape_interpolations(content)

                    with open(active_path, 'w', encoding='utf-8') as f:
                        f.write(content)
                except Exception as e:
                    self.org_logger.error(f"    HCL カスタマイズ失敗 {raw_path}: {e}")
                    sys.exit(1)

        # customize 済みの dst プロジェクト ID をプロジェクトごとに記録する。
        # 次回 make run の Step 3 skip_on_run チェックがこのマーカーを参照して、
        # 同じ dst のままなら customize を丸ごとスキップできるようにする。
        # 旧実装では _reset_stale_state_if_needed (Step 4) だけがこのマーカーを
        # 書いていたが、Step 4 は dry_run/mock では呼ばれないため `make plan` 後の
        # `make run` で毎回 customize が再実行される regression があった。
        # dry_run では .tf を実書き出ししない (上のループ内 `if self.dry_run: continue`)
        # ため、マーカーも更新しないこと (.tf と marker の整合を保つ)。
        if not self.dry_run and os.path.isdir(active_dir):
            for name in sorted(os.listdir(active_dir)):
                proj_dir = os.path.join(active_dir, name)
                if not os.path.isdir(proj_dir):
                    continue
                dst_for = proj_map.get(name)
                if not dst_for:
                    continue
                marker = os.path.join(proj_dir, ".dst_project")
                try:
                    with open(marker, "w", encoding="utf-8") as f:
                        f.write(dst_for)
                except OSError as e:
                    self.org_logger.warning(
                        f"  .dst_project マーカー書き出し失敗 {marker}: {e}"
                    )

    def _escape_interpolations(self, content: str) -> str:
        """Terraform が補間として解釈する `${...}` / `%{...}` をエスケープする。

        bulk-export が出力する起動スクリプト等の文字列にはシェル変数 `${VAR}` が
        そのまま含まれ、Terraform はこれを補間式として解釈してエラーになる。
        bulk-export 出力に正規の Terraform 補間は無い（すべてリテラル）ため、
        `${` → `$${`、`%{` → `%%{` に一括エスケープしてリテラル化する。
        既にエスケープ済み（`$${` / `%%{`）は二重エスケープしない。
        """
        content = re.sub(r'(?<!\$)\$\{', '$${', content)
        content = re.sub(r'(?<!%)%\{', '%%{', content)
        return content

    def _skip_reason_for_file(self, content: str) -> Optional[str]:
        """Terraform で作成不能 / 管理不能なリソースを含むファイルはスキップ理由を返す。

        bulk-export は「プロジェクト自体」や「自動生成される既定リソース」まで出力する。
        これらを dst に apply すると "already exists" や "forbidden" で失敗するため除外する。

        対象:
        - `google_project`            … dst プロジェクトは既存。再作成は不可。
        - `google_logging_project_sink` の `_Default` / `_Required`
                                       … 既定ログシンク。作成/更新できない（forbidden）。
        - `google_service_account` の account_id が GCP 命名規則
          `^[a-z]([-a-z0-9]*[a-z0-9])?$` に反するもの
                                       … プロジェクト番号始まりの Google 管理既定 SA。
        """
        # プロジェクト本体（google_project_service 等は別物なので閉じ引用符込みで判定）
        if 'resource "google_project"' in content:
            return "既存プロジェクト（google_project は作成不可）"

        if 'resource "google_logging_project_sink"' in content:
            m = re.search(r'\bname\s*=\s*"([^"]+)"', content)
            if m and m.group(1) in ("_Default", "_Required"):
                return f"既定ログシンク（{m.group(1)}）は変更不可"

        if 'resource "google_service_account"' in content:
            m = re.search(r'\baccount_id\s*=\s*"([^"]+)"', content)
            if m and not re.match(r'^[a-z]([-a-z0-9]*[a-z0-9])?$', m.group(1)):
                return f"作成不能なデフォルト SA: account_id={m.group(1)}"

        # VM とディスクは Step 5（スナップショット復元）が管理する。
        # terraform での create/replace は Step 5 の復元と衝突するためスキップ。
        # Step 4 は VPC/サービス/SA 等のインフラを担当し、VM 実体は Step 5 任せ。
        if 'resource "google_compute_instance"' in content:
            return "VM は Step5（スナップショット復元）が管理するため除外"
        if 'resource "google_compute_disk"' in content:
            return "disk は Step5（スナップショット復元）が管理するため除外"

        # BigQuery dataset / table は Step 6 (data_sync) が `bq mk` / `bq cp` で
        # 作る所有モデル（_ASSET_COVERAGE で data_sync 担当と宣言済み）。
        # terraform 側に残すと「dataset 未作成のまま table を作ろうとして 404」
        # （例: Not found: Dataset <proj>:<ds>）になる。
        if 'resource "google_bigquery_dataset"' in content:
            return "BQ dataset は Step6 (data_sync) が bq mk で作成するため除外"
        if 'resource "google_bigquery_table"' in content:
            return "BQ table は Step6 (data_sync) が bq cp で作成するため除外"

        # NAT 用に自動払い出しされる外部 IP は手動作成できない。
        if 'resource "google_compute_address"' in content and \
                re.search(r'\bpurpose\s*=\s*"NAT_AUTO"', content):
            return "NAT_AUTO アドレス（手動作成不可）"

        # クロスプロジェクト（共有 VPC ホスト）の subnet を参照する予約アドレスは
        # サービスプロジェクトからは作成できない（Cross-project references not allowed）。
        if 'resource "google_compute_address"' in content:
            pm = re.search(r'\bproject\s*=\s*"([^"]+)"', content)
            sm = re.search(r'\bsubnetwork\s*=\s*"[^"]*projects/([^/"]+)/', content)
            if pm and sm and pm.group(1) != sm.group(1):
                return f"クロスプロジェクト subnet 参照のアドレス（host={sm.group(1)}）は作成不可"

        # スナップショット / イメージは移行モデルでは terraform で作らない。
        # snapshot は dst に存在しないディスクを source にしており、image はその
        # snapshot 由来。VM ディスクは Step 5 で src スナップショットから復元する。
        if 'resource "google_compute_snapshot"' in content:
            return "snapshot は terraform 対象外（Step 5 で src から復元）"
        if 'resource "google_compute_image"' in content:
            return "image（snapshot 由来）は dst に存在せず作成不可"
        return None

    def _enforce_uniform_bucket_access(self, content: str) -> str:
        """google_storage_bucket に uniform_bucket_level_access = true を強制する。

        組織ポリシー constraints/storage.uniformBucketLevelAccess が有効な環境では、
        この指定が無い / false のバケット作成は弾かれるため統一する。
        """
        if 'resource "google_storage_bucket"' not in content:
            return content
        if re.search(r'\buniform_bucket_level_access\b', content):
            return re.sub(
                r'\buniform_bucket_level_access\s*=\s*\w+',
                'uniform_bucket_level_access = true', content,
            )
        return re.sub(
            r'(resource\s+"google_storage_bucket"\s+"[^"]+"\s*\{)',
            r'\1\n  uniform_bucket_level_access = true',
            content, count=1,
        )

    def _strip_reserved_ip(self, content: str) -> str:
        """google_compute_address / global_address の固定 IP 指定を外し自動採番にする。

        src の予約 IP（例: 34.x.x.x）は dst プロジェクトに割り当てられていないため、
        その IP のまま作成しようとすると "IP address is not allocated" で失敗する。
        `address = "<ip>"` 行を削除して GCP に新しい IP を採番させる（移行先で IP が
        変わるのは許容）。`address_type` 行は別物なので消さない。
        """
        if ('resource "google_compute_address"' not in content
                and 'resource "google_compute_global_address"' not in content):
            return content
        out: List[str] = []
        for line in content.split('\n'):
            if re.match(r'^\s*address\s*=\s*"[0-9A-Fa-f:.]+"\s*$', line):
                self.org_logger.info(f"      予約IP指定を削除し自動採番に変更: {line.strip()}")
                continue
            out.append(line)
        return '\n'.join(out)

    def _sanitize_resource_names(self, content: str, rel: str) -> str:
        """Terraform のリソース/データ名（block の 2 番目のラベル）を有効化する。

        Terraform では 2 番目のラベルは英字か `_` で始まる必要があるが、bulk-export は
        プロジェクト番号などから数字始まりのラベル（例: "1007606807581_compute"）を
        生成することがある。先頭が不正な場合は `_` を付与し、宣言・式中の参照・
        `# terraform import` コメントを揃えて書き換える。
        """
        decl_re = re.compile(r'^\s*(?:resource|data)\s+"[^"]+"\s+"([^"]+)"\s*\{')
        renames: Dict[str, str] = {}
        for line in content.split('\n'):
            m = decl_re.match(line)
            if m:
                label = m.group(1)
                if label and not re.match(r'[A-Za-z_]', label[0]):
                    renames[label] = '_' + label

        for old, new in renames.items():
            # 宣言の 2 番目ラベル "old" → "new"
            content = re.sub(rf'("\s+)"{re.escape(old)}"(\s*\{{)', rf'\1"{new}"\2', content)
            # 式中の参照および import コメントの .old → .new
            content = re.sub(rf'\.{re.escape(old)}\b', f'.{new}', content)
            self.org_logger.info(f"      リソース名を有効化: {old} → {new} ({rel})")
        return content

    def _rename_bucket_names_in_blocks(
        self, content: str, method: Optional[str], value: str, overrides: Dict[str, str]
    ) -> str:
        """`resource "google_storage_bucket" ...` ブロックの内側にある name 値だけを置換する。

        GCS バケット名はグローバル名前空間で一意。複数の src プロジェクトに同名
        バケット（例: org-bucket-shared-data）があると、共通サフィックスを付けても
        dst 側で同名になり 409 conflict になる。これを避けるため、ブロック内の
        project（ID 置換後＝dst プロジェクト）を読み取り、name に dst 固有トークンを
        混ぜて一意化する。override で明示された名前はそのまま尊重する。
        """
        lines = content.split('\n')
        out: List[str] = []
        block: List[str] = []
        in_bucket_block = False
        depth = 0
        bucket_block_re = re.compile(r'^\s*resource\s+"google_storage_bucket"')

        for line in lines:
            if not in_bucket_block:
                if bucket_block_re.search(line):
                    in_bucket_block = True
                    depth = line.count('{') - line.count('}')
                    block = [line]
                    if depth <= 0:
                        out.extend(self._rewrite_bucket_block(block, method, value, overrides))
                        in_bucket_block = False
                    continue
                out.append(line)
                continue

            # ブロック内
            block.append(line)
            depth += line.count('{') - line.count('}')
            if depth <= 0:
                out.extend(self._rewrite_bucket_block(block, method, value, overrides))
                in_bucket_block = False

        if in_bucket_block:  # 閉じ括弧が無い不正ブロックはそのまま出力
            out.extend(block)
        return '\n'.join(out)

    def _rewrite_bucket_block(
        self, block_lines: List[str], method: Optional[str],
        value: str, overrides: Dict[str, str],
    ) -> List[str]:
        """単一の google_storage_bucket ブロックの name 行を一意な dst 名に書き換える。"""
        text = '\n'.join(block_lines)
        pm = re.search(r'\bproject\s*=\s*"([^"]+)"', text)
        dst_proj = pm.group(1) if pm else ''
        name_re = re.compile(r'^(\s*name\s*=\s*)"([^"]+)"\s*$')
        out: List[str] = []
        for line in block_lines:
            m = name_re.match(line)
            if not m:
                out.append(line)
                continue
            prefix, orig_name = m.group(1), m.group(2)
            if orig_name in overrides:
                new_name = overrides[orig_name]
            else:
                if method == 'suffix':
                    base = f"{orig_name}{value}"
                elif method == 'prefix':
                    base = f"{value}{orig_name}"
                else:
                    base = orig_name
                new_name = self._uniquify_bucket_name(base, dst_proj)
            self.org_logger.info(f"      bucket リネーム: {orig_name} → {new_name}")
            out.append(f'{prefix}"{new_name}"')
        return out

    def _uniquify_bucket_name(self, base: str, dst_proj: str) -> str:
        """base に dst プロジェクト由来の短いハッシュを付けてグローバル一意にする。

        dst_proj が空（project 行が無い）の場合は base をそのまま返す。
        GCS バケット名は 63 文字以内のため、超過時は base を切り詰める。
        """
        if not dst_proj:
            return base
        import hashlib
        h = hashlib.sha1(dst_proj.encode('utf-8')).hexdigest()[:6]
        name = f"{base}-{h}"
        if len(name) > 63:
            keep = max(1, 63 - 1 - len(h))
            name = f"{base[:keep]}-{h}"
        return name

    def _strip_boot_disk_source(self, content: str, rel: str) -> str:
        if 'google_compute_instance' not in content or 'boot_disk' not in content:
            return content
        lines = content.split('\n')
        out: List[str] = []
        in_boot_disk = False
        depth = 0
        for line in lines:
            if 'boot_disk {' in line:
                in_boot_disk = True
                depth = 1
                out.append(line)
                continue
            if in_boot_disk:
                depth += line.count('{') - line.count('}')
                if depth <= 0:
                    in_boot_disk = False
                if 'source =' in line and '/disks/' in line:
                    self.org_logger.info(f"      boot_disk.source 行を削除: {rel}: {line.strip()}")
                    continue
            out.append(line)
        return '\n'.join(out)

    # ============================================================
    # Step 4: Terraform plan → apply
    # ============================================================
    def step_terraform_apply(self):
        log_stage_header(self.dst_logger, 4, "Terraform 適用 (plan → apply)")
        output_dir_base = self.config.get('steps', {}).get('bulk_export', {}).get('output_dir', './terraform')
        active_dir = os.path.join(output_dir_base, 'active')

        if not os.path.isdir(active_dir):
            if self.dry_run or self.mock:
                self.dst_logger.info(f"  active_dir が無いため計画のみログ表示: {active_dir}")
                self.dst_logger.info("  ✓ Step 4 完了")
                return
            self.dst_logger.error(f"  active_dir が無いため停止: {active_dir}")
            sys.exit(1)

        # Terraform はサブディレクトリを再帰しないため、active 直下ではなく
        # プロジェクトごとの平坦なルート（customize_hcl が active/<project>/ に集約）で
        # init → plan → apply を実行する。
        project_dirs = self._collect_terraform_roots(active_dir)
        if not project_dirs:
            msg = f"  適用対象の .tf を直下に持つプロジェクトディレクトリが {active_dir} にありません"
            if self.dry_run or self.mock:
                self.dst_logger.info(msg + "（dry-run/mock のためスキップ）")
                self.dst_logger.info("  ✓ Step 4 完了")
                return
            self.dst_logger.error(msg)
            sys.exit(1)

        proj_map = self._build_proj_id_map()
        sa_map = self._build_dst_sa_map()
        self.dst_logger.info(
            f"  対象 Terraform ルート: {len(project_dirs)} 件 (parallel_jobs={self.parallel_jobs})"
        )

        # 各 proj_dir は独立 workdir（terraform/active/<src>/）で state も分離されているため
        # 並列化しても干渉しない。dst プロジェクトもプロジェクトごとに異なるので API 競合も
        # 起こりにくい（Shared VPC の host 設定は本ステップでは作らない / Step 5 が担当）。
        def worker(proj_dir):
            self._terraform_one_project(proj_dir, proj_map, sa_map)

        self._parallel_for_each(project_dirs, worker, "tf-plan")
        self.dst_logger.info("  ✓ Step 4 完了")

    def _terraform_one_project(self, proj_dir: str, proj_map: Dict[str, str],
                                sa_map: Dict[str, Optional[str]]):
        self.dst_logger.info(f"  → Terraform ルート: {proj_dir}")
        # dst プロジェクトが前回と変わった（= 別環境への移行）場合、前回の
        # terraform.tfstate は旧プロジェクトのリソースを指したままで、import が
        # 「既に state にある」と誤判定し、plan で新プロジェクトへ再作成 → 既存と
        # 衝突（409）する。dst が変わっていれば state を破棄して import からやり直す。
        dst_proj = proj_map.get(os.path.basename(proj_dir))
        if not self.dry_run and not self.mock and dst_proj:
            self._reset_stale_state_if_needed(proj_dir, dst_proj)
            # google_project_service / data.google_project などは Cloud Resource
            # Manager / Service Usage / IAM API に依存する。dst プロジェクトでこれらが
            # 無効だと apply 中に「<api> has not been used in project ... before」の
            # 403 で止まる。bootstrap で漏れていても自動で復旧できるよう init 前に
            # 必ず有効化する（冪等）。
            self._ensure_dst_prereq_apis(dst_proj, sa_map.get(os.path.basename(proj_dir)))
        # bulk-export は provider 設定を生成しないため init は通るが import/plan で
        # "Invalid provider configuration" になる。dst プロジェクトと借用 SA を
        # 明示した provider.tf を毎回書き出して回避する（冪等）。
        if not self.mock and dst_proj:
            self._write_provider_tf(
                proj_dir, dst_proj, sa_map.get(os.path.basename(proj_dir))
            )
        self.run_command(
            "terraform init", side="local", logger=self.dst_logger,
            desc=f"TF Init {os.path.basename(proj_dir)}",
            explanation="Terraform 初期化",
            cwd=proj_dir,
        )
        if not self.dry_run and not self.mock:
            self._terraform_import_existing(proj_dir)
        self.run_command(
            "terraform plan -out=tfplan", side="local", logger=self.dst_logger,
            desc=f"TF Plan {os.path.basename(proj_dir)}",
            explanation="差分を tfplan に保存して内容を確認可能に",
            cwd=proj_dir,
        )
        self.dst_logger.info("    → tfplan を生成しました。dry_run でない場合のみ apply します。")
        if not self.dry_run:
            self.run_command(
                "terraform apply -auto-approve tfplan", side="local", logger=self.dst_logger,
                desc=f"TF Apply {os.path.basename(proj_dir)}",
                explanation="先ほど作成した tfplan を適用",
                cwd=proj_dir,
            )

    def _reset_stale_state_if_needed(self, proj_dir: str, dst_proj: str):
        """active/<src> の terraform state が現在の dst プロジェクト用でなければ破棄する。

        判定:
        - `.dst_project` マーカー != 現 dst → 旧 customize 用の残骸。stale。
        - マーカーが現 dst と一致していても、state 本文が現 dst を一切参照
          していなければ別 dst で apply された state とみなして stale。
          customize_hcl が plan 時にもマーカーを書く運用に変えた (Step 3
          skip_on_run の高速パス対応) ため、「マーカー一致 = state も新鮮」
          とは限らなくなった。state は apply でしか更新されないため
          state 本文を独立に検査する必要がある。

        stale なら terraform.tfstate（+backup）、.terraform、lock を削除し、
        import からクリーンにやり直せるようにする。最後にマーカーを現 dst で更新。
        """
        marker = os.path.join(proj_dir, ".dst_project")
        state = os.path.join(proj_dir, "terraform.tfstate")
        stale = False
        if os.path.exists(marker):
            try:
                if open(marker, encoding="utf-8").read().strip() != dst_proj:
                    stale = True
            except OSError:
                pass
        if not stale and os.path.exists(state):
            try:
                txt = open(state, encoding="utf-8").read()
            except OSError:
                txt = ""
            # リソースを持つ state なのに現 dst プロジェクトを一度も参照しない＝旧環境用。
            if '"resources"' in txt and len(txt) > 200 and dst_proj not in txt:
                stale = True

        if stale:
            self.dst_logger.warning(
                f"    stale state を検出（dst={dst_proj} 用ではない）。state を破棄して再取り込み: {proj_dir}"
            )
            for f in (state, state + ".backup", os.path.join(proj_dir, ".terraform.lock.hcl")):
                try:
                    os.remove(f)
                except OSError:
                    pass
            shutil.rmtree(os.path.join(proj_dir, ".terraform"), ignore_errors=True)

        try:
            with open(marker, "w", encoding="utf-8") as f:
                f.write(dst_proj)
        except OSError:
            pass

    def _ensure_dst_prereq_apis(self, dst_proj: str, dst_sa: Optional[str]):
        """terraform 実行前に dst プロジェクトの基盤 API を有効化する（冪等）。

        google_project_service / data.google_project 等が依存する API。
        既に有効なら gcloud は no-op で成功する。借用 SA を経由するため
        SA に serviceusage.services.enable 権限（roles/editor 等に含む）が必要。

        有効化直後は反映遅延（伝播）で terraform plan が
        「<API> has not been used in project ... before or it is disabled」と
        403 を返すことがある。enable 後に `services list --enabled` をポーリングし、
        必須 API が全部 enabled として見えるまで（最大 120 秒）待つ。
        """
        prereq = [
            "cloudresourcemanager.googleapis.com",
            "serviceusage.googleapis.com",
            "iam.googleapis.com",
            "iamcredentials.googleapis.com",
        ]
        self.run_command(
            f"gcloud services enable {' '.join(prereq)} --project={dst_proj}",
            side="dst", logger=self.dst_logger,
            desc=f"Prereq APIs {dst_proj}",
            explanation=f"{dst_proj} で Terraform 必須 API（CRM/ServiceUsage/IAM）を有効化",
            impersonate_sa=dst_sa, allow_fail=True,
        )
        self._wait_for_apis_enabled(dst_proj, dst_sa, prereq, timeout_sec=120, interval_sec=8)

    def _wait_for_apis_enabled(
        self, dst_proj: str, dst_sa: Optional[str],
        apis: List[str], timeout_sec: int = 120, interval_sec: int = 8,
    ):
        """`gcloud services list --enabled` をポーリングし、apis 全てが有効と見えるまで待機。

        gcloud services enable は有効化を要求するが、後続 API 呼び出しに反映するまで
        数秒〜数十秒のラグがある（Google 側エラーメッセージにも propagate の注記あり）。
        timeout を超えても見えない API は警告ログのみで続行（terraform 側のエラーで露見）。
        """
        env = os.environ.copy()
        if dst_sa:
            env['CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT'] = dst_sa
        deadline = time.monotonic() + timeout_sec
        need = set(apis)
        last_seen: set = set()
        while time.monotonic() < deadline:
            try:
                res = subprocess.run(
                    f"gcloud services list --enabled --project={dst_proj} "
                    f"--format='value(config.name)'",
                    shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, env=env, timeout=30,
                )
            except Exception:
                time.sleep(interval_sec)
                continue
            if res.returncode == 0:
                enabled = {line.strip() for line in res.stdout.splitlines() if line.strip()}
                last_seen = enabled
                missing = need - enabled
                if not missing:
                    self.dst_logger.info(
                        f"    必須 API は全て有効化済み（{dst_proj}）"
                    )
                    return
            time.sleep(interval_sec)
        missing = need - last_seen
        if missing:
            self.dst_logger.warning(
                f"    必須 API が timeout 内に有効化を確認できませんでした: "
                f"{sorted(missing)}（plan で失敗する場合は数分待って再実行）"
            )

    def _write_provider_tf(self, proj_dir: str, dst_proj: str, dst_sa: Optional[str]):
        """active/<src>/ に provider.tf を書き出して認証/プロジェクトを明示する。

        bulk-export 出力には provider 設定が無いため、ADC 未設定環境では plan が
        "Invalid provider configuration" / "Application Default Credentials" で落ちる。
        dst プロジェクト ID と借用 SA を入れた provider ブロックを毎回書き出して
        冪等にする（ファイル名は他の .tf より先に読まれる "_provider.tf"）。
        """
        lines = [
            'provider "google" {',
            f'  project = "{dst_proj}"',
        ]
        if dst_sa:
            lines.append(f'  impersonate_service_account = "{dst_sa}"')
        lines.append("}")
        path = os.path.join(proj_dir, "_provider.tf")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        except OSError as e:
            self.dst_logger.warning(f"    provider.tf 書込失敗: {path}: {e}")

    def _terraform_import_existing(self, proj_dir: str):
        """apply 前に既存リソースを terraform state へ取り込み、再実行を冪等にする。

        bulk-export は各 .tf に `# terraform import <addr> <id>` コメントを残すため、
        それを使って「dst に既に存在するリソース」を adopt する。既に state にある／
        実在しないリソースの import 失敗は無視（best-effort）。
        - 一部リソースは comment の id 形式が古い/不正なので補正する:
          * google_project_iam_custom_role は `proj##role` → `projects/proj/roles/role`
        - google_storage_bucket は名前変更しているため comment の id は使わず、.tf 本体の
          実際の `name`（リネーム後）を import id にして adopt する。これにより前回 run で
          作成済みのバケットも state に取り込まれ、再 apply が冪等になる。
        """
        import glob
        pairs: List[tuple] = []
        for tf in sorted(glob.glob(os.path.join(proj_dir, "*.tf"))):
            try:
                with open(tf, encoding="utf-8") as f:
                    content = f.read()
            except OSError:
                continue
            cm = re.search(r'#\s*terraform import\s+(\S+)\s+(.+?)\s*$', content, re.MULTILINE)
            if not cm:
                continue
            addr, imp_id = cm.group(1), cm.group(2)
            if addr.startswith("google_storage_bucket."):
                # リネーム後の実名を本体から取得して import id にする。
                nm = re.search(r'\bname\s*=\s*"([^"]+)"', content)
                if not nm:
                    continue
                imp_id = nm.group(1)
            pairs.append((addr, imp_id))
        if not pairs:
            return
        self.dst_logger.info(f"    既存リソースの取り込みを試行: {len(pairs)} 件")
        imported = 0
        skipped_already = 0
        failed: List[tuple] = []
        for addr, imp_id in pairs:
            if addr.startswith("google_project_iam_custom_role.") and "##" in imp_id:
                proj, role = imp_id.split("##", 1)
                imp_id = f"projects/{proj}/roles/{role}"
            rc, _o, err = self._sa_preflight_run(
                f"terraform -chdir={proj_dir} import -input=false -lock=false "
                f"'{addr}' '{imp_id}'"
            )
            if rc == 0:
                imported += 1
                continue
            # "Resource already managed by Terraform" は state に既にあるだけなので無視
            low = (err or "").lower()
            if "already managed by terraform" in low or "resource address" in low and "already" in low:
                skipped_already += 1
                continue
            # 存在しないリソースの import は無視（dst にまだ無いだけ＝apply で作成）
            if "cannot import non-existent remote object" in low or "status code: 404" in low:
                continue
            failed.append((addr, imp_id, (err or "").strip().splitlines()[-1] if err else ""))
        self.dst_logger.info(
            f"    取り込み結果: 成功={imported} 件 / 既存スキップ={skipped_already} 件 / 失敗={len(failed)} 件"
        )
        for addr, imp_id, last in failed[:10]:
            self.dst_logger.warning(f"      import 失敗: {addr} ← {imp_id} : {last}")

    def _collect_terraform_roots(self, active_dir: str) -> List[str]:
        """active 配下で「直下に .tf を持つ」プロジェクトディレクトリを列挙して返す。

        customize_hcl により各プロジェクトの .tf は active/<project>/ 直下へ平坦化
        されている前提。直下に .tf が無いディレクトリ（中間階層のみ）は除外する。
        """
        roots: List[str] = []
        for name in sorted(os.listdir(active_dir)):
            d = os.path.join(active_dir, name)
            if not os.path.isdir(d):
                continue
            try:
                if any(f.endswith('.tf') for f in os.listdir(d)):
                    roots.append(d)
            except OSError:
                continue
        return roots

    # ============================================================
    # Step 5: GCE VM 復元
    # ============================================================
    def step_network_firewall(self):
        """Step 4.5: host project のファイアウォールルール・ポリシーを dst に冪等複製。

        bulk-export は google_compute_network_firewall_policy を出力しない。
        classic firewall rule も Shared VPC ネットワーク URL が src を向くため
        terraform では適用困難。本ステップで gcloud を直接使い冪等に複製する。

        前提: dst host の Shared VPC ネットワーク (例: shared-vpc) が存在していること。
        FW rule / FW policy association は --network=<NAME> を要求するため、
        dst host に VPC が無いと "Could not fetch resource" で失敗する。
        以前は _replicate_host_networks() が step_gce_restore (Step 5) でのみ呼ばれて
        いたため、Step 4.5 が先に走って全 FW 操作が失敗していた (regression)。
        本ステップ冒頭で先に呼ぶことで解消。_replicate_host_networks は冪等
        (_gcloud_exists ガード) なので Step 5 で再度呼ばれてもコストは describe のみ。
        """
        log_stage_header(self.dst_logger, 45, "Network Firewall 複製 (rules + policies)")

        host = self.config.get('project_mapping', {}).get('host_project', {})
        src_host = host.get('src')
        dst_host = host.get('dst')
        src_sa = host.get('src_impersonate_service_account')
        dst_sa = host.get('dst_impersonate_service_account')

        if not src_host or not dst_host:
            self.dst_logger.warning("  host_project が未設定のため network_firewall をスキップ")
            return

        # dst host の VPC topology を Step 4.5 で先に用意する (bug fix)。
        # Step 5 (gce_restore) でも同じ呼び出しがあるが冪等なので問題ない。
        self._replicate_host_networks()
        self.dst_logger.info(
            f"  [Network] dst host {dst_host} VPC topology ready — FW 同期へ進む"
        )

        self._sync_classic_firewall_rules(src_host, dst_host, src_sa, dst_sa)
        self._sync_network_firewall_policies(src_host, dst_host, src_sa, dst_sa)
        self.dst_logger.info("  ✓ Step 4.5 完了")

    def _sync_classic_firewall_rules(
        self, src_host: str, dst_host: str,
        src_sa: Optional[str], dst_sa: Optional[str],
    ):
        """src host の classic VPC ファイアウォールルールを dst host に冪等コピー。"""
        self.dst_logger.info(f"  [FW Rules] {src_host} → {dst_host}")

        raw = self.run_command(
            f"gcloud compute firewall-rules list --project={src_host} --format=json",
            side="src", logger=self.org_logger,
            desc=f"List FW Rules {src_host}",
            explanation=f"{src_host} のファイアウォールルール一覧取得",
            impersonate_sa=src_sa, allow_fail=True,
        )
        try:
            rules = json.loads(raw) if raw else []
        except Exception:
            rules = []

        # Pre-flight: 参照される dst ネットワークが本当に存在するか一度だけ確認する。
        # _replicate_host_networks() は Step 4.5 冒頭で呼ばれているはずだが、何らかの
        # 理由 (権限不足 / 部分失敗 / config の host_project ミスマッチ) で未作成だと
        # rule 毎に "Could not fetch resource" が量産されるため、ここで一括 skip+WARN
        # に倒す。dry_run/mock では _gcloud_exists が常に False を返すため、確認は
        # スキップして全 rule の create パスを通す (今までの plan 挙動を維持)。
        missing_dst_nets: set = set()
        if not (self.dry_run or self.mock):
            referenced_nets = {
                (r.get('network') or '').split('/')[-1] or 'default'
                for r in rules if r.get('name')
            }
            for net_name in sorted(referenced_nets):
                if not self._gcloud_exists(
                    f"gcloud compute networks describe {net_name} --project={dst_host} "
                    f"--format='value(name)'",
                    dst_sa,
                ):
                    missing_dst_nets.add(net_name)
            if missing_dst_nets:
                self.dst_logger.warning(
                    f"  [FW Rules] dst host {dst_host} に未存在の network: "
                    f"{sorted(missing_dst_nets)} — 参照する FW rule はスキップします "
                    f"(bootstrap_shared_vpc.sh / _replicate_host_networks の結果を確認)"
                )

        def rule_worker(rule):
            name = rule.get('name', '')
            if not name:
                return

            if self._gcloud_exists(
                f"gcloud compute firewall-rules describe {name} --project={dst_host} "
                f"--format='value(name)'",
                dst_sa,
            ):
                self.dst_logger.info(f"    FW rule '{name}' は既存。スキップ")
                return

            # ネットワーク名を src → dst に置換（URL 末尾から取得）
            net_url = rule.get('network', '')
            net_name = net_url.split('/')[-1] if net_url else 'default'

            if net_name in missing_dst_nets:
                self.dst_logger.warning(
                    f"    FW rule '{name}': dst network '{net_name}' が "
                    f"{dst_host} に未存在のためスキップ"
                )
                return

            direction = rule.get('direction', 'INGRESS')
            priority = rule.get('priority', 1000)
            disabled = rule.get('disabled', False)

            allowed = rule.get('allowed', [])
            denied = rule.get('denied', [])
            action_flag, proto_list = self._fw_action_and_rules(allowed, denied)
            if not action_flag:
                self.dst_logger.warning(f"    FW rule '{name}': allowed/denied が空のためスキップ")
                return

            cmd = (
                f"gcloud compute firewall-rules create {name} "
                f"--project={dst_host} "
                f"--network={net_name} "
                f"--direction={direction} "
                f"--priority={priority} "
                f"{action_flag}={proto_list}"
            )
            if disabled:
                cmd += " --disabled"

            for field, flag in [
                ('sourceRanges',        '--source-ranges'),
                ('destinationRanges',   '--destination-ranges'),
                ('sourceTags',          '--source-tags'),
                ('targetTags',          '--target-tags'),
                ('sourceServiceAccounts', '--source-service-accounts'),
                ('targetServiceAccounts', '--target-service-accounts'),
            ]:
                val = rule.get(field)
                if val:
                    cmd += f" {flag}={','.join(val)}"

            desc = rule.get('description', '')
            if desc:
                cmd += f" --description={desc!r}"

            self.run_command(
                cmd, side="dst", logger=self.dst_logger,
                desc=f"Create FW Rule {name}",
                explanation=f"dst host {dst_host} にファイアウォールルール '{name}' を作成",
                impersonate_sa=dst_sa, allow_fail=True,
            )

        # 各 FW ルールは独立して create 可能（gcloud は個別 API 呼び出し）。
        # 並列化でスループット向上。
        self._parallel_for_each(rules, rule_worker, f"fw-rule-{dst_host}")

    @staticmethod
    def _fw_action_and_rules(allowed: list, denied: list) -> tuple:
        """allowed/denied リストから (--allow/--deny フラグ名, ルール文字列) を返す。"""
        entries = allowed if allowed else denied
        flag = '--allow' if allowed else '--deny'
        parts = []
        for e in entries:
            proto = e.get('IPProtocol', 'all')
            ports = e.get('ports', [])
            if ports:
                for p in ports:
                    parts.append(f"{proto}:{p}")
            else:
                parts.append(proto)
        return (flag, ','.join(parts)) if parts else ('', '')

    def _sync_network_firewall_policies(
        self, src_host: str, dst_host: str,
        src_sa: Optional[str], dst_sa: Optional[str],
    ):
        """src host のネットワークファイアウォールポリシーを dst host に冪等コピー。

        ISSUE-02 で改善した点:
        - global と各 region 両方の policy を対象 (regional FW policy 対応)
        - layer4Configs の複数ポート / 複数プロトコルを正しく展開
        - disabled / enableLogging / description / target SA / secure tags を保持
        - association の存在判定を list ベースに変更 (describe では取れないため)
        """
        self.dst_logger.info(f"  [FW Policies] {src_host} → {dst_host}")
        proj_map = self._build_proj_id_map()

        # global + region 両スコープを巡回する。region 検出は src の subnet 一覧から推定。
        # gcloud のフラグ仕様がサブコマンドごとに異なる:
        #   - `list`              : `--regions=R1,R2,...`（複数形）
        #   - `describe`/`create` : `--region=R`（単数, ポリシー scope）
        #   - `rules ...` / `associations create`
        #                         : `--firewall-policy-region=R` / `--global-firewall-policy`
        # ここでは前者 2 種を tuple で持ち、3 番目は fw_rule_scope_flag() で変換する。
        scopes: List[Tuple[str, str, str]] = [("--global", "--global", "global")]
        for region in sorted(self._discover_src_regions(src_host, src_sa)):
            scopes.append((f"--regions={region}", f"--region={region}", region))

        for list_scope_flag, scope_flag, scope_label in scopes:
            raw = self.run_command(
                f"gcloud compute network-firewall-policies list "
                f"--project={src_host} {list_scope_flag} --format=json",
                side="src", logger=self.org_logger,
                desc=f"List FW Policies {src_host} ({scope_label})",
                explanation=f"{src_host} の FW ポリシー一覧取得 (scope={scope_label})",
                impersonate_sa=src_sa, allow_fail=True,
            )
            try:
                policies = json.loads(raw) if raw else []
            except Exception:
                policies = []

            if not policies:
                self.dst_logger.info(f"    {scope_label}: ポリシー無し")
                continue

            for policy in policies:
                self._sync_one_fw_policy(
                    policy, scope_flag, scope_label,
                    src_host, dst_host, src_sa, dst_sa, proj_map,
                )

    def _discover_src_regions(self, src_host: str, src_sa: Optional[str]) -> List[str]:
        """src host が利用している region を subnet 一覧から推定する。"""
        raw = self.run_command(
            f"gcloud compute networks subnets list --project={src_host} --format=json",
            side="src", logger=self.org_logger,
            desc=f"List Subnets {src_host} (region discover)",
            explanation=f"{src_host} のサブネット一覧から region を抽出",
            impersonate_sa=src_sa, allow_fail=True,
        )
        try:
            subs = json.loads(raw) if raw else []
        except Exception:
            subs = []
        return list({(s.get('region') or '').split('/')[-1] for s in subs if s.get('region')})

    def _sync_one_fw_policy(
        self, policy: Dict[str, Any], scope_flag: str, scope_label: str,
        src_host: str, dst_host: str,
        src_sa: Optional[str], dst_sa: Optional[str],
        proj_map: Dict[str, str],
    ):
        pname = policy.get('name', '')
        if not pname:
            return

        if not self._gcloud_exists(
            f"gcloud compute network-firewall-policies describe {pname} "
            f"--project={dst_host} {scope_flag} --format='value(name)'",
            dst_sa,
        ):
            self.run_command(
                f"gcloud compute network-firewall-policies create {pname} "
                f"--project={dst_host} {scope_flag} --quiet",
                side="dst", logger=self.dst_logger,
                desc=f"Create FW Policy {pname} ({scope_label})",
                explanation=f"dst host {dst_host} にポリシー '{pname}' を作成 (scope={scope_label})",
                impersonate_sa=dst_sa, allow_fail=True,
            )
        else:
            self.dst_logger.info(f"    FW policy '{pname}' ({scope_label}) は既存。ルールのみ同期")

        self._sync_fw_policy_rules(
            pname, scope_flag, scope_label,
            src_host, dst_host, src_sa, dst_sa, proj_map,
        )
        self._sync_fw_policy_associations(
            pname, scope_flag, scope_label,
            src_host, dst_host, src_sa, dst_sa, proj_map,
        )

    def _fw_secure_tag_map(self) -> Dict[str, str]:
        """config の steps.network_firewall.secure_tag_map を返す（未設定なら空）。

        src の secure tag (`tagValues/<id>`) は ORG スコープの permanent ID で
        dst ORG には存在しないため、dst の値へ変換するための map。
        """
        m = (
            self.config.get('steps', {})
            .get('network_firewall', {})
            .get('secure_tag_map', {})
        )
        return m if isinstance(m, dict) else {}

    def _sync_fw_policy_rules(
        self, pname: str, scope_flag: str, scope_label: str,
        src_host: str, dst_host: str,
        src_sa: Optional[str], dst_sa: Optional[str],
        proj_map: Dict[str, str],
    ):
        """ポリシー pname のルールを並列に作成する。

        各ルールは異なる priority を持ち、独立して create できる（gcloud は
        rule の priority 単位で個別 API 呼び出し）。`_parallel_for_each` で
        スループットを稼ぐ（既存/未マップタグのスキップ判定も並列実行で OK）。
        """
        # `network-firewall-policies rules list` というサブコマンドは存在しない
        # （gcloud 公式 CLI に未実装）。ルール一覧は `describe --format=json` の
        # `rules` フィールドから取得する。
        policy_raw = self.run_command(
            f"gcloud compute network-firewall-policies describe {pname} "
            f"--project={src_host} {scope_flag} --format=json",
            side="src", logger=self.org_logger,
            desc=f"Describe FW Policy {pname} ({scope_label}) [rules]",
            explanation=f"ポリシー '{pname}' の describe からルール一覧を抽出",
            impersonate_sa=src_sa, allow_fail=True,
        )
        policy_obj = _parse_gcloud_describe_json(policy_raw)
        fw_rules = policy_obj.get('rules') or []

        rule_scope_flag = fw_rule_scope_flag(scope_flag)
        secure_tag_map = self._fw_secure_tag_map()

        def rule_worker(r):
            prio = r.get('priority')
            action = r.get('action', 'allow')
            direction = r.get('direction', 'INGRESS')
            if prio is None:
                return

            # secure tag は ORG スコープの permanent ID で別 ORG には存在しない。
            # map 未定義のタグを参照するルールはエラーになる（Could not fetch resource）。
            # FW を意図せず緩めないよう、create を試行せずスキップして警告する。
            unmapped = [t for t in fw_policy_rule_secure_tags(r) if t not in secure_tag_map]
            if unmapped:
                self.dst_logger.warning(
                    f"      ポリシールール {pname}/{prio} は dst ORG に存在しない "
                    f"secure tag {unmapped} を参照するためスキップ。dst で同等タグを作成し "
                    f"config の steps.network_firewall.secure_tag_map に "
                    f"'<src tagValues/...>: <dst tagValues/...>' を追加すると複製されます。"
                )
                return

            if self._gcloud_exists(
                f"gcloud compute network-firewall-policies rules describe {prio} "
                f"--firewall-policy={pname} --project={dst_host} {rule_scope_flag} "
                f"--format='value(priority)'",
                dst_sa,
            ):
                self.dst_logger.info(
                    f"      ポリシールール {pname}/{prio} は既存。スキップ"
                )
                return

            layer4 = fw_policy_rule_layer4(r)
            extra_flags = fw_policy_rule_flags(r, proj_map, secure_tag_map)

            rule_cmd = (
                f"gcloud compute network-firewall-policies rules create {prio} "
                f"--firewall-policy={pname} --project={dst_host} {rule_scope_flag} "
                f"--action={action} --direction={direction} "
                f"--layer4-configs={layer4}"
            )
            if extra_flags:
                rule_cmd += " " + " ".join(extra_flags)

            self.run_command(
                rule_cmd, side="dst", logger=self.dst_logger,
                desc=f"Create FW Policy Rule {pname}/{prio} ({scope_label})",
                explanation=f"ポリシー '{pname}' にルール priority={prio} を追加",
                impersonate_sa=dst_sa, allow_fail=True,
            )

        self._parallel_for_each(fw_rules, rule_worker, f"fw-rule-{pname}")

    def _sync_fw_policy_associations(
        self, pname: str, scope_flag: str, scope_label: str,
        src_host: str, dst_host: str,
        src_sa: Optional[str], dst_sa: Optional[str],
        proj_map: Dict[str, str],
    ):
        # `network-firewall-policies associations list` は CLI に存在しない。
        # describe の `associations` フィールドから取得する（src / dst 両方）。
        src_raw = self.run_command(
            f"gcloud compute network-firewall-policies describe {pname} "
            f"--project={src_host} {scope_flag} --format=json",
            side="src", logger=self.org_logger,
            desc=f"Describe FW Policy {pname} ({scope_label}) [assoc src]",
            explanation=f"ポリシー '{pname}' の describe から association を抽出 (src)",
            impersonate_sa=src_sa, allow_fail=True,
        )
        src_obj = _parse_gcloud_describe_json(src_raw)
        assocs = src_obj.get('associations') or []

        if not assocs:
            return

        # dst 側 association も describe から取得する。
        dst_raw = self.run_command(
            f"gcloud compute network-firewall-policies describe {pname} "
            f"--project={dst_host} {scope_flag} --format=json",
            side="dst", logger=self.dst_logger,
            desc=f"Describe FW Policy {pname} ({scope_label}) [assoc dst]",
            explanation=f"ポリシー '{pname}' の dst 側 association を取得 (冪等判定)",
            impersonate_sa=dst_sa, allow_fail=True,
        )
        dst_obj = _parse_gcloud_describe_json(dst_raw)
        dst_assoc_list = dst_obj.get('associations') or []
        existing_assoc_names = {a.get('name') for a in dst_assoc_list if a.get('name')}

        for assoc in assocs:
            net_url = assoc.get('attachmentTarget', '')
            net_name = net_url.split('/')[-1] if net_url else ''
            assoc_name = assoc.get('name', f"{pname}-assoc")
            if not net_name:
                continue
            dst_net_url = net_url
            for s, d in proj_map.items():
                dst_net_url = dst_net_url.replace(s, d)

            if assoc_name in existing_assoc_names:
                self.dst_logger.info(
                    f"      アソシエーション '{assoc_name}' は既存。スキップ"
                )
                continue

            # dst network が無い状態で association create を叩くと
            # "Could not fetch resource" で失敗する (regression 防止)。
            # _replicate_host_networks() は Step 4.5 冒頭で済んでいるはずだが、
            # 念のため確認して未存在なら skip + WARNING に倒す。
            dst_net_name = dst_net_url.split('/')[-1] if dst_net_url else ''
            if dst_net_name and not self._gcloud_exists(
                f"gcloud compute networks describe {dst_net_name} --project={dst_host} "
                f"--format='value(name)'",
                dst_sa,
            ):
                self.dst_logger.warning(
                    f"      Assoc '{assoc_name}': dst network '{dst_net_name}' が "
                    f"{dst_host} に未存在のためスキップ "
                    f"(bootstrap_shared_vpc.sh / _replicate_host_networks の結果を確認)"
                )
                continue

            self.run_command(
                f"gcloud compute network-firewall-policies associations create "
                f"--firewall-policy={pname} --project={dst_host} {fw_rule_scope_flag(scope_flag)} "
                f"--name={assoc_name} --network={dst_net_url}",
                side="dst", logger=self.dst_logger,
                desc=f"Create FW Policy Assoc {assoc_name} ({scope_label})",
                explanation=f"ポリシー '{pname}' をネットワーク '{net_name}' に関連付け",
                impersonate_sa=dst_sa, allow_fail=True,
            )

    def step_gce_restore(self):
        """Step 5: src VM をスナップショット経由で dst に復元する。

        並列化方針:
          1. _replicate_host_networks() は VM 作成の前提なのでシングルスレッドで先に完了させる。
          2. プロジェクト単位の VM/snapshot 一覧取得は src read-only なので
             _parallel_for_each で並列化（thread-prefix=gce-list）。
          3. (project, vm) のフラット work unit に展開し、_restore_one_vm を
             _parallel_for_each で並列実行（thread-prefix=gce-restore）。
             VM 復元チェーン (stop→detach→delete→create→attach→start) はVM内で直列。

        並列モードでは「snapshot 未検出」での sys.exit(1) を行わず、stats.failed
        に記録して return する。他の VM の進行を巻き添えで止めないため。
        run() 終了時に stats.failed > 0 なら main() が exit code 1 を返す
        （Makefile/CI の検知挙動は保たれる）。
        """
        pairs = list(self._iter_project_pairs())
        log_stage_header(self.dst_logger, 5, "GCE VM 復元（スナップショット → ディスク差し替え）", len(pairs))

        max_age_days = self.config.get('steps', {}).get('gce_snapshot', {}).get('max_age_days', 30)
        proj_map = self._build_proj_id_map()

        # bulk-export は Shared VPC のネットワーク定義を出力しないため、VM を共有
        # サブネットに作成する前に src host の VPC/subnet を dst host へ複製する。
        # 通常は Step 4.5 (step_network_firewall) で既に作成済み。冪等なので
        # ここでは _gcloud_exists で skip するだけで実 create は走らない。
        # Step 5 単体実行 (network_firewall.enabled = false) でも動くよう、
        # 呼び出しは残しておくこと。
        self._replicate_host_networks()

        # 1) プロジェクトごとの (vms, snapshots) を並列取得（src read-only）
        project_data: Dict[str, Tuple[List[Dict], List[Dict], str, Optional[str], Optional[str]]] = {}
        data_lock = threading.Lock()

        def list_worker(pair):
            src_proj, dst_proj, src_sa, dst_sa = pair
            self.dst_logger.info(f"  → src '{src_proj}' → dst '{dst_proj}'")
            vm_json = self.run_command(
                f"gcloud compute instances list --project={src_proj} --format=json",
                side="src", logger=self.org_logger,
                desc=f"List Src VMs {src_proj}",
                explanation=f"{src_proj} の VM 一覧を取得（復元対象を確定するため）",
                impersonate_sa=src_sa, allow_fail=True,
            )
            if not vm_json:
                self.dst_logger.info(f"    {src_proj}: VM 無し / 取得失敗")
                return
            try:
                vms = json.loads(vm_json)
            except Exception as e:
                self.dst_logger.error(f"    VM JSON 解析失敗 ({src_proj}): {e}")
                return
            snap_json = self.run_command(
                f"gcloud compute snapshots list --project={src_proj} --format=json",
                side="src", logger=self.org_logger,
                desc=f"List Src Snaps {src_proj}",
                explanation=f"{src_proj} のスナップショット一覧を取得",
                impersonate_sa=src_sa, allow_fail=True,
            )
            try:
                snapshots = json.loads(snap_json) if snap_json else []
            except Exception as e:
                self.dst_logger.error(f"    Snapshot JSON 解析失敗 ({src_proj}): {e}")
                return
            with data_lock:
                project_data[src_proj] = (vms, snapshots, dst_proj, src_sa, dst_sa)

        self._parallel_for_each(pairs, list_worker, "gce-list")

        # 2) (project, vm) のフラット work unit に展開し並列復元
        units: List[Tuple[Dict, List[Dict], str, str, Optional[str], Optional[str]]] = []
        for src_proj, (vms, snapshots, dst_proj, src_sa, dst_sa) in project_data.items():
            for vm in vms:
                units.append((vm, snapshots, src_proj, dst_proj, src_sa, dst_sa))

        if units:
            self.dst_logger.info(
                f"  並列復元開始: {len(units)} VM (parallel_jobs={self.parallel_jobs})"
            )

        def restore_worker(unit):
            vm, snapshots, src_proj, dst_proj, src_sa, dst_sa = unit
            self._restore_one_vm(
                vm, snapshots, src_proj, dst_proj, src_sa, dst_sa,
                proj_map, max_age_days,
            )

        self._parallel_for_each(units, restore_worker, "gce-restore")

        # 3) 最終フェーズ: src と同じ電源状態 (TERMINATED / SUSPENDED) に揃える。
        #    boot 直後だと guest OS が ACPI S3 に応答できず suspend が失敗するため、
        #    全 VM の復元完了 + 待機時間を挟んでから実施する。
        pending: List[Tuple[str, str, str, Optional[str], str]] = []
        for src_proj, (vms, _snaps, dst_proj, _src_sa, dst_sa) in project_data.items():
            for vm in vms:
                name = vm.get('name')
                zone = (vm.get('zone') or '').split('/')[-1]
                if not name or not zone:
                    continue
                s = (vm.get('status') or 'RUNNING').upper()
                if s in ('TERMINATED', 'SUSPENDED'):
                    pending.append((name, zone, dst_proj, dst_sa, s))
        if pending:
            self._finalize_vm_power_states(pending)

        self.dst_logger.info("  ✓ Step 5 完了")

    def _restore_one_vm(
        self, vm: Dict, snapshots: List[Dict],
        src_proj: str, dst_proj: str,
        src_sa: Optional[str], dst_sa: Optional[str],
        proj_map: Dict[str, str], max_age_days: int,
    ):
        """1 VM 分の復元処理。step_gce_restore のループ本体を抽出したもの。

        並列実行されるため、共有可変状態は触らない（StageStats はロック済み、
        logger はスレッドセーフ）。VM 内の操作チェーン
        (stop→detach→delete→create→attach→start) は依存があるため直列実行。
        """
        vm_name = vm.get('name')
        if not vm_name:
            return
        zone = vm.get('zone', '').split('/')[-1]
        machine_type = vm.get('machineType', '').split('/')[-1] or 'e2-micro'
        boot_disk = next((d for d in vm.get('disks', []) if d.get('boot')), None)
        if not boot_disk:
            return
        disk_name = boot_disk.get('source', '').split('/')[-1]

        snap_name = self._find_valid_snapshot(snapshots, disk_name, max_age_days)
        if not snap_name:
            # 並列モードでは sys.exit せず failed に記録して return。
            # 他 VM を巻き添えで止めないため。最終的に main() で exit 1。
            msg = f"有効スナップショットが無いため復元不能 (disk={disk_name})"
            self.dst_logger.error(f"    ✗ {vm_name}: {msg}")
            self.stats.add_failure(f"Restore VM {vm_name}", msg)
            self.stats.incr("failed")
            return

        dst_disk_name = vm_name
        snap_path = f"projects/{src_proj}/global/snapshots/{snap_name}"

        # VM/disk は Terraform(Step4) では作らず Step5 で管理する。dst に VM が
        # 既にあるかで分岐: 無ければ snapshot 復元ディスクで新規作成、あれば
        # ブートディスクを復元ディスクに差し替える（どちらも冪等）。
        vm_exists = self._gcloud_exists(
            f"gcloud compute instances describe {vm_name} --zone={zone} "
            f"--project={dst_proj} --format='value(name)'",
            dst_sa,
        )

        if not vm_exists:
            self.dst_logger.info(
                f"    {vm_name} を新規作成して復元 (zone={zone}, type={machine_type}, snap={snap_name})"
            )
            self._create_disk_from_snapshot(dst_disk_name, snap_path, zone, dst_proj, dst_sa)
            nic = self._build_restore_nic(vm, proj_map, dst_proj, dst_sa)
            with tempfile.TemporaryDirectory(prefix=f"vm-{vm_name}-") as tmpdir:
                extra = self._build_vm_create_extra_args(vm, tmpdir)
                self.run_command(
                    f"gcloud compute instances create {vm_name} --zone={zone} "
                    f"--project={dst_proj} --machine-type={machine_type} {nic} "
                    f"--disk=name={dst_disk_name},boot=yes,auto-delete=yes "
                    f"{extra} --quiet",
                    side="dst", logger=self.dst_logger,
                    desc=f"Create VM {vm_name}",
                    explanation="復元ディスクをブートに指定して dst VM を新規作成（metadata/tags/labels/SA/scheduling 引き継ぎ）",
                    impersonate_sa=dst_sa,
                )
            self._attach_secondary_disks(
                vm, vm_name, zone, src_proj, dst_proj, dst_sa, snapshots, max_age_days
            )
            return

        self.dst_logger.info(
            f"    {vm_name} は既存。ブートディスクを復元ディスクに差し替え (snap={snap_name})"
        )
        src_ip = ((vm.get('networkInterfaces') or [{}])[0]).get('networkIP')
        if src_ip:
            self._warn_if_dst_internal_ip_mismatch(vm_name, zone, dst_proj, dst_sa, src_ip)
        self.run_command(
            f"gcloud compute instances stop {vm_name} --zone={zone} --project={dst_proj} --quiet",
            side="dst", logger=self.dst_logger,
            desc=f"Stop dst VM {vm_name}",
            explanation=f"dst の {vm_name} を停止し、ブートディスクを差し替え可能にする",
            impersonate_sa=dst_sa, allow_fail=True,
        )
        self.run_command(
            f"gcloud compute instances detach-disk {vm_name} --disk={dst_disk_name} "
            f"--zone={zone} --project={dst_proj} --quiet",
            side="dst", logger=self.dst_logger,
            desc=f"Detach disk {dst_disk_name}",
            explanation="現在のブートディスクをデタッチ",
            impersonate_sa=dst_sa, allow_fail=True,
        )
        self.run_command(
            f"gcloud compute disks delete {dst_disk_name} --zone={zone} --project={dst_proj} --quiet",
            side="dst", logger=self.dst_logger,
            desc=f"Delete placeholder disk {dst_disk_name}",
            explanation="差し替え前の旧ブートディスクを削除",
            impersonate_sa=dst_sa, allow_fail=True,
        )
        self._create_disk_from_snapshot(dst_disk_name, snap_path, zone, dst_proj, dst_sa)
        self.run_command(
            f"gcloud compute instances attach-disk {vm_name} --disk={dst_disk_name} "
            f"--boot --zone={zone} --project={dst_proj} --quiet",
            side="dst", logger=self.dst_logger,
            desc=f"Attach disk {dst_disk_name}",
            explanation="復元ディスクをブートディスクとしてアタッチ",
            impersonate_sa=dst_sa,
        )
        self.run_command(
            f"gcloud compute instances start {vm_name} --zone={zone} --project={dst_proj} --quiet",
            side="dst", logger=self.dst_logger,
            desc=f"Start VM {vm_name}",
            explanation="復元ディスクで VM を起動（電源状態反映は Step 5 最終フェーズで実施）",
            impersonate_sa=dst_sa,
        )
        self._attach_secondary_disks(
            vm, vm_name, zone, src_proj, dst_proj, dst_sa, snapshots, max_age_days
        )

    def _finalize_vm_power_states(
        self, pending: List[Tuple[str, str, str, Optional[str], str]],
    ):
        """Step 5 の最終フェーズ: 復元後 RUNNING になっている VM を src と同じ
        電源状態 (TERMINATED / SUSPENDED) に揃える。

        suspend は guest OS が ACPI S3 シグナルに 3 分以内に応答する必要があり、
        新規復元 boot 直後の VM では失敗しがち。そのため:
          1. 全 VM の復元完了後にまとめて実施し、
          2. `steps.gce_restore.power_state_wait_seconds` (既定 120s) 待ってから、
          3. suspend は `_try_dst_suspend` で stats 非記録の soft fail にする
        ことで run 全体の exit code を suspend 失敗で落とさない。
        """
        bar = "━" * 60
        self.dst_logger.info("")
        self.dst_logger.info(bar)
        self.dst_logger.info(
            f" ステップ 5.5: 電源状態の反映 (src と同じ TERMINATED / SUSPENDED に揃える)"
            f"  （対象 {len(pending)} 件）"
        )
        self.dst_logger.info(bar)
        wait_s = int(
            self.config.get('steps', {}).get('gce_restore', {})
            .get('power_state_wait_seconds', 120)
        )
        if self.mock or self.dry_run:
            self.dst_logger.info(f"  [DRY RUN/MOCK] guest OS 起動待ち {wait_s}s をスキップ")
        elif wait_s > 0:
            self.dst_logger.info(
                f"  guest OS 起動完了を待機中 ({wait_s}s)…"
                f" suspend は ACPI S3 応答に依存するため即時実施は失敗しやすい"
            )
            time.sleep(wait_s)

        def worker(item):
            vm_name, zone, dst_proj, dst_sa, src_status = item
            self.dst_logger.info(
                f"    {vm_name}: 目標電源状態 = {src_status} (zone={zone})"
            )
            if src_status == 'TERMINATED':
                self.run_command(
                    f"gcloud compute instances stop {vm_name} --zone={zone} --project={dst_proj} --quiet",
                    side="dst", logger=self.dst_logger,
                    desc=f"Stop VM {vm_name}",
                    explanation=f"src が TERMINATED のため dst {vm_name} を停止",
                    impersonate_sa=dst_sa, allow_fail=True,
                )
            elif src_status == 'SUSPENDED':
                self._try_dst_suspend(vm_name, zone, dst_proj, dst_sa)

        self._parallel_for_each(pending, worker, "gce-power")
        self.dst_logger.info("  ✓ 電源状態の反映 完了")

    def _try_dst_suspend(
        self, vm_name: str, zone: str, dst_proj: str, dst_sa: Optional[str],
    ) -> bool:
        """`gcloud compute instances suspend` を stats 非記録で soft fail 実行する。

        suspend は guest OS の ACPI S3 応答に依存し失敗しやすい。`run_command` 経由だと
        失敗が `stats.failed` に積まれて run 全体が exit 1 になるため、ここでは
        `_gcloud_exists` と同様に subprocess を直接呼ぶ。成功 True / 失敗 False。
        """
        if self.mock or self.dry_run:
            self.dst_logger.info(
                f"    [DRY RUN] gcloud compute instances suspend {vm_name} "
                f"--zone={zone} --project={dst_proj}"
            )
            return True
        cmd = (
            f"gcloud compute instances suspend {vm_name} "
            f"--zone={zone} --project={dst_proj} --quiet"
        )
        env = os.environ.copy()
        if dst_sa:
            env['CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT'] = dst_sa
        try:
            res = subprocess.run(
                cmd, shell=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, env=env, timeout=900,
            )
        except Exception as e:
            self.dst_logger.warning(f"    ⚠ {vm_name}: suspend 例外: {e}")
            return False
        if res.returncode == 0:
            self.dst_logger.info(f"    ✓ {vm_name}: suspend 成功")
            return True
        err = (res.stderr or res.stdout or '').strip()
        self.dst_logger.warning(
            f"    ⚠ {vm_name}: suspend 失敗 (exit={res.returncode}): {err[:500]}"
        )
        self.dst_logger.warning(
            f"      原因例: guest OS が ACPI S3 に未応答 (boot 未完了 / 非対応 OS) /"
            f" GPU・TPU 付き / Confidential VM / メモリ 208GB 超 / CSEK 付きディスク。\n"
            f"      手動復旧: gcloud compute instances suspend {vm_name} "
            f"--zone={zone} --project={dst_proj}"
        )
        return False

    def _gcloud_exists(self, cmd: str, impersonate_sa: Optional[str]) -> bool:
        """read-only な describe 等で対象リソースの存在を確認する（stats を汚さない）。

        run_command は失敗を failed カウントに含め、dst の dry-run をスキップする。
        存在確認の 404 を「失敗」と数えないよう、専用に subprocess を直接叩く。
        mock/dry-run では実 GCP を叩かず「存在しない」とみなす（作成パスを通す）。
        """
        if self.mock or self.dry_run:
            return False
        env = os.environ.copy()
        if impersonate_sa:
            env['CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT'] = impersonate_sa
        try:
            res = subprocess.run(
                cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, env=env, timeout=60,
            )
            return res.returncode == 0 and bool(res.stdout.strip())
        except Exception:
            return False

    def _create_disk_from_snapshot(
        self, disk_name: str, snap_path: str, zone: str,
        dst_proj: str, dst_sa: Optional[str],
    ):
        """snapshot から dst ディスクを作成する。既存なら再利用（冪等）。"""
        if self._gcloud_exists(
            f"gcloud compute disks describe {disk_name} --zone={zone} "
            f"--project={dst_proj} --format='value(name)'",
            dst_sa,
        ):
            self.dst_logger.info(f"      復元ディスク {disk_name} は既存。再利用")
            return
        self.run_command(
            f"gcloud compute disks create {disk_name} --source-snapshot={snap_path} "
            f"--zone={zone} --project={dst_proj} --quiet",
            side="dst", logger=self.dst_logger,
            desc=f"Create disk {disk_name}",
            explanation="src snapshot から dst にクローンディスクを作成",
            impersonate_sa=dst_sa,
        )

    def _build_restore_nic(self, vm: Dict, proj_map: Dict[str, str],
                           dst_proj: str, dst_sa: Optional[str]) -> str:
        """src VM の networkInterfaces から dst 用の --network-interface 引数を組む。

        Shared VPC の network/subnet は host プロジェクトに属するため、URL 内の
        プロジェクト ID を proj_map で dst host にマップする。
        src VM の内部 IP (networkIP) は **service project (dst_proj) 側** で静的予約し、
        subnet 参照は host project の subnet URL とする（共有 VPC の正しい構成）。
        host project に予約すると "reserved by another project" で VM 作成に失敗するため。
        外部 IP は付けない。
        """
        ni = (vm.get('networkInterfaces') or [{}])[0]
        parts: List[str] = []
        m_net = re.search(r'projects/([^/]+)/global/networks/([^/]+)', ni.get('network', '') or '')
        if m_net:
            host = proj_map.get(m_net.group(1), m_net.group(1))
            parts.append(f"network=projects/{host}/global/networks/{m_net.group(2)}")
        dst_host = None
        region = None
        subnet = None
        m_sub = re.search(
            r'projects/([^/]+)/regions/([^/]+)/subnetworks/([^/]+)',
            ni.get('subnetwork', '') or '',
        )
        if m_sub:
            dst_host = proj_map.get(m_sub.group(1), m_sub.group(1))
            region = m_sub.group(2)
            subnet = m_sub.group(3)
            parts.append(
                f"subnet=projects/{dst_host}/regions/{region}/subnetworks/{subnet}"
            )

        ip_addr = ni.get('networkIP')
        vm_name = vm.get('name') or 'vm'
        if ip_addr and dst_host and region and subnet:
            addr_name = self._internal_addr_name(vm_name, ip_addr)
            # 共有 VPC: service project (dst_proj) で予約、subnet は host を指す
            subnet_uri = f"projects/{dst_host}/regions/{region}/subnetworks/{subnet}"
            self._reserve_internal_ip(dst_proj, dst_sa, region, subnet_uri, ip_addr, addr_name)
            parts.append(f"private-network-ip={ip_addr}")

        # 外部 IP: src に accessConfigs があれば dst でも付与（ephemeral）。
        # global IP はユニークなので src が static でも dst では auto-assign に変換。
        # network tier は src の設定を引き継ぐ。
        ext_configs = ni.get('accessConfigs') or []
        if ext_configs:
            tier = ext_configs[0].get('networkTier')
            if tier:
                parts.append(f"network-tier={tier}")
            # no-address を付けない → gcloud は ephemeral 外部 IP を自動採番する
        else:
            parts.append("no-address")
        return "--network-interface=" + ",".join(parts)

    def _build_vm_create_extra_args(self, vm: Dict, tmpdir: str) -> str:
        """src VM の追加属性（metadata/tags/labels/SA/scheduling 等）を
        gcloud compute instances create の引数文字列に変換する。

        - metadata は値に , や = や改行を含むため `--metadata-from-file key=path` で渡す
        - compute 既定 SA（プロジェクト番号始まり）は dst で別 ID になるため SA 指定しない
        - 値は shlex.quote でエスケープ
        """
        args: List[str] = []

        tags = (vm.get('tags') or {}).get('items') or []
        if tags:
            args.append("--tags=" + ",".join(shlex.quote(t) for t in tags))

        labels = vm.get('labels') or {}
        if labels:
            # `--labels=k=v,k=v` 形式（key/value は英数 + _ - のみのため quote 不要）
            args.append("--labels=" + ",".join(f"{k}={v}" for k, v in labels.items()))

        sa = (vm.get('serviceAccounts') or [{}])[0]
        sa_email = sa.get('email') or ''
        sa_scopes = sa.get('scopes') or []
        # `<project-number>-compute@developer.gserviceaccount.com` は dst で番号違いとなり
        # 借用不可。dst の compute 既定 SA を使わせるため email は付けない。
        if sa_email and not re.match(r'^\d+-compute@developer\.gserviceaccount\.com$', sa_email):
            args.append(f"--service-account={shlex.quote(sa_email)}")
            if sa_scopes:
                args.append("--scopes=" + ",".join(sa_scopes))

        sched = vm.get('scheduling') or {}
        if sched.get('preemptible'):
            args.append("--preemptible")
        pm = (sched.get('provisioningModel') or '').upper()
        if pm == 'SPOT' and not sched.get('preemptible'):
            args.append("--provisioning-model=SPOT")
        ohm = sched.get('onHostMaintenance')
        if ohm:
            args.append(f"--maintenance-policy={ohm}")
        if sched.get('automaticRestart') is False:
            args.append("--no-restart-on-failure")

        if vm.get('minCpuPlatform'):
            args.append(f"--min-cpu-platform={shlex.quote(vm['minCpuPlatform'])}")
        if vm.get('canIpForward'):
            args.append("--can-ip-forward")
        if vm.get('deletionProtection'):
            args.append("--deletion-protection")
        if vm.get('description'):
            args.append(f"--description={shlex.quote(vm['description'])}")
        if vm.get('hostname'):
            args.append(f"--hostname={shlex.quote(vm['hostname'])}")

        # Shielded VM 設定（true のときだけ付ける。false はデフォルトなので無指定）
        sh = vm.get('shieldedInstanceConfig') or {}
        if sh.get('enableSecureBoot'):
            args.append("--shielded-secure-boot")
        if sh.get('enableVtpm'):
            args.append("--shielded-vtpm")
        if sh.get('enableIntegrityMonitoring'):
            args.append("--shielded-integrity-monitoring")

        # Confidential VM
        conf = vm.get('confidentialInstanceConfig') or {}
        if conf.get('enableConfidentialCompute'):
            args.append("--confidential-compute")

        # GPU / アクセラレータ
        for a in vm.get('guestAccelerators') or []:
            t = (a.get('acceleratorType') or '').split('/')[-1]
            c = a.get('acceleratorCount') or 1
            if t:
                args.append(f"--accelerator=type={t},count={c}")

        md_items = (vm.get('metadata') or {}).get('items') or []
        for it in md_items:
            key = it.get('key')
            val = it.get('value')
            if not key or val is None:
                continue
            # 安全な key 名のみ（gcloud は英数/-/_）
            safe_key = re.sub(r'[^A-Za-z0-9_-]', '_', key)
            fpath = os.path.join(tmpdir, f"md_{safe_key}.txt")
            try:
                with open(fpath, "w", encoding="utf-8") as f:
                    f.write(val if isinstance(val, str) else str(val))
                args.append(f"--metadata-from-file={key}={fpath}")
            except OSError as e:
                self.dst_logger.warning(f"      metadata 書込失敗 ({key}): {e}")

        return " ".join(args)

    def _attach_secondary_disks(self, vm: Dict, vm_name: str, zone: str,
                                src_proj: str, dst_proj: str, dst_sa: Optional[str],
                                snapshots: List[Dict], max_age_days: int):
        """src VM のセカンダリ（boot 以外）ディスクを snapshot から復元して attach する。

        - boot ディスクは Step5 メインフローが扱うのでスキップ
        - 各セカンダリは src 名と同じ disk 名で dst に作成
        - attach は冪等性のため allow_fail（既 attach は再 attach で失敗するのを許容）
        - 複数セカンダリがある場合は `_create_disk_from_snapshot` を並列化
          （独立操作のためスループット改善）。attach 自体は同一 VM への並列 attach で
          競合する可能性があるためここでは直列。
        """
        secondaries = [d for d in (vm.get('disks') or []) if not d.get('boot')]
        if not secondaries:
            return

        # snapshot 解決 → 作成タスクリスト
        plans: List[Tuple[str, str, str, str]] = []  # (src_disk_name, snap_path, device_name, mode_flag)
        for d in secondaries:
            src_disk_name = (d.get('source') or '').split('/')[-1]
            if not src_disk_name:
                continue
            snap_name = self._find_valid_snapshot(snapshots, src_disk_name, max_age_days)
            if not snap_name:
                self.dst_logger.warning(
                    f"      ⚠ セカンダリディスク {src_disk_name}: 有効スナップショットが無いためスキップ"
                )
                continue
            snap_path = f"projects/{src_proj}/global/snapshots/{snap_name}"
            device_name = d.get('deviceName') or src_disk_name
            mode = d.get('mode') or 'READ_WRITE'
            mode_flag = "--mode=ro" if mode == 'READ_ONLY' else "--mode=rw"
            plans.append((src_disk_name, snap_path, device_name, mode_flag))
            self.dst_logger.info(
                f"      セカンダリディスク復元: {src_disk_name} (snap={snap_name})"
            )

        # ディスク作成は独立操作。複数あれば並列化。
        def create_worker(plan):
            src_disk_name, snap_path, _device_name, _mode_flag = plan
            self._create_disk_from_snapshot(src_disk_name, snap_path, zone, dst_proj, dst_sa)

        self._parallel_for_each(plans, create_worker, f"sec-disk-{vm_name}")

        # attach は同一 VM のメタデータ更新が走るため直列（同時 attach で 409 が出る）
        for src_disk_name, _snap_path, device_name, mode_flag in plans:
            self.run_command(
                f"gcloud compute instances attach-disk {vm_name} --disk={src_disk_name} "
                f"--device-name={device_name} {mode_flag} "
                f"--zone={zone} --project={dst_proj} --quiet",
                side="dst", logger=self.dst_logger,
                desc=f"Attach secondary disk {src_disk_name}",
                explanation=f"セカンダリディスク {src_disk_name} を {vm_name} に attach",
                impersonate_sa=dst_sa, allow_fail=True,
            )

    def _warn_if_dst_internal_ip_mismatch(self, vm_name: str, zone: str,
                                          dst_proj: str, dst_sa: Optional[str],
                                          expected_ip: str):
        """既存 dst VM の内部 IP が src と異なる場合に警告（ゴール: IP 引き継ぎ）。

        差し替えパスでは NIC を触らず IP は変わらない。再作成すれば引き継げる旨を案内。
        """
        if self.mock or self.dry_run:
            return
        env = os.environ.copy()
        if dst_sa:
            env['CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT'] = dst_sa
        try:
            res = subprocess.run(
                f"gcloud compute instances describe {vm_name} --zone={zone} "
                f"--project={dst_proj} "
                f"--format='value(networkInterfaces[0].networkIP)'",
                shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, env=env, timeout=60,
            )
        except Exception:
            return
        if res.returncode != 0:
            return
        cur = res.stdout.strip()
        if cur and cur != expected_ip:
            self.dst_logger.warning(
                f"      ⚠ {vm_name} の内部IPが src と異なります "
                f"(dst={cur}, src={expected_ip})。"
                f"src の IP を引き継ぐには dst VM をいったん削除して再作成してください: "
                f"gcloud compute instances delete {vm_name} --zone={zone} --project={dst_proj}"
            )

    def _internal_addr_name(self, vm_name: str, ip_addr: str) -> str:
        """共有VPC host で予約する static internal address のリソース名（冪等な命名）。"""
        ip_part = ip_addr.replace('.', '-').replace(':', '-')
        raw = f"mig-{vm_name}-{ip_part}".lower()
        raw = re.sub(r'[^a-z0-9-]', '-', raw)
        return raw[:63].rstrip('-') or "mig-ip"

    def _reserve_internal_ip(self, svc_proj: str, svc_sa: Optional[str],
                             region: str, subnet_uri: str,
                             ip_addr: str, addr_name: str):
        """共有 VPC の service project (svc_proj) 側に静的内部 IP を予約する（冪等）。

        共有 VPC では host project に IP を予約すると service project の VM から参照したとき
        "reserved by another project" で拒否される。正しい構成は:
          - subnet は host project のものを URI で指定（compute.networkUser 権限で借りる）
          - address は service project 側に作成
        既存 address があり IP 一致なら再利用、IP 不一致なら警告のみ。
        """
        # 既存チェック（read-only: stats を汚さない）
        if not (self.mock or self.dry_run):
            env = os.environ.copy()
            if svc_sa:
                env['CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT'] = svc_sa
            try:
                res = subprocess.run(
                    f"gcloud compute addresses describe {addr_name} "
                    f"--region={region} --project={svc_proj} --format='value(address)'",
                    shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, env=env, timeout=60,
                )
                if res.returncode == 0 and res.stdout.strip():
                    existing_ip = res.stdout.strip()
                    if existing_ip == ip_addr:
                        self.dst_logger.info(
                            f"      内部IP予約 {addr_name}={ip_addr} は既存。再利用"
                        )
                        return
                    self.dst_logger.warning(
                        f"      内部IP予約 {addr_name} は既存ですが IP が異なります "
                        f"(existing={existing_ip}, expected={ip_addr})。手動で確認してください"
                    )
                    return
            except Exception:
                pass

        self.run_command(
            f"gcloud compute addresses create {addr_name} "
            f"--region={region} --project={svc_proj} "
            f"--subnet={subnet_uri} --addresses={ip_addr} --quiet",
            side="dst", logger=self.dst_logger,
            desc=f"Reserve internal IP {addr_name}",
            explanation=f"service project {svc_proj} に静的内部IP {ip_addr} を予約 (共有 VPC subnet={subnet_uri})",
            impersonate_sa=svc_sa, allow_fail=True,
        )

    def step_vpc_sc(self):
        """Step 7: dst で作成したプロジェクトを既存の VPC Service Controls ペリメタに追加。

        org は触らない: アクセスポリシーやペリメタ自体の作成/削除は一切行わず、
        config.steps.vpc_sc で指定された **既存ペリメタ** に dst プロジェクトを
        `--add-resources` で追記するだけ（冪等）。

        ペリメタのメンバーはプロジェクト番号 (projects/<number>) で管理されるため、
        各 dst プロジェクト ID を describe して番号へ解決する（read-only / local 認証）。
        """
        cfg = self.config.get('steps', {}).get('vpc_sc', {})
        policy = str(cfg.get('access_policy', '') or '').strip()
        perimeter = str(cfg.get('perimeter', '') or '').strip()
        include_host = cfg.get('include_host_project', True)
        impersonate = (cfg.get('impersonate_service_account') or '').strip() or None

        mapping = self.config.get('project_mapping', {})
        dst_projects: List[str] = []
        host = mapping.get('host_project', {})
        if include_host and host.get('dst'):
            dst_projects.append(host['dst'])
        for svc in mapping.get('service_projects', []):
            if svc.get('dst'):
                dst_projects.append(svc['dst'])

        # quota/billing project は明示必須（フォールバックしない）。
        # access-context-manager は --project を持たないため、未指定だと gcloud が
        # ローカル config の core/project（移行と無関係なプロジェクト）を quota に使い、
        # そこで API 無効 → SERVICE_DISABLED で失敗する。誤ったプロジェクトを自動推測
        # して間違ったペリメタ操作をしないよう、未設定なら設定不足としてスキップする。
        billing = str(cfg.get('billing_project', '') or '').strip()
        billing_flag = f" --billing-project={billing}"

        log_stage_header(
            self.dst_logger, 7,
            "VPC SC ペリメタへ dst プロジェクトを追加", len(dst_projects),
        )

        if not policy or not perimeter or not billing:
            self.dst_logger.warning(
                "  steps.vpc_sc.access_policy / perimeter / billing_project の"
                "いずれかが未設定のためスキップ（billing_project は自動補完しない）"
            )
            self.stats.incr("skipped")
            return
        if not dst_projects:
            self.dst_logger.warning("  追加対象の dst プロジェクトがありません。スキップ")
            self.stats.incr("skipped")
            return

        # dst プロジェクト ID → projects/<番号> を解決（read-only / local 認証）。
        # mock では describe が使えないためプレースホルダ番号で代用する。
        resources: List[str] = []
        for dst in dst_projects:
            num = self._get_project_number(dst)
            if not num and self.mock:
                num = f"000{abs(hash(dst)) % 10**9}"
            if not num:
                self.dst_logger.warning(
                    f"  {dst} のプロジェクト番号を取得できずスキップ"
                    f"（projects describe 権限 / projectNumber を確認）"
                )
                self.stats.incr("skipped")
                continue
            resources.append(f"projects/{num}")

        if not resources:
            self.dst_logger.warning("  解決できた dst プロジェクト番号がありません。スキップ")
            self.stats.incr("skipped")
            return

        # quota project で accesscontextmanager API を有効化（冪等）。これをしないと
        # describe/update が quota project の API 無効で SERVICE_DISABLED になる。
        self.run_command(
            f"gcloud services enable accesscontextmanager.googleapis.com "
            f"--project={billing}",
            side="dst", logger=self.dst_logger,
            desc=f"VPC SC quota project API 有効化 {billing}",
            explanation=(
                f"{billing} で accesscontextmanager API を有効化"
                f"（access-context-manager の quota/billing project 用）"
            ),
            impersonate_sa=impersonate, allow_fail=True,
        )

        # 既存メンバーを取得し差分のみ追加（冪等・ログを綺麗に）。
        # dry_run では describe も実行されないため existing=None → 計画として全件を表示。
        existing = self._get_perimeter_resources(policy, perimeter, impersonate, billing)
        if existing is None:
            to_add = list(resources)
        else:
            to_add = [r for r in resources if r not in existing]

        if not to_add:
            self.dst_logger.info(
                f"  すべての dst プロジェクト ({len(resources)} 件) は既にペリメタ "
                f"{perimeter} 内です。スキップ"
            )
            self.stats.incr("skipped")
            return

        self.run_command(
            f"gcloud access-context-manager perimeters update {perimeter} "
            f"--policy={policy}{billing_flag} --add-resources={','.join(to_add)} --quiet",
            side="dst", logger=self.dst_logger,
            desc=f"VPC SC ペリメタ更新 {perimeter}",
            explanation=(
                f"既存ペリメタ {perimeter} に dst プロジェクト {len(to_add)} 件を追加"
                f"（org / access policy 自体は変更しない）"
            ),
            impersonate_sa=impersonate, allow_fail=True,
        )
        self.dst_logger.info("  ✓ Step 7 完了")

    def _get_perimeter_resources(
        self, policy: str, perimeter: str, impersonate: Optional[str],
        billing: str,
    ) -> Optional[set]:
        """ペリメタの現在の resources (projects/<番号>) を集合で返す（read-only）。

        取得不能（dry_run でスキップ / describe 失敗 / mock）なら None。
        None は呼び出し側で「差分不明 → 全件を計画として追加」と解釈する。

        access-context-manager は org/policy スコープのコマンドで `--project` を持たない。
        billing（quota project）は必須。呼び出し側が明示指定済みの前提でフラグを必ず付ける。
        付けないと gcloud がローカル config の core/project（無関係な dst 外プロジェクト）を
        quota に使い SERVICE_DISABLED で落ちる。
        """
        billing_flag = f" --billing-project={billing}"
        out = self.run_command(
            f"gcloud access-context-manager perimeters describe {perimeter} "
            f"--policy={policy}{billing_flag} --format='value(status.resources)' --quiet",
            side="dst", logger=self.dst_logger,
            desc=f"VPC SC ペリメタ参照 {perimeter}",
            impersonate_sa=impersonate, allow_fail=True,
            expect_not_found_ok=True,
        )
        if not out:
            return None
        # value(...) の繰り返しフィールドは ';' 区切り。projects/<num> 以外は弾く
        # （mock の "Success" など）。
        items = {tok.strip() for tok in re.split(r'[;\n,]', out) if tok.strip()}
        resources = {it for it in items if it.startswith('projects/')}
        return resources or None

    def _replicate_host_networks(self):
        """src host の custom VPC ネットワークとサブネットを dst host に複製する（冪等）。

        bulk-export は Shared VPC のネットワーク定義を出力しないため、Step5 で VM を
        共有サブネットに作成する前にここで dst host の VPC topology を src に合わせて
        用意する。default ネットワークは対象外。Shared VPC ホスト化・サービス
        プロジェクト関連付け・networkUser 付与は bootstrap_shared_vpc.sh の担当。
        """
        host = self.config.get('project_mapping', {}).get('host_project', {})
        src_host = host.get('src')
        dst_host = host.get('dst')
        src_sa = host.get('src_impersonate_service_account')
        dst_sa = host.get('dst_impersonate_service_account')
        if not src_host or not dst_host:
            return
        self.dst_logger.info(f"  [Network] src host '{src_host}' → dst host '{dst_host}' VPC 複製")

        nets_json = self.run_command(
            f"gcloud compute networks list --project={src_host} --format=json",
            side="src", logger=self.org_logger,
            desc=f"List Src Networks {src_host}",
            explanation=f"{src_host} の VPC 一覧を取得（dst host に複製）",
            impersonate_sa=src_sa, allow_fail=True,
        )
        subs_json = self.run_command(
            f"gcloud compute networks subnets list --project={src_host} --format=json",
            side="src", logger=self.org_logger,
            desc=f"List Src Subnets {src_host}",
            explanation=f"{src_host} のサブネット一覧を取得（dst host に複製）",
            impersonate_sa=src_sa, allow_fail=True,
        )
        try:
            nets = json.loads(nets_json) if nets_json else []
        except Exception:
            nets = []
        try:
            all_subs = json.loads(subs_json) if subs_json else []
        except Exception:
            all_subs = []

        for net in nets:
            name = net.get('name')
            if not name or name == 'default':
                continue
            mode = 'auto' if net.get('autoCreateSubnetworks') else 'custom'
            if self._gcloud_exists(
                f"gcloud compute networks describe {name} --project={dst_host} "
                f"--format='value(name)'",
                dst_sa,
            ):
                self.dst_logger.info(f"    VPC {name} は dst host に既存。再利用")
            else:
                self.run_command(
                    f"gcloud compute networks create {name} --subnet-mode={mode} "
                    f"--project={dst_host} --quiet",
                    side="dst", logger=self.dst_logger,
                    desc=f"Create Network {name}",
                    explanation=f"dst host {dst_host} に VPC {name}（{mode}）を作成",
                    impersonate_sa=dst_sa,
                )
            if mode != 'custom':
                continue
            for sub in all_subs:
                if (sub.get('network') or '').split('/')[-1] != name:
                    continue
                sname = sub.get('name')
                region = (sub.get('region') or '').split('/')[-1]
                cidr = sub.get('ipCidrRange')
                if not sname or not region or not cidr:
                    continue
                if self._gcloud_exists(
                    f"gcloud compute networks subnets describe {sname} --region={region} "
                    f"--project={dst_host} --format='value(name)'",
                    dst_sa,
                ):
                    self.dst_logger.info(f"      サブネット {sname}({region}) は既存。再利用")
                    continue
                self.run_command(
                    f"gcloud compute networks subnets create {sname} --network={name} "
                    f"--region={region} --range={cidr} --project={dst_host} --quiet",
                    side="dst", logger=self.dst_logger,
                    desc=f"Create Subnet {sname}",
                    explanation=f"dst host に サブネット {sname}（{region},{cidr}）を作成",
                    impersonate_sa=dst_sa,
                )

    def _find_valid_snapshot(self, snapshots: List[Dict], disk_name: str, max_age_days: int) -> Optional[str]:
        for snap in snapshots:
            if snap.get('sourceDisk', '').split('/')[-1] != disk_name:
                continue
            ts = snap.get('creationTimestamp')
            if not ts:
                continue
            try:
                created = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if datetime.datetime.now(datetime.timezone.utc) - created <= datetime.timedelta(days=max_age_days):
                    return snap.get('name')
            except Exception:
                continue
        return None

    # ============================================================
    # Step 6: Data Sync (GCS + BQ)
    # ============================================================
    def step_data_sync(self):
        pairs = list(self._iter_project_pairs())
        log_stage_header(self.dst_logger, 6, "データ同期 (GCS / BigQuery)", len(pairs))

        rename_gcs = self.config.get('rename_rules', {}).get('gcs', {})
        gcs_method = rename_gcs.get('method')
        gcs_val = self._resolve_gcs_rename_value(rename_gcs)
        gcs_overrides = rename_gcs.get('overrides', {}) or {}

        for src_proj, dst_proj, src_sa, dst_sa in pairs:
            self.dst_logger.info(f"  → {src_proj} → {dst_proj}")
            self._sync_gcs(src_proj, dst_proj, src_sa, dst_sa, gcs_method, gcs_val, gcs_overrides)
            self._sync_bq(src_proj, dst_proj, src_sa, dst_sa)
        self.dst_logger.info("  ✓ Step 6 完了")

    def _sync_gcs(self, src_proj, dst_proj, src_sa, dst_sa, method, value, overrides):
        self.dst_logger.info("  [GCS] バケット同期")
        buckets_json = self.run_command(
            f"gcloud storage buckets list --project={src_proj} --format=json",
            side="src", logger=self.org_logger,
            desc=f"List Src Buckets {src_proj}",
            explanation=f"{src_proj} のバケット一覧を取得",
            impersonate_sa=src_sa, allow_fail=True,
        )
        try:
            buckets = json.loads(buckets_json) if buckets_json else []
        except Exception:
            buckets = []
        if not buckets:
            self.dst_logger.info("    バケット無し / 取得失敗")
            return
        # dst バケット名を算出する:
        #   1) バケット名に含まれる src プロジェクト ID を dst ID に置換（単語境界）
        #   2) override / suffix / prefix のリネーム規則を適用
        #   3) dst プロジェクト固有トークンで一意化（customize_hcl と同一規則）
        # Terraform は bulk-export の .tf にあるバケットしか作らないため、実在する
        # src バケットに対応する dst バケットが無いことがある。rsync 前に dst バケットを
        # 無ければ作成して同期先を保証する（location は src を維持・冪等）。
        proj_map = self._build_proj_id_map()

        def bucket_worker(b):
            orig = (b.get('name') or '').replace('gs://', '').strip('/')
            if not orig:
                return
            location = b.get('location') or 'US'
            base = orig
            for s in sorted(proj_map.keys(), key=len, reverse=True):
                base = re.sub(
                    rf'(?<![A-Za-z0-9_-]){re.escape(s)}(?![A-Za-z0-9_-])',
                    proj_map[s], base,
                )
            if base in overrides:
                dst_bucket = overrides[base]
            else:
                if method == 'suffix':
                    renamed = f"{base}{value}"
                elif method == 'prefix':
                    renamed = f"{value}{base}"
                else:
                    renamed = base
                dst_bucket = self._uniquify_bucket_name(renamed, dst_proj)
            self.dst_logger.info(f"    gs://{orig} → gs://{dst_bucket} (loc={location})")
            if not self._gcloud_exists(
                f"gcloud storage buckets describe gs://{dst_bucket} --format='value(name)'",
                dst_sa,
            ):
                self.run_command(
                    f"gcloud storage buckets create gs://{dst_bucket} "
                    f"--project={dst_proj} --location={location} "
                    f"--uniform-bucket-level-access",
                    side="dst", logger=self.dst_logger,
                    desc=f"Create dst Bucket {dst_bucket}",
                    explanation="同期先 dst バケットを作成（無い場合）",
                    impersonate_sa=dst_sa, allow_fail=True,
                )
            self.run_command(
                f"gcloud storage rsync gs://{orig} gs://{dst_bucket} --recursive --project={dst_proj}",
                side="dst", logger=self.dst_logger,
                desc=f"GCS Rsync {orig}",
                explanation=f"src バケットから dst バケットにデータ同期",
                impersonate_sa=dst_sa,
            )

        self._parallel_for_each(buckets, bucket_worker, "gcs-rsync")

    def _sync_bq(self, src_proj, dst_proj, src_sa, dst_sa):
        self.dst_logger.info("  [BQ] BigQuery 同期")
        ds_json = self.run_command(
            f"bq ls --project_id={src_proj} --format=json",
            side="src", logger=self.org_logger,
            desc=f"List Src BQ DS {src_proj}",
            explanation=f"{src_proj} の BigQuery データセット一覧を取得",
            impersonate_sa=src_sa, allow_fail=True,
        )
        if not ds_json:
            self.dst_logger.info("    BQ データセット無し / 取得失敗")
            return
        try:
            datasets = json.loads(ds_json)
        except Exception as e:
            self.dst_logger.error(f"    BQ JSON 解析失敗: {e}")
            return

        for ds in datasets:
            ds_id = ds.get('datasetReference', {}).get('datasetId')
            if not ds_id:
                continue
            self.dst_logger.info(f"    Dataset: {ds_id}")

            # src データセットの location を取得し、dst でも同じ location で作成
            location = ds.get('location')
            if not location:
                src_show = self.run_command(
                    f"bq show --project_id={src_proj} --format=json {ds_id}",
                    side="src", logger=self.org_logger,
                    desc=f"Show src DS {ds_id}",
                    explanation="src データセットの location を取得（dst で同一 location を維持）",
                    impersonate_sa=src_sa, allow_fail=True,
                )
                try:
                    location = json.loads(src_show).get('location') if src_show else None
                except Exception:
                    location = None

            # dst データセットの存在確認 → 無ければ location 付きで作成
            dst_show = self.run_command(
                f"bq show --project_id={dst_proj} --format=json {ds_id}",
                side="dst", logger=self.dst_logger,
                desc=f"Show dst DS {ds_id}",
                explanation="dst にデータセットが既にあるか確認",
                impersonate_sa=dst_sa, allow_fail=True,
                expect_not_found_ok=True,
            )
            if not dst_show:
                loc_flag = f" --location={location}" if location else ""
                self.run_command(
                    f"bq mk{loc_flag} --project_id={dst_proj} {ds_id}",
                    side="dst", logger=self.dst_logger,
                    desc=f"Mk dst DS {ds_id}",
                    explanation=f"dst に {ds_id} を作成 (location={location or 'デフォルト'})",
                    impersonate_sa=dst_sa,
                )
            else:
                self.stats.incr("skipped")
                self.dst_logger.info(f"      ✓ スキップ: {ds_id} は既存")

            tables_json = self.run_command(
                f"bq ls --project_id={src_proj} --format=json {ds_id}",
                side="src", logger=self.org_logger,
                desc=f"List src tables {ds_id}",
                explanation=f"src データセット {ds_id} のテーブル一覧",
                impersonate_sa=src_sa, allow_fail=True,
            )
            if not tables_json:
                continue
            try:
                tables = json.loads(tables_json)
            except Exception:
                continue
            def table_worker(t):
                t_id = t.get('tableReference', {}).get('tableId')
                if not t_id:
                    return
                self.dst_logger.info(f"      Table: {ds_id}.{t_id}")
                self.run_command(
                    f"bq --project_id={dst_proj} cp --force {src_proj}:{ds_id}.{t_id} {dst_proj}:{ds_id}.{t_id}",
                    side="dst", logger=self.dst_logger,
                    desc=f"BQ Cp {ds_id}.{t_id}",
                    explanation=f"テーブルを src → dst にコピー（同一 location 必要）",
                    impersonate_sa=dst_sa,
                )

            self._parallel_for_each(tables, table_worker, f"bq-cp-{ds_id}")


def main():
    parser = argparse.ArgumentParser(
        description="GCP プロジェクトまるごとコピー (Terraform ベース、ORG read-only 保証)"
    )
    parser.add_argument("--config", default="dst/config.yaml", help="config.yaml のパス")
    parser.add_argument("--dry-run", action="store_true", default=None, help="ドライランモード")
    parser.add_argument("--no-dry-run", action="store_false", dest="dry_run", help="本番実行")
    parser.add_argument("--verbose", action="store_true", default=None, help="詳細ログを有効化")
    parser.add_argument("--no-verbose", action="store_false", dest="verbose", help="詳細ログを無効化")
    parser.add_argument("--mock", action="store_true", default=None, help="Mock モードを有効化")
    parser.add_argument("--no-mock", action="store_false", dest="mock", help="Mock モードを無効化")
    args = parser.parse_args()

    orchestrator = MigrationOrchestrator(
        config_path=args.config,
        dry_run_override=args.dry_run,
        verbose_override=args.verbose,
        mock_override=args.mock,
    )
    orchestrator.execute()
    if orchestrator.stats.failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
