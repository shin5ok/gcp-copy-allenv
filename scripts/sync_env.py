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
import ipaddress
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
import fnmatch
import fcntl
from typing import Dict, Iterable, List, Optional, Any, Set, Tuple

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
    # Artifact Registry イメージ複製で使う書込動詞。src 側で誤って実行されないよう
    # 拒否リストに入れる（copy は将来 gcloud に生えた場合の保険）。
    "copy", "push", "tag", "rmi",
)

# Mock モード時に「分かっている」と判定するコマンド先頭パターン。
# これに該当しないコマンドは fail-closed（即時エラー）にする。
_MOCK_KNOWN_PATTERNS = (
    "gcloud asset search-all-resources",
    "gcloud beta resource-config bulk-export",
    "gcloud beta resource-config list-resource-types",
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
    "gcloud services list",
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
    "gcloud run services list",
    "gcloud run services get-iam-policy",
    "gcloud run services describe",
    "gcloud run services add-iam-policy-binding",
    "gcloud iam service-accounts create",
    "gcloud projects get-iam-policy",
    "gcloud projects add-iam-policy-binding",
    "gcloud services enable",
    "gcloud artifacts repositories list",
    "gcloud artifacts repositories describe",
    "gcloud artifacts repositories create",
    "gcloud artifacts docker images list",
    "gcloud artifacts docker images describe",
    "gcloud auth configure-docker",
    "gcrane cp",
    "crane cp",
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
    # --- compute (GKE 派生。gke-*/k8s-* は customize_hcl でドロップし dst クラスタが再生成) ---
    "compute.googleapis.com/InstanceTemplate":     "terraform_apply",
    "compute.googleapis.com/InstanceGroupManager": "terraform_apply",
    "compute.googleapis.com/InstanceGroup":        "terraform_apply",
    "compute.googleapis.com/NetworkEndpointGroup": "terraform_apply",
    "compute.googleapis.com/TargetPool":           "terraform_apply",   # k8s LB 由来は skip + DIFF 参考
    "compute.googleapis.com/HttpHealthCheck":      "terraform_apply",
    "compute.googleapis.com/HttpsHealthCheck":     "terraform_apply",
    "compute.googleapis.com/HealthCheck":          "terraform_apply",
    "compute.googleapis.com/Autoscaler":           "terraform_apply",
    "compute.googleapis.com/ForwardingRule":       "terraform_apply",
    # --- container (GKE: クラスタ構成のみ複製。ノード VM は Step 2/5 で除外) ---
    "container.googleapis.com/Cluster":       "terraform_apply",
    "container.googleapis.com/NodePool":      "terraform_apply",
    # --- gkehub (fleet 登録は dst で手動再登録。複製しない) ---
    "gkehub.googleapis.com/Feature":          None,
    "gkehub.googleapis.com/Fleet":            None,
    "gkehub.googleapis.com/Membership":       None,
    # --- storage / bigquery ---
    "storage.googleapis.com/Bucket":          "data_sync",             # terraform で作成、data_sync で内容コピー
    "bigquery.googleapis.com/Dataset":        "data_sync",
    "bigquery.googleapis.com/Table":          "data_sync",
    # --- artifact registry ---
    "artifactregistry.googleapis.com/Repository":  "terraform_apply",  # bulk-export 出力
    "artifactregistry.googleapis.com/DockerImage": "data_sync",        # _sync_artifact_registry がイメージ複製
    # --- pubsub / monitoring / logging（bulk-export が出力する） ---
    "pubsub.googleapis.com/Topic":            "terraform_apply",
    "monitoring.googleapis.com/AlertPolicy":  "terraform_apply",
    "monitoring.googleapis.com/NotificationChannel": "terraform_apply",  # 越境分は customize が skip + 注記
    "monitoring.googleapis.com/Dashboard":    None,   # 可視化。運用継続に必須でない（必要なら手動エクスポート）
    "monitoring.googleapis.com/UptimeCheckConfig": None,  # 監視設定。dst URL が変わるため手動再作成が自然
    "logging.googleapis.com/LogMetric":       "terraform_apply",  # export されない場合は要対応で出る
    # --- LB フロント（bulk-export が出力する） ---
    "compute.googleapis.com/BackendService":  "terraform_apply",
    "compute.googleapis.com/SecurityPolicy":  "terraform_apply",
    "compute.googleapis.com/TargetHttpsProxy": "terraform_apply",
    "compute.googleapis.com/TargetHttpProxy":  "terraform_apply",
    "compute.googleapis.com/UrlMap":          "terraform_apply",
    "compute.googleapis.com/SslCertificate":  "terraform_apply",  # self-managed は customize が skip + 要対応注記
    # --- secret / functions / build（export されない = 欠落は要対応で出る） ---
    "secretmanager.googleapis.com/Secret":        "terraform_apply",
    "secretmanager.googleapis.com/SecretVersion": "terraform_apply",  # classify で Secret 本体へ集約
    "cloudfunctions.googleapis.com/Function":     "terraform_apply",
    "cloudbuild.googleapis.com/BuildTrigger":     "terraform_apply",
    "cloudbuild.googleapis.com/GlobalTriggerSettings": None,  # プロジェクト設定シングルトン。作成不可
    # --- dataplex / servicedirectory（大半は自動生成。classify で判定） ---
    "dataplex.googleapis.com/EntryGroup":     "terraform_apply",
    "servicedirectory.googleapis.com/Namespace": "terraform_apply",
    "servicedirectory.googleapis.com/Service":   "terraform_apply",
    "servicedirectory.googleapis.com/Endpoint":  "terraform_apply",
    # --- Security Command Center（サービス設定オブジェクト。自動存在・作成不可） ---
    "securitycentermanagement.googleapis.com/SecurityCenterService": None,
    "securitycenter.googleapis.com/ContainerThreatDetectionSettings": None,
    "securitycenter.googleapis.com/EventThreatDetectionSettings": None,
    "securitycenter.googleapis.com/SecurityHealthAnalyticsSettings": None,
    "securitycenter.googleapis.com/VirtualMachineThreatDetectionSettings": None,
    "securitycenter.googleapis.com/WebSecurityScannerSettings": None,
    # --- その他（export されない。欠落は要対応） ---
    "certificatemanager.googleapis.com/DnsAuthorization": "terraform_apply",
    "networkservices.googleapis.com/WasmPlugin":         "terraform_apply",
    "networkservices.googleapis.com/WasmPluginVersion":  "terraform_apply",
    "aiplatform.googleapis.com/NotebookRuntimeTemplate": "terraform_apply",
    "networkconnectivity.googleapis.com/InternalRange":  "terraform_apply",
    "dataform.googleapis.com/Repository":                "terraform_apply",
    "firestore.googleapis.com/Database":                 "terraform_apply",
    "dns.googleapis.com/ResourceRecordSet":  None,  # zone とセットで手動移行（zone の注記参照）
    "orgpolicy.googleapis.com/Policy":       "terraform_apply",  # export されない。dst 挙動に影響するため要対応
    # --- dns ---
    "dns.googleapis.com/ManagedZone":        "terraform_apply",  # public は customize が skip + DIFF 要対応
    "dns.googleapis.com/ResourceRecordSet":  None,               # zone とセットで手動移行（委任切替が要る）
    # --- cloud run ---
    # bulk-export はリージョンによって取りこぼす（regression: us-central1 の
    # www-1 / test-1 が未出力）。欠落は DIFF 要対応で手動作成を案内する。
    "run.googleapis.com/Service":  "terraform_apply",
    "run.googleapis.com/Revision": None,   # リビジョン履歴。デプロイで再生成される
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
    "serviceusage.googleapis.com/Service":             "enable_apis",  # Step 1.5 が dst に有効化
    "cloudresourcemanager.googleapis.com/Project":     None,           # create_projects.py
    "cloudresourcemanager.googleapis.com/Lien":        None,           # 削除保護用メタ。複製不要
    "cloudbilling.googleapis.com/ProjectBillingInfo":  None,           # create_projects.py の billing link
    # --- osconfig (任意機能、運用継続には不要) ---
    "osconfig.googleapis.com/OSPolicyAssignment":       None,
    "osconfig.googleapis.com/OSPolicyAssignmentReport": None,
}

# 専用ステップが dst へリソースを複製するため、bulk-export 出力に無くても想定内
# （手動対応不要）。DIFF.md からは除外し件数だけ集計する。
_AUTO_HANDLED_STEPS = frozenset({
    "gce_restore", "network_firewall", "data_sync", "enable_apis",
})


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


def _is_k8s_asset_type(atype: str) -> bool:
    """CAI assetType が GKE クラスタ内の k8s オブジェクトなら True。

    `k8s.io/Pod` / `apps.k8s.io/Deployment` / `rbac.authorization.k8s.io/Role` など
    クラスタ内リソースは GCP リソースとしての複製対象外（dst にクラスタを作った後に
    ワークロードを再デプロイする運用）。種類が無数にあるため個別列挙せず、
    サービス名が `k8s.io` / `*.k8s.io` かで判定する。
    """
    svc = (atype or "").split("/", 1)[0]
    return svc == "k8s.io" or svc.endswith(".k8s.io")


def diff_coverage(asset_types: List[str]) -> Tuple[List[str], List[str]]:
    """(uncovered, covered_but_unimplemented) を返す。

    - uncovered: _ASSET_COVERAGE に存在しない assetType（= 知識ベースに無い）。
      ただし GKE クラスタ内の k8s.io/* オブジェクトは複製対象外が自明なので除く
      （種類が無数にあり、列挙しても警告ノイズにしかならない）。
    - covered_but_unimplemented: マップ上 None = 「意図的対象外」だが
      ISSUE 等で「将来対応予定」とコメントされたものを別途警告したい場合に使用。
      現状は None = 全て対象外扱いとし、空リストを返す（拡張余地）。
    """
    covered = set(_ASSET_COVERAGE.keys())
    uncovered = sorted({
        t for t in asset_types
        if t and t not in covered and not _is_k8s_asset_type(t)
    })
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
    "compute.googleapis.com/InstanceTemplate": ("google_compute_instance_template",
                                                "google_compute_region_instance_template"),
    "compute.googleapis.com/InstanceGroupManager": ("google_compute_instance_group_manager",
                                                    "google_compute_region_instance_group_manager"),
    "compute.googleapis.com/InstanceGroup":   ("google_compute_instance_group",),
    # Cloud Run のサーバーレス NEG は必ず regional として export される。
    # zonal 変種しか登録しないと「bulk-export が出力しなかった」と誤報し続ける。
    "compute.googleapis.com/NetworkEndpointGroup": (
        "google_compute_network_endpoint_group",
        "google_compute_region_network_endpoint_group",
        "google_compute_global_network_endpoint_group"),
    "compute.googleapis.com/TargetPool":      ("google_compute_target_pool",),
    "compute.googleapis.com/HttpHealthCheck": ("google_compute_http_health_check",),
    "compute.googleapis.com/HttpsHealthCheck": ("google_compute_https_health_check",),
    "compute.googleapis.com/HealthCheck":     ("google_compute_health_check",
                                               "google_compute_region_health_check"),
    "compute.googleapis.com/Autoscaler":      ("google_compute_autoscaler",
                                               "google_compute_region_autoscaler"),
    "compute.googleapis.com/ForwardingRule":  ("google_compute_forwarding_rule",
                                               "google_compute_global_forwarding_rule"),
    "container.googleapis.com/Cluster":       ("google_container_cluster",),
    "container.googleapis.com/NodePool":      ("google_container_node_pool",),
    "artifactregistry.googleapis.com/Repository": ("google_artifact_registry_repository",),
    "run.googleapis.com/Service": ("google_cloud_run_v2_service",
                                   "google_cloud_run_service"),
    "pubsub.googleapis.com/Topic": ("google_pubsub_topic",),
    "monitoring.googleapis.com/AlertPolicy": ("google_monitoring_alert_policy",),
    "monitoring.googleapis.com/NotificationChannel": ("google_monitoring_notification_channel",),
    "logging.googleapis.com/LogMetric": ("google_logging_metric",),
    "compute.googleapis.com/BackendService": ("google_compute_backend_service",
                                              "google_compute_region_backend_service"),
    "compute.googleapis.com/SecurityPolicy": ("google_compute_security_policy",),
    "compute.googleapis.com/TargetHttpsProxy": ("google_compute_target_https_proxy",),
    "compute.googleapis.com/TargetHttpProxy": ("google_compute_target_http_proxy",),
    "compute.googleapis.com/UrlMap": ("google_compute_url_map",),
    "compute.googleapis.com/SslCertificate": ("google_compute_ssl_certificate",
                                              "google_compute_managed_ssl_certificate"),
    "secretmanager.googleapis.com/Secret": ("google_secret_manager_secret",),
    "cloudfunctions.googleapis.com/Function": ("google_cloudfunctions_function",
                                               "google_cloudfunctions2_function"),
    "cloudbuild.googleapis.com/BuildTrigger": ("google_cloudbuild_trigger",),
    "dataplex.googleapis.com/EntryGroup": ("google_dataplex_entry_group",),
    "servicedirectory.googleapis.com/Namespace": ("google_service_directory_namespace",),
    "servicedirectory.googleapis.com/Service": ("google_service_directory_service",),
    "servicedirectory.googleapis.com/Endpoint": ("google_service_directory_endpoint",),
    "certificatemanager.googleapis.com/DnsAuthorization": ("google_certificate_manager_dns_authorization",),
    "networkconnectivity.googleapis.com/InternalRange": ("google_network_connectivity_internal_range",),
    "firestore.googleapis.com/Database": ("google_firestore_database",),
    "dataform.googleapis.com/Repository": ("google_dataform_repository",),
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
    `state`（RESERVED / IN_USE 等）と additionalAttributes 内の `address:`（IP 値）は
    Address の要対応 / 参考分類に使うため拾う。
    Returns:
        [{asset_type, name, short_name, location, project, display_name,
          state?, ip_address?}, ...]
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
            if not s:
                continue
            if s.startswith(" ") or s.startswith("\t"):
                ss = s.strip()
                if ss.startswith("address:") and "ip_address" not in current:
                    current["ip_address"] = ss.split(":", 1)[1].strip()
                continue
            if s.startswith("-"):
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
            elif k == "state":
                current["state"] = v
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

    走査は再帰。bulk-export の raw は
    `<src>/projects/<proj>/<Kind>/<location>/<name>.tf` の深いツリーで、
    フラット走査だと raw から 1 件も拾えず DIFF が全リソースを
    「bulk-export が出力しなかった」と誤検知する。
    """
    out: Dict[str, List[str]] = {}
    if not os.path.isdir(tf_dir):
        return out
    for root, dirs, files in os.walk(tf_dir):
        # .terraform/ は provider / module キャッシュ。移行対象の定義ではない。
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for fn in sorted(files):
            if not fn.endswith(".tf"):
                continue
            path = os.path.join(root, fn)
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
                if nm:
                    out.setdefault(rtype, []).append(nm.group(1))
                    continue
                # name を持たない型は型固有の ID 属性を使う（例: Artifact Registry の
                # repository_id、SA の account_id、Secret の secret_id）。これを
                # 見ないと Terraform ラベル（ハイフンが _ に変換されている）と
                # CAI 名（ハイフン）が食い違い、export 済みリソースまで
                # 「bulk-export が出力しなかった」と誤検知する（regression:
                # AR リポジトリ 5 件が要対応に出ていた）。
                idm = re.search(
                    r'^\s*(?:repository_id|account_id|secret_id|metric_id|'
                    r'trigger_id|topic|dataset_id)\s*=\s*"([^"]+)"', body, re.M)
                if idm:
                    out.setdefault(rtype, []).append(idm.group(1))
                    continue
                # ラベルにフォールバック。bulk-export のラベルは名前のハイフンを
                # `_` に変えただけのことが多いので、逆変換の別名も登録して
                # CAI 名（ハイフン）と照合できるようにする。
                out.setdefault(rtype, []).append(label)
                if "_" in label:
                    out.setdefault(rtype, []).append(label.replace("_", "-"))
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


# ---------------------------------------------------------------------------
# GKE 関連の判定（ノード VM 除外 / 自動生成リソース判定）
# ---------------------------------------------------------------------------
# GKE は「クラスタ / ノードプールの構成情報だけ」を terraform (Step 3/4) で複製する。
# ノード VM とその派生リソース（instance template / MIG / FW rule 等）は dst に
# クラスタを作れば GKE が自分で作り直すため、src からコピーしてはいけない
# （二重化するうえ、src の ORG 固有 ID / クラスタハッシュを含み dst では無効）。
#
# ノード VM の名前接頭辞（standard: gke-、Autopilot: gk3-）。
_GKE_NODE_NAME_PREFIXES = ("gke-", "gk3-")
# ノードであることを示す metadata キー（labels が取得できない場合の補助判定）。
_GKE_NODE_METADATA_KEYS = frozenset({"kube-labels", "kube-env", "cluster-name"})
# GKE / k8s コントローラが自動生成するリソース名の接頭辞。
#   gke- / gk3- : ノード関連（instance template / MIG / autoscaler / FW rule）
#   k8s- / k8s2-: Service type=LoadBalancer が作る FW / forwarding rule 等
#   k8s1-       : NEG、gkegw1-: Gateway controller
_GKE_MANAGED_NAME_PREFIXES = ("gke-", "gk3-", "k8s-", "k8s1-", "k8s2-", "gkegw1-")

# customize_hcl がドロップする GKE 派生 terraform リソース型
# （name が gke-*/k8s-* のもののみドロップ。ユーザー作成の同型リソースは残す）。
# google_container_cluster / google_container_node_pool はここに入れない
# （＝ GKE 構成そのものは複製する。これが本機能の目的）。
_GKE_MANAGED_TF_RESOURCE_TYPES = (
    "google_compute_instance_template",
    "google_compute_instance_group_manager",
    "google_compute_region_instance_group_manager",
    "google_compute_autoscaler",
    "google_compute_region_autoscaler",
    "google_compute_instance_group",
    "google_compute_health_check",
    "google_compute_http_health_check",
    "google_compute_target_pool",
    "google_compute_route",
    "google_compute_firewall",
)

# CAI 上で GKE が自動生成する派生 compute アセット。名前が gke-*/k8s-* なら
# DIFF.md では「参考」扱いにする（dst クラスタが再生成するため実害なし）。
_GKE_DERIVED_ASSET_TYPES = frozenset({
    "compute.googleapis.com/InstanceTemplate",
    "compute.googleapis.com/InstanceGroupManager",
    "compute.googleapis.com/InstanceGroup",
    "compute.googleapis.com/NetworkEndpointGroup",
    # k8s Service (type=LoadBalancer) が作る LB 一式。名前は gke-*/k8s-* 接頭辞
    # または a+32hex（is_k8s_lb_resource_name）。
    "compute.googleapis.com/TargetPool",
    "compute.googleapis.com/ForwardingRule",
    "compute.googleapis.com/HttpHealthCheck",
    "compute.googleapis.com/HealthCheck",
    "compute.googleapis.com/Autoscaler",
    # GKE の Pod range に対応して自動作成される internal range 表現
    "networkconnectivity.googleapis.com/InternalRange",
})


def is_gke_node_vm(vm: Dict[str, Any]) -> bool:
    """VM が GKE のノード（standard / Autopilot）なら True。

    判定順:
      1. ラベル `goog-gke-node` を持つ（GKE が全ノードに付与する。値は空文字）
      2. 名前が gke- / gk3- 始まり **かつ** ノード固有 metadata (kube-env 等) を持つ
         … ラベルが取得できなかった場合の保険

    名前の一致だけでは True にしない。ユーザーが `gke-` で始まる VM を作っている
    ことがあり、誤除外すると「コピーしたはずの VM が dst に無い」事故になる。
    """
    if not isinstance(vm, dict):
        return False
    labels = vm.get('labels') or {}
    if isinstance(labels, dict) and 'goog-gke-node' in labels:
        return True
    name = vm.get('name') or ''
    if not name.startswith(_GKE_NODE_NAME_PREFIXES):
        return False
    items = (vm.get('metadata') or {}).get('items') or []
    for it in items:
        if isinstance(it, dict) and it.get('key') in _GKE_NODE_METADATA_KEYS:
            return True
    return False


def is_gke_managed_name(name: Optional[str]) -> bool:
    """リソース名が GKE / k8s の自動生成命名なら True。

    接頭辞を足すと terraform ドロップ (`_skip_reason_for_file`) / classic FW ルール
    スキップ / DIFF.md 分類の 3 箇所すべてに同時に効く点に注意。
    """
    return bool(name) and str(name).startswith(_GKE_MANAGED_NAME_PREFIXES)


# k8s の service controller が GCE リソース（target pool / forwarding rule /
# health check / FW ルール）に必ず書き込む所有者マーカー。名前が hex UID
# （`a<31hex>`）で接頭辞判定に掛からないリソースはこれで判定する。
# HCL 上はエスケープ済み（description = "{\"kubernetes.io/service-name\":...}"）。
_K8S_OWNER_MARKER_RE = re.compile(
    r'^\s*description\s*=\s*".*kubernetes\.io/(service-name|service-ip|cluster-id)',
    re.M,
)

# k8s Service (type=LoadBalancer) が作るリソース名: "a" + サービス UID の hex 32 桁。
_K8S_LB_NAME_RE = re.compile(r'^a[0-9a-f]{31}$')


def has_k8s_owner_marker(content: str) -> bool:
    """HCL 本文の description に kubernetes.io 所有者マーカーがあれば True。"""
    return bool(_K8S_OWNER_MARKER_RE.search(content or ""))


def is_k8s_lb_resource_name(name: Optional[str]) -> bool:
    """名前が k8s LB リソースの UID 由来命名（a+32hex）なら True。"""
    return bool(name) and bool(_K8S_LB_NAME_RE.match(str(name)))


# GKE 本体が作る classic FW ルール名: gke-<cluster>-<8hex>-<suffix>
# （vms / all / master / exkubelet / inkubelet 等。suffix は増えるので固定しない）。
# こちらは description マーカーを持たないため構造で判定する。
_GKE_CORE_FW_NAME_RE = re.compile(r'^(gke|gk3)-.+-[0-9a-f]{8}-[a-z0-9-]+$')

# k8s service controller のルール名（hex ハッシュ入りの機械命名）。description
# マーカーが正だが、gcloud の list 出力に description が無い等の場合の保険。
_K8S_FW_NAME_RE = re.compile(
    r'^(k8s-fw-a?[0-9a-f]{6,}(-[a-z0-9-]+)?'
    r'|k8s-[0-9a-f]{12,}-node(-[a-z0-9-]+)?'
    r'|k8s2-[0-9a-f]{4,10}-.+-[0-9a-f]{4,10})$'
)


def is_gke_managed_fw_rule(rule: Dict[str, Any]) -> bool:
    """classic FW ルールが GKE / k8s の自動生成なら True。

    第一判定は description の kubernetes.io 所有者マーカー（k8s service
    controller が作る k8s-fw-* / k8s2-* / ノード HC ルールは必ず持つ）。
    フォールバックは構造判定（GKE 本体ルールのクラスタ固有 8 hex /
    k8s ルールの hex ハッシュ命名）。**接頭辞だけでは判定しない**:
    `k8s-nodeport-allow` / `gke-admin-bastion` のような利用者ルール
    （DENY かもしれない）を落とすと dst が src より緩くなる。
    """
    if 'kubernetes.io/' in (rule.get('description') or ''):
        return True
    name = rule.get('name') or ''
    return bool(_GKE_CORE_FW_NAME_RE.match(name)) or bool(_K8S_FW_NAME_RE.match(name))


# ---------------------------------------------------------------------------
# DIFF.md の「要対応 (action)」/「参考 (reference)」分類
# ---------------------------------------------------------------------------
# CAI ↔ TF の欠落は放置すると数十件になり、本当に手を動かす必要があるものが埋もれる。
# 「dst に無いと実害があるか」で二分し、先頭の WHAT / WHY / HOW テーブルには action
# だけを載せる。reference も消さずに残す（後から判断を追える形で記録する）。
#
# GCP が全プロジェクトに自動生成する logging リソース。dst にも既に存在し create 不可。
_MANAGED_LOG_RESOURCE_NAMES = frozenset({"_Default", "_Required"})
# 移行オーケストレータ自身が bootstrap_cross_project.sh で src に作る借用 SA 用ロール。
# 移行後の dst 運用には不要なので複製しない。
_MIGRATION_TOOL_ROLE_IDS = frozenset({"migrationSrcReader"})

# DIFF.md「参考」の優先度。テーブル / 詳細はこの昇順でソートして出力する。
# 1: 別ステップが自動対応済み → dst 側で結果を一度確認すると確実
# 2: src 側にカスタム / 取り置きの意図がある場合のみ手動対応
# 3: どの環境でも何もしなくてよい
_DIFF_PRIORITY_LABELS = {1: "確認推奨", 2: "条件付き", 3: "対応不要"}


def _is_private_ip(value: Optional[str]) -> bool:
    """RFC1918 等のプライベート IP なら True。パース不能は False（安全側 = 判定不能扱い）。"""
    try:
        return ipaddress.ip_address(value or "").is_private
    except ValueError:
        return False


def bound_custom_role_ids(src_policies: Dict[str, Dict[str, Any]]) -> set:
    """src の project IAM ポリシーで実際に誰かへ付与されているカスタムロール ID の集合。

    `projects/<p>/roles/<r>` / `organizations/<id>/roles/<r>` 形式のみ返す。
    定義だけあってどこにも付与されていないカスタムロールを DIFF.md の
    「参考」に落とすための判定材料。
    """
    out: set = set()
    for policy in (src_policies or {}).values():
        for b in ((policy or {}).get('bindings') or []):
            if not isinstance(b, dict):
                continue
            role = b.get('role') or ''
            if role.startswith("projects/") or role.startswith("organizations/"):
                out.add(role)
    return out


def cai_in_use_internal_addresses(cai_path: str) -> Set[str]:
    """CAI から「src で VM 等が使用中（IN_USE）の内部アドレス」名を返す（純粋関数）。

    これらの予約は Step 5 (gce_restore) が VM と同じプロジェクトに
    `mig-<vm>-<ip>` として作り直す責務を持つ（DIFF の P1 分類と同じ設計知識）。
    Terraform 側でも複製すると二重予約になり、特に Shared VPC では
    「host 側の予約が service プロジェクトの VM 作成をブロックする」
    （reserved by another project）ため、customize で複製から外す。
    RESERVED（未使用の取り置き）は元 IP のまま複製する（対象外）。
    """
    names: Set[str] = set()
    for rec in parse_cai_resources(cai_path):
        if not rec.get("asset_type", "").endswith("/Address"):
            continue
        if rec.get("state") != "IN_USE":
            continue
        if not _is_private_ip(rec.get("ip_address")):
            continue
        short = (rec.get("short_name") or "").strip()
        if short:
            names.add(short)
    return names


def parse_krm_kinds(text: Optional[str]) -> List[str]:
    """`gcloud beta resource-config list-resource-types --format=json` を解析する。

    bulk-export 可能な KRM Kind 名（`ComputeInstance` 等）だけをソートして返す
    （純粋関数）。この一覧は **GCP リソースの Kind のみ**で、クラスタ内の
    k8s オブジェクト（Pod / Deployment 等）は最初から含まれない。
    """
    try:
        data = json.loads(text) if text else []
    except (ValueError, TypeError):
        return []
    kinds: Set[str] = set()
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict) or not item.get("SupportsBulkExport"):
            continue
        kind = ((item.get("GVK") or {}).get("Kind") or "").strip()
        if kind:
            kinds.add(kind)
    return sorted(kinds)


def tf_type_kept(
    tf_type: str, include: Iterable[str], exclude: Iterable[str],
) -> bool:
    """Terraform リソース型が移行対象かを判定する（純粋関数）。

    `steps.bulk_export.resource_types` による移行範囲の絞り込み。
    - `exclude` に一致したら対象外（**exclude が include より強い**）
    - `include` が空なら全型が対象（既定 = 全量コピー）
    - `include` があればそれに一致する型だけが対象
    パターンは fnmatch（`google_compute_*` のようなワイルドカード）。
    """
    inc = [p for p in (include or []) if p]
    exc = [p for p in (exclude or []) if p]
    if any(fnmatch.fnmatchcase(tf_type, p) for p in exc):
        return False
    if not inc:
        return True
    return any(fnmatch.fnmatchcase(tf_type, p) for p in inc)


def resource_type_filter_reason(
    tf_types: Iterable[str], include: Iterable[str], exclude: Iterable[str],
) -> Optional[str]:
    """ファイル内の全リソース型が対象外なら skip 理由を返す（純粋関数）。

    **1 つでも対象の型が残るファイルは落とさない**（安全側 = コピーする）。
    resource ブロックが無いファイルやフィルタ未指定のときは None。
    """
    types = [t for t in (tf_types or []) if t]
    if not types:
        return None
    if not [p for p in (include or []) if p] and not [p for p in (exclude or []) if p]:
        return None
    if any(tf_type_kept(t, include, exclude) for t in types):
        return None
    return (f"resource_types の対象外（{', '.join(sorted(set(types)))}）")


def classify_missing_asset(
    item: Dict[str, Any],
    iam_sync_enabled: bool = True,
    bound_custom_roles: Optional[set] = None,
    gce_restore_enabled: bool = True,
    rt_include: Optional[Iterable[str]] = None,
    rt_exclude: Optional[Iterable[str]] = None,
    run_service_names: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    """欠落 1 件を action / reference に分類し、WHAT 用の種別と WHY / HOW を返す。

    Args:
        item: analyze_cai_tf_diff が組み立てる missing エントリ
        iam_sync_enabled: Step 5.7 iam_sync が有効か（SA を dst に作る担当）
        bound_custom_roles: src の project IAM ポリシーで付与済みのカスタムロール ID。
                            None は「判定材料が無い」= 安全側で action に倒す。
        gce_restore_enabled: Step 5 gce_restore が有効か（VM 内部 IP を dst に予約する担当）

    Returns: `{'level': 'action'|'reference', 'kind': str, 'why': str, 'how': str,
               'priority': int}`
             priority は reference のみ意味を持つ（_DIFF_PRIORITY_LABELS。action は 0）。

    判定できないものは必ず action 側に倒す（見落とすより過剰報告を選ぶ）。
    reference に落とすのは「実害が無いと言い切れる」ものだけ。
    """
    atype = item.get("asset_type", "")
    short = item.get("short_name", "")
    full = item.get("full_name", "")
    kind = atype.split("/", 1)[1] if "/" in atype else atype

    def ref(why: str, how: str = "対応不要。", priority: int = 3) -> Dict[str, Any]:
        return {"level": "reference", "kind": kind, "why": why, "how": how,
                "priority": priority}

    def act(why: str, how: str) -> Dict[str, Any]:
        return {"level": "action", "kind": kind, "why": why, "how": how, "priority": 0}

    # 利用者が resource_types で意図的に対象外にした型は「対応不要」。
    # 期待 TF 型が全て対象外のときだけ落とす（型が引けないものは従来判定へ）。
    expected_tf = _CAI_TO_TF_RESOURCE.get(atype, ())
    if expected_tf and (rt_include or rt_exclude) and not any(
            tf_type_kept(t, rt_include or [], rt_exclude or []) for t in expected_tf):
        return ref(
            "steps.bulk_export.resource_types で移行対象から除外している型"
            f"（{', '.join(expected_tf)}）。設定どおり dst に作られていない。",
            "移行したくなった場合は resource_types の指定を外して再実行。",
            priority=3)

    if atype == "iam.googleapis.com/ServiceAccount":
        if not parse_user_managed_sa(short):
            return ref(
                "default compute / appspot / Google 管理 service agent。dst には dst 自身の"
                "プロジェクト番号を持つ同等 SA が既定で存在する（同名で作っても別物になる）。",
                priority=3)
        if iam_sync_enabled:
            return ref(
                "Step 5.7 iam_sync が dst に同名 SA を冪等作成し、src の project IAM ロールも"
                "あわせて複製する。",
                "dst.log の `[SA] ... を新規作成しました` を確認。無ければ `make run` を再実行。",
                priority=1)
        return act(
            "steps.iam_sync.enabled=false のため、この SA を dst に作るステップが存在しない。",
            "iam_sync を有効化して `make run` するか、下記の create コマンドを手動実行。")

    if atype == "iam.googleapis.com/Role":
        role_id = full.rsplit("/", 1)[-1] if full else short
        if role_id in _MIGRATION_TOOL_ROLE_IDS:
            return ref(
                "移行ツール自身が bootstrap_cross_project.sh で src に作った借用 SA 用ロール。"
                "移行後の dst 運用には不要。",
                priority=3)
        role_name = full.split("//iam.googleapis.com/", 1)[-1] if full else ""
        if bound_custom_roles is not None and role_name and role_name not in bound_custom_roles:
            return ref(
                "src の project IAM ポリシーで誰にも付与されていない（定義だけが残っている）。"
                "複製しなくても src と dst で実効権限は変わらない。",
                "バケット / データセット等リソース単位で付与している場合のみ手動複製。",
                priority=2)
        return act(
            "src で SA に付与されているカスタムロール。dst に定義が無いと Step 5.7 が付与を"
            "スキップし、dst SA の権限が src より不足する。",
            "`gcloud iam roles describe <ID> --project=<src> --format=json` で定義を取得 → "
            "下記の create コマンドで dst に作成 → `make run` 再実行（Step 5.7 が付与）。")

    if atype in ("logging.googleapis.com/LogBucket", "logging.googleapis.com/LogSink"):
        if short in _MANAGED_LOG_RESOURCE_NAMES:
            return ref(
                "GCP が全プロジェクトに自動生成する既定リソース。dst にも既に存在し、create は"
                "「already exists」で失敗する。",
                "src で保持期間 / フィルタをカスタムしている場合のみ dst 側を "
                "`gcloud logging buckets update` / `sinks update` で合わせる。",
                priority=2)
        return act(
            "ユーザー定義のログルーティング設定。dst に無いとログの転送先 / 保持期間が src と"
            "変わる。",
            "src で `describe` した内容で下記の create コマンドを埋めて実行。")

    if atype == "compute.googleapis.com/Address":
        # CAI の state（RESERVED / IN_USE）と IP 値から「実害が無いと言い切れる」ものだけ
        # reference に落とす。state 不明 / 使用中の外部 IP は従来どおり action。
        state = (item.get("state") or "").upper()
        if short.startswith("nat-auto-ip-"):
            return ref(
                "Cloud NAT が自動割当した外部 IP。dst で NAT を構成すれば自動採番される"
                "（system 生成名のため同名の手動作成は不可能かつ無意味）。",
                priority=3)
        if state == "RESERVED":
            return ref(
                "どのリソースにも使われていない予約（取り置き）。dst に無くても何も壊れない。",
                "IP の取り置きを dst でも維持したい場合のみ下記 create コマンドで予約"
                "（内部 IP は同じ値で予約可。外部 IP は値が変わる）。",
                priority=2)
        if state == "IN_USE" and gce_restore_enabled and _is_private_ip(item.get("ip_address")):
            return ref(
                "VM にアタッチ中の内部 IP。Step 5 gce_restore が同じ IP 値を dst に "
                "`mig-<vm>-<ip>` 名で静的予約してアタッチする（予約リソース名が src と"
                "異なるだけで機能は等価）。",
                "dst.log の `内部IP予約 mig-...` / `private-network-ip=` を確認。"
                "VM 以外（内部 LB 等）が使用している IP の場合のみ下記 create コマンドで"
                "手動予約。",
                priority=1)
        return act(
            "使用中 (IN_USE) だが自動複製の担当が無い、または状態を判定できない予約 IP。"
            "dst に同等の予約が無いと、参照しているリソースの静的 IP 前提が崩れる。",
            "src で `gcloud compute addresses describe <名前> --project=<src> "
            "--format='value(address,status,users)'` で用途を確認 → 必要なら下記の "
            "create コマンドで dst に予約（**IP 値は変わる**ので参照側の設定も更新）。")

    # Cloud DNS はゾーンを name と数値 ID（v2 API 表現）の 2 系統で CAI に出す。
    # 数値 ID の行は名前付きゾーン行の重複計上なので、独立の作業項目にしない。
    if atype == "dns.googleapis.com/ManagedZone" and short.isdigit():
        return ref(
            "同一ゾーンの別表現（CAI の v2 API 形式は数値 ID で同じゾーンを再掲する）。",
            "名前付きの ManagedZone 行（または customize 注記）で対応すれば足りる。",
            priority=2)

    # 通知チャネルは server 採番 ID のため別プロジェクトへ同 ID では複製できない。
    # customize が参照を除去して注記済み（alert_notification_channels）。
    if atype == "monitoring.googleapis.com/NotificationChannel":
        return ref(
            "通知チャネルは server 採番 ID で、同じ ID を dst に作ることは不可能。"
            "customize がアラートからの参照を外し、DIFF の注記に再設定手順がある。",
            "dst でチャネルを作成し直し、アラートポリシーに再設定する（注記参照）。",
            priority=2)

    # gen2 Cloud Functions の実体は Cloud Run サービス。同名の run.Service が
    # CAI にあるなら二重計上なので、Cloud Run 側の行に集約する。
    if (atype == "cloudfunctions.googleapis.com/Function"
            and run_service_names and short in run_service_names):
        return ref(
            "gen2 Cloud Functions。実体は同名の Cloud Run サービス"
            "（別行の run.googleapis.com/Service）で、二重計上を避けるため集約。",
            "Cloud Run サービス側の行に従って対応する。",
            priority=1)

    # Dataplex Universal Catalog のシステム EntryGroup（@bigquery / @storage 等）は
    # 連携サービスから自動生成される。手動作成は不可能かつ不要（dst でデータを
    # 作れば自動的に再生成される）。`@` 始まりでないものは利用者作成なので action。
    if atype == "dataplex.googleapis.com/EntryGroup" and short.startswith("@"):
        return ref(
            "Dataplex Universal Catalog のシステム EntryGroup。BigQuery / GCS 等の"
            "連携サービスから自動生成されるもので、手動作成は不可能かつ不要。",
            "dst 側でデータ（dataset / bucket 等）が複製されれば自動的に再生成される。",
            priority=3)

    # Service Directory の GKE / PSC 自動登録（gk3-* の control plane endpoint、
    # goog-psc-default namespace 等）。クラスタ / PSC を作れば dst で再生成される。
    if atype.startswith("servicedirectory.googleapis.com/") and (
            is_gke_managed_name(short) or short.startswith("goog-")
            or "gk3-" in full or "gke-" in full or "goog-psc" in full):
        return ref(
            "GKE / Private Service Connect が自動登録する Service Directory エントリ。",
            "dst クラスタ / PSC 構成が出来れば自動的に再登録される。",
            priority=3)

    # SecretVersion（秘密値そのもの）は Secret 本体の移行に含めて扱う。
    # 値の複製はツールの対象外（秘密情報を自動で写さない方針）。
    if atype == "secretmanager.googleapis.com/SecretVersion":
        return ref(
            "Secret の値（バージョン）。Secret 本体の移行（別行の要対応）に含めて"
            "対応するもので、独立した作業項目ではない。",
            "Secret 作成時に `gcloud secrets versions add <name> --data-file=-` で"
            "値を投入する（値の転記はツール対象外 = 秘密情報を自動で写さない）。",
            priority=2)

    if atype in _GKE_DERIVED_ASSET_TYPES and (
            is_gke_managed_name(short) or is_k8s_lb_resource_name(short)):
        return ref(
            "GKE / k8s コントローラが自動生成するリソース。Step 4 terraform が "
            "google_container_cluster / node_pool を apply し、Backup for GKE の "
            "restore（または再デプロイ）でワークロードを戻せば、dst クラスタが"
            "同等物を自分で再生成する（src の名前にはクラスタ固有ハッシュが入るため"
            "同名複製は無意味）。",
            "Backup for GKE の restore 完了後、dst で同等リソースが再生成されたことを"
            "確認（例: `gcloud container clusters list` / Service の EXTERNAL-IP）。",
            priority=3)

    if item.get("coverage_step") == "<unknown>":
        return act(
            "_ASSET_COVERAGE に未登録の assetType。どのステップも複製を担当しておらず、複製漏れ"
            "の可能性がある。",
            "dst で必要か判断し、必要なら手動作成。恒久対応として scripts/sync_env.py の "
            "_ASSET_COVERAGE に担当ステップ（不要なら None）を追記。")

    return act(
        item.get("reason") or "自動複製されていない。",
        "下記詳細の推奨コマンドを確認し、必要なら dst で手動作成。")


def analyze_cai_tf_diff(
    cai_path: str, tf_dirs: List[str],
    src_project: str, dst_project: str,
    iam_sync_enabled: bool = True,
    bound_custom_roles: Optional[set] = None,
    gce_restore_enabled: bool = True,
    rt_include: Optional[Iterable[str]] = None,
    rt_exclude: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """CAI と terraform 出力を突合し、欠落リソースとリカバリコマンドを返す。

    Args:
        cai_path:  cai_export/cai_resources_<src>.txt
        tf_dirs:   走査する terraform ディレクトリ群（raw 優先 / active fallback など）。
                   先頭から順に資料を統合し、いずれかに resource が見つかれば「カバー済み」。
        src_project: src プロジェクト ID（ログ表示用）
        dst_project: dst プロジェクト ID（生成コマンドに埋め込む）
        iam_sync_enabled / bound_custom_roles / gce_restore_enabled:
                   classify_missing_asset に渡す判定材料

    Returns:
        {
            'src_project': str,
            'dst_project': str,
            'cai_total':   int,
            'tf_total':    int,
            'covered':     int,
            'missing':     [ {asset_type, short_name, full_name, location,
                              tf_resource_type, coverage_step, reason, commands,
                              level, kind, why, how}, ...],
            'action_total': int,   # level == 'action' の件数
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
    # gen2 Cloud Functions（実体 = Cloud Run）の二重計上を classify で集約するための集合
    run_service_names = {
        r.get("short_name", "") for r in cai_records
        if r.get("asset_type") == "run.googleapis.com/Service"
    }

    for r in cai_records:
        atype = r.get("asset_type", "")
        # GKE クラスタ内の k8s オブジェクトは GCP リソースとしての複製対象外。
        # 種類も件数も多く、DIFF.md に出すと本当に手を動かすものが埋もれる。
        if _is_k8s_asset_type(atype):
            auto_handled += 1
            continue
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

        entry = {
            "asset_type": atype,
            "short_name": short,
            "full_name": full,
            "location": loc,
            "state": r.get("state", ""),
            "ip_address": r.get("ip_address", ""),
            "tf_resource_type": "/".join(tf_types) if tf_types else None,
            "coverage_step": coverage_step,
            "reason": reason,
            "commands": gcloud_recreate_command(atype, short, loc, dst_project, full),
        }
        entry.update(classify_missing_asset(
            entry, iam_sync_enabled=iam_sync_enabled,
            bound_custom_roles=bound_custom_roles,
            gce_restore_enabled=gce_restore_enabled,
            rt_include=rt_include, rt_exclude=rt_exclude,
            run_service_names=run_service_names,
        ))
        missing.append(entry)

    return {
        "src_project": src_project,
        "dst_project": dst_project,
        "cai_total": len(cai_records),
        "tf_total": sum(len(v) for v in tf_resources.values()),
        "covered": covered,
        "auto_handled": auto_handled,
        "missing": missing,
        "action_total": sum(1 for m in missing if m["level"] == "action"),
        "unknown_types": sorted(unknown_types),
    }


def _md_cell(text: str) -> str:
    """Markdown テーブルのセルとして安全な 1 行文字列にする。"""
    return (text or "").replace("|", "\\|").replace("\n", " ").strip()


def _diff_summary_rows(
    reports: List[Dict[str, Any]], level: str,
) -> List[Tuple[int, str, str, str]]:
    """missing を (dst, kind, why) でまとめ、(priority, WHAT, WHY, HOW) の行に畳む。

    同じ理由の欠落が 1 プロジェクトに何十件も出る（Address 等）ため、件数と代表名
    だけをテーブルに載せ、個別の gcloud コマンドは詳細セクションへ譲る。
    reference は priority 昇順（同順位は検出順を維持）でソートして返す。
    """
    groups: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    order: List[Tuple[str, str, str]] = []
    for r in reports:
        dst = r["dst_project"] or "<未設定>"
        for m in r["missing"]:
            if m["level"] != level:
                continue
            key = (dst, m["kind"], m["why"])
            if key not in groups:
                groups[key] = {"names": [], "how": m["how"],
                               "priority": m.get("priority", 0)}
                order.append(key)
            groups[key]["names"].append(m["short_name"])

    if level == "reference":
        order.sort(key=lambda k: groups[k]["priority"])

    rows: List[Tuple[int, str, str, str]] = []
    for key in order:
        dst, kind, why = key
        names = groups[key]["names"]
        shown = ", ".join(f"`{n}`" for n in names[:3])
        if len(names) > 3:
            shown += f" 他 {len(names) - 3} 件"
        what = f"`{dst}` の **{kind}** {len(names)} 件<br>{shown}"
        rows.append((groups[key]["priority"], _md_cell(what), _md_cell(why),
                     _md_cell(groups[key]["how"])))
    return rows


def format_diff_report(
    reports: List[Dict[str, Any]],
    manual_notes: Optional[List[Dict[str, str]]] = None,
) -> str:
    """analyze_cai_tf_diff の結果群を Markdown レポートに整形する。

    構成は「先頭に要対応の WHAT / WHY / HOW テーブル → customize の手動対応・
    確認注記 → 参考（対応不要と判定したもの）→ プロジェクト別の詳細」。
    実際に手を動かす必要があるものが 50 件超の一覧に埋もれないようにするのが目的。

    manual_notes は customize_hcl が積んだ注記（`load_customize_notes` の戻り値）。
    CAI 差分とは別系統（「リソースが無い」ではなく「複製したが補正した / 意図的に
    スキップした」）なので専用セクションで出す。

    DIFF.md と stdout の両方で同じテキストを使い、ログには `\n`.split() で行ごと書く。
    """
    action_rows = _diff_summary_rows(reports, "action")
    ref_rows = _diff_summary_rows(reports, "reference")
    action_total = sum(r.get("action_total", 0) for r in reports)
    ref_total = sum(
        len(r["missing"]) - r.get("action_total", 0) for r in reports
    )
    note_rows = sorted(
        (customize_note_row(n) for n in (manual_notes or [])),
        key=lambda row: (0 if row[0] == "要対応" else 1, row[1]),
    )

    lines: List[str] = []
    lines.append("# CAI ↔ Terraform bulk-export 差分レポート")
    lines.append("")
    lines.append("Cloud Asset Inventory（CAI）が観測した src 側リソースのうち、")
    lines.append("bulk-export / terraform で自動再現されなかったものを、")
    lines.append("**要対応**（dst の動作に必要で、手動対応しないと実害が出るもの）と")
    lines.append("**参考**（実害が無いと判定したもの。優先度順）に分けて記録します。")
    lines.append("")
    summary = f"- 要対応: **{action_total}** 件 / 参考: **{ref_total}** 件"
    if note_rows:
        summary += f" / customize 補正・スキップ: **{len(note_rows)}** 件"
    lines.append(summary)
    lines.append("")
    lines.append("## 要対応")
    lines.append("")
    if not action_rows:
        lines.append("要対応の欠落はありません。 ✓")
        lines.append("")
    else:
        lines.append("| WHAT（何が dst に無いか） | WHY（なぜ対応が必要か） | HOW（どう対応するか） |")
        lines.append("| --- | --- | --- |")
        for _p, what, why, how in action_rows:
            lines.append(f"| {what} | {why} | {how} |")
        lines.append("")
        lines.append("個別リソースの gcloud コマンドは「プロジェクト別 詳細」を参照してください。")
        lines.append("")

    if note_rows:
        lines.append("## customize による補正・スキップ（手動対応・確認）")
        lines.append("")
        lines.append("Terraform を通すために customize_hcl が行った補正・スキップのうち、")
        lines.append("利用者の**手動対応**または**確認**が必要なものです")
        lines.append("（`.tf` を再生成するたびに更新されます）。")
        lines.append("")
        lines.append("| 種別 | 対象 | 理由 | 対応 |")
        lines.append("| --- | --- | --- | --- |")
        for kind, what, why, how in note_rows:
            mark = "**要対応**" if kind == "要対応" else "確認"
            lines.append(f"| {mark} | {what} | {why} | {how} |")
        lines.append("")

    lines.append("## 参考（実害なしと判定したもの / 優先度順）")
    lines.append("")
    lines.append("記録として残しますが、放置して問題ありません。優先度の意味:")
    lines.append("")
    lines.append("- **1: 確認推奨** … 別ステップが自動対応済み。dst 側で結果を一度確認すると確実")
    lines.append("- **2: 条件付き** … src 側にカスタムや取り置きの意図がある場合のみ手動対応")
    lines.append("- **3: 対応不要** … どの環境でも何もしなくてよい")
    lines.append("")
    if not ref_rows:
        lines.append("該当なし。")
        lines.append("")
    else:
        lines.append("| 優先度 | WHAT | 実害なしと判定した理由 | 補足 |")
        lines.append("| --- | --- | --- | --- |")
        for p, what, why, how in ref_rows:
            label = _DIFF_PRIORITY_LABELS.get(p, "?")
            lines.append(f"| {p}: {label} | {what} | {why} | {how} |")
        lines.append("")

    lines.append("なお、以下は差分としても数えていません（件数のみ集計）:")
    lines.append("")
    lines.append("- 専用ステップ（Step 4.5 network_firewall / Step 5 gce_restore / Step 6 data_sync）が複製。")
    lines.append("- `_ASSET_COVERAGE` で None 指定の意図的対象外（実害なし）。")
    lines.append("- GKE クラスタ内の k8s.io/* オブジェクト（dst クラスタ作成後に "
                 "**Backup for GKE の backup/restore** で移行、または再デプロイ）。")
    lines.append("")
    lines.append("## プロジェクト別 詳細")
    lines.append("")
    lines.append("（read 操作の describe / list は省き、作成系コマンドのみ掲載）")
    lines.append("")

    grand_total = 0
    for r in reports:
        sp = r["src_project"]
        dp = r["dst_project"] or "<未設定>"
        lines.append(f"### プロジェクト: `{sp}` → `{dp}`")
        lines.append("")
        lines.append(
            f"- CAI 検出リソース: **{r['cai_total']}** 件"
            f" / TF 出力リソース: **{r['tf_total']}** 件"
            f" / 一致: **{r['covered']}** 件"
            f" / 要対応: **{r.get('action_total', 0)}** 件"
            f" / 参考: **{len(r['missing']) - r.get('action_total', 0)}** 件"
            f" / 自動処理・対象外: **{r.get('auto_handled', 0)}** 件"
        )
        if r["unknown_types"]:
            lines.append(
                f"- 未登録 assetType: " + ", ".join(f"`{t}`" for t in r["unknown_types"])
            )
        lines.append("")
        if not r["missing"]:
            lines.append("欠落なし。 ✓")
            lines.append("")
            continue

        # 種別ごとにグルーピングし、要対応を先に並べる
        by_type: Dict[str, List[Dict[str, Any]]] = {}
        for m in r["missing"]:
            by_type.setdefault(m["asset_type"], []).append(m)
        for atype in sorted(
            by_type, key=lambda t: (
                all(x["level"] != "action" for x in by_type[t]), t,
            )
        ):
            items = sorted(by_type[atype], key=lambda x: x["level"] != "action")
            n_act = sum(1 for x in items if x["level"] == "action")
            lines.append(
                f"#### `{atype}` （{len(items)} 件 / うち要対応 {n_act} 件）"
            )
            lines.append("")
            # 同じ WHY / HOW を項目ごとに繰り返すと読めなくなるため、判定単位で
            # 見出しにまとめ、項目にはリソース固有の情報だけを書く。
            sub: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = {}
            for m in items:
                sub.setdefault((m["level"], m["why"], m["how"]), []).append(m)
            for (level, why, how), group in sorted(
                sub.items(),
                key=lambda kv: (kv[0][0] != "action",
                                kv[1][0].get("priority", 0)),
            ):
                if level == "action":
                    badge = "要対応"
                else:
                    p = group[0].get("priority", 0)
                    badge = f"参考 / 優先度 {p}: {_DIFF_PRIORITY_LABELS.get(p, '?')}"
                lines.append(f"##### [{badge}] {len(group)} 件")
                lines.append("")
                lines.append(f"- WHY: {why}")
                lines.append(f"- HOW: {how}")
                cov = group[0].get("coverage_step")
                cov_disp = cov if cov is not None else "意図的対象外 (None)"
                lines.append(f"- 担当ステップ: `{cov_disp}`")
                lines.append(f"- 期待 TF 型: `{group[0]['tf_resource_type'] or 'なし'}`")
                lines.append(f"- 検出理由: {group[0]['reason']}")
                lines.append("")
                for m in group:
                    grand_total += 1
                    lines.append(
                        f"`{m['short_name']}` (location=`{m['location'] or 'global'}`)"
                        f" — `{m['full_name']}`"
                    )
                    lines.append("")
                    lines.append("```bash")
                    for c in m["commands"]:
                        lines.append(c)
                    lines.append("```")
                    lines.append("")
    lines.append("---")
    lines.append(
        f"合計: 要対応 **{action_total}** 件 / 参考 **{grand_total - action_total}** 件"
    )
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
    # IAM ロール複製は src の project IAM ポリシーを読むだけ（read-only）。
    # roles/viewer に含まれるが、bootstrap_cross_project.sh の絞ったカスタムロール
    # (migrationSrcReader) を使う場合は明示付与が要るため preflight で検査する。
    "iam_sync":         ("resourcemanager.projects.getIamPolicy",),
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
    # resourcemanager.projects.setIamPolicy は **あえてここに入れない**。
    # 既存環境の dst SA (roles/editor 等) には無く、fail-fast にすると
    # bootstrap_dst_sa.sh を再実行するまで移行全体が止まってしまう。
    # 権限の有無は step_iam_sync が dst プロジェクト単位で確認し、
    # 無ければ「スキップ + 手動コマンド案内」に倒す。
    "iam_sync":         ("iam.serviceAccounts.create",),
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
# dst API 事前有効化 (Step 1.5) の純粋関数
# ---------------------------------------------------------------------------
# API サービス名の形式 (例: container.googleapis.com)。CAI assetType の先頭部や
# `gcloud services list` の出力からサービス名だけを拾うのに使う。
_API_SERVICE_RE = re.compile(r"^[a-z0-9][a-z0-9-]*(\.[a-z0-9][a-z0-9-]*)*\.googleapis\.com$")

# `gcloud services enable` の batch 上限（Service Usage の batchEnable は 20 件/回）。
_API_ENABLE_BATCH = 20

# ステップ構成に関わらず dst で必要な API。
_BASE_DST_APIS = (
    "cloudresourcemanager.googleapis.com",
    "serviceusage.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
)

# 有効なステップが dst で必ず叩く API。src 側で無効でも dst には要る
# （例: src が GCE を使っていなくても gce_restore が有効なら compute は要る）。
_STEP_DST_APIS: Dict[str, Tuple[str, ...]] = {
    "terraform_apply":  ("compute.googleapis.com", "storage.googleapis.com",
                         "logging.googleapis.com"),
    "network_firewall": ("compute.googleapis.com",),
    "gce_restore":      ("compute.googleapis.com",),
    "iam_sync":         ("iam.googleapis.com",),
    "data_sync":        ("storage.googleapis.com", "bigquery.googleapis.com"),
}

# src で有効でも dst には複製しない API。追加は steps.enable_apis.skip_apis でも可能。
# 対象は「単体で有効化できない（= enable が必ず失敗する）」ものだけに限る:
#   - 廃止済み / 旧エイリアス
#   - 親 API の有効化に伴って GCP が自動で有効化する内部サービス
# 契約や申請が要るだけの実 API（edgecache 等）は**入れない**。落として黙って消すより、
# 有効化を試して失敗を WARNING + 手動コマンドで見せる方が安全側。
_DST_API_SKIP = frozenset({
    # 廃止 / 旧エイリアス
    "bigquery-json.googleapis.com",          # 旧エイリアス（bigquery.googleapis.com）
    "clouddebugger.googleapis.com",          # 2023 年提供終了。新規有効化不可
    # 親 API 有効化時に自動で付く内部サービス
    "autoscaling.googleapis.com",            # compute (MIG) の内部
    "containerfilesystem.googleapis.com",    # GKE image streaming の内部
    "multiclustermetering.googleapis.com",   # GKE fleet の内部
    "dataproc-control.googleapis.com",       # Dataproc の内部
    "dataprocrm.googleapis.com",             # Dataproc の内部
    "bigqueryunified.googleapis.com",        # BigQuery の内部
    "telemetry.googleapis.com",              # Google 内部テレメトリ
})


def api_from_asset_type(asset_type: str) -> Optional[str]:
    """CAI assetType のサービス部を API 名として返す。

    'container.googleapis.com/Cluster' → 'container.googleapis.com'。
    クラスタ内 k8s オブジェクト（'k8s.io/Pod' 等）は API ではないので None。
    """
    head = (asset_type or "").split("/", 1)[0].strip()
    return head if _API_SERVICE_RE.match(head) else None


def cai_api_hints(cai_path: str) -> Tuple[Set[str], Set[str]]:
    """CAI 出力から (src で有効な API 名, 観測した assetType) を抽出する。

    CAI は `serviceusage.googleapis.com/Service` として「src で有効な API」を
    そのまま列挙してくれるので、追加の権限なしに有効 API 一覧が得られる
    （`gcloud services list` が権限不足で読めない場合のフォールバックになる）。
    """
    services: Set[str] = set()
    asset_types: Set[str] = set()
    for rec in parse_cai_resources(cai_path):
        atype = rec.get("asset_type", "")
        if not atype:
            continue
        asset_types.add(atype)
        if atype == "serviceusage.googleapis.com/Service":
            short = (rec.get("short_name") or "").strip()
            if short:
                services.add(short)
    return services, asset_types


def build_api_enable_plan(
    src_services: Iterable[str],
    asset_types: Iterable[str],
    steps: Dict[str, Any],
    extra_apis: Iterable[str] = (),
    skip_apis: Iterable[str] = (),
) -> List[str]:
    """dst で有効化すべき API 名のソート済みリストを返す（純粋関数）。

    Args:
        src_services: src で有効な API 名（`gcloud services list --enabled` / CAI）
        asset_types:  src の CAI assetType。サービス部を API 名として拾い、
                      「リソースはあるが services list が読めなかった」場合の保険にする
        steps:        config の steps。有効ステップが dst で必ず使う API を足す
        extra_apis:   config での明示追加（形式チェックせずそのまま採用）
        skip_apis:    config での明示除外
    """
    want: Set[str] = set(_BASE_DST_APIS)
    for step_name, apis in _STEP_DST_APIS.items():
        if step_enabled(steps, step_name):
            want.update(apis)
    for name in list(src_services or []):
        n = (name or "").strip()
        if _API_SERVICE_RE.match(n):
            want.add(n)
    for atype in list(asset_types or []):
        n = api_from_asset_type(atype)
        if n:
            want.add(n)
    for name in list(extra_apis or []):
        n = str(name or "").strip()
        if n:
            want.add(n)
    skip = set(_DST_API_SKIP) | {
        str(s or "").strip() for s in list(skip_apis or []) if str(s or "").strip()
    }
    # 基盤 API（CRM / ServiceUsage / IAM）は skip_apis でも外させない。
    # enable 失敗時の案内文が「不要なら skip_apis へ」と勧めるため、transient な
    # 失敗の対処として基盤 API を skip に入れてしまうと、以後 terraform が一切
    # 動かない dst を「設定どおり」に作り続けてしまう。
    return sorted((want - skip) | set(_BASE_DST_APIS))


# ---------------------------------------------------------------------------
# Terraform リソース型 → 必要な dst API
# ---------------------------------------------------------------------------
# Step 1.5 は「src で有効な API」を dst に写すが、それだけでは
#   - src の `services list` / CAI が読めなかった
#   - リソースは export されているのに親 API が src 側で無効だった
# ケースを取りこぼし、Step 4 の apply が 403 で落ちる。**実際に apply する .tf**
# から必要 API を引き直すのが最も確実なので、init 直前にもう一度差分を埋める。
#
# キーは `google_` を除いたリソース型の接頭辞。前方一致で引くため、モジュール
# ロード時に長い順へ並べ替える（"container_registry" が "container" より先に
# 当たるようにする）。未知の型は None = 何も有効化しない（安全側）。
_TF_TYPE_API_PREFIX_MAP: Dict[str, str] = {
    "compute": "compute.googleapis.com",
    "container_attached": "gkemulticloud.googleapis.com",
    "container_aws": "gkemulticloud.googleapis.com",
    "container_azure": "gkemulticloud.googleapis.com",
    "container_analysis": "containeranalysis.googleapis.com",
    "container_registry": "containerregistry.googleapis.com",
    "container": "container.googleapis.com",
    "gke_hub": "gkehub.googleapis.com",
    "gke_backup": "gkebackup.googleapis.com",
    "gkeonprem": "gkeonprem.googleapis.com",
    "storage_transfer": "storagetransfer.googleapis.com",
    "storage": "storage.googleapis.com",
    "bigquery_analytics_hub": "analyticshub.googleapis.com",
    "bigquery_connection": "bigqueryconnection.googleapis.com",
    "bigquery_datapolicy": "bigquerydatapolicy.googleapis.com",
    "bigquery_data_transfer": "bigquerydatatransfer.googleapis.com",
    "bigquery_reservation": "bigqueryreservation.googleapis.com",
    "bigquery": "bigquery.googleapis.com",
    "bigtable": "bigtableadmin.googleapis.com",
    "sql": "sqladmin.googleapis.com",
    "spanner": "spanner.googleapis.com",
    "firestore": "firestore.googleapis.com",
    "datastore": "datastore.googleapis.com",
    "redis": "redis.googleapis.com",
    "memcache": "memcache.googleapis.com",
    "memorystore": "memorystore.googleapis.com",
    "filestore": "file.googleapis.com",
    "netapp": "netapp.googleapis.com",
    "pubsub_lite": "pubsublite.googleapis.com",
    "pubsub": "pubsub.googleapis.com",
    "cloudfunctions2": "cloudfunctions.googleapis.com",
    "cloudfunctions": "cloudfunctions.googleapis.com",
    "cloud_run_v2": "run.googleapis.com",
    "cloud_run": "run.googleapis.com",
    "cloud_scheduler": "cloudscheduler.googleapis.com",
    "cloud_tasks": "cloudtasks.googleapis.com",
    "cloud_asset": "cloudasset.googleapis.com",
    "cloud_identity": "cloudidentity.googleapis.com",
    "cloud_ids": "ids.googleapis.com",
    "cloudbuildv2": "cloudbuild.googleapis.com",
    "cloudbuild": "cloudbuild.googleapis.com",
    "clouddeploy": "clouddeploy.googleapis.com",
    "workflows": "workflows.googleapis.com",
    "eventarc": "eventarc.googleapis.com",
    "dns": "dns.googleapis.com",
    "kms": "cloudkms.googleapis.com",
    "privateca": "privateca.googleapis.com",
    "certificate_manager": "certificatemanager.googleapis.com",
    "secret_manager": "secretmanager.googleapis.com",
    "parameter_manager": "parametermanager.googleapis.com",
    "artifact_registry": "artifactregistry.googleapis.com",
    "binary_authorization": "binaryauthorization.googleapis.com",
    "logging": "logging.googleapis.com",
    "monitoring": "monitoring.googleapis.com",
    "dataproc_metastore": "metastore.googleapis.com",
    "dataproc": "dataproc.googleapis.com",
    "dataflow": "dataflow.googleapis.com",
    "data_fusion": "datafusion.googleapis.com",
    "data_catalog": "datacatalog.googleapis.com",
    "data_loss_prevention": "dlp.googleapis.com",
    "dataplex": "dataplex.googleapis.com",
    "datastream": "datastream.googleapis.com",
    "composer": "composer.googleapis.com",
    "notebooks": "notebooks.googleapis.com",
    "workbench": "notebooks.googleapis.com",
    "workstations": "workstations.googleapis.com",
    "vertex_ai": "aiplatform.googleapis.com",
    "colab": "aiplatform.googleapis.com",
    "discovery_engine": "discoveryengine.googleapis.com",
    "document_ai": "documentai.googleapis.com",
    "dialogflow": "dialogflow.googleapis.com",
    "healthcare": "healthcare.googleapis.com",
    "apigee": "apigee.googleapis.com",
    "api_gateway": "apigateway.googleapis.com",
    "apphub": "apphub.googleapis.com",
    "endpoints": "servicemanagement.googleapis.com",
    "network_services": "networkservices.googleapis.com",
    "network_connectivity": "networkconnectivity.googleapis.com",
    "network_security": "networksecurity.googleapis.com",
    "network_management": "networkmanagement.googleapis.com",
    "service_networking": "servicenetworking.googleapis.com",
    "vpc_access": "vpcaccess.googleapis.com",
    "beyondcorp": "beyondcorp.googleapis.com",
    "iap": "iap.googleapis.com",
    "identity_platform": "identitytoolkit.googleapis.com",
    "recaptcha": "recaptchaenterprise.googleapis.com",
    "security_center": "securitycenter.googleapis.com",
    "scc": "securitycenter.googleapis.com",
    "backup_dr": "backupdr.googleapis.com",
    "migration_center": "migrationcenter.googleapis.com",
    "vmwareengine": "vmwareengine.googleapis.com",
    "looker": "looker.googleapis.com",
    "integrations": "integrations.googleapis.com",
    "oracle_database": "oracledatabase.googleapis.com",
    "access_context_manager": "accesscontextmanager.googleapis.com",
    "org_policy": "orgpolicy.googleapis.com",
    "essential_contacts": "essentialcontacts.googleapis.com",
    "billing": "cloudbilling.googleapis.com",
    "deployment_manager": "deploymentmanager.googleapis.com",
    "os_config": "osconfig.googleapis.com",
    "os_login": "oslogin.googleapis.com",
    "service_account": "iam.googleapis.com",
    "iam": "iam.googleapis.com",
    "project_service": "serviceusage.googleapis.com",
    "project": "cloudresourcemanager.googleapis.com",
    "folder": "cloudresourcemanager.googleapis.com",
    "organization": "cloudresourcemanager.googleapis.com",
    "tags": "cloudresourcemanager.googleapis.com",
}
_TF_TYPE_API_PREFIXES: Tuple[Tuple[str, str], ...] = tuple(
    sorted(_TF_TYPE_API_PREFIX_MAP.items(), key=lambda kv: len(kv[0]), reverse=True)
)

# `resource "google_container_cluster" "x" {` / `data "google_project" "y" {`
_TF_BLOCK_RE = re.compile(r'^\s*(?:resource|data)\s+"([A-Za-z0-9_-]+)"', re.M)


# ---------------------------------------------------------------------------
# Artifact Registry イメージ複製（Step 6）
# ---------------------------------------------------------------------------
# Terraform が作るのは**リポジトリ（箱）だけ**でイメージ本体は複製されない。
# Cloud Run は `...@sha256:<digest>` でイメージを固定参照するため、イメージが
# 無いと revision 作成が `Image '...' not found.` で失敗し、サービスが tainted で
# state に残る（regression: my-argolis の Cloud Run 3 件）。GCS/BQ と同じ
# 「データ移行」として Step 6 で複製する。
#
# gcloud には（SDK 580 時点で）`artifacts docker images copy` が無く、
# crane/gcrane/buildx も前提にできないため docker CLI で pull→tag→push する。
# **docker 経由は digest が変わりうる**（マルチアーキ index を単一プラットフォームに
# 落とす等）ので、push 後に dst 側へ同一 digest が存在するか必ず確認し、
# 変わっていたら WARNING で知らせる（Cloud Run の digest 固定参照が壊れるため）。
_AR_DIGEST_RE = re.compile(r'^sha256:[0-9a-f]{64}$')

# cosign / Cloud Build が attestation・署名・SBOM に付けるタグ形式。
# これしかタグが無い version は実イメージではないので複製プランから外す
# （タグ無しの attestation は list 時点では見分けられないため、
#  pull 時の "unsupported media type" 判定が本命のガード）。
_AR_ARTIFACT_TAG_RE = re.compile(r'^sha256-[0-9a-f]{64}\.(att|sig|sbom)$')


def parse_ar_repositories(text: Optional[str]) -> List[Dict[str, str]]:
    """`gcloud artifacts repositories list --format=json` を解析する（純粋関数）。

    name は `projects/<p>/locations/<loc>/repositories/<repo>` 形式。
    DOCKER 形式のみ返す（他形式は docker CLI で複製できない）。
    """
    try:
        data = json.loads(text) if text else []
    except (ValueError, TypeError):
        return []
    out: List[Dict[str, str]] = []
    for r in data if isinstance(data, list) else []:
        if not isinstance(r, dict):
            continue
        if (r.get("format") or "").upper() != "DOCKER":
            continue
        m = re.match(r'^projects/([^/]+)/locations/([^/]+)/repositories/(.+)$',
                     r.get("name") or "")
        if not m:
            continue
        out.append({"project": m.group(1), "location": m.group(2), "repo": m.group(3)})
    return sorted(out, key=lambda d: (d["location"], d["repo"]))


def build_ar_image_copy_plan(
    text: Optional[str], src_proj: str, dst_proj: str,
) -> List[Dict[str, Any]]:
    """`gcloud artifacts docker images list --include-tags --format=json` から
    複製プランを作る（純粋関数）。

    package は `<loc>-docker.pkg.dev/<project>/<repo>/<image>`。project 部だけを
    dst に差し替える（image 名に src プロジェクト ID が含まれても壊さないよう、
    パス区切りで分解して 2 番目の要素だけ置換する）。
    """
    try:
        data = json.loads(text) if text else []
    except (ValueError, TypeError):
        return []
    plan: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for img in data if isinstance(data, list) else []:
        if not isinstance(img, dict):
            continue
        pkg = (img.get("package") or "").strip()
        digest = (img.get("version") or "").strip()
        if not pkg or not _AR_DIGEST_RE.match(digest):
            continue
        parts = pkg.split("/")
        if len(parts) < 3 or parts[1] != src_proj:
            continue
        dst_pkg = "/".join([parts[0], dst_proj] + parts[2:])
        raw_tags = img.get("tags")
        if isinstance(raw_tags, str):
            tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
        elif isinstance(raw_tags, list):
            tags = [str(t).strip() for t in raw_tags if str(t).strip()]
        else:
            tags = []
        if tags and all(_AR_ARTIFACT_TAG_RE.match(t) for t in tags):
            # attestation / 署名 / SBOM 専用タグしか持たない = 実イメージではない
            continue
        key = f"{pkg}@{digest}"
        if key in seen:
            continue
        seen.add(key)
        plan.append({
            "src_ref": key,
            "dst_pkg": dst_pkg,
            "dst_ref": f"{dst_pkg}@{digest}",
            "digest": digest,
            "tags": sorted(tags),
        })
    return plan


# `steps.data_sync.artifact_registry.scope` に指定できる値。
#   all    … 全 digest を複製（既定。移行範囲を勝手に狭めない安全側）
#   tagged … tag の付いた digest だけ複製。**ただし .tf が digest 固定で参照する
#            ものは tag の有無に関わらず必ず含める**（含めないと Step 4 の apply が
#            `Image ... not found` で落ちる）
_AR_SCOPES = ("all", "tagged")

# `image = "<repo>@sha256:<64hex>"` 等、HCL 内の digest 固定参照。
# リソース型に依存しない（Cloud Run v1/v2・Job・Functions gen2 いずれも拾う）。
_TF_IMAGE_DIGEST_RE = re.compile(r'@(sha256:[0-9a-f]{64})')


def tf_referenced_image_digests(tf_dir: Optional[str]) -> Set[str]:
    """Terraform ルート直下の `.tf` が digest 固定で参照するイメージ digest を集める。

    Step 3.7 は Step 3（bulk_export + customize）の**後**に走るので、この時点の
    `active/<src>/*.tf` は「これから apply される内容」そのもの。ここから引いた
    digest 集合は **apply が必要とする最小集合と完全に一致する**ため、これさえ
    複製すれば `scope` をどれだけ絞っても Step 4 は落ちない。

    走査は Terraform ルート直下のみ（terraform 自体がサブディレクトリを再帰
    しないので customize 後の active は平坦）。
    """
    found: Set[str] = set()
    if not tf_dir or not os.path.isdir(tf_dir):
        return found
    for fn in sorted(os.listdir(tf_dir)):
        if not fn.endswith(".tf"):
            continue
        try:
            with open(os.path.join(tf_dir, fn), encoding="utf-8") as f:
                found.update(_TF_IMAGE_DIGEST_RE.findall(f.read()))
        except OSError:
            continue
    return found


def filter_ar_plan_by_scope(
    plan: List[Dict[str, Any]], scope: Optional[str],
    keep_digests: Optional[Set[str]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """複製プランを `scope` で絞る（純粋関数）。戻り値は (残す, 落とす)。

    `tagged` は「tag 無し = 新しいビルドに tag を奪われた過去ビルド」を落とす。
    Cloud Build は push のたびに tag を移すため、実測では **87 件中 64 件（74%）**
    がこれに該当した。

    ただし **`.tf` が参照する digest（keep_digests）は tag が無くても必ず残す**。
    落とすと apply が `Image ... not found` で失敗する。判定材料が無い側（tag 無し
    かつ参照も無い）だけを落とすので、誤って落としても壊れるのは「後から手動で
    古い digest に戻す」操作だけになる。
    """
    if str(scope or "all").strip().lower() != "tagged":
        return list(plan), []
    keep = set(keep_digests or ())
    kept: List[Dict[str, Any]] = []
    dropped: List[Dict[str, Any]] = []
    for e in plan:
        if e.get("tags") or e.get("digest") in keep:
            kept.append(e)
        else:
            dropped.append(e)
    return kept, dropped


# provider 既定で deletion_protection = true になり、**export には出てこない**型。
# 既定 true のままだと、apply が途中で失敗して tainted になったリソースを
# 次回 replace できず `cannot destroy service without setting
# deletion_protection=false` で移行が恒久的に詰む（regression: my-argolis の
# Cloud Run サービス 3 件）。export に無い＝src の設定ではなく provider 既定なので、
# dst 側だけ false を明示して収束できるようにする。
# **export が明示している場合は触らない**（src の意図を上書きしない）。
_DELETION_PROTECTION_DEFAULT_TRUE_TYPES = (
    "google_cloud_run_v2_service",
    "google_cloud_run_v2_job",
    "google_container_cluster",
    "google_sql_database_instance",
)


def ensure_tf_resource_arg(
    content: str, tf_type: str, arg_line: str,
) -> Tuple[str, List[str]]:
    """resource ブロック直下に引数が無ければ先頭へ挿入する（純粋関数）。

    `ensure_hcl_block_arg` は `name {` 形式のネストブロック用で、
    `resource "type" "label" {` には一致しないため別関数にする。
    戻り値は (書き換え後, 挿入したラベル一覧)。
    """
    arg_name = arg_line.split('=', 1)[0].strip()
    decl_re = re.compile(
        rf'^(\s*)resource\s+"{re.escape(tf_type)}"\s+"([^"]+)"\s*\{{\s*$'
    )
    arg_re = re.compile(rf'^\s*{re.escape(arg_name)}\s*=', re.M)
    lines = content.split('\n')
    out: List[str] = []
    inserted: List[str] = []
    i = 0
    while i < len(lines):
        m = decl_re.match(lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue
        depth = 1
        j = i + 1
        while j < len(lines) and depth > 0:
            depth += lines[j].count('{') - lines[j].count('}')
            j += 1
        body = lines[i + 1:j]
        out.append(lines[i])
        # ネストブロック内の同名引数を誤検出しないよう、depth 1 の行だけ見る
        depth_now = 1
        has_arg = False
        for ln in body:
            if depth_now == 1 and arg_re.match(ln):
                has_arg = True
                break
            depth_now += ln.count('{') - ln.count('}')
        if not has_arg:
            out.append(f"{m.group(1)}  {arg_line}")
            inserted.append(m.group(2))
        out.extend(body)
        i = j
    return '\n'.join(out), inserted


def is_api_disabled_error(text: Optional[str]) -> bool:
    """API 未有効（＝そのサービスを src で使っていない）エラーなら True。

    src がその機能を使っていないだけなので、失敗ではなく「対象なし」として
    静かに扱う（`make run` の exit code を落とさない）。
    """
    low = (text or "").lower()
    return ("has not been used in project" in low
            or "service_disabled" in low
            or "api is not enabled" in low
            or "is disabled" in low and "enable it by visiting" in low)


# ---------------------------------------------------------------------------
# Cloud Run サービス個別 IAM（公開設定）の複製
# ---------------------------------------------------------------------------
# Cloud Run の「未認証アクセスを許可」は **サービスリソース個別の IAM**
# （allUsers → roles/run.invoker）で表現される。bulk-export は IAM バインディングを
# 出力せず、iam_sync もプロジェクト IAM のみ対象のため、放置すると dst が
# 認証必須になる（= src と挙動が変わる）。invoker の公開 2 メンバーだけを
# 忠実複製し、付与したら最後に警告でまとめて見せる（roles/owner と同じ方針）。
_RUN_PUBLIC_MEMBERS = ("allUsers", "allAuthenticatedUsers")


def parse_run_services_list(text: Optional[str]) -> List[Tuple[str, str]]:
    """`gcloud run services list --format=json` から [(name, region), ...] を返す（純粋関数）。"""
    try:
        data = json.loads(text) if text else []
    except (ValueError, TypeError):
        return []
    out: List[Tuple[str, str]] = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        meta = item.get("metadata") or {}
        name = (meta.get("name") or "").strip()
        region = ((meta.get("labels") or {}).get("cloud.googleapis.com/location")
                  or "").strip()
        if name and region:
            out.append((name, region))
    return sorted(set(out))


def run_service_public_invoker_members(text: Optional[str]) -> List[str]:
    """サービス IAM ポリシー JSON から公開 invoker メンバーを返す（純粋関数）。

    対象は allUsers / allAuthenticatedUsers × roles/run.invoker のみ。
    それ以外のメンバー（SA 個別付与など）はプロジェクト間で意味が変わるため
    複製しない（iam_sync の対象外方針と同じ）。
    """
    try:
        data = json.loads(text) if text else {}
    except (ValueError, TypeError):
        return []
    members: Set[str] = set()
    for b in (data.get("bindings") or []) if isinstance(data, dict) else []:
        if not isinstance(b, dict) or b.get("role") != "roles/run.invoker":
            continue
        if b.get("condition"):
            continue
        for m in b.get("members") or []:
            if m in _RUN_PUBLIC_MEMBERS:
                members.add(m)
    return sorted(members)


def coerce_nonneg_int(value: Any, default: int) -> int:
    """値を非負整数へ安全に変換する（純粋関数）。

    validate_steps_config は**有効なステップしか検査しない**が、
    `steps.enable_apis` の設定は enabled: false でも Step 4
    (`_ensure_dst_prereq_apis`) が読む。そこで `int("2m")` のような
    ValueError が並列 worker から送出されると、apply 途中の `make run` が
    traceback で落ちるため、型不正は default に倒す。
    """
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return n if n >= 0 else default


def import_error_kind(text: str) -> Optional[str]:
    """terraform import の失敗出力を分類する（純粋関数）。

    Returns:
        "already": state に取り込み済み（無視してよい）
        "missing": リモートに実体が無い（apply が作成するので無視してよい）。
                   provider により表現が違う: compute は `Error 404 ... notFound`、
                   GKE は `googleapi: Error 404: Not found:`、terraform 本体は
                   `Cannot import non-existent remote object`。
        None:      本当の失敗（権限・API 無効など。警告として見せる）
    """
    low = (text or "").lower()
    if "already managed by terraform" in low or (
            "resource address" in low and "already" in low):
        return "already"
    if ("cannot import non-existent remote object" in low
            or "status code: 404" in low
            or "error 404" in low
            or "notfound" in low
            or "does not exist" in low):
        return "missing"
    return None


def tf_blocks_of_type(content: str, tf_type: str) -> List[Tuple[str, str]]:
    """指定型の resource ブロックを [(label, body), ...] で返す（純粋関数）。

    ネストしたブロック（`secondary_ip_range { ... }` など）があるため、
    `[^}]*` の素朴な正規表現ではなく brace の対応を数えて本文を切り出す。
    """
    out: List[Tuple[str, str]] = []
    decl_re = re.compile(
        rf'resource\s+"{re.escape(tf_type)}"\s+"([^"]+)"\s*\{{'
    )
    for m in decl_re.finditer(content):
        depth = 1
        i = m.end()
        while i < len(content) and depth > 0:
            if content[i] == '{':
                depth += 1
            elif content[i] == '}':
                depth -= 1
            i += 1
        out.append((m.group(1), content[m.end():i - 1]))
    return out


def tf_type_to_api(tf_type: str) -> Optional[str]:
    """Terraform リソース型から必要な API 名を返す。未知なら None。"""
    t = (tf_type or "").strip()
    if not t.startswith("google_"):
        return None
    rest = t[len("google_"):]
    for prefix, api in _TF_TYPE_API_PREFIXES:
        if rest == prefix or rest.startswith(prefix + "_"):
            return api
    return None


def tf_resource_types(text: str) -> Set[str]:
    """HCL テキストから resource / data の型名を抽出する。"""
    return set(_TF_BLOCK_RE.findall(text or ""))


def tf_required_apis(tf_dir: str) -> List[str]:
    """Terraform ルート直下の .tf が必要とする API 名（ソート済み）を返す。

    Terraform はサブディレクトリを再帰しないため、走査も直下の .tf だけでよい
    （active/<src>/ は customize_hcl が平坦化済み）。
    """
    apis: Set[str] = set()
    try:
        names = sorted(os.listdir(tf_dir))
    except OSError:
        return []
    for name in names:
        if not name.endswith(".tf"):
            continue
        try:
            # errors="replace": bulk-export は VM の metadata / startup-script を
            # 原文のまま出すため非 UTF-8 バイトが混ざりうる。except OSError では
            # UnicodeDecodeError を捕まえられず、_parallel_for_each の worker から
            # 送出されて run 全体が traceback で落ちる。
            with open(os.path.join(tf_dir, name), encoding="utf-8",
                      errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        for t in tf_resource_types(text):
            api = tf_type_to_api(t)
            if api:
                apis.add(api)
    return sorted(apis)


# ---------------------------------------------------------------------------
# mock 生成物の検出
# ---------------------------------------------------------------------------
# mock が書いたダミー .tf を実行用の terraform ルートで apply すると、dst に
# 実在しないリソース（mock-cluster / mock bucket 等）が**本当に作られる**。
# mock の出力先は `_tf_base_dir()` で分離済みだが、分離前に汚染された
# terraform/active/ が残っている環境でも事故らないよう、内容からも検出する。
_MOCK_TF_MARK = "copy-all-env:mock-generated"
# 旧 mock（マーク行が無い時代）が書いたリソースラベル。**宣言の 2 番目ラベル**
# として照合する。裸の部分文字列照合だと、実リソースの name = "mock_bucket"
# （GCS はアンダースコア可）まで mock 残骸と誤検知し、正当な active の再利用拒否 +
# Step 4 の apply 拒否（rm -rf 案内つき）で移行が止まる。
_LEGACY_MOCK_TF_LABELS = ("mock_vm", "mock_bucket", "mock_cluster",
                          "mock_gke_template")
_LEGACY_MOCK_DECL_RE = re.compile(
    r'\bresource\s+"[A-Za-z0-9_-]+"\s+"(' + "|".join(_LEGACY_MOCK_TF_LABELS) + r')"'
)


def tf_dir_has_mock_artifacts(tf_dir: str) -> bool:
    """Terraform ルート直下に mock 生成の .tf が混ざっていれば True。"""
    try:
        names = sorted(os.listdir(tf_dir))
    except OSError:
        return False
    for name in names:
        if not name.endswith(".tf"):
            continue
        try:
            # errors="replace": 非 UTF-8 バイト混入で run を落とさない（tf_required_apis と同じ）。
            with open(os.path.join(tf_dir, name), encoding="utf-8",
                      errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        if _MOCK_TF_MARK in text:
            return True
        if _LEGACY_MOCK_DECL_RE.search(text):
            return True
    return False


# ---------------------------------------------------------------------------
# flatten 時の resource ラベル重複解消
# ---------------------------------------------------------------------------
# bulk-export はリソース「名」だけからラベルを作るため、同名リソースが複数
# location にあると（例: Artifact Registry の cloud-run-source-deploy が
# asia-northeast1 と us-central1 の両方にある）、customize_hcl の平坦化で
# 同一 Terraform ルートに同じ (type, label) が同居し、
# 「Duplicate resource "<type>" configuration」で init/plan が落ちる。
_TF_RESOURCE_DECL_RE = re.compile(
    r'\b(resource|data)\s+"([A-Za-z0-9_-]+)"\s+"([^"]+)"')


def dedupe_tf_resource_labels(
    content: str, discriminator: str, seen: Set[Tuple[str, str, str]],
) -> Tuple[str, List[Tuple[str, str, str]]]:
    """flatten 後に同居する resource/data の (kind, type, label) 重複を一意化する（純粋関数）。

    seen（同一 Terraform ルートで既に確定した (kind, type, label) の集合）に
    無いラベルはそのまま登録し、衝突したブロックだけ `<label>_<discriminator>`
    （さらに衝突したら `_2`, `_3`, …）へ改名する。`data` ブロックも対象
    （resource と data は別名前空間なので kind をキーに含める）。
    同一ファイル内の参照（`<type>.<label>` / `data.<type>.<label>`、
    `# terraform import` コメント含む）も揃えて書き換える
    （import アドレスがズレると `_terraform_import_existing` の adopt が外れる）。

    実装メモ:
    - 宣言の改名は **findall+re.sub ではなく span 置換**。ファイル内に
      `x` と `x_asia` が並ぶ場合、`x` → `x_asia` の全文 sub は既存の
      `x_asia` ブロックまで巻き込み、両方が同一ラベルに潰れて
      「Duplicate resource」を自ら作ってしまう（regression）。
    - 参照の書き換えは「旧ラベル → 一意トークン → 新ラベル」の 2 段階。
      改名 A の新ラベルが改名 B の旧ラベルと一致するケース
      （`x`→`x_asia` と `x_asia`→`x_asia_2`）で、A の書き換え結果を
      B が再度書き換える連鎖を防ぐ。旧ラベルの長い順に token 化する。
    - 改名は**走査順に依存する**ため、呼び出し側はファイル走査を必ずソートして
      決定的にすること（順序が変わると前回 state のアドレスと食い違う）。

    Args:
        content:       .tf ファイル本文
        discriminator: 改名時の suffix 素材（location ディレクトリ名など）。
                       Terraform ラベルに使えない文字は `_` に置換される
        seen:          このルートで確定済みの (kind, type, label)。**呼び出しで更新される**
    Returns:
        (書き換え後の content, [(type, old_label, new_label), ...])
    """
    planned: List[Tuple[re.Match, str, str, str, str]] = []
    for m in _TF_RESOURCE_DECL_RE.finditer(content):
        kind, rtype, label = m.group(1), m.group(2), m.group(3)
        key = (kind, rtype, label)
        if key not in seen:
            seen.add(key)
            continue
        base = re.sub(r'[^A-Za-z0-9_]', '_', discriminator or '').strip('_') or 'dup'
        new_label = f"{label}_{base}"
        n = 2
        while (kind, rtype, new_label) in seen:
            new_label = f"{label}_{base}_{n}"
            n += 1
        seen.add((kind, rtype, new_label))
        planned.append((m, kind, rtype, label, new_label))
    if not planned:
        return content, []

    # 1. 宣言ラベルを span で改名（後ろから。前方の span を壊さない）
    for m, _kind, _rtype, _old, new_label in reversed(planned):
        s, e = m.span(3)
        content = content[:s] + new_label + content[e:]

    # 2. 参照を token 化 → 展開の 2 段階で書き換え（旧ラベルの長い順）
    order = sorted(range(len(planned)), key=lambda i: -len(planned[i][3]))
    for i in order:
        _m, kind, rtype, old, _new = planned[i]
        prefix = "data." if kind == "data" else ""
        content = re.sub(
            rf'(?<![A-Za-z0-9_.-]){re.escape(prefix + rtype)}\.{re.escape(old)}'
            rf'(?![A-Za-z0-9_-])',
            f"\x00DEDUPE{i}\x00", content,
        )
    renames: List[Tuple[str, str, str]] = []
    for i, (_m, kind, rtype, old, new_label) in enumerate(planned):
        prefix = "data." if kind == "data" else ""
        content = content.replace(f"\x00DEDUPE{i}\x00", f"{prefix}{rtype}.{new_label}")
        renames.append((rtype, old, new_label))
    return content, renames


# ---------------------------------------------------------------------------
# customize の手動対応・確認注記（DIFF.md 掲載用）
# ---------------------------------------------------------------------------
# customize_hcl が Terraform を通すために行った補正・スキップのうち、利用者の
# 手動対応（例: SSL 証明書の手動作成）や確認（例: IAP を有効側で複製）が要る
# ものは、ログに流すだけだと埋もれて見落とされる。ルール:
#   1. 補正/スキップの実装箇所で `self._customize_notes` に注記を積む
#   2. customize_hcl 末尾が `active/<src>/.customize_notes.json` に永続化する
#      （skip_on_run で customize を飛ばした `make run` でも DIFF に出すため）
#   3. Step 99 が読み出して DIFF.md の専用セクションに掲載する
# 新しい補正・スキップを足すときも、手動対応/確認が要るなら必ずこの経路に載せる。
_CUSTOMIZE_NOTES_FILE = ".customize_notes.json"


def customize_note_row(note: Dict[str, str]) -> Tuple[str, str, str, str]:
    """注記 1 件を DIFF.md のテーブル行 (種別, 対象, 理由, 対応) に整形する（純粋関数）。"""
    kind = note.get("kind", "")
    res = note.get("resource", "?")
    proj = note.get("project", "?")
    if kind == "ssl_certificate":
        return (
            "要対応",
            f"SSL 証明書 `{res}`（{proj}）",
            "自己管理証明書の秘密鍵は API から export できず Terraform では作成不能。"
            "作成するまで参照元の HTTPS LB（target proxy）の apply が失敗する。"
            "クラスタ外の Compute リソースのため **Backup for GKE でも移行されない**",
            f"鍵を用意して手動作成: `gcloud compute ssl-certificates create {res}"
            f" --certificate=<crt> --private-key=<key> --project={proj}`",
        )
    if kind == "dns_managed_zone":
        return (
            "要対応",
            f"public DNS ゾーン `{res}`（{proj}）",
            "ドメインはグローバル一意で、同一ドメインの public ゾーンは別プロジェクトに"
            "作成できない場合がある（reserved/policy の 400）。作成できても NS 委任は"
            "src 側を向いたままで機能しない",
            "移行するなら: ① dst で "
            f"`gcloud dns managed-zones create {res} --dns-name=<domain> "
            f"--description=migrated --project={proj}` ② レコードを移行 "
            "③ レジストラ / 親ゾーンの NS 委任を dst のネームサーバーへ切替。"
            "src 併用中は切替えないこと（検証用途なら不要）",
        )
    if kind == "dotted_bucket":
        return (
            "要対応",
            f"ドット入りバケット `{res}`（{proj}）",
            "ドメイン形式のバケットはドメイン検証済み TLD 配下でないと作成できない"
            "（*.appspot.com は Google 管理のシステムバケットで複製自体が不可能）",
            "データが必要な場合のみ `rename_rules.gcs.overrides` に"
            "ドット無しの dst バケット名を指定 → data_sync が作成 + 同期する。"
            "GCR レイヤー（us.artifacts.*）や GAE staging は通常移行不要",
        )
    if kind == "lb_blocked_on_cert":
        return (
            "要対応",
            f"HTTPS LB フロントエンド `{res}`（{proj}）",
            "参照する SSL 証明書が dst に未作成のため、今回の terraform 適用から"
            "保留した（文字列参照のまま適用すると証明書を作るまで毎回 404 で "
            "run が失敗する）",
            "DIFF の SSL 証明書の項に従い `gcloud compute ssl-certificates create ...` "
            "で証明書を作成 → 次回 `make run` で proxy / forwarding rule が自動適用される",
        )
    if kind == "gke_backup_restore":
        src_p = note.get("src_dir") or "<src>"
        return (
            "要対応",
            f"GKE クラスタ `{res}` のワークロード・PV データ（{proj}）",
            "本ツールはクラスタ / ノードプールの**構成のみ**複製する。クラスタ内の "
            "k8s オブジェクト・Secret・PersistentVolume のデータは対象外で、"
            "移行しない限り dst クラスタは空のまま。"
            "**Backup for GKE は既定では同一プロジェクト内の restore しかできない**"
            "（restore plan は別プロジェクトの backup plan を参照できない）ため、"
            "別プロジェクトへの移行では backup channel / restore channel が要る",
            "① src で backup（ツール対象外・手動）: "
            f"`gcloud container clusters update <srcクラスタ> --project={src_p} "
            f"--update-addons=BackupRestore=ENABLED` → "
            f"`gcloud beta container backup-restore backup-plans create {res}-bp "
            f"--project={src_p} --location=<loc> --cluster=<srcクラスタのフルパス> "
            f"--all-namespaces --include-secrets --include-volume-data` → "
            f"`backups create`  "
            "② クロスプロジェクトの通り道を作る: "
            f"`gcloud beta container backup-restore backup-channels create <ch> "
            f"--project={src_p} --location=<loc> --destination-project=projects/<dst番号>` / "
            f"`restore-channels create <rch> --project={src_p} --location=<loc> "
            f"--destination-project=projects/<dst番号>`  "
            "③ dst 側の権限（サービスエージェント）: "
            f"`gcloud beta services identity create --service=gkebackup.googleapis.com "
            f"--project={proj}` → {proj} に "
            "`roles/gkebackup.serviceAgent`（gcp-sa-gkebackup）、"
            f"backup 保持プロジェクトに `roles/gkebackup.crossProjectServiceAgent`"
            "（container-engine-robot）を付与  "
            f"④ dst で restore: `restore-plans create` → `restores create`"
            f"（project={proj}）。"
            "dst クラスタのエージェントは本ツールが有効化済み。"
            "クラスタ外リソース（Cloud Run 用 LB の SSL 証明書等）は対象外のまま",
        )
    if kind == "alert_notification_channels":
        return (
            "確認",
            f"アラートポリシーの通知チャネル（{proj}）",
            "通知チャネルは server 採番 ID で参照されるため別プロジェクトへ複製できず、"
            "旧 ID のままでは解決しない。チャネル指定を外してアラート本体のみ複製した"
            "（アラートは発火するが通知は飛ばない）",
            "dst で通知チャネルを作成し直し、アラートポリシーに再設定: "
            f"`gcloud beta monitoring channels create --project={proj} ...` → "
            "コンソールでポリシーに紐付け",
        )
    if kind == "deletion_protection":
        return (
            "確認",
            f"`{res}`（{proj}）の削除保護",
            "export に deletion_protection が含まれず provider 既定の true が効くと、"
            "apply 途中で失敗した (tainted) リソースを次回 replace できず移行が詰むため、"
            "dst 側は false を明示して複製した",
            "本番運用に切り替える際は dst で削除保護を戻す"
            "（`deletion_protection = true` に変更して apply、またはコンソールで有効化）",
        )
    if kind == "container_analysis_occurrence":
        return (
            "確認",
            f"Container Analysis の occurrence（{proj}）",
            "過去ビルドの来歴・署名レコード。参照先の note（`built-by-cloud-build` 等）は"
            "Cloud Build が自プロジェクトに作るもので dst には存在せず、"
            "署名鍵も Google 管理プロジェクトのため複製不能",
            "dst で再ビルドすれば自動生成される。Binary Authorization で"
            "attestation を必須にしている場合は、再ビルドか手動 attestation が必要",
        )
    if kind == "iap_enabled":
        return (
            "確認",
            f"backend service `{res}`（{proj}）の IAP",
            "export に enabled が含まれないため **true（認証壁を外さない安全側）**で複製。"
            "oauth2_client_id が src ORG の OAuth クライアントを指したままの可能性もある",
            f"dst で IAP 不要なら: `gcloud compute backend-services update {res}"
            f" --global --iap=disabled --project={proj}`",
        )
    # 未知 kind は握り潰さず確認として出す（登録漏れに気付けるように）
    return ("確認", f"{kind}: {res}（{proj}）", "詳細は実行ログを参照", "-")


def load_customize_notes(active_dir: str) -> List[Dict[str, str]]:
    """active/<src>/.customize_notes.json を全プロジェクト分読み出す（純粋関数）。"""
    notes: List[Dict[str, str]] = []
    try:
        names = sorted(os.listdir(active_dir))
    except OSError:
        return notes
    for name in names:
        path = os.path.join(active_dir, name, _CUSTOMIZE_NOTES_FILE)
        try:
            with open(path, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, list):
                notes.extend(n for n in loaded if isinstance(n, dict))
        except (OSError, ValueError):
            continue
    return notes


# ---------------------------------------------------------------------------
# bulk-export 出力と現行 google provider の非互換吸収
# ---------------------------------------------------------------------------
# bulk-export (config-connector) は古い provider スキーマ相当の HCL を出すため、
# 現行 provider では (a) 廃止済みブロックが Unsupported block type になる、
# (b) 後から必須化された引数が Missing required argument になる。
# provider を古い版に固定する手もあるが、他リソースの新フィールドを巻き添えに
# するため、customize 時に内容を直す方が影響範囲が狭い。

# google_container_cluster から現行 provider で削除されたブロック。
# クラスタ本体 (.tf) は複製の主目的なのでファイルごと skip せず、ブロックだけ除去する。
_GKE_REMOVED_TF_BLOCKS = (
    "cluster_telemetry",           # logging_config / monitoring_config に置換済み
    "pod_security_policy_config",  # PSP は GKE 1.25 で廃止
    "protect_config",              # GKE Protect (Container Threat Detection) 提供終了
)


def strip_hcl_blocks(content: str, block_names: Iterable[str]) -> Tuple[str, List[str]]:
    """`<name> { ... }` ブロックを（ネスト込みで）丸ごと除去する（純粋関数）。

    行単位の brace 数勘定で閉じ括弧まで落とす。`_strip_boot_disk_source` と
    同じ方式。除去したブロック名のリストも返す（呼び出し側でログする）。
    """
    names = set(block_names)
    open_re = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*\{\s*$')
    lines = content.split('\n')
    out: List[str] = []
    removed: List[str] = []
    i = 0
    while i < len(lines):
        m = open_re.match(lines[i])
        if m and m.group(1) in names:
            depth = 1
            j = i + 1
            while j < len(lines) and depth > 0:
                depth += lines[j].count('{') - lines[j].count('}')
                j += 1
            removed.append(m.group(1))
            # ブロック直後の空行も 1 つ食って余分な空行を残さない
            if j < len(lines) and not lines[j].strip():
                j += 1
            i = j
            continue
        out.append(lines[i])
        i += 1
    return '\n'.join(out), removed


def ensure_hcl_block_arg(content: str, block_name: str, arg_line: str) -> Tuple[str, int]:
    """`<block_name> { ... }` の本文に引数が無ければブロック先頭へ挿入する（純粋関数）。

    arg_line は `enabled = true` のような 1 行。既に同名引数があるブロックは
    変更しない。挿入した件数も返す。
    """
    arg_name = arg_line.split('=', 1)[0].strip()
    open_re = re.compile(rf'^(\s*){re.escape(block_name)}\s*\{{\s*$')
    arg_re = re.compile(rf'^\s*{re.escape(arg_name)}\s*=', re.M)
    lines = content.split('\n')
    out: List[str] = []
    inserted = 0
    i = 0
    while i < len(lines):
        m = open_re.match(lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue
        depth = 1
        j = i + 1
        while j < len(lines) and depth > 0:
            depth += lines[j].count('{') - lines[j].count('}')
            j += 1
        out.append(lines[i])
        if not arg_re.search('\n'.join(lines[i + 1:j])):
            out.append(f"{m.group(1)}  {arg_line}")
            inserted += 1
        out.extend(lines[i + 1:j])
        i = j
    return '\n'.join(out), inserted


# ---------------------------------------------------------------------------
# ステップの enabled 既定値
# ---------------------------------------------------------------------------
# config.yaml に該当キーが無いときの enabled。既存 config に手を入れなくても
# 動いてほしいステップだけ True にする。execute() と check_service_accounts()
# の両方がこの関数を経由すること（片方だけだと「preflight は権限を要求しないのに
# 本体は走る」といった不整合になる）。
_STEP_ENABLED_DEFAULTS: Dict[str, bool] = {
    "network_firewall": True,
    "iam_sync": True,
    # dst の API 無効は Step 4 以降を軒並み 403 で落とす（GKE = container API が典型）。
    # config を書き換えていない既存環境でも復旧できるよう既定 true。
    "enable_apis": True,
}


def step_enabled(steps: Dict[str, Any], name: str) -> bool:
    """steps.<name>.enabled を既定値込みで解決する。"""
    s = steps.get(name, {})
    default = _STEP_ENABLED_DEFAULTS.get(name, False)
    if not isinstance(s, dict):
        return default
    return bool(s.get('enabled', default))


# ---------------------------------------------------------------------------
# IAM ロール複製 (Step 5.7) の純粋関数
# ---------------------------------------------------------------------------
# user-managed SA の email 形式: <account-id>@<project-id>.iam.gserviceaccount.com
# default compute (<番号>-compute@developer...) / appspot / Google 管理の service
# agent (service-<番号>@gcp-sa-*) はこの形にマッチしないため自然に対象外になる。
# これらは dst 側に同等の SA が既定で存在し、権限も Google 側が管理するため
# 複製してはいけない。
_USER_MANAGED_SA_RE = re.compile(
    r'^([a-zA-Z0-9-]+)@([a-z][-a-z0-9]{4,28}[a-z0-9])\.iam\.gserviceaccount\.com$'
)
# Google 管理の service agent も `.iam.gserviceaccount.com` を使う。
#   service-<プロジェクト番号>@gcp-sa-<api>.iam.gserviceaccount.com
#   service-org-<ORG番号>@<...>.iam.gserviceaccount.com
# これらは Google 側が権限を管理し dst にも自動生成されるため複製対象外。
# 「project_mapping に無い」warning を量産しないよう、email 形式の時点で弾く。
_GOOGLE_MANAGED_SA_ACCOUNT_RE = re.compile(r'^service-(org-)?\d+$')
_PROJECT_ROLE_RE = re.compile(r'^projects/([^/]+)/roles/(.+)$')
_ORG_ROLE_RE = re.compile(r'^organizations/([^/]+)/roles/(.+)$')

# 複製自体は行うが、実行後に必ず一覧で WARNING を出す超高権限ロール。
# いずれも「付与された SA が自分でさらに権限を配れる」= 権限昇格の起点になるため、
# 移行後に人間がレビューできるようログの最後にまとめて出す。
_IAM_HIGH_PRIVILEGE_ROLES = frozenset({
    "roles/owner",
    "roles/resourcemanager.organizationAdmin",
    "roles/resourcemanager.projectIamAdmin",
    "roles/iam.securityAdmin",
})


def parse_user_managed_sa(email: str) -> Optional[Tuple[str, str]]:
    """user-managed SA の email を (account_id, project_id) に分解する。該当外は None。"""
    m = _USER_MANAGED_SA_RE.match((email or "").strip())
    if not m:
        return None
    account_id, project = m.group(1), m.group(2)
    if project.startswith("gcp-sa-") or _GOOGLE_MANAGED_SA_ACCOUNT_RE.match(account_id):
        return None
    return account_id, project


def remap_sa_email(email: str, proj_map: Dict[str, str]) -> Optional[str]:
    """src SA email を dst プロジェクトの同名 SA email に読み替える。対象外は None。"""
    parsed = parse_user_managed_sa(email)
    if not parsed:
        return None
    account_id, proj = parsed
    if proj not in proj_map:
        return None
    return f"{account_id}@{proj_map[proj]}.iam.gserviceaccount.com"


def remap_iam_role(role: str, proj_map: Dict[str, str]) -> Tuple[Optional[str], str]:
    """IAM ロール ID を dst 用に読み替える。`(dst_role, skip_reason)` を返す。

    - 定義済みロール `roles/<name>` … ORG に依存しないためそのまま複製する。
    - プロジェクトカスタムロール `projects/<p>/roles/<r>` … p が project_mapping に
      あれば dst プロジェクトへ読み替える（ロール定義自体は Step 4 の
      google_project_iam_custom_role が複製済みの想定）。無ければスキップ。
    - ORG カスタムロール `organizations/<id>/roles/<r>` … 別 ORG には同じ ID が
      存在しないためスキップ（secure tag と同じ「別 ORG では ID が変わる」問題）。
    """
    role = (role or "").strip()
    if not role:
        return None, "ロールが空です"
    if role.startswith("roles/"):
        return role, ""
    m = _PROJECT_ROLE_RE.match(role)
    if m:
        src_proj, role_id = m.group(1), m.group(2)
        if src_proj in proj_map:
            return f"projects/{proj_map[src_proj]}/roles/{role_id}", ""
        return None, (
            f"カスタムロールの定義元プロジェクト '{src_proj}' が project_mapping に無い"
        )
    if _ORG_ROLE_RE.match(role):
        return None, "ORG カスタムロールは dst ORG に同じ ID が存在しない"
    return None, "解釈できないロール形式"


def build_iam_replication_plan(
    src_policies: Dict[str, Dict[str, Any]],
    proj_map: Dict[str, str],
    exclude_sa_emails: Optional[Any] = None,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """src の project IAM ポリシー群から、dst へ付与すべきバインディングを組み立てる。

    Args:
        src_policies: {src_project_id: `gcloud projects get-iam-policy --format=json` の dict}
        proj_map: src project id → dst project id
        exclude_sa_emails: 複製対象から外す src SA email（移行用の借用 SA など）

    Returns:
        (grants, warnings)
        grants: `{dst_project, dst_member, dst_role, src_project, src_member,
                  src_role, high_privilege}` の list。
                (dst_project, dst_member, dst_role) でユニーク化し、決定的に並べる。
        warnings: 複製しなかったバインディングの理由（呼び出し側が WARNING 出力する）

    スキップ方針（いずれも「dst の権限が src より緩くならない」側に倒す）:
    - 条件付きバインディング … 条件式が src のリソース名を参照しうるため複製しない
    - 読み替え不能なロール … remap_iam_role の理由を添えてスキップ
    - project_mapping 外のプロジェクトに属する SA … 対応する dst SA が決まらない
    """
    exclude = {
        str(e).strip().lower() for e in (exclude_sa_emails or ()) if e
    }
    grants: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    warnings: List[str] = []
    seen: set = set()

    def warn(msg: str):
        if msg not in seen:
            seen.add(msg)
            warnings.append(msg)

    for src_proj in sorted(src_policies):
        policy = src_policies.get(src_proj) or {}
        dst_proj = proj_map.get(src_proj)
        if not dst_proj:
            continue
        for binding in (policy.get('bindings') or []):
            if not isinstance(binding, dict):
                continue
            role = binding.get('role') or ''
            sa_emails: List[str] = []
            for member in (binding.get('members') or []):
                if not isinstance(member, str) or not member.startswith('serviceAccount:'):
                    continue
                email = member.split(':', 1)[1].strip()
                if email.lower() in exclude:
                    continue
                if not parse_user_managed_sa(email):
                    # default compute / appspot / Google 管理 service agent。
                    # dst 側に同等物が既定で存在するため複製しない（警告も不要）。
                    continue
                sa_emails.append(email)
            if not sa_emails:
                continue

            if binding.get('condition'):
                title = (binding.get('condition') or {}).get('title') or '(タイトル無し)'
                warn(
                    f"{src_proj}: 条件付きバインディング (role={role}, condition='{title}') は"
                    f"条件式が src のリソース名を参照しうるため複製しません"
                    f"（権限が緩む方向には作用しません）。対象 SA: "
                    f"{', '.join(sorted(set(sa_emails)))}"
                )
                continue

            dst_role, skip_reason = remap_iam_role(role, proj_map)
            if not dst_role:
                warn(
                    f"{src_proj}: role '{role}' は複製できません（{skip_reason}）。"
                    f"必要なら dst で手動付与してください。対象 SA: "
                    f"{', '.join(sorted(set(sa_emails)))}"
                )
                continue

            for email in sorted(set(sa_emails)):
                dst_email = remap_sa_email(email, proj_map)
                if not dst_email:
                    _acc, sa_proj = parse_user_managed_sa(email)
                    warn(
                        f"{src_proj}: SA '{email}' のプロジェクト '{sa_proj}' は "
                        f"project_mapping に無いため複製しません"
                        f"（必要なら project_mapping に追加してください）"
                    )
                    continue
                dst_member = f"serviceAccount:{dst_email}"
                key = (dst_proj, dst_member, dst_role)
                if key in grants:
                    continue
                grants[key] = {
                    "dst_project": dst_proj,
                    "dst_member": dst_member,
                    "dst_role": dst_role,
                    "src_project": src_proj,
                    "src_member": f"serviceAccount:{email}",
                    "src_role": role,
                    "high_privilege": dst_role in _IAM_HIGH_PRIVILEGE_ROLES,
                }

    return [grants[k] for k in sorted(grants)], warnings


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


# gcloud が 403/404 で吐く構造化エラー詳細（google.rpc.ErrorInfo の YAML ダンプ）。
# 情報量が無いうえ `- '@type': ...ErrorInfo` が 'Error' 判定に引っかかって
# サマリーを占拠する（regression: 失敗詳細が全部 `- '@type': ...` になっていた）。
_ERROR_DETAIL_RE = re.compile(
    r"^(-\s*'?@type'?:|domain:|reason:|metadata:|links:|-\s*description:|url:|"
    r"activationUrl:|consumer:|containerInfo:|service:|serviceTitle:)"
)


def _first_meaningful_line(stderr: Optional[str], stdout: Optional[str], limit: int = 200) -> str:
    """サマリー用に、stderr/stdout から最も情報量の多い1行を抽出する。

    WARNING / impersonation 警告 / 装飾用の空行や枠線、gcloud の構造化エラー詳細は
    飛ばし、エラー行（gcloud の `ERROR:` / terraform の `Error:`）を優先する。
    """
    for src in (stderr, stdout):
        if not src:
            continue
        text = _ANSI_RE.sub('', src)
        lines = [ln.strip(' │╷╵') for ln in text.splitlines() if ln.strip(' │╷╵')]
        lines = [ln for ln in lines if not _ERROR_DETAIL_RE.match(ln)]
        # エラー行を優先。'Error' の単純な部分一致だと gcloud の大文字 `ERROR:` を
        # 取り逃し、代わりに 'ErrorInfo' を含む詳細行を拾ってしまう。
        for ln in lines:
            if 'WARNING' in ln:
                continue
            if re.match(r'^(ERROR|Error)\b', ln) or 'Error:' in ln or 'error:' in ln:
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
      （standalone_projects のみの構成では host/service を省略可能）
    - standalone_projects（共有 VPC 非所属の独立プロジェクト）の src/dst が埋まっている
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
    standalones = mapping.get('standalone_projects', [])
    if standalones and not isinstance(standalones, list):
        errors.append("project_mapping.standalone_projects はリストで定義してください")
        standalones = []
    has_standalone = isinstance(standalones, list) and len(standalones) > 0

    host = mapping.get('host_project')
    services = mapping.get('service_projects', [])
    has_services = isinstance(services, list) and len(services) > 0

    # host は Shared VPC 構成（service_projects あり）でのみ必須。
    # standalone のみの構成では host_project / service_projects とも省略可。
    if not isinstance(host, dict):
        if has_services or not has_standalone:
            errors.append("project_mapping.host_project が定義されていません"
                          "（standalone_projects のみの構成では省略可）")
    else:
        entries.append(("host_project", host))

    if not has_services:
        if not has_standalone:
            errors.append("project_mapping.service_projects が空、または定義されていません"
                          "（共有 VPC 非所属のみなら standalone_projects を定義）")
    else:
        for i, svc in enumerate(services):
            entries.append((f"service_projects[{i}]", svc))

    if has_standalone:
        for i, ent in enumerate(standalones):
            entries.append((f"standalone_projects[{i}]", ent))

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

    # --- Step 3: 移行範囲の絞り込み (bulk_export.resource_types) ---
    # typo（google_ 無し）は include が何にも一致せず「全部除外」という静かな事故に
    # なるため、パターン形式を実行前に検査する。
    bulk = steps.get('bulk_export', {})
    rt = bulk.get('resource_types') if isinstance(bulk, dict) else None
    if rt is not None:
        if not isinstance(rt, dict):
            errors.append("steps.bulk_export.resource_types は辞書で指定してください"
                          "（include / exclude のリスト）")
        else:
            for key in ("include", "exclude"):
                val = rt.get(key)
                if val is None:
                    continue
                if not isinstance(val, list):
                    errors.append(
                        f"steps.bulk_export.resource_types.{key} はリストで"
                        f"指定してください（例: [\"google_compute_*\"]）")
                    continue
                for pat in val:
                    ps = str(pat or '').strip()
                    if not ps:
                        errors.append(
                            f"steps.bulk_export.resource_types.{key} に空の"
                            f"パターンがあります")
                    elif not ps.startswith("google_"):
                        errors.append(
                            f"steps.bulk_export.resource_types.{key} の "
                            f"'{ps}' は Terraform リソース型ではありません"
                            f"（google_ で始まる必要があります。例: google_compute_*）")

    # --- Step 3: CAI エクスポートの規模調整 ---
    # export_resource_types は **KRM Kind**（ComputeInstance）で、customize 側の
    # resource_types が取る **Terraform 型**（google_compute_instance）とは別物。
    # 取り違えると「何にも一致せず全除外」または gcloud の引数エラーになるため、
    # 実行前に形式を検査する。
    if isinstance(bulk, dict):
        ert = bulk.get('export_resource_types')
        if isinstance(ert, str) and ert.strip().lower() == "auto":
            ert = None   # "auto" は実行時に list-resource-types から引く
        if ert is not None:
            if not isinstance(ert, list):
                errors.append(
                    "steps.bulk_export.export_resource_types はリストまたは "
                    "\"auto\" で指定してください"
                    "（例: [\"ComputeInstance\", \"ComputeNetwork\"] / \"auto\"）")
            else:
                for kind in ert:
                    ks = str(kind or '').strip()
                    if not ks:
                        errors.append(
                            "steps.bulk_export.export_resource_types に空の要素があります")
                    elif ks.startswith("google_"):
                        errors.append(
                            f"steps.bulk_export.export_resource_types の '{ks}' は "
                            f"Terraform 型です。ここは KRM Kind を指定してください"
                            f"（例: ComputeInstance）。Terraform 型で絞るなら "
                            f"steps.bulk_export.resource_types を使います")
                    elif not ks[0].isupper():
                        errors.append(
                            f"steps.bulk_export.export_resource_types の '{ks}' は "
                            f"KRM Kind ではありません（大文字始まり。一覧は "
                            f"`gcloud beta resource-config list-resource-types "
                            f"--project=<src>`）")
        sp = bulk.get('storage_path')
        if sp is not None:
            sps = str(sp or '').strip()
            if sps and not sps.startswith("gs://"):
                errors.append(
                    f"steps.bulk_export.storage_path は gs:// で始まる GCS パスを"
                    f"指定してください（現在: '{sps}'）")

    # --- Step 3.7: Artifact Registry イメージ複製 ---
    # scope の綴り間違い（"tag" / "referenced" 等）を黙って "all" に倒すと
    # 「絞ったつもりが全量」で気付けないため、実行前エラーにする。
    if enabled('data_sync'):
        ar = (steps.get('data_sync', {}) or {}).get('artifact_registry', {})
        if isinstance(ar, dict):
            sc = ar.get('scope')
            if sc is not None:
                scs = str(sc or '').strip().lower()
                if scs and scs not in _AR_SCOPES:
                    errors.append(
                        f"steps.data_sync.artifact_registry.scope の '{sc}' は"
                        f"未知の値です（指定できるのは {' / '.join(_AR_SCOPES)}）")

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

    # --- Step 1.5: enable_apis（既定 true なので step_enabled で解決する）---
    if step_enabled(steps, 'enable_apis'):
        ec = steps.get('enable_apis', {}) or {}
        if not isinstance(ec, dict):
            ec = {}
        for key in ('extra_apis', 'skip_apis'):
            val = ec.get(key)
            if val is None:
                continue
            if not isinstance(val, list) or any(not str(v or '').strip() for v in val):
                errors.append(
                    f"steps.enable_apis.{key} は API 名の文字列リストにしてください"
                    f"（例: ['container.googleapis.com']）"
                )
        wait = ec.get('wait_seconds', 120)
        try:
            ok = int(wait) >= 0
        except (TypeError, ValueError):
            ok = False
        if not ok:
            errors.append(
                f"steps.enable_apis.wait_seconds='{wait}' は 0 以上の整数にしてください"
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
        auto_approve: bool = False,
        skip_on_run_override: Optional[bool] = None,
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
        # --yes / -y。続行確認 ([y/N]) を自動承認する。
        # 環境変数では「いつ設定したか気付けない」ため、明示的な引数だけを承認手段とする。
        self.auto_approve = auto_approve
        # bulk_export.skip_on_run の実行時上書き（config.yaml を触らず 1 回だけ
        # 変えたいとき用。None = config に従う）。make run SKIP_ON_RUN=0 / 1。
        self.skip_on_run_override = skip_on_run_override
        self.stats = StageStats()
        self.start_t = time.time()
        # src プロジェクト番号 → dst プロジェクト番号 の対応（customize で番号置換に使用）
        self.proj_num_map: Dict[str, str] = {}
        # VM 復元時の user-managed SA 解決キャッシュ {src_sa_email: dst_sa_email|None}。
        # 並列 restore worker から同一 SA を二重作成しないよう lock で直列化する。
        self._vm_sa_resolved: Dict[str, Optional[str]] = {}
        self._vm_sa_lock = threading.Lock()
        # Step 5.7 が取得した src の project IAM ポリシー。DIFF.md の分類
        # （カスタムロールが実際に誰かへ付与されているか）で再利用する。
        self._src_iam_policies: Dict[str, Dict[str, Any]] = {}
        # customize_hcl が積む手動対応・確認注記（customize 実行のたびにリセットし、
        # active/<src>/.customize_notes.json へ永続化 → Step 99 が DIFF.md に掲載）。
        self._customize_notes: List[Dict[str, str]] = []

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
        - gcrane / crane  … artifact_registry (Step 3.7) のイメージ複製
        """
        if self.mock:
            self.org_logger.info("  [前提チェック] Mock モードのため外部コンポーネントのチェックをスキップ")
            return

        steps = self.config.get('steps', {})

        def enabled(name: str) -> bool:
            return step_enabled(steps, name)

        gcloud_steps = ("cai_scan", "enable_apis", "gce_snapshot", "bulk_export",
                        "gce_restore", "data_sync", "vpc_sc")

        # Step 3.7（AR イメージ複製）は data_sync 配下の設定で on/off する。
        ar_cfg = (steps.get('data_sync', {}) or {}).get('artifact_registry', {})
        ar_enabled = enabled("data_sync") and not (
            isinstance(ar_cfg, dict) and ar_cfg.get('enabled') is False)

        # (ツール名 or 代替候補のタプル, 必要か, 不足時の説明)
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
            (
                ("gcrane", "crane"),
                ar_enabled,
                "gcrane（または crane）。Step 3.7 の Artifact Registry イメージ複製に必須。"
                "インストール: `go install "
                "github.com/google/go-containerregistry/cmd/gcrane@latest`"
                "（バイナリ: https://github.com/google/go-containerregistry/releases）。"
                "docker は代替になりません: pull → push でマルチアーキイメージの "
                "digest が変わり、Cloud Run の `@sha256:` 固定参照が解決できなくなる上、"
                "再実行時も『コピー先に既にある』と判定されず毎回再送されます。"
                "イメージ複製が不要なら steps.data_sync.artifact_registry.enabled: false",
            ),
        ]

        missing: List[str] = []
        ok: List[str] = []
        for tool, needed, hint in required:
            if not needed:
                continue
            # 代替候補が複数あるものは「どれか 1 つあれば OK」
            names = (tool,) if isinstance(tool, str) else tuple(tool)
            found = next((n for n in names if shutil.which(n)), None)
            if found is None:
                missing.append(f"{' / '.join(names)} … {hint}")
            else:
                ok.append(found)

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
        - `--yes` / `-y`（`make plan/run YES=1`）で確認スキップ（CI/非対話用）。
          環境変数による承認は採用しない（設定済みであることに気付けず暗黙承認になるため）。
        - 非対話セッション（stdin が tty でない）かつ `--yes` 未指定ならエラー終了。
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

        if self.auto_approve:
            self.org_logger.warning(
                "  [SA事前チェック] --yes により続行確認を自動承認"
            )
            return

        if not sys.stdin.isatty():
            print(
                " 非対話セッションのため自動続行できません。"
                " 続行する場合は --yes (make plan/run YES=1) を明示指定してください。",
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
            return step_enabled(steps, name)

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
        retry_wait_seconds: Optional[int] = None,
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
                t0 = time.time()
                result = subprocess.run(
                    cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, cwd=cwd, env=env,
                )
                elapsed = int(time.time() - t0)
                if result.returncode != 0:
                    # まだリトライ余地があれば、失敗カウントせず再試行。
                    # 待ち時間は既定 min(5*n, 30) 秒だが、サーバ側の長時間処理が
                    # timeout したケース（bulk-export の 30 分待ち等）では即再試行しても
                    # 同じ時間を溶かすだけなので retry_wait_seconds で延ばせるようにする。
                    if attempt < retries:
                        attempt += 1
                        wait = (retry_wait_seconds if retry_wait_seconds is not None
                                else min(5 * attempt, 30))
                        logger.warning(
                            f"{tag}一時失敗 (exit={result.returncode}, {elapsed}秒経過)。"
                            f"{wait}秒待って再試行 {attempt}/{retries}"
                        )
                        time.sleep(wait)
                        continue
                    combined = f"{result.stdout or ''}\n{result.stderr or ''}"
                    if expect_not_found_ok and "Not found" in combined:
                        logger.info(f"{tag}存在しません（Not Found）")
                        return None
                    logger.error(
                        f"{tag}✗ 失敗 (exit={result.returncode}, {elapsed}秒経過)"
                    )
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

    def _soft_run(
        self,
        cmd: str,
        side: str,
        logger: logging.Logger,
        impersonate_sa: Optional[str] = None,
        timeout: int = 300,
        skip_on_dry_run: bool = True,
    ) -> Tuple[int, str, str]:
        """stats を汚さずコマンドを実行し (returncode, stdout, stderr) を返す。

        `run_command` と違い失敗を `stats.failed` に積まないため、run 全体の
        exit code に影響しない soft fail 用（`_try_dst_suspend` と同じ方針）。
        「失敗しても後続ステップが本来のエラーで気付かせてくれる」補助的な操作に使う。
        ORG 保護（src の書き込み動詞拒否）と mock の fail-closed 判定は
        `run_command` と同じものを通す。
        `skip_on_dry_run=False` の read コマンドは dry_run でも実行する
        （`make plan` で「何を有効化する予定か」を正確に出すため）。
        """
        if side == "src" and not is_src_read_only(cmd):
            logger.error(
                f"[ORG 保護] src 操作で書き込み動詞が検出されたため拒否しました。"
                f" コマンド: {cmd}"
            )
            sys.exit(1)
        if self.mock:
            if not is_known_mock_command(cmd):
                logger.error(f"[Mock] 未対応コマンドのため安全のため停止します: {cmd}")
                sys.exit(1)
            out = self._simulate_command(cmd, logger, "")
            self.stats.incr("mocked")
            return 0, out or "", ""
        if self.dry_run and side != "src" and skip_on_dry_run:
            logger.info(f"    [DRY RUN] 予定: {cmd}")
            return 0, "", ""
        env = os.environ.copy()
        if impersonate_sa:
            env['CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT'] = impersonate_sa
        try:
            res = subprocess.run(
                cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, env=env, timeout=timeout,
            )
            return res.returncode, res.stdout or "", res.stderr or ""
        except Exception as e:
            return 1, "", str(e)

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
                # GKE ノード。対応するスナップショットを意図的に用意していないので、
                # 除外が壊れると Step 2 が「有効スナップショットがない」で exit 1 する
                # （= make mock がそのまま回帰テストになる）。
                {
                    "name": "gke-mock-cluster-default-pool-1234abcd-xyz1",
                    "zone": f"projects/{proj_id}/zones/asia-northeast1-a",
                    "labels": {"goog-gke-node": ""},
                    "disks": [{"boot": True, "source": f"projects/{proj_id}/zones/asia-northeast1-a/disks/gke-mock-cluster-default-pool-1234abcd-xyz1"}],
                },
            ])

        if cmd.strip().startswith("gcloud beta resource-config list-resource-types"):
            logger.info(f"{tag}[MOCK] KRM Kind 一覧をシミュレート ({proj_id})")
            # k8s の Kind は含まれない（実機と同じ）。除外が壊れると気付けるよう
            # bulk-export 非対応の Kind も 1 つ混ぜておく。
            return json.dumps([
                {"GVK": {"Group": "compute.cnrm.cloud.google.com",
                         "Kind": "ComputeInstance"}, "SupportsBulkExport": True},
                {"GVK": {"Group": "compute.cnrm.cloud.google.com",
                         "Kind": "ComputeNetwork"}, "SupportsBulkExport": True},
                {"GVK": {"Group": "container.cnrm.cloud.google.com",
                         "Kind": "ContainerCluster"}, "SupportsBulkExport": True},
                {"GVK": {"Group": "iam.cnrm.cloud.google.com",
                         "Kind": "IAMPolicy"}, "SupportsBulkExport": False},
            ])

        if cmd.strip().startswith("gcloud run services list"):
            logger.info(f"{tag}[MOCK] Cloud Run サービス一覧をシミュレート ({proj_id})")
            return json.dumps([{
                "metadata": {
                    "name": "mock-public-api",
                    "labels": {"cloud.googleapis.com/location": "asia-northeast1"},
                },
            }])

        if cmd.strip().startswith("gcloud run services get-iam-policy"):
            logger.info(f"{tag}[MOCK] Cloud Run IAM ポリシーをシミュレート")
            # 公開サービス（allUsers → run.invoker）。複製パスが壊れると
            # make mock の add-iam-policy-binding が出なくなるので気付ける。
            return json.dumps({
                "bindings": [
                    {"role": "roles/run.invoker", "members": ["allUsers"]},
                ],
            })

        if cmd.strip().startswith("gcloud artifacts repositories list"):
            logger.info(f"{tag}[MOCK] AR リポジトリ一覧をシミュレート ({proj_id})")
            return json.dumps([
                {
                    "name": f"projects/{proj_id}/locations/asia-northeast1"
                            f"/repositories/cloud-run-source-deploy",
                    "format": "DOCKER",
                },
                # DOCKER 以外は複製対象外。除外が壊れると docker pull が走る。
                {
                    "name": f"projects/{proj_id}/locations/asia-northeast1"
                            f"/repositories/python-repo",
                    "format": "PYTHON",
                },
            ])

        if cmd.strip().startswith("gcloud artifacts docker images list"):
            m = re.search(r'images list (\S+)', cmd)
            path = m.group(1) if m else "asia-northeast1-docker.pkg.dev/p/r"
            logger.info(f"{tag}[MOCK] AR イメージ一覧をシミュレート ({path})")
            return json.dumps([
                {
                    "package": f"{path}/mock-api",
                    "version": "sha256:" + "ab" * 32,
                    "tags": ["latest"],
                },
                # tag 無し（digest 参照専用）。合成タグでの push が壊れると気付ける。
                {
                    "package": f"{path}/mock-worker",
                    "version": "sha256:" + "cd" * 32,
                    "tags": [],
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

        if cmd.strip().startswith("gcloud services list"):
            logger.info(f"{tag}[MOCK] 有効 API 一覧をシミュレート ({proj_id})")
            # dst 側 (`*2026*` 等) は最小構成、src 側は GKE 等を使っている想定にして
            # 「src で有効 / dst で無効」の差分が mock でも必ず出るようにする。
            base = [
                "cloudresourcemanager.googleapis.com",
                "serviceusage.googleapis.com",
                "iam.googleapis.com",
                "iamcredentials.googleapis.com",
                "compute.googleapis.com",
            ]
            if proj_id.startswith("dst") or "2026" in proj_id:
                return "\n".join(base)
            return "\n".join(base + [
                "container.googleapis.com",
                "storage.googleapis.com",
                "bigquery.googleapis.com",
                "bigquery-json.googleapis.com",   # skip 対象（旧エイリアス）
            ])

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

        if cmd.strip().startswith("gcloud projects get-iam-policy"):
            m = re.search(r'get-iam-policy\s+(\S+)', cmd)
            pid = m.group(1) if m else proj_id
            logger.info(f"{tag}[MOCK] IAM ポリシーをシミュレート ({pid})")
            # 定義済みロール / owner / プロジェクトカスタムロール / 条件付き /
            # Google 管理 SA を一通り含め、複製ロジックの各分岐を mock で通す。
            sa = f"serviceAccount:editor@{pid}.iam.gserviceaccount.com"
            return json.dumps({
                "bindings": [
                    {"role": "roles/storage.admin", "members": [sa]},
                    {"role": "roles/owner", "members": [sa]},
                    {"role": f"projects/{pid}/roles/customViewer",
                     "members": [f"serviceAccount:app-sa@{pid}.iam.gserviceaccount.com"]},
                    {"role": "roles/compute.admin",
                     "members": ["serviceAccount:123456789-compute@developer.gserviceaccount.com"]},
                    {"role": "roles/bigquery.dataViewer", "members": [sa],
                     "condition": {"title": "expire", "expression": "request.time < timestamp('2030-01-01T00:00:00Z')"}},
                ],
                "etag": "BwXmock==",
            })


        # 残りのパターン（_MOCK_KNOWN_PATTERNS に含まれるもの）はすべて成功扱い
        logger.info(f"{tag}[MOCK] コマンド成功をシミュレート: {cmd.split()[0]} {cmd.split()[1] if len(cmd.split()) > 1 else ''}")
        return "Success"

    def _resource_type_filters(self) -> Tuple[List[str], List[str]]:
        """steps.bulk_export.resource_types の (include, exclude) を返す。

        移行範囲の絞り込み。未指定なら両方空 = 全量コピー（既定）。
        DIFF (Step 99) も同じ値を見て、除外した型の欠落を「対応不要」に分類する。
        """
        cfg = (self.config.get('steps', {}).get('bulk_export', {}) or {}).get(
            'resource_types', {})
        if not isinstance(cfg, dict):
            return [], []
        inc = [str(p).strip() for p in (cfg.get('include') or [])
               if isinstance(cfg.get('include'), list) and str(p).strip()]
        exc = [str(p).strip() for p in (cfg.get('exclude') or [])
               if isinstance(cfg.get('exclude'), list) and str(p).strip()]
        return inc, exc

    # ----- Terraform 作業ディレクトリ -----
    def _tf_base_dir(self) -> str:
        """terraform の raw/active を置くベースディレクトリ。

        mock は **必ず別ディレクトリ**（<base>/mock）に出す。同じ active/ を使うと
        mock が書いたダミー .tf（mock-cluster / mock bucket 等）が残り、次の
        `make run` が skip_on_run=true で「既存 active を再利用」して **dst に実在
        しないリソースを本当に作ってしまう**（regression: mock 直後の run で
        mock_bucket が dst に作成され、mock_cluster は container API 無効の 403 で
        失敗した）。
        """
        base = (self.config.get('steps', {}).get('bulk_export', {}) or {}).get(
            'output_dir', './terraform')
        return os.path.join(base, 'mock') if self.mock else base

    # ----- Mock 時のダミー TF ファイル書き出し -----
    def _write_dummy_tf_files(self, proj_dir: str, proj_id: str):
        self.org_logger.info(f"  [MOCK] ダミー TF を書き出し: {proj_dir}")
        vm_hcl = f"""
# {_MOCK_TF_MARK}
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
# {_MOCK_TF_MARK}
resource "google_storage_bucket" "mock_bucket" {{
  name     = "org-bucket-shared-data"
  project  = "{proj_id}"
  location = "US"
}}
"""
        # GKE 派生の instance template は active に出ないこと、クラスタ本体は
        # 出ることを make mock で確認できるようにする。
        gke_template_hcl = f"""
# {_MOCK_TF_MARK}
resource "google_compute_instance_template" "mock_gke_template" {{
  name         = "gke-mock-cluster-default-pool-1234abcd"
  project      = "{proj_id}"
  machine_type = "e2-medium"
}}
"""
        cluster_hcl = f"""
# {_MOCK_TF_MARK}
resource "google_container_cluster" "mock_cluster" {{
  name     = "mock-cluster"
  project  = "{proj_id}"
  location = "asia-northeast1-a"
}}
"""
        try:
            with open(os.path.join(proj_dir, "google_compute_instance.tf"), "w", encoding="utf-8") as f:
                f.write(vm_hcl)
            with open(os.path.join(proj_dir, "google_storage_bucket.tf"), "w", encoding="utf-8") as f:
                f.write(bucket_hcl)
            with open(os.path.join(proj_dir, "google_compute_instance_template.tf"), "w", encoding="utf-8") as f:
                f.write(gke_template_hcl)
            with open(os.path.join(proj_dir, "google_container_cluster.tf"), "w", encoding="utf-8") as f:
                f.write(cluster_hcl)
        except Exception as e:
            self.org_logger.error(f"  [MOCK] ダミー TF 書き出し失敗: {e}")

    def check_dst_projects_exist(self):
        """dst プロジェクトの実在を fail-fast で検査する（mock はスキップ）。

        config の dst を新しい番号に書き換えたのに `make projects` を忘れると、
        Step 1〜3（src read 中心）と API 有効化（soft fail）は素通りし、
        **30 分走った Step 4 の apply で初めて**
        `The resource 'projects/<dst>' was not found` の 404 で全滅する
        （regression: 081401 系で発生）。dst へ何も書く前に全件列挙して止め、
        `make projects` を案内する。`make plan` でも実行する。
        """
        if self.mock:
            return
        dsts = sorted({d for _s, d, _ss, _ds in self._iter_project_pairs()})
        missing: List[str] = []
        for dst in dsts:
            try:
                res = subprocess.run(
                    f"gcloud projects describe {dst} "
                    f"--format='value(lifecycleState)' --quiet",
                    shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, env=os.environ.copy(), timeout=60,
                )
                state = (res.stdout or "").strip()
                if res.returncode != 0 or state != "ACTIVE":
                    missing.append(
                        f"{dst}（{state or _first_meaningful_line(res.stderr, res.stdout)[:80]}）"
                    )
            except Exception as e:
                missing.append(f"{dst}（確認失敗: {e}）")
        if missing:
            self.org_logger.error(
                "[dst 実在チェック] 以下の dst プロジェクトが存在しない/アクセスできません:"
            )
            for m in missing:
                self.org_logger.error(f"  - {m}")
            self.org_logger.error(
                "  config.yaml の dst を新しい ID に変えた場合は、先に "
                "`make projects`（作成）と `make bootstrap`（Shared VPC 等）を"
                "実行してください。dst へは何も書き込まずに停止します"
            )
            sys.exit(1)
        self.org_logger.info(
            f"  [dst 実在チェック] {len(dsts)} プロジェクトすべて ACTIVE ✓"
        )

    def _acquire_run_lock(self):
        """多重起動ガード（flock）。

        同じ terraform 作業ディレクトリを 2 つの `make run` / `make plan` が
        同時に触ると、state lock 競合（`Error acquiring the state lock`）・
        `Saved plan is stale`・`-lock=false` の import 並走による state 破壊で
        **両方の run が壊れる**（regression: run の二重起動で 3 ルートが
        lock/stale で失敗した）。terraform 配下の `.sync_env.lock` を
        排他 flock し、取れなければ即エラーで止める。
        プロセス終了（異常終了含む）で OS が自動解放するため、
        古いロックの掃除は不要。mock は `_tf_base_dir()` が別ディレクトリを
        指すので実行系とは競合しない。
        """
        lock_dir = self._tf_base_dir()
        os.makedirs(lock_dir, exist_ok=True)
        lock_path = os.path.join(lock_dir, ".sync_env.lock")
        self._run_lock_file = open(lock_path, "w", encoding="utf-8")
        try:
            fcntl.flock(self._run_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print(
                f"エラー: 別の make run / make plan がこの作業ディレクトリ"
                f"（{lock_dir}）で実行中です。多重実行は terraform state を"
                f"破壊するため停止します。先行プロセスの終了を待ってください。",
                file=sys.stderr,
            )
            sys.exit(1)
        self._run_lock_file.write(str(os.getpid()))
        self._run_lock_file.flush()

    # ----- 実行制御 -----
    def execute(self):
        self.load_config()
        self._acquire_run_lock()
        self.check_prerequisites()
        self.check_service_accounts()
        self.check_dst_projects_exist()

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
            if step_enabled(steps, 'cai_scan'):
                self.step_cai_scan()
            # API 有効化は他の dst 書き込みより前に。dst で API が無効だと Step 4 の
            # terraform apply（GKE = container.googleapis.com が典型）や Step 5/6 が
            # 軒並み 403 で落ちる。CAI 直後なら「src で有効な API」が手元にあり、
            # かつ Step 2/3（src 読み取り + export）の間に有効化の伝播時間も稼げる。
            if step_enabled(steps, 'enable_apis'):
                self.step_enable_apis()
            if step_enabled(steps, 'gce_snapshot'):
                self.step_gce_snapshot()
            if step_enabled(steps, 'bulk_export'):
                self.step_bulk_export()
            # Step 3.5: 必要 API が確定する唯一の時点（.tf が出揃った直後）。
            # ここで全 dst プロジェクト分をまとめて有効化 + 伝播確認してから
            # terraform に進む。Step 1.5 は src 由来だけなので、export された
            # .tf 固有の API（GKE 等）を取りこぼしうる。
            if step_enabled(steps, 'enable_apis'):
                self.step_enable_apis(final=True)
            # Step 3.7: AR イメージは terraform より前に置く。Cloud Run は
            # revision 作成時に digest を解決するため、apply の後に複製しても
            # 間に合わない（Image ... not found で apply が失敗する）。
            if step_enabled(steps, 'data_sync'):
                self.step_artifact_registry()
            if step_enabled(steps, 'terraform_apply'):
                self.step_terraform_apply()
            if step_enabled(steps, 'network_firewall'):
                self.step_network_firewall()
            if step_enabled(steps, 'gce_restore'):
                self.step_gce_restore()
            # IAM は SA が dst に出来たあと（Step 4 の terraform / Step 5 の VM SA 作成後）
            # に流す。data_sync は dst 借用 SA で動くのでこの位置で影響を受けない。
            if step_enabled(steps, 'iam_sync'):
                self.step_iam_sync()
            if step_enabled(steps, 'data_sync'):
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
            2. 欠落リソース + 推奨 gcloud コマンドを log と DIFF.md に出力。
        DIFF.md の実体は `logs/<timestamp>/DIFF.md`（org.log / dst.log と同居）に書き、
        リポジトリ直下 (cwd) の `DIFF.md` は常に最新実行を指す symlink として張り替える。
        log は org_logger（INFO） を経由するため stdout にも自動で流れる。
        """
        log_stage_header(self.org_logger, 99, "CAI ↔ TF 差分レポート", 0)
        cai_cfg = self.config.get('steps', {}).get('cai_scan', {})
        bulk_cfg = self.config.get('steps', {}).get('bulk_export', {})
        cai_dir = cai_cfg.get('output_dir', './cai_export')
        tf_base = self._tf_base_dir()
        proj_map = self._build_proj_id_map()

        # 要対応 / 参考 の分類材料。Step 5.7 が取得済みの src ポリシーを再利用し、
        # 未取得（iam_sync 無効 / 取得失敗）なら None を渡して安全側（要対応）に倒す。
        iam_sync_enabled = step_enabled(self.config.get('steps', {}), 'iam_sync')
        gce_restore_enabled = step_enabled(self.config.get('steps', {}), 'gce_restore')
        rt_include, rt_exclude = self._resource_type_filters()
        bound_roles = (
            bound_custom_role_ids(self._src_iam_policies)
            if self._src_iam_policies else None
        )

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
                iam_sync_enabled=iam_sync_enabled,
                bound_custom_roles=bound_roles,
                gce_restore_enabled=gce_restore_enabled,
                rt_include=rt_include, rt_exclude=rt_exclude,
            )
            reports.append(report)
            self.org_logger.info(
                f"  {src_proj}: CAI {report['cai_total']} 件 / "
                f"TF {report['tf_total']} 件 / 一致 {report['covered']} 件 / "
                f"要対応 {report['action_total']} 件 / "
                f"参考 {len(report['missing']) - report['action_total']} 件 / "
                f"自動・対象外 {report.get('auto_handled', 0)} 件"
            )

        if not reports:
            self.org_logger.info("  解析対象なし（CAI 出力が見つかりません）。")
            return

        # customize が積んだ手動対応・確認注記（SSL 証明書の手動作成、IAP を
        # 有効側で複製した確認など）。ファイル永続なので skip_on_run でも出る。
        manual_notes = load_customize_notes(os.path.join(self._tf_base_dir(), 'active'))

        # 標準出力 / org.log にも詳細を流す
        text = format_diff_report(reports, manual_notes=manual_notes)
        for line in text.splitlines():
            self.org_logger.info(line)

        # 実体は logs/<timestamp>/DIFF.md に出力し、cwd の DIFF.md は symlink で
        # 最新実行に張り替える。ファイル書き込みは dry_run でも実行する
        # （src への書き込みは発生しない）。
        diff_in_run = os.path.abspath(os.path.join(self.run_dir, "DIFF.md"))
        diff_symlink = os.path.abspath("DIFF.md")
        try:
            with open(diff_in_run, "w", encoding="utf-8") as f:
                f.write(text)
            self.org_logger.info(f"  ✓ 差分レポートを書き出しました: {diff_in_run}")
        except OSError as e:
            self.org_logger.error(f"  DIFF.md の書き出しに失敗: {e}")
            return

        # cwd の DIFF.md を最新の実体への相対シンボリックリンクに張り替える。
        # リポジトリを別パスに移しても壊れないよう、target は相対パスにする。
        try:
            if os.path.islink(diff_symlink) or os.path.exists(diff_symlink):
                os.remove(diff_symlink)
            rel_target = os.path.relpath(
                diff_in_run, start=os.path.dirname(diff_symlink)
            )
            os.symlink(rel_target, diff_symlink)
            self.org_logger.info(
                f"  ✓ {diff_symlink} を最新版にリンク: → {rel_target}"
            )
        except OSError as e:
            self.org_logger.warning(
                f"  DIFF.md の symlink 更新に失敗（実体は {diff_in_run} に保存済み）: {e}"
            )

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
    def _host_skipped(self) -> bool:
        """project_mapping.host_project.skip=true なら host を処理対象から外す。"""
        return bool(self.config.get('project_mapping', {})
                    .get('host_project', {}).get('skip', False))

    def _skipped_host_src(self) -> Optional[str]:
        """skip 指定された host の src ID（skip でなければ None）。"""
        host = self.config.get('project_mapping', {}).get('host_project', {})
        return host.get('src') if host.get('skip', False) else None

    def _standalone_entries(self) -> List[Dict]:
        """共有 VPC 非所属の standalone_projects エントリ（dict のみ）を返す。"""
        raw = (self.config.get('project_mapping', {})
               .get('standalone_projects', []) or [])
        return [e for e in raw if isinstance(e, dict)]

    def _iter_src_projects(self, include_skipped: bool = False):
        """(src_proj_id, src_sa) を順に返す。skip 指定の host は既定で除外。"""
        mapping = self.config.get('project_mapping', {})
        host = mapping.get('host_project', {})
        if host.get('src') and (include_skipped or not self._host_skipped()):
            yield host['src'], host.get('src_impersonate_service_account')
        for svc in mapping.get('service_projects', []):
            if svc.get('src'):
                yield svc['src'], svc.get('src_impersonate_service_account')
        for ent in self._standalone_entries():
            if ent.get('src'):
                yield ent['src'], ent.get('src_impersonate_service_account')

    def _iter_project_pairs(self, include_skipped: bool = False):
        """(src, dst, src_sa, dst_sa) を順に返す。skip 指定の host は既定で除外。"""
        mapping = self.config.get('project_mapping', {})
        host = mapping.get('host_project', {})
        if (host.get('src') and host.get('dst')
                and (include_skipped or not self._host_skipped())):
            yield (host['src'], host['dst'],
                   host.get('src_impersonate_service_account'),
                   host.get('dst_impersonate_service_account'))
        for svc in mapping.get('service_projects', []):
            if svc.get('src') and svc.get('dst'):
                yield (svc['src'], svc['dst'],
                       svc.get('src_impersonate_service_account'),
                       svc.get('dst_impersonate_service_account'))
        for ent in self._standalone_entries():
            if ent.get('src') and ent.get('dst'):
                yield (ent['src'], ent['dst'],
                       ent.get('src_impersonate_service_account'),
                       ent.get('dst_impersonate_service_account'))

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
        k8s_types = [t for t in counts if _is_k8s_asset_type(t)]

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

        if k8s_types:
            total = sum(counts[t] for t in k8s_types)
            self.org_logger.info(
                f"  ℹ GKE クラスタ内の k8s オブジェクト（複製対象外）: "
                f"{len(k8s_types)} 種 / {total} 件"
            )

        if uncovered and fail_on_uncovered:
            self.org_logger.error(
                f"  fail_on_uncovered=true のため未登録アセット {len(uncovered)} 種で停止"
            )
            sys.exit(1)

    # ============================================================
    # Step 1.5: dst API 事前有効化
    # ============================================================
    def step_enable_apis(self, final: bool = False):
        """src で有効な API を dst でも有効化する（冪等 / soft fail）。

        dst で API が無効なままだと Step 4 の terraform apply が
        「<API> has not been used in project ... before or it is disabled」の 403 で
        止まる（GKE = container.googleapis.com が典型）。

        **必要 API がいつ確定するか**（この 2 段構えの理由）:
          - Step 1 (cai_scan) 完了時点 … src で有効な API と assetType が分かる。
            ただし「terraform が実際に何を作るか」はまだ分からない。
          - Step 3 (bulk_export + customize) 完了時点 … active/<src>/*.tf が確定し、
            `tf_required_apis()` で **apply に要る API が確定する**。
            つまり **完全な一覧が揃う最初の時点が Step 3 完了後**。
        そこで:
          - `final=False`（Step 1.5, cai_scan 直後）… src 由来を先行有効化し、
            Step 2/3 の実行中に伝播時間を稼ぐ。
          - `final=True`（Step 3.5, bulk_export 直後 / terraform の前）…
            .tf 由来を含めた**全量**を有効化し、terraform を回す前に
            「全部 enabled として見えるか」を検証する（取りこぼしゼロの関門）。

        有効 API の取得元（どれか欠けても動く）:
          1. `gcloud services list --enabled`（src read-only）
          2. Step 1 の CAI 出力にある `serviceusage.googleapis.com/Service`
          3. active/<src>/*.tf のリソース型（final=True では必ず揃っている）
          4. 有効ステップが dst で必ず叩く API (`_STEP_DST_APIS` / `_BASE_DST_APIS`)
        失敗は WARNING + 手動コマンド案内に留める（`stats.failed` に積まない）。
        本当に必要な API なら、後続ステップが本来のエラーで止めてくれる。
        """
        pairs = list(self._iter_project_pairs())
        log_stage_header(
            self.dst_logger,
            35 if final else 15,
            ("dst API 最終有効化 (.tf から確定した必要 API を全て有効化・検証)"
             if final else "dst API 事前有効化 (src で有効な API を dst に反映)"),
            len(pairs),
        )
        results: List[Tuple[str, int, int, List[str]]] = []
        results_lock = threading.Lock()
        steps = self.config.get('steps', {})
        cfg = steps.get('enable_apis', {})
        if not isinstance(cfg, dict):
            cfg = {}
        extra = cfg.get('extra_apis') or []
        skip = cfg.get('skip_apis') or []
        wait_sec = coerce_nonneg_int(cfg.get('wait_seconds', 120), 120)
        cai_dir = (steps.get('cai_scan', {}) or {}).get('output_dir', './cai_export')

        def worker(item):
            src_proj, dst_proj, src_sa, dst_sa = item
            cai_services, asset_types = cai_api_hints(
                os.path.join(cai_dir, f"cai_resources_{src_proj}.txt")
            )
            listed = self._list_enabled_services(src_proj, "src", src_sa)
            if listed is None and not cai_services:
                self.dst_logger.warning(
                    f"  → {dst_proj}: src '{src_proj}' の有効 API を特定できませんでした"
                    f"（serviceusage.services.list 権限 / CAI 出力を確認）。"
                    f" 有効ステップの必須 API のみ有効化します"
                )
            src_services = (listed or set()) | cai_services
            # active/<src> の .tf から「これから apply する型」→ API を引く。
            # final=True（Step 3.5）では bulk_export 直後なので **必ず確定している**。
            # final=False（Step 1.5）では前回 plan の残りがあれば拾えるだけの保険。
            tf_dir = os.path.join(self._tf_base_dir(), 'active', src_proj)
            # mock 生成物ガードは実行時のみ。mock モードでは _tf_base_dir() が
            # terraform/mock/ を指しており、そこの .tf は mock にとっての正なので
            # 読む（読まないと mock が TF 由来パスの回帰テストにならない）。
            ignore_tf = not self.mock and tf_dir_has_mock_artifacts(tf_dir)
            tf_apis = [] if ignore_tf else tf_required_apis(tf_dir)
            if final and not tf_apis and not self.dry_run:
                # dry_run は customize が .tf を書き出さないので空で正常。
                self.dst_logger.warning(
                    f"  → {dst_proj}: {tf_dir} から必要 API を引けませんでした"
                    f"（.tf 無し / mock 生成物）。src 由来の API のみ有効化します"
                )
            want = build_api_enable_plan(
                src_services, asset_types, steps, list(extra) + tf_apis, skip,
            )
            # 除外した API は黙って消さずログに残す（skip 判断の誤りに気付けるように）。
            dropped = sorted(
                {a for a in src_services if _API_SERVICE_RE.match(a)} - set(want)
            )
            if dropped:
                self.dst_logger.info(
                    f"    dst で有効化しない API（自動管理 / 廃止 / skip_apis）"
                    f" {len(dropped)} 件: {', '.join(dropped)}"
                )
            have = self._list_enabled_services(dst_proj, "dst", dst_sa) or set()
            missing = [a for a in want if a not in have]
            self.dst_logger.info(
                f"  → {dst_proj} (src={src_proj}): 必要 {len(want)} 件 / "
                f"有効済み {len(want) - len(missing)} 件 / 追加 {len(missing)} 件"
            )
            failed: List[str] = []
            if missing:
                self.dst_logger.info(f"    有効化対象: {', '.join(missing)}")
                failed = self._enable_apis_on_dst(dst_proj, dst_sa, missing)
            enabled_now = [a for a in missing if a not in failed]
            with results_lock:
                results.append((dst_proj, len(want), len(missing), list(failed)))
            if wait_sec > 0 and not self.dry_run and not self.mock:
                # 有効化直後は反映ラグがあり、すぐ terraform を回すと 403 になる。
                # final では**新規分だけでなく want 全体**が enabled として見えることを
                # 確認してから次へ進む（「事前に全部有効」を保証する関門）。
                verify = [a for a in want if a not in failed] if final else enabled_now
                if verify:
                    self._wait_for_apis_enabled(
                        dst_proj, dst_sa, verify,
                        timeout_sec=wait_sec, interval_sec=8,
                    )
            if failed:
                self.dst_logger.warning(
                    f"    ⚠ {dst_proj} で有効化できなかった API {len(failed)} 件: "
                    f"{', '.join(failed)}"
                )
                self.dst_logger.warning(
                    f"      移行に必要なら手動で: "
                    f"gcloud services enable {' '.join(failed)} --project={dst_proj}"
                )
                self.dst_logger.warning(
                    f"      権限不足なら実行 SA に roles/serviceusage.serviceUsageAdmin"
                    f"（serviceusage.services.enable）を付与。"
                    f" dst で不要な API は steps.enable_apis.skip_apis に追加してください"
                )

        self._parallel_for_each(pairs, worker, "enable-apis")

        label = "Step 3.5" if final else "Step 1.5"
        all_failed = [(p, f) for p, _w, _m, f in results if f]
        total_want = sum(w for _p, w, _m, _f in results)
        total_added = sum(m for _p, _w, m, _f in results)
        if all_failed:
            self.dst_logger.warning(
                f"  ⚠ {label}: 有効化できなかった API があります"
                f"（apply が 403 で止まる可能性）:"
            )
            for proj, apis in all_failed:
                self.dst_logger.warning(f"      {proj}: {', '.join(apis)}")
        else:
            self.dst_logger.info(
                f"  ✓ {label} 完了: {len(results)} プロジェクトで必要 API を確保"
                f"（必要 計 {total_want} 件 / 今回追加 {total_added} 件）"
            )

    def _list_enabled_services(
        self, project: str, side: str, sa: Optional[str],
    ) -> Optional[Set[str]]:
        """プロジェクトで有効な API 名の集合を返す。取得できなければ None。"""
        logger = self.org_logger if side == "src" else self.dst_logger
        # --quiet: serviceusage API 自体が無効なプロジェクトでは gcloud が
        # 「enable and retry? (y/N)」の対話プロンプトを出す。src 側で y と答えると
        # src への書き込みになる（is_src_read_only はコマンド文字列しか見ないので
        # 検出できない）。非対話でも timeout までハングする。
        rc, out, err = self._soft_run(
            f"gcloud services list --enabled --project={project} "
            f"--format='value(config.name)' --quiet",
            side, logger, impersonate_sa=sa, timeout=180, skip_on_dry_run=False,
        )
        if rc != 0:
            logger.warning(
                f"    有効 API 一覧を取得できませんでした ({project}): "
                f"{_first_meaningful_line(err, out)}"
            )
            return None
        return {ln.strip() for ln in (out or "").splitlines() if ln.strip()}

    def _enable_apis_on_dst(
        self, dst_proj: str, dst_sa: Optional[str], apis: List[str],
    ) -> List[str]:
        """API を batch 有効化し、最後まで失敗したものを返す。

        batchEnable は 1 件でも不正 / 権限外だと chunk 全体が失敗するため、
        chunk が失敗したら 1 件ずつやり直して「本当に有効化できない API」だけを残す。
        """
        failed: List[str] = []
        # 実際に走ったときだけ書込成功にカウントする（mock は "mocked"、
        # dry_run は未実行。run_command と同じ数え方に揃える）。
        count_executed = not self.mock and not self.dry_run
        for i in range(0, len(apis), _API_ENABLE_BATCH):
            chunk = apis[i:i + _API_ENABLE_BATCH]
            rc, out, err = self._soft_run(
                f"gcloud services enable {' '.join(chunk)} --project={dst_proj} --quiet",
                "dst", self.dst_logger, impersonate_sa=dst_sa, timeout=900,
            )
            if rc == 0:
                if count_executed:
                    self.stats.incr("executed")
                continue
            if len(chunk) == 1:
                failed.append(chunk[0])
                self.dst_logger.warning(
                    f"      ✗ {chunk[0]}: {_first_meaningful_line(err, out)}"
                )
                continue
            self.dst_logger.warning(
                f"    一括有効化に失敗したため 1 件ずつ再試行します "
                f"({len(chunk)} 件): {_first_meaningful_line(err, out)}"
            )
            for api in chunk:
                rc1, out1, err1 = self._soft_run(
                    f"gcloud services enable {api} --project={dst_proj} --quiet",
                    "dst", self.dst_logger, impersonate_sa=dst_sa, timeout=300,
                )
                if rc1 == 0:
                    if count_executed:
                        self.stats.incr("executed")
                else:
                    failed.append(api)
                    self.dst_logger.warning(
                        f"      ✗ {api}: {_first_meaningful_line(err1, out1)}"
                    )
        return failed

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

            gke_excluded = 0
            for vm in vms:
                vm_name = vm.get('name')
                # GKE ノードは dst クラスタが作り直すためコピー対象外。
                # スナップショット不在で run 全体を止めない（errors に入れない）。
                if is_gke_node_vm(vm):
                    gke_excluded += 1
                    self.org_logger.info(
                        f"    - {vm_name}: GKE ノード VM のためコピー対象外"
                        f"（クラスタ構成は terraform で複製）"
                    )
                    continue
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

            if gke_excluded:
                self.org_logger.info(
                    f"    {proj_id}: GKE ノード VM {gke_excluded} 台を検証対象から除外"
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
        output_dir_base = self._tf_base_dir()
        raw_dir = os.path.join(output_dir_base, 'raw')
        active_dir = os.path.join(output_dir_base, 'active')

        # `make run` 時のみ skip。`make plan` (dry-run) と mock では従来どおり実行し、
        # active が無い・空の場合は安全側で実行する。
        # 実行時上書き（--skip-on-run / --no-skip-on-run = make run SKIP_ON_RUN=1/0）が
        # あれば config より優先する（config.yaml を触らず 1 回だけ変えたいとき用）。
        skip_on_run = bulk_cfg.get('skip_on_run')
        if self.skip_on_run_override is not None:
            skip_on_run = self.skip_on_run_override
            self.org_logger.info(
                f"  skip_on_run をコマンドラインで上書き: {skip_on_run}"
                f"（config: {bool(bulk_cfg.get('skip_on_run'))}）"
            )
        if skip_on_run and not self.dry_run and not self.mock:
            has_active = os.path.isdir(active_dir) and any(
                any(f.endswith('.tf') for f in os.listdir(os.path.join(active_dir, d)))
                for d in os.listdir(active_dir)
                if os.path.isdir(os.path.join(active_dir, d))
            )
            # mock 生成物が残った active を再利用すると dst に実在しないリソースを
            # 作ってしまう。出力先は分離済みだが、分離前の残骸がある環境のために
            # 内容からも検出し、見つかったら再利用せず export をやり直す。
            mock_dirs = [
                d for d in sorted(os.listdir(active_dir))
                if os.path.isdir(os.path.join(active_dir, d))
                and tf_dir_has_mock_artifacts(os.path.join(active_dir, d))
            ] if os.path.isdir(active_dir) else []

            if mock_dirs:
                self.org_logger.warning(
                    "  skip_on_run=true ですが mock が生成した .tf が残っています: "
                    + ", ".join(mock_dirs)
                    + " → 再利用せず bulk-export からやり直します"
                )
            elif has_active:
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
                    raw_is_mock = os.path.isdir(raw_dir) and any(
                        tf_dir_has_mock_artifacts(os.path.join(raw_dir, d))
                        for d in os.listdir(raw_dir)
                        if os.path.isdir(os.path.join(raw_dir, d))
                    )
                    if raw_is_mock:
                        self.org_logger.warning(
                            f"  raw に mock 生成の .tf が残っているため再利用しません: {raw_dir}"
                        )
                    elif os.path.isdir(raw_dir):
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

        # CAI エクスポートの規模・再試行の調整（大規模プロジェクトの timeout 対策）。
        # "auto" 指定時は list-resource-types から Kind を引く（後述の worker 内）。
        raw_kinds = bulk_cfg.get('export_resource_types')
        auto_kinds = isinstance(raw_kinds, str) and raw_kinds.strip().lower() == "auto"
        export_kinds = [] if auto_kinds else [
            str(k).strip() for k in (raw_kinds or []) if str(k).strip()
        ]
        storage_path = str(bulk_cfg.get('storage_path') or '').strip()
        export_retries = coerce_nonneg_int(bulk_cfg.get('retries', 2), 2)
        export_retry_wait = coerce_nonneg_int(
            bulk_cfg.get('retry_wait_seconds', 180), 180)
        if auto_kinds:
            self.org_logger.info(
                "  export 対象 Kind を自動判定します（list-resource-types。"
                "k8s オブジェクトは対象外）"
            )
            if storage_path:
                self.org_logger.warning(
                    "  export_resource_types と storage_path は排他のため、"
                    "storage_path は無視します（gcloud の仕様）"
                )
        elif export_kinds:
            self.org_logger.info(
                f"  export 対象 Kind を {len(export_kinds)} 種に限定: "
                f"{', '.join(export_kinds)}"
            )
            if storage_path:
                self.org_logger.warning(
                    "  export_resource_types と storage_path は排他のため、"
                    "storage_path は無視します（gcloud の仕様）"
                )
        elif storage_path:
            self.org_logger.info(f"  CAI エクスポート先バケット: {storage_path}")

        def bulk_export_worker(item):
            proj_id, sa = item
            self.org_logger.info(f"  → src '{proj_id}' をエクスポート")
            proj_raw_dir = os.path.join(raw_dir, proj_id)
            # make plan は raw 全体を作り直さない（Makefile の clean 依存を撤去）ため、
            # 前回 export の残骸（src で削除済みリソースの .tf 等）が混ざらないよう
            # プロジェクト単位で作り直す。
            shutil.rmtree(proj_raw_dir, ignore_errors=True)
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
            # `--resource-types`（KRM Kind）と `--storage-path` は gcloud 上で排他。
            # 資産数が多いプロジェクトは CAI エクスポートが config-connector 内部の
            # 30 分待ちに引っかかって `error waiting for operation:` で落ちるため、
            # ① Kind を絞って CAI クエリ自体を小さくする（GCS を使わない経路）か
            # ② 既存バケットを使って毎回の一時バケット作成を省く、を選べるようにする。
            kinds = export_kinds
            if auto_kinds:
                # プロジェクトが対応する KRM Kind を実機から引いて全指定する。
                # 一覧は GCP リソースの Kind だけなので **k8s オブジェクトは自動的に
                # 対象外**になり、移行範囲を狭めずに CAI クエリだけを小さくできる
                # （k8s は Backup for GKE の担当。my-argolis では CAI 1,480 件中
                #  908 件が k8s オブジェクト）。
                rc_k, out_k, err_k = self._soft_run(
                    f"gcloud beta resource-config list-resource-types "
                    f"--project={proj_id} --format=json --quiet",
                    "src", self.org_logger, impersonate_sa=sa, timeout=300,
                    skip_on_dry_run=False,
                )
                kinds = parse_krm_kinds(out_k) if rc_k == 0 else []
                if kinds:
                    self.org_logger.info(
                        f"    {proj_id}: KRM Kind {len(kinds)} 種を明示指定"
                        f"（k8s オブジェクトは対象外）"
                    )
                else:
                    self.org_logger.warning(
                        f"    {proj_id}: Kind 一覧を取得できませんでした"
                        f"（{_first_meaningful_line(err_k, out_k)}）。"
                        f" 絞り込みなしで export します"
                    )
            if kinds:
                cmd += f" --resource-types={','.join(kinds)}"
            elif storage_path:
                cmd += f" --storage-path={storage_path}"
            self.run_command(
                cmd, side="src", logger=self.org_logger,
                desc=f"Bulk Export {proj_id}",
                explanation=f"{proj_id} のリソース定義を Terraform HCL としてエクスポート",
                impersonate_sa=sa, allow_fail=True,
                # config-connector は時々フレーキーに失敗するため再試行するが、
                # timeout 起因（30 分待ちの末に失敗）の場合は即再試行しても同じ時間を
                # 溶かすだけ。既定 2 回 / 間隔 180 秒に抑え、config で調整可能にする。
                retries=export_retries,
                retry_wait_seconds=export_retry_wait,
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
        # host_project.skip でも host は含める: service project の .tf 内にある
        # host プロジェクト番号参照を dst へ置換するため（proj_id_map と同じ扱い）。
        for src, dst, src_sa, dst_sa in self._iter_project_pairs(include_skipped=True):
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
        for ent in self._standalone_entries():
            if ent.get('src') and ent.get('dst'):
                proj_map[ent['src']] = ent['dst']
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
        out_base = self._tf_base_dir()
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

    def _rewrite_subnet_refs_in_active(self, active_dir: str):
        """active の各 Terraform ルートで subnetwork URL を terraform 参照に変換する。

        bulk-export は `subnetwork = "https://.../projects/<p>/regions/<r>/
        subnetworks/<n>"` のハードコード URL を出すため、同じルートに subnetwork の
        定義があっても Terraform が依存関係を認識できず、address / instance などが
        subnetwork より先に作られて
        `The resource 'projects/<dst>/regions/<r>/subnetworks/<n>' was not found`
        の 404 になる（regression: shingo-ar-sharedhost0926 の fix-tokyo1）。
        network 参照 (`_rewrite_network_refs`) と同じ対処を subnetwork にも行う。

        **customize の全書き出しが終わってから**呼ぶこと。ラベルは
        `dedupe_tf_resource_labels` で改名されうるため、確定した active を読んで
        マップを作らないと存在しないラベルを参照してしまう。
        """
        try:
            roots = sorted(os.listdir(active_dir))
        except OSError:
            return
        for name in roots:
            root = os.path.join(active_dir, name)
            if os.path.isdir(root):
                self._rewrite_subnet_refs_one_root(root)

    def _rewrite_subnet_refs_one_root(self, root: str):
        files = sorted(f for f in os.listdir(root) if f.endswith('.tf'))
        contents: Dict[str, str] = {}
        # {(project, region, subnet_name): label}
        sub_map: Dict[tuple, str] = {}
        for fn in files:
            try:
                with open(os.path.join(root, fn), encoding='utf-8', errors='replace') as f:
                    contents[fn] = f.read()
            except OSError:
                continue
            if 'google_compute_subnetwork' not in contents[fn]:
                continue
            for label, body in tf_blocks_of_type(
                    contents[fn], "google_compute_subnetwork"):
                pm = re.search(r'\bproject\s*=\s*"([^"]+)"', body)
                nm = re.search(r'\bname\s*=\s*"([^"]+)"', body)
                rm = re.search(r'\bregion\s*=\s*"([^"]+)"', body)
                if pm and nm and rm:
                    sub_map[(pm.group(1), rm.group(1), nm.group(1))] = label
        if not sub_map:
            return
        url_re = re.compile(
            r'"https://www\.googleapis\.com/compute/v\d+(?:beta\d*)?/projects/'
            r'([A-Za-z0-9_.-]+)/regions/([A-Za-z0-9-]+)/subnetworks/([A-Za-z0-9_.-]+)"'
        )
        for fn, content in contents.items():
            def repl(m: re.Match) -> str:
                label = sub_map.get((m.group(1), m.group(2), m.group(3)))
                if not label:
                    # 別ルート（別プロジェクト = Shared VPC host など）の subnet は
                    # ここでは解決できないので URL のまま残す。
                    return m.group(0)
                return f"google_compute_subnetwork.{label}.self_link"

            new = url_re.sub(repl, content)
            if new == content:
                continue
            try:
                with open(os.path.join(root, fn), 'w', encoding='utf-8') as f:
                    f.write(new)
                self.org_logger.info(
                    f"      subnetwork 参照を terraform 参照に変換: {fn}"
                )
            except OSError as e:
                self.org_logger.warning(f"      subnetwork 参照の書き換え失敗 {fn}: {e}")

    # ----- 同一ルート内の文字列参照 → terraform 参照 変換（2 パス目） -----
    # bulk-export は他リソースへの参照を URL / email / リソースパスの**文字列**で
    # 出力するため、Terraform が依存関係を認識できず、参照先より先に参照元を
    # 作ろうとして 403/404/400 になる（regression: Cloud Run の actAs 403、
    # backend service の security policy 400、forwarding rule の 404。
    # network/subnetwork と同じ問題の別リソース版）。
    # (URL パス種別, 参照先 tf 型) — 参照属性は self_link。
    _URL_REF_KINDS = (
        ("securityPolicies", "google_compute_security_policy"),
        ("targetHttpsProxies", "google_compute_target_https_proxy"),
        ("targetHttpProxies", "google_compute_target_http_proxy"),
        ("urlMaps", "google_compute_url_map"),
        ("backendServices", "google_compute_backend_service"),
        ("sslCertificates", "google_compute_ssl_certificate"),
    )
    # account_id は 6-30 文字、project ID は 6-30 文字（上限を切り詰めると
    # 30 文字ちょうどの dst プロジェクト ID で不一致になる。regression:
    # shingo-ar-standalone2026081400 = 30 文字で actAs 修正が効かなかった）。
    _SA_EMAIL_REF_RE = re.compile(
        r'"([a-z][a-z0-9-]{5,29})@([a-z][a-z0-9-]{5,29})\.iam\.gserviceaccount\.com"'
    )
    _NOTIF_CHANNEL_REF_RE = re.compile(
        r'"projects/[A-Za-z0-9_.-]+/notificationChannels/(\d+)"'
    )

    def _rewrite_resource_refs_in_active(self, active_dir: str):
        """active の各ルートで SA email / compute URL / 通知チャネル参照を変換する。

        `_rewrite_subnet_refs_in_active` と同じ 2 パス目（全書き出し後）。
        ラベルは dedupe で改名されうるため、確定した active を読んでマップを作る。
        同一ルートに定義が無い参照は文字列のまま残す（別プロジェクト参照や、
        skip 済みリソース＝SSL 証明書などは apply 時の本来のエラーで露見させる）。
        """
        try:
            roots = sorted(os.listdir(active_dir))
        except OSError:
            return
        for name in roots:
            root = os.path.join(active_dir, name)
            if os.path.isdir(root):
                self._rewrite_resource_refs_one_root(root)

    # 証明書 URL（global / region 両対応）
    _SSL_CERT_URL_RE = re.compile(
        r'https://www\.googleapis\.com/compute/[a-z0-9]+/projects/'
        r'([A-Za-z0-9_.-]+)/(global|regions/[A-Za-z0-9-]+)/sslCertificates/'
        r'([A-Za-z0-9_.-]+)'
    )

    def _drop_cert_blocked_lb_files(
        self, root: str, contents: Dict[str, str],
    ) -> Dict[str, str]:
        """未作成の SSL 証明書に依存する LB フロント（proxy / FR）を今回の適用から外す。

        self-managed 証明書は秘密鍵が export 不能で **dst に手動作成するしかない**
        （DIFF 要対応）。参照する target proxy を文字列参照のまま残すと、証明書を
        作るまで**毎回 `make run` が 404 で exit 1** になる（regression:
        `Error creating TargetHttpsProxy: ... sslCertificates/notify-api not found`）。
        そこで証明書が dst に実在するかを確認し:
          - 実在（手動作成済み）→ そのまま適用（URL は API で解決できる）
          - 未作成 → proxy と、それを参照する forwarding rule を active から外し、
            DIFF に「証明書作成後の次回 run で自動適用」と要対応で載せる。
            次回 customize が再判定するので、証明書を作れば自動的に適用対象に戻る。
        """
        cert_defined: Set[Tuple[str, str]] = set()
        for content in contents.values():
            for _label, body in tf_blocks_of_type(
                    content, "google_compute_ssl_certificate"):
                pm = re.search(r'\bproject\s*=\s*"([^"]+)"', body)
                nm = re.search(r'\bname\s*=\s*"([^"]+)"', body)
                if pm and nm:
                    cert_defined.add((pm.group(1), nm.group(1)))

        sa_map = self._build_dst_sa_map()
        dst_sa = sa_map.get(os.path.basename(root))
        cert_exists_cache: Dict[Tuple[str, str, str], bool] = {}

        def cert_available(proj: str, scope: str, name: str) -> bool:
            if (proj, name) in cert_defined:
                return True   # 同ルートで作られる（Google-managed 等）
            key = (proj, scope, name)
            if key not in cert_exists_cache:
                loc_flag = ("--global" if scope == "global"
                            else f"--region={scope.split('/', 1)[1]}")
                cert_exists_cache[key] = self._gcloud_exists(
                    f"gcloud compute ssl-certificates describe {name} {loc_flag} "
                    f"--project={proj} --format='value(name)' --quiet",
                    dst_sa,
                )
            return cert_exists_cache[key]

        removed_proxies: Set[Tuple[str, str]] = set()
        for fn in sorted(contents):
            content = contents[fn]
            for tf_type in ("google_compute_target_https_proxy",
                            "google_compute_region_target_https_proxy"):
                if f'resource "{tf_type}"' not in content:
                    continue
                missing = [
                    m for m in self._SSL_CERT_URL_RE.finditer(content)
                    if not cert_available(m.group(1), m.group(2), m.group(3))
                ]
                if not missing:
                    continue
                for _label, body in tf_blocks_of_type(content, tf_type):
                    pm = re.search(r'\bproject\s*=\s*"([^"]+)"', body)
                    nm = re.search(r'\bname\s*=\s*"([^"]+)"', body)
                    if pm and nm:
                        removed_proxies.add((pm.group(1), nm.group(1)))
                names = ", ".join(sorted({m.group(3) for m in missing}))
                self.org_logger.warning(
                    f"      SSL 証明書未作成のため LB proxy を保留（証明書 {names} "
                    f"を作成後の次回 run で適用）: {fn}"
                )
                self._add_customize_note("lb_blocked_on_cert", content, fn)
                try:
                    os.remove(os.path.join(root, fn))
                except OSError:
                    pass
                del contents[fn]
                break

        if removed_proxies:
            proxy_url_re = re.compile(
                r'https://www\.googleapis\.com/compute/[a-z0-9]+/projects/'
                r'([A-Za-z0-9_.-]+)/(?:global|regions/[A-Za-z0-9-]+)/'
                r'targetHttpsProxies/([A-Za-z0-9_.-]+)'
            )
            for fn in sorted(contents):
                content = contents[fn]
                if 'forwarding_rule"' not in content and \
                        '_forwarding_rule"' not in content:
                    continue
                refs = {(m.group(1), m.group(2))
                        for m in proxy_url_re.finditer(content)}
                if not (refs & removed_proxies):
                    continue
                self.org_logger.warning(
                    f"      保留した proxy を参照するため forwarding rule も保留: {fn}"
                )
                self._add_customize_note("lb_blocked_on_cert", content, fn)
                try:
                    os.remove(os.path.join(root, fn))
                except OSError:
                    pass
                del contents[fn]
        return contents

    def _rewrite_resource_refs_one_root(self, root: str):
        files = sorted(f for f in os.listdir(root) if f.endswith('.tf'))
        contents: Dict[str, str] = {}
        for fn in files:
            try:
                with open(os.path.join(root, fn), encoding='utf-8',
                          errors='replace') as f:
                    contents[fn] = f.read()
            except OSError:
                continue

        # 未作成 SSL 証明書に依存する LB フロントを先に外す（外したファイルへの
        # 参照が terraform 参照に書き換わって validate エラーになるのを防ぐため、
        # 参照書き換えより前に行う）。
        contents = self._drop_cert_blocked_lb_files(root, contents)

        # 参照先マップを確定後の active から構築する
        url_maps: Dict[str, Dict[Tuple[str, str], str]] = {}
        for _path_kind, tf_type in self._URL_REF_KINDS:
            m: Dict[Tuple[str, str], str] = {}
            for fn, content in contents.items():
                if tf_type not in content:
                    continue
                for label, body in tf_blocks_of_type(content, tf_type):
                    pm = re.search(r'\bproject\s*=\s*"([^"]+)"', body)
                    nm = re.search(r'\bname\s*=\s*"([^"]+)"', body)
                    if pm and nm:
                        m[(pm.group(1), nm.group(1))] = label
            url_maps[tf_type] = m
        # network / subnetwork の短縮パス参照用マップ（(project, name) → label）。
        net_short_map: Dict[str, Dict[Tuple[str, str], str]] = {}
        for path_kind, tf_type in (("networks", "google_compute_network"),
                                   ("subnetworks", "google_compute_subnetwork")):
            m2: Dict[Tuple[str, str], str] = {}
            for fn, content in contents.items():
                if tf_type not in content:
                    continue
                for label, body in tf_blocks_of_type(content, tf_type):
                    pm = re.search(r'\bproject\s*=\s*"([^"]+)"', body)
                    nm = re.search(r'^\s*name\s*=\s*"([^"]+)"', body, re.M)
                    if pm and nm:
                        m2[(pm.group(1), nm.group(1))] = label
            net_short_map[path_kind] = m2

        # GKE クラスタ名 → ラベル。node pool の `cluster = "<name>"` は文字列なので
        # 依存が張られず、クラスタより先に node pool を作ろうとして 404 になる
        # （さらに remove_default_node_pool の既定プール削除とも競合しうる）。
        cluster_map: Dict[str, str] = {}
        for fn, content in contents.items():
            if 'google_container_cluster' not in content:
                continue
            for label, body in tf_blocks_of_type(content, "google_container_cluster"):
                nm = re.search(r'^\s*name\s*=\s*"([^"]+)"', body, re.M)
                if nm:
                    cluster_map[nm.group(1)] = label

        sa_map: Dict[Tuple[str, str], str] = {}
        for fn, content in contents.items():
            if 'google_service_account' not in content:
                continue
            for label, body in tf_blocks_of_type(content, "google_service_account"):
                pm = re.search(r'\bproject\s*=\s*"([^"]+)"', body)
                am = re.search(r'\baccount_id\s*=\s*"([^"]+)"', body)
                if pm and am:
                    sa_map[(am.group(1), pm.group(1))] = label
        # 通知チャネルは server 採番 ID で参照される。旧 ID は import コメントに
        # しか残らないため、そこから 旧 ID → ラベル を引く。
        channel_map: Dict[str, str] = {}
        for fn, content in contents.items():
            if 'google_monitoring_notification_channel' not in content:
                continue
            for label, _body in tf_blocks_of_type(
                    content, "google_monitoring_notification_channel"):
                cm = re.search(
                    rf'#\s*terraform import\s+google_monitoring_notification_channel\.'
                    rf'{re.escape(label)}\b[^\n]*notificationChannels/(\d+)', content)
                if cm:
                    channel_map[cm.group(1)] = label

        url_re = re.compile(
            r'"https://www\.googleapis\.com/compute/[a-z0-9]+/projects/'
            r'([A-Za-z0-9_.-]+)/(?:global|regions/[A-Za-z0-9-]+)/'
            r'([A-Za-z]+)/([A-Za-z0-9_.-]+)"'
        )
        # GKE 等は network/subnetwork を **短縮パス**（URL でない
        # `projects/<p>/global/networks/<n>`）で出す。URL 版だけ変換していると
        # 依存が張られず、VPC より先にクラスタが作られて 404 になる。
        short_path_re = re.compile(
            r'"projects/([A-Za-z0-9_.-]+)/(?:global|regions/[A-Za-z0-9-]+)/'
            r'(networks|subnetworks)/([A-Za-z0-9_.-]+)"'
        )
        kind_to_type = dict(self._URL_REF_KINDS)

        for fn, content in contents.items():
            orig = content

            def url_repl(m: re.Match) -> str:
                tf_type = kind_to_type.get(m.group(2))
                if not tf_type:
                    return m.group(0)
                label = url_maps.get(tf_type, {}).get((m.group(1), m.group(3)))
                if not label:
                    return m.group(0)
                return f"{tf_type}.{label}.self_link"

            content = url_re.sub(url_repl, content)

            def short_repl(m: re.Match) -> str:
                tf_type = ("google_compute_network" if m.group(2) == "networks"
                           else "google_compute_subnetwork")
                label = net_short_map.get(m.group(2), {}).get((m.group(1), m.group(3)))
                if not label:
                    # 別ルート（Shared VPC host）の参照は解決できないので URL のまま。
                    return m.group(0)
                return f"{tf_type}.{label}.self_link"

            content = short_path_re.sub(short_repl, content)

            def sa_repl(m: re.Match) -> str:
                label = sa_map.get((m.group(1), m.group(2)))
                if not label:
                    return m.group(0)
                return f"google_service_account.{label}.email"

            content = self._SA_EMAIL_REF_RE.sub(sa_repl, content)

            if 'resource "google_container_node_pool"' in content:
                def cluster_repl(m: re.Match) -> str:
                    label = cluster_map.get(m.group(2))
                    if not label:
                        return m.group(0)
                    return f"{m.group(1)}google_container_cluster.{label}.name"

                content = re.sub(
                    r'^(\s*cluster\s*=\s*)"([^"]+)"', cluster_repl, content,
                    flags=re.M,
                )

            def ch_repl(m: re.Match) -> str:
                label = channel_map.get(m.group(1))
                if not label:
                    return m.group(0)
                # .name = "projects/<dst>/notificationChannels/<新 ID>"（apply 後に確定）
                return f"google_monitoring_notification_channel.{label}.name"

            content = self._NOTIF_CHANNEL_REF_RE.sub(ch_repl, content)

            # 解決できなかった通知チャネル参照は旧 ID のままでは絶対に見つからない
            # （ID は server 採番で dst に同じ番号は存在しない）ため、行ごと落として
            # DIFF に「確認」で出す。チャネル無しでもアラート本体は作成できる。
            if re.search(r'^\s*notification_channels\s*=.*notificationChannels/',
                         content, re.M):
                content = re.sub(
                    r'^\s*notification_channels\s*=[^\n]*\n', '', content, flags=re.M)
                self.org_logger.warning(
                    f"      通知チャネル参照を除去（dst に存在しない旧 ID）: {fn}"
                )
                self._add_customize_note("alert_notification_channels", content, fn)

            if content == orig:
                continue
            try:
                with open(os.path.join(root, fn), 'w', encoding='utf-8') as f:
                    f.write(content)
                self.org_logger.info(
                    f"      リソース参照を terraform 参照に変換: {fn}"
                )
            except OSError as e:
                self.org_logger.warning(f"      参照の書き換え失敗 {fn}: {e}")

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
        # 注記は customize のたびに作り直す（.tf と同じライフサイクル）。
        self._customize_notes = []

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
            protected_host = self._skipped_host_src()
            for name in os.listdir(active_dir):
                d = os.path.join(active_dir, name)
                if os.path.isdir(d):
                    if name == protected_host:
                        # skip 指定 host は export 対象外で raw に無いが、
                        # 孤児扱いで消さず active/state を丸ごと温存する。
                        continue
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

        # Terraform ルート（active/<project>/ または active 直下）ごとに確定済みの
        # resource (type, label)。flatten での重複ラベル一意化に使う。
        seen_labels: Dict[str, Set[Tuple[str, str]]] = {}
        # 越境リソース判定用: dst として正当なプロジェクト ID の集合。
        dst_project_ids = set(proj_map.values())
        rt_include, rt_exclude = self._resource_type_filters()
        # src で使用中（IN_USE）の内部アドレス名（src プロジェクトごと）。
        # Step 5 が VM と同じプロジェクトに予約し直すため Terraform 複製から外す。
        gce_restore_on = step_enabled(self.config.get('steps', {}), 'gce_restore')
        cai_dir = (self.config.get('steps', {}).get('cai_scan', {}) or {}).get(
            'output_dir', './cai_export')
        in_use_addrs: Dict[str, Set[str]] = {}

        # dedupe_tf_resource_labels は走査順依存（先勝ちで元ラベルを維持）なので、
        # 実行ごとに改名対象が入れ替わらないよう walk を必ずソートする。
        for root, dirs, files in os.walk(raw_dir):
            dirs.sort()
            for file in sorted(files):
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

                    # 3.7. bulk-export 出力と現行 google provider の非互換を吸収
                    #    （廃止ブロック除去 / 必須化された引数の補完）。
                    content = self._fix_provider_compat(content, rel)

                    # 3.8. project_mapping 外プロジェクトのリソースを除外する。
                    #    bulk-export は monitoring workspace などを介して**別プロジェクト
                    #    のリソースまで越境出力する**ことがある（例: my-argolis の export に
                    #    shingo-ar-genai0718 の notification channel が混入）。ID 置換は
                    #    mapping に無いプロジェクトを変えないため、そのまま apply すると
                    #    **無関係な実プロジェクトへ書き込もうとする**。安全のため落とす。
                    #    プロジェクト番号（数値）は proj_num_map で dst に置換済みのため
                    #    ここでは判定対象外（dry_run では番号 map が無く誤検知するため）。
                    pm_ = re.search(r'^\s*project\s*=\s*"([^"]+)"', content, re.M)
                    if pm_ and not pm_.group(1).isdigit() and \
                            pm_.group(1) not in dst_project_ids:
                        self.org_logger.warning(
                            f"      スキップ（project_mapping 外プロジェクト "
                            f"'{pm_.group(1)}' への越境リソース）: {active_rel}"
                        )
                        self.stats.incr("skipped")
                        continue

                    # 4. Google 管理のデフォルト SA など、Terraform で作成不能な
                    #    リソースはスキップ（account_id が GCP 命名規則違反のもの）。
                    skip_reason = self._skip_reason_for_file(content)
                    if skip_reason:
                        # 手動対応が必要なスキップは DIFF.md に載せる（ログだけだと
                        # 埋もれる）。新しい「複製不能 → skip」を足すときも同様に注記する。
                        if 'resource "google_compute_ssl_certificate"' in content:
                            self._add_customize_note("ssl_certificate", content, rel)
                        elif 'resource "google_container_analysis_occurrence"' in content:
                            self._add_customize_note(
                                "container_analysis_occurrence", content, rel)
                        elif 'resource "google_dns_managed_zone"' in content:
                            self._add_customize_note("dns_managed_zone", content, rel)
                        elif ('resource "google_storage_bucket"' in content
                              and "ドット入り" in (skip_reason or "")):
                            self._add_customize_note("dotted_bucket", content, rel)
                        self.org_logger.info(f"      スキップ（{skip_reason}）: {active_rel}")
                        continue

                    # 3.85. src で VM が使用中（IN_USE）の内部アドレスは複製しない。
                    #    Step 5 が `mig-<vm>-<ip>` として VM と同じプロジェクトに予約
                    #    し直す（DIFF P1 と同じ設計）。Terraform 側でも作ると二重予約で、
                    #    Shared VPC では host 側予約が svc の VM 作成をブロックする。
                    if gce_restore_on and 'resource "google_compute_address"' in content:
                        src_p = parts[0] if len(parts) > 1 else ''
                        if src_p and src_p not in in_use_addrs:
                            in_use_addrs[src_p] = cai_in_use_internal_addresses(
                                os.path.join(cai_dir, f"cai_resources_{src_p}.txt"))
                        nm_ = re.search(r'^\s*name\s*=\s*"([^"]+)"', content, re.M)
                        if nm_ and nm_.group(1) in in_use_addrs.get(src_p, set()):
                            self.org_logger.info(
                                f"      スキップ（src で使用中の内部 IP 予約。"
                                f"Step 5 が VM 側で予約し直す）: {active_rel}"
                            )
                            self.stats.incr("skipped")
                            continue

                    # 3.9. 移行範囲の絞り込み（steps.bulk_export.resource_types）。
                    #    利用者が「Cloud Run や GKE は移さない」等を選べるようにする。
                    #    全リソース型が対象外のファイルだけ落とす（1 つでも対象の型が
                    #    残るなら安全側でコピーする）。GKE 移行手順 note を出す前に
                    #    判定する（除外したクラスタの手順を出さないため）。
                    rt_reason = resource_type_filter_reason(
                        tf_resource_types(content), rt_include, rt_exclude)
                    if rt_reason:
                        self.org_logger.info(
                            f"      スキップ（{rt_reason}）: {active_rel}")
                        self.stats.incr("skipped")
                        continue

                    # GKE クラスタは構成のみ複製する方針のため、ワークロード・PV の
                    # 移行（Backup for GKE の backup/restore）が別途必要になる。
                    # クラスタごとに DIFF.md へ手順を「要対応」で載せる（ルール:
                    # ツールが対象外とした手動移行は DIFF に必ず手順つきで出す）。
                    if 'resource "google_container_cluster"' in content:
                        self._add_customize_note("gke_backup_restore", content, rel)

                    # 4.5. flatten で同居する resource ラベルの重複を一意化。
                    #    bulk-export はラベルをリソース名だけから作るため、同名
                    #    リソースが複数 location にあると（例: Artifact Registry の
                    #    cloud-run-source-deploy が asia-northeast1 と us-central1）、
                    #    平坦化後に「Duplicate resource ... configuration」で
                    #    terraform init/plan が落ちる。suffix には location ディレクトリ
                    #    名を使う。skip 済みファイルのラベルは登録しない（ここが
                    #    _skip_reason_for_file より後にある理由）。
                    disc = os.path.basename(os.path.dirname(rel)) or os.path.splitext(file)[0]
                    proj_key = parts[0] if len(parts) > 1 else ''
                    content, dup_renames = dedupe_tf_resource_labels(
                        content, disc, seen_labels.setdefault(proj_key, set()),
                    )
                    for rtype, old_l, new_l in dup_renames:
                        self.org_logger.info(
                            f"      重複ラベルを一意化: {rtype}.{old_l} → {rtype}.{new_l} ({rel})"
                        )

                    # 5. シェル変数 ${VAR} / Terraform ディレクティブ %{...} が
                    #    起動スクリプト等の文字列に含まれると Terraform が補間として
                    #    誤解釈するためエスケープ（最後に適用）。
                    content = self._escape_interpolations(content)

                    with open(active_path, 'w', encoding='utf-8') as f:
                        f.write(content)
                except Exception as e:
                    self.org_logger.error(f"    HCL カスタマイズ失敗 {raw_path}: {e}")
                    sys.exit(1)

        # 同一ルート内の subnetwork URL 参照を terraform 参照へ書き換える。
        # **全ファイル書き出し後**に走らせること: ラベルは dedupe で改名されうるので、
        # 確定後の active を読まないと存在しないラベルを指してしまう。
        if not self.dry_run:
            self._rewrite_subnet_refs_in_active(active_dir)
            self._rewrite_resource_refs_in_active(active_dir)

        # 手動対応・確認注記を active/<src>/.customize_notes.json に永続化する。
        # skip_on_run で customize を飛ばす `make run` でも Step 99 が DIFF.md に
        # 載せられるように、メモリではなくファイルを正とする。今回 customize した
        # プロジェクト（raw にある dir）だけ更新し、注記が無ければ古いファイルを消す
        # （温存 host など customize していない dir の注記は残す）。
        if not self.dry_run and os.path.isdir(active_dir):
            notes_by_dir: Dict[str, List[Dict[str, str]]] = {}
            for note in self._customize_notes:
                notes_by_dir.setdefault(note.get("src_dir") or "", []).append(note)
            customized = set(os.listdir(raw_dir)) if os.path.isdir(raw_dir) else set()
            for name in sorted(os.listdir(active_dir)):
                proj_dir = os.path.join(active_dir, name)
                if not os.path.isdir(proj_dir) or name not in customized:
                    continue
                path = os.path.join(proj_dir, _CUSTOMIZE_NOTES_FILE)
                notes = notes_by_dir.get(name)
                try:
                    if notes:
                        with open(path, "w", encoding="utf-8") as f:
                            json.dump(notes, f, ensure_ascii=False, indent=1)
                    elif os.path.exists(path):
                        os.remove(path)
                except OSError as e:
                    self.org_logger.warning(f"  customize 注記の書き出し失敗 {path}: {e}")

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
                if name == self._skipped_host_src():
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

    def _fix_provider_compat(self, content: str, rel: str) -> str:
        """bulk-export 出力を現行 google provider のスキーマに合わせて補正する。

        - GKE クラスタの廃止済みブロック（`_GKE_REMOVED_TF_BLOCKS`）を除去。
          クラスタ .tf は複製の主目的なのでファイルごと skip しない。
        - `iap {}`（backend service）は provider v5+ で `enabled` が必須。
          bulk-export は oauth2_client_id しか出さないため補完する。
          **`enabled = true` に倒す**: block が export されている＝src で IAP が
          構成されていた。false に倒すと dst で認証壁が外れて公開されてしまう
          （「dst が src より緩くならない方向」の原則。厳しすぎた場合はアクセス
          不能になるだけで、後から dst 側で無効化すればよい）。
        - `advanced_datapath_observability_config` は `enable_relay` が必須化。
          API 既定値の false を補完する。
        """
        content, removed = strip_hcl_blocks(content, _GKE_REMOVED_TF_BLOCKS)
        for name in removed:
            self.org_logger.info(
                f"      provider 廃止ブロックを除去: {name} ({rel})"
            )
        # region 版 (google_compute_region_backend_service) も同じ iap ブロックを持つ。
        # 完全一致文字列だと region 版が素通りして plan が Missing required argument
        # で落ちる。
        if re.search(r'resource "google_compute(_region)?_backend_service"', content):
            content, n = ensure_hcl_block_arg(content, "iap", "enabled = true")
            if n:
                self.org_logger.info(
                    f"      iap.enabled=true を補完（IAP を無効化しない安全側）: {rel}"
                )
                self._add_customize_note("iap_enabled", content, rel)
        content, n = ensure_hcl_block_arg(
            content, "advanced_datapath_observability_config", "enable_relay = false"
        )
        if n:
            self.org_logger.info(
                f"      advanced_datapath_observability_config.enable_relay=false を補完: {rel}"
            )
        # GKE: 旧 routes-based 引数 cluster_ipv4_cidr と VPC-native の
        # ip_allocation_policy は provider 上排他（Conflicting configuration
        # arguments）。export は両方出すが、値は ip_allocation_policy.
        # cluster_ipv4_cidr_block と同一なので旧引数の行だけ落とす。
        if ('resource "google_container_cluster"' in content
                and 'ip_allocation_policy' in content
                and re.search(r'^\s*cluster_ipv4_cidr\s*=', content, re.M)):
            content = re.sub(r'^\s*cluster_ipv4_cidr\s*=[^\n]*\n', '', content, flags=re.M)
            self.org_logger.info(
                f"      cluster_ipv4_cidr を除去（ip_allocation_policy と排他）: {rel}"
            )
        # provider 既定の deletion_protection = true を dst 側で false に倒す。
        # export が明示していれば触らない（src の意図が入っている）。
        for tf_type in _DELETION_PROTECTION_DEFAULT_TRUE_TYPES:
            if f'resource "{tf_type}"' not in content:
                continue
            content, added = ensure_tf_resource_arg(
                content, tf_type, "deletion_protection = false")
            for label in added:
                self.org_logger.info(
                    f"      deletion_protection=false を補完（provider 既定 true だと"
                    f" 失敗時に replace できず詰む）: {tf_type}.{label} ({rel})"
                )
                self._add_customize_note("deletion_protection", content, rel)

        # GKE: Backup for GKE のエージェントを dst 側で有効化する。
        # 本ツールはクラスタ構成しか複製せず、ワークロード / PV は Backup for GKE の
        # restore で戻す前提（DIFF に手順を出す）。エージェントは **復元先クラスタにも
        # 必須**（addonsConfig.gkeBackupAgentConfig.enabled: true）だが、src で無効なら
        # export も false になり、そのままだと restore できないクラスタが出来上がる。
        # 「dst が緩くなる」変更ではなく（バックアップ機能の追加）、移行のゴールに
        # 必要なので true に倒す。src 側の有効化は read-only のため利用者の手動作業。
        if 'resource "google_container_cluster"' in content:
            content, n_bk = ensure_hcl_block_arg(
                content, "gke_backup_agent_config", "enabled = true")
            if n_bk:
                self.org_logger.info(
                    f"      gke_backup_agent_config.enabled=true を補完"
                    f"（Backup for GKE の restore に必須）: {rel}"
                )
            elif re.search(r'gke_backup_agent_config\s*\{\s*\n\s*enabled\s*=\s*false',
                           content):
                content = re.sub(
                    r'(gke_backup_agent_config\s*\{\s*\n\s*enabled\s*=\s*)false',
                    r'\1true', content)
                self.org_logger.info(
                    f"      gke_backup_agent_config.enabled を false→true に変更"
                    f"（Backup for GKE の restore に必須）: {rel}"
                )
            if 'gke_backup_agent_config' not in content:
                # addons_config ごと無いクラスタ（最小構成）にも足す。
                content, n_add = ensure_tf_resource_arg(
                    content, "google_container_cluster",
                    "addons_config {\n    gke_backup_agent_config {\n"
                    "      enabled = true\n    }\n  }")
                if n_add:
                    self.org_logger.info(
                        f"      addons_config.gke_backup_agent_config を追加"
                        f"（Backup for GKE の restore に必須）: {rel}"
                    )

        # GKE: 別リソースの google_container_node_pool で運用するクラスタには
        # `initial_node_count` と `remove_default_node_pool` が必要。GKE API は
        # 「ノードプール 0 個のクラスタ」を作れないため、export のままだと
        # initial_node_count=0 で送られ
        # `Cluster.initial_node_count must be greater than zero` の 400 になる
        # （regression: my-argolis の 2 クラスタ）。provider ドキュメントの定石
        # どおり「最小の既定プールを作って即削除」する形に補う（既定プールを
        # 残すと、別リソース側の同名 default-pool 作成が 409 になる）。
        # 対象外: inline `node_pool {}` を持つクラスタ（既定プールが要る）と
        # Autopilot（ノード管理は GKE 側。remove_default_node_pool は
        # enable_autopilot と ConflictsWith）。
        if ('resource "google_container_cluster"' in content
                and not re.search(r'^\s*node_pool\s*\{', content, re.M)
                and not re.search(r'^\s*enable_autopilot\s*=\s*true', content, re.M)):
            content, added_c = ensure_tf_resource_arg(
                content, "google_container_cluster", "initial_node_count = 1")
            content, added_r = ensure_tf_resource_arg(
                content, "google_container_cluster",
                "remove_default_node_pool = true")
            for label in sorted(set(added_c) | set(added_r)):
                self.org_logger.info(
                    f"      initial_node_count=1 / remove_default_node_pool=true を"
                    f"補完（別リソースの node_pool で運用するため）: "
                    f"google_container_cluster.{label} ({rel})"
                )

        # GKE: node pool は create 時に `initial_node_count` と `node_count` を
        # **両方指定できない**（`Cannot set both initial_node_count and node_count
        # on node pool ...`）。export は両方出す。org のノード数を引き継ぐのは
        # `node_count`（現在の管理台数）なので、そちらを残して initial 側を落とす。
        # クラスタ本体の initial_node_count（remove_default_node_pool 用）は対象外。
        if ('resource "google_container_node_pool"' in content
                and 'resource "google_container_cluster"' not in content
                and re.search(r'^\s*node_count\s*=', content, re.M)
                and re.search(r'^\s*initial_node_count\s*=', content, re.M)):
            content = re.sub(r'^\s*initial_node_count\s*=[^\n]*\n', '', content,
                             flags=re.M)
            self.org_logger.info(
                f"      node pool の initial_node_count を除去"
                f"（node_count と排他。台数は node_count が引き継ぐ）: {rel}"
            )

        # GKE: node pool の network_config は `pod_range`（既存 secondary range を
        # 名前で参照）と `pod_ipv4_cidr_block`（新規レンジを作るときの CIDR）が
        # 対になっている。provider の定義上 **pod_ipv4_cidr_block は
        # create_pod_range = true のときだけ有効**で、export は両方出すため、
        # そのままだと「既存レンジ参照」なのに CIDR も送られて曖昧になる。
        # GKE が subnet に作った range は subnet の .tf ごと dst に複製されるので、
        # 名前参照を残して CIDR を落とす（クラスタ側 ip_allocation_policy と同じ判断）。
        if ('resource "google_container_node_pool"' in content
                and re.search(r'^\s*pod_range\s*=\s*"', content, re.M)
                and not re.search(r'^\s*create_pod_range\s*=\s*true', content, re.M)
                and re.search(r'^\s*pod_ipv4_cidr_block\s*=', content, re.M)):
            content = re.sub(r'^\s*pod_ipv4_cidr_block\s*=[^\n]*\n', '', content,
                             flags=re.M)
            self.org_logger.info(
                f"      node pool の pod_ipv4_cidr_block を除去"
                f"（pod_range で既存レンジを参照するため）: {rel}"
            )

        # GKE: ノードプールの version 固定は master 版と食い違うと
        # 「Node version must be <= master version」で落ちる。クラスタ側の
        # node_version を除去して release_channel 追従にしたのと揃え、
        # ノードプールも master 版に追従させる（未指定 = master と同版）。
        if 'resource "google_container_node_pool"' in content and \
                re.search(r'^\s*version\s*=\s*"', content, re.M):
            content = re.sub(r'^\s*version\s*=\s*"[^"]*"[^\n]*\n', '', content,
                             flags=re.M)
            self.org_logger.info(
                f"      node pool の version を除去（master 版に追従させる）: {rel}"
            )

        # GKE: node_version は create 時に min_master_version と同値でなければ
        # ならない（provider の検証）。export は現在のノード版だけを出すことが
        # あり（min_master_version 無し）、そのままだと
        # 「node_version and min_master_version must be set to equivalent values
        # on create」で apply 前に落ちる。版は release_channel（export 済み）に
        # 追従させるのが最も堅いので、同値でない node_version は除去する
        # （min_master_version 側は残す）。
        if 'resource "google_container_cluster"' in content:
            nv = re.search(r'^\s*node_version\s*=\s*"([^"]+)"', content, re.M)
            mm = re.search(r'^\s*min_master_version\s*=\s*"([^"]+)"', content, re.M)
            if nv and (not mm or mm.group(1) != nv.group(1)):
                content = re.sub(r'^\s*node_version\s*=[^\n]*\n', '', content,
                                 flags=re.M)
                self.org_logger.info(
                    f"      node_version を除去（min_master_version と同値必須。"
                    f"dst は release channel に追従）: {rel}"
                )

        # GKE: ip_allocation_policy は「subnet の secondary range を名前で参照する
        # モード」と「CIDR を指定して自動作成させるモード」が排他で、
        # *_secondary_range_name は cluster/services **両方の** *_ipv4_cidr_block と
        # conflicts になる。export は両方出すが:
        #   - GKE が subnet に作った range（gke-*-pods-* 等）は subnet の .tf ごと
        #     dst に複製されるので、**range 名参照を残す**のが正
        #     （CIDR 側を残すと同じ CIDR で range を二重作成しようとして衝突）
        #   - services_ipv4_cidr_block が Google 管理の自動 range（34.118.x）の
        #     場合も、落とせば dst で同様に自動割当されるので機能等価
        if ('resource "google_container_cluster"' in content
                and re.search(r'^\s*\w+_secondary_range_name\s*=', content, re.M)):
            for cidr in ("cluster_ipv4_cidr_block", "services_ipv4_cidr_block"):
                if re.search(rf'^\s*{cidr}\s*=', content, re.M):
                    content = re.sub(rf'^\s*{cidr}\s*=[^\n]*\n', '', content, flags=re.M)
                    self.org_logger.info(
                        f"      {cidr} を除去（secondary_range_name 参照と排他）: {rel}"
                    )
        return content

    def _add_customize_note(self, kind: str, content: str, rel: str):
        """customize の補正/スキップ注記を積む（DIFF.md 掲載用。ルールはモジュール
        コメント `_CUSTOMIZE_NOTES_FILE` 参照）。resource 名と project は補正後の
        content から拾う（プロジェクト ID 置換済みなので dst 側の値になる）。"""
        nm = re.search(r'\bname\s*=\s*"([^"]+)"', content)
        pm = re.search(r'\bproject\s*=\s*"([^"]+)"', content)
        note = {
            "kind": kind,
            "src_dir": rel.split(os.sep)[0] if os.sep in rel else "",
            "resource": nm.group(1) if nm else "?",
            "project": pm.group(1) if pm else "?",
        }
        # 同一内容は 1 行にまとめる（name を持たないリソースは resource="?" に
        # 潰れるため、そのまま積むと同じ行が件数分並んで DIFF が読めなくなる）。
        if note not in self._customize_notes:
            self._customize_notes.append(note)

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

        # public DNS ゾーンはドメインがグローバル一意で、同一ドメインのゾーンは
        # 「reserved or registered already / prohibited by policy」の 400 で
        # 別プロジェクトに作れないことがある（regression: kawanos.demo.altostrat.com）。
        # 作れたとしても NS 委任は src ゾーンを向いたままで dst ゾーンは機能しない。
        # ドメイン委任の切替は利用者の判断が要るため skip + DIFF 要対応。
        # private ゾーン（VPC スコープ）は複製可能なので対象外。
        if 'resource "google_dns_managed_zone"' in content and \
                not re.search(r'^\s*visibility\s*=\s*"private"', content, re.M):
            return "public DNS ゾーン（ドメインはグローバル一意。委任切替は手動）"

        # ドット入り（ドメイン形式）バケットはドメイン検証済み TLD 配下でないと
        # 作成できない（400: contains a '.' but is not under a recognized TLD）。
        # 特に *.appspot.com（GCR レイヤー / GAE staging）は Google 管理の
        # システムバケットで別プロジェクトへの複製自体が不可能。
        # data_sync (_sync_gcs) の同種 skip と同じ扱い（rename_rules.gcs.overrides で
        # ドット無しの dst 名を指定すれば data_sync が作成 + 同期する）。
        if 'resource "google_storage_bucket"' in content:
            m = re.search(r'^\s*name\s*=\s*"([^"]+)"', content, re.M)
            if m and '.' in m.group(1):
                return (f"ドット入り（ドメイン形式）バケット {m.group(1)} は"
                        f"dst に作成不可")

        # self-managed SSL 証明書は秘密鍵を API からエクスポートできず、
        # provider スキーマ上 private_key 必須のため apply 不能。dst で鍵を持つ
        # 利用者が手動作成するしかない（DIFF に要対応として出る）。
        # Google-managed 証明書 (google_compute_managed_ssl_certificate) は別型で対象外。
        if 'resource "google_compute_ssl_certificate"' in content:
            return "self-managed SSL 証明書（秘密鍵は export 不能。dst で手動作成が必要）"

        # Container Analysis の occurrence は「過去ビルドの来歴・署名」レコードで、
        # インフラ構成ではない。参照する note（`built-by-cloud-build` 等）は
        # Cloud Build が自プロジェクトに作るため dst には存在せず、apply が
        # `note with ID "..." for project "<dst>" does not exist` の 404 で失敗する
        # （regression: my-argolis）。署名鍵も Google 管理プロジェクトを指しており
        # 複製不能。dst で再ビルドすれば同種の occurrence が自動生成される。
        if 'resource "google_container_analysis_occurrence"' in content:
            return "Container Analysis occurrence（参照先 note は dst に存在せず作成不能）"

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

        # GKE / k8s コントローラが自動生成する派生リソース（instance template / MIG /
        # autoscaler / gke-*, k8s-* FW ルール等）。dst にクラスタを作れば GKE が同等物を
        # 作り直すため複製不要。src の名前にはクラスタ固有ハッシュが入っており、
        # dst に持ち込むと衝突 / 無効参照になる。
        # google_container_cluster / node_pool は対象外（＝ GKE 構成そのものは複製する）。
        for rtype in _GKE_MANAGED_TF_RESOURCE_TYPES:
            if f'resource "{rtype}"' not in content:
                continue
            m = re.search(r'\bname\s*=\s*"([^"]+)"', content)
            if m and is_gke_managed_name(m.group(1)):
                return f"GKE 自動生成リソース（dst クラスタが再生成）: {m.group(1)}"

        # k8s Service (type=LoadBalancer) が作る LB リソースは名前が hex UID
        # （a<31hex>）で接頭辞判定に掛からないが、description に kubernetes.io の
        # 所有者マーカーを必ず持つ。落とさないと、参照先の k8s-* health check だけが
        # 上の接頭辞判定で除外され、target pool / forwarding rule が宙ぶらりんの
        # 参照を抱えて apply が 404 になる（regression: my-argolis の
        # a0cb2a...）。forwarding rule は名前接頭辞では判定しない
        # （利用者の LB を誤って落とさない。マーカーがある場合のみ除外）。
        if has_k8s_owner_marker(content):
            for rtype in _GKE_MANAGED_TF_RESOURCE_TYPES + (
                    "google_compute_forwarding_rule",):
                if f'resource "{rtype}"' in content:
                    m = re.search(r'\bname\s*=\s*"([^"]+)"', content)
                    return (
                        f"k8s(GKE) 管理リソース（kubernetes.io マーカー。"
                        f"dst クラスタが再生成）: {m.group(1) if m else rtype}"
                    )

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
        """外部アドレスの固定 IP 指定だけを外し自動採番にする。

        - **外部** IP（EXTERNAL / 指定なし）は Google 採番のグローバル資源で、
          src と同じ値を dst で確保できないため `address = "<ip>"` を外す。
        - **内部**（`address_type = "INTERNAL"`。subnet 予約 / PSA レンジ）は
          **元 IP を必ず残す**。subnet は同じ CIDR で dst に複製されるので元 IP は
          有効であり、剥がすと自動採番がサブネット最若 IP（.2 など）を掴んで
          **Step 5 が復元する VM の IP を横取りする**（regression: 複製した
          svc1-fix1 が 10.100.1.2 を自動採番で確保し、iam-vm の復元が
          「IP already used / reserved by another project」で失敗した）。
          そもそも予約の意味は「その IP を押さえること」なので、値を変えた複製は
          取り置きとして機能しない。
        """
        if ('resource "google_compute_address"' not in content
                and 'resource "google_compute_global_address"' not in content):
            return content
        if re.search(r'^\s*address_type\s*=\s*"INTERNAL"', content, re.M):
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
        output_dir_base = self._tf_base_dir()
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
        # mock 生成の .tf を実 apply すると dst に実在しないリソースが作られる。
        # Step 3 でも弾いているが、export をスキップする経路（skip_on_run など）が
        # あるため apply 直前にもう一度確認する（最終防衛線）。
        if not self.mock and tf_dir_has_mock_artifacts(proj_dir):
            msg = (
                f"mock 生成の .tf が残っているため apply しません: {proj_dir}"
                f"（`rm -rf {proj_dir}` して make plan からやり直してください）"
            )
            self.dst_logger.error(f"    ✗ {msg}")
            self.stats.incr("failed")
            self.stats.add_failure(f"TF Apply {os.path.basename(proj_dir)}", msg)
            return
        # dst プロジェクトが前回と変わった（= 別環境への移行）場合、前回の
        # terraform.tfstate は旧プロジェクトのリソースを指したままで、import が
        # 「既に state にある」と誤判定し、plan で新プロジェクトへ再作成 → 既存と
        # 衝突（409）する。dst が変わっていれば state を破棄して import からやり直す。
        dst_proj = proj_map.get(os.path.basename(proj_dir))
        if not self.dry_run and not self.mock and dst_proj:
            self._reset_stale_state_if_needed(proj_dir, dst_proj)
            # 基盤 API（CRM / Service Usage / IAM）に加え、**これから apply する
            # .tf が使う API** も init 前に有効化する。dst で無効だと apply 中に
            # 「<api> has not been used in project ... before」の 403 で止まる
            # （GKE = container.googleapis.com が典型）。Step 1.5 の src 由来の
            # 一覧が取りこぼしても、ここで .tf から引き直して埋める（冪等）。
            self._ensure_dst_prereq_apis(
                dst_proj, sa_map.get(os.path.basename(proj_dir)), proj_dir=proj_dir,
            )
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

    def _ensure_dst_prereq_apis(
        self, dst_proj: str, dst_sa: Optional[str], proj_dir: Optional[str] = None,
    ):
        """terraform 実行前に dst プロジェクトの必要 API を有効化する（冪等 / soft fail）。

        2 系統を足して差分だけ有効化する:
          1. 基盤 API（CRM / ServiceUsage / IAM）… google_project_service や
             data.google_project が依存する。
          2. **これから apply する .tf が使う API**（`tf_required_apis`）…
             Step 1.5 は「src で有効な API」を dst に写すが、src 側の一覧取得に
             失敗した場合や、export された .tf の親 API が src で無効だった場合を
             取りこぼす。実際に apply するファイルから引き直すのが最も確実
             （GKE = container.googleapis.com が典型）。

        有効化直後は反映遅延で terraform が
        「<API> has not been used in project ... before or it is disabled」の 403 を
        返すため、`services list --enabled` で見えるまでポーリングして待つ。
        失敗は WARNING のみで `stats.failed` に積まない（本当に必要な API なら
        terraform 側が本来のエラーで止めるので二重報告しない）。
        """
        steps = self.config.get('steps', {})
        cfg = steps.get('enable_apis', {})
        if not isinstance(cfg, dict):
            cfg = {}
        skip = set(_DST_API_SKIP) | {
            str(s or "").strip() for s in (cfg.get('skip_apis') or [])
            if str(s or "").strip()
        }
        # `.tf` 由来の API は enable_apis ステップの拡張機能なので off-switch に従う。
        # ただし基盤 API（CRM / ServiceUsage / IAM）は enable_apis 導入前から Step 4 が
        # 無条件で有効化していた必須品なので、enabled: false でも skip_apis でも外さない
        # （外せると terraform が一切動かない dst を「設定どおり」に作ってしまう）。
        want: Set[str] = set()
        if step_enabled(steps, 'enable_apis') and proj_dir:
            want |= set(tf_required_apis(proj_dir))
        want -= skip
        want |= set(_BASE_DST_APIS)

        have = self._list_enabled_services(dst_proj, "dst", dst_sa)
        # 一覧が取れないときは全部 enable する（enable 自体は冪等）。
        missing = sorted(want) if have is None else [a for a in sorted(want) if a not in have]
        if not missing:
            self.dst_logger.info(f"    必要 API は全て有効化済み（{dst_proj}）")
            return
        self.dst_logger.info(
            f"    apply 前に有効化 ({dst_proj}) {len(missing)} 件: {', '.join(missing)}"
        )
        failed = self._enable_apis_on_dst(dst_proj, dst_sa, missing)
        enabled_now = [a for a in missing if a not in failed]
        # wait_seconds は enabled: false のとき validate_steps_config を通らない
        # （検査は有効ステップのみ）ので、ここでは型不正でも落ちない形で読む。
        # 0 は「待たない」（テンプレート記載どおり。0 で _wait_for_apis_enabled を
        # 呼ぶと必ず「timeout 内に確認できませんでした」の偽警告になる）。
        wait_sec = coerce_nonneg_int(cfg.get('wait_seconds', 120), 120)
        if enabled_now and wait_sec > 0:
            self._wait_for_apis_enabled(
                dst_proj, dst_sa, enabled_now,
                timeout_sec=wait_sec, interval_sec=8,
            )
        if failed:
            self.dst_logger.warning(
                f"    ⚠ {dst_proj} で有効化できなかった API {len(failed)} 件: "
                f"{', '.join(failed)}"
            )
            self.dst_logger.warning(
                f"      apply が 403 になる場合は手動で: "
                f"gcloud services enable {' '.join(failed)} --project={dst_proj}"
            )

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
                    f"gcloud services list --enabled --project={dst_proj} --quiet "
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
            rc, out, err = self._sa_preflight_run(
                f"terraform -chdir={proj_dir} import -input=false -lock=false "
                f"'{addr}' '{imp_id}'"
            )
            if rc == 0:
                imported += 1
                continue
            # stderr / stdout どちらに出るかは provider 依存なので両方を見る。
            kind = import_error_kind(f"{err or ''}\n{out or ''}")
            if kind == "already":
                # state に取り込み済みなだけ。無視。
                skipped_already += 1
                continue
            if kind == "missing":
                # リモートに実体が無いだけ（apply が作成する）。無視。
                continue
            # 理由は先頭の意味のある行を出す（terraform のエラー枠は末尾が
            # 空行で終わるため、末尾行だと空文字になり原因が読めない）。
            failed.append((addr, imp_id, _first_meaningful_line(err, out)))
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
        skipped_host = self._skipped_host_src()
        for name in sorted(os.listdir(active_dir)):
            d = os.path.join(active_dir, name)
            if not os.path.isdir(d):
                continue
            if name == skipped_host:
                self.dst_logger.info(
                    f"  host_project.skip=true のため Terraform 適用から除外: {d}"
                )
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
        standalones = [e for e in self._standalone_entries()
                       if e.get('src') and e.get('dst')]

        # --- host (Shared VPC) 分 ---
        if not src_host or not dst_host:
            if not standalones:
                self.dst_logger.warning("  host_project が未設定のため network_firewall をスキップ")
                return
            self.dst_logger.info(
                "  host_project が未設定（standalone のみ構成）のため host FW 同期をスキップ"
            )
        elif self._host_skipped():
            self.dst_logger.info(
                "  host_project.skip=true のため host FW 同期をスキップ（dst host の FW は既存構成を利用）"
            )
        else:
            # dst host の VPC topology を Step 4.5 で先に用意する (bug fix)。
            # Step 5 (gce_restore) でも同じ呼び出しがあるが冪等なので問題ない。
            self._replicate_host_networks()
            self.dst_logger.info(
                f"  [Network] dst host {dst_host} VPC topology ready — FW 同期へ進む"
            )
            self._sync_classic_firewall_rules(src_host, dst_host, src_sa, dst_sa)
            self._sync_network_firewall_policies(src_host, dst_host, src_sa, dst_sa)

        # --- standalone (共有 VPC 非所属) 分 ---
        # standalone プロジェクトの FW は自プロジェクトの VPC に属するため、
        # host と同じ同期処理を src→dst の同一プロジェクトペアで実行する。
        for ent in standalones:
            s_src, s_dst = ent['src'], ent['dst']
            s_src_sa = ent.get('src_impersonate_service_account')
            s_dst_sa = ent.get('dst_impersonate_service_account')
            self.dst_logger.info(f"  [Standalone FW] {s_src} → {s_dst}")
            self._replicate_project_networks(s_src, s_dst, s_src_sa, s_dst_sa)
            self._sync_classic_firewall_rules(s_src, s_dst, s_src_sa, s_dst_sa)
            self._sync_network_firewall_policies(s_src, s_dst, s_src_sa, s_dst_sa)

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

        # GKE / k8s が自動生成した FW ルールは複製しない。dst にクラスタを作れば
        # GKE が同等ルールを作り直す。src のルールはクラスタ固有ハッシュを含む
        # target tag を参照しており、dst に持ち込んでも効かない（＝ FW を緩めない）。
        # pre-flight より前に落として、gke 専用 network の存在確認も省く。
        # 判定は名前接頭辞ではなく is_gke_managed_fw_rule（description の
        # kubernetes.io マーカー + GKE 本体ルールの構造判定）。接頭辞だけだと
        # `k8s-nodeport-allow` のような利用者ルール（DENY かもしれない）まで
        # 落とし、dst が src より緩くなる。
        gke_rules = [r for r in rules if is_gke_managed_fw_rule(r)]
        if gke_rules:
            names = ", ".join(r.get('name', '?') for r in gke_rules)
            self.dst_logger.warning(
                f"  ⚠ [FW Rules] GKE/k8s 自動生成ルール {len(gke_rules)} 件をスキップ"
                f"（dst クラスタ作成時に GKE が再生成）: {names}"
            )
            for _ in gke_rules:
                self.stats.incr("skipped")
            rules = [r for r in rules if not is_gke_managed_fw_rule(r)]

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
        # 呼び出しは残しておくこと。standalone プロジェクトの自前 VPC も同様。
        self._replicate_host_networks()
        self._replicate_standalone_networks()

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

            # GKE ノードは dst クラスタ (Step 4 terraform) が自分で作り直すため復元しない。
            # ここで 1 回落とせば、下の work unit 展開と電源状態の最終調整の両方に効く。
            gke_nodes = [v for v in vms if is_gke_node_vm(v)]
            if gke_nodes:
                names = ", ".join(v.get('name', '?') for v in gke_nodes)
                self.dst_logger.info(
                    f"    {src_proj}: GKE ノード VM {len(gke_nodes)} 台を復元対象から除外"
                    f"（クラスタ構成のみ terraform で複製）: {names}"
                )
                for _ in gke_nodes:
                    self.stats.incr("skipped")
                vms = [v for v in vms if not is_gke_node_vm(v)]
            if not vms:
                self.dst_logger.info(f"    {src_proj}: 復元対象の VM が無い")
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
                extra = self._build_vm_create_extra_args(vm, tmpdir, proj_map, dst_sa)
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

    def _resolve_dst_vm_service_account(
        self, sa_email: str, proj_map: Dict[str, str], dst_sa: Optional[str],
        label: str = "VM SA",
    ) -> Optional[str]:
        """src VM の user-managed SA email を dst 側 SA email に解決する。

        SA は project スコープのリソースで、src email のまま dst VM にアタッチすると
        cross-project SA attach となり org policy
        (constraints/iam.disableCrossProjectServiceAccountUsage 既定 enforced) と
        actAs 権限の両方で拒否される（"does not have access to service account"）。

        - SA のプロジェクトが proj_map にあれば `<id>@<dst_proj>.iam.gserviceaccount.com`
          に置換。dst に未存在なら **空の SA を冪等作成**する（IAM ロールは複製しない。
          必要な権限は WARNING で手動付与を案内）。
        - proj_map 外プロジェクトの SA / 解釈できない email は None を返し、呼び出し元は
          --service-account を付けない（dst の compute 既定 SA で起動）+ WARNING。
          FW の未登録 secure tag と同じ「安全側に倒して WARNING」パターン。
        - 結果は SA 単位でキャッシュし、並列 restore worker からの二重作成を防ぐ。
        """
        with self._vm_sa_lock:
            if sa_email in self._vm_sa_resolved:
                return self._vm_sa_resolved[sa_email]

            result: Optional[str] = None
            m = re.match(r'^([^@]+)@([^.]+)\.iam\.gserviceaccount\.com$', sa_email)
            if not m:
                self.dst_logger.warning(
                    f"  [{label}] '{sa_email}' を解釈できないため複製せず、"
                    f"dst の既定 SA で起動します"
                )
            elif m.group(2) not in proj_map:
                self.dst_logger.warning(
                    f"  [{label}] '{sa_email}' のプロジェクト '{m.group(2)}' は "
                    f"project_mapping に無いため複製せず、dst の既定 SA で起動します"
                    f"（必要なら project_mapping への追加 or 手動で SA 作成 + 指定）"
                )
            else:
                account_id = m.group(1)
                dst_proj_for_sa = proj_map[m.group(2)]
                dst_email = f"{account_id}@{dst_proj_for_sa}.iam.gserviceaccount.com"
                if self._gcloud_exists(
                    f"gcloud iam service-accounts describe {dst_email} "
                    f"--project={dst_proj_for_sa} --format='value(email)'",
                    dst_sa,
                ):
                    self.dst_logger.info(f"  [{label}] {dst_email} は dst に既存。再利用")
                    result = dst_email
                else:
                    out = self.run_command(
                        f"gcloud iam service-accounts create {account_id} "
                        f"--project={dst_proj_for_sa} "
                        f"--display-name={shlex.quote(account_id)} --quiet",
                        side="dst", logger=self.dst_logger,
                        desc=f"Create SA {account_id}",
                        explanation=f"dst {dst_proj_for_sa} に SA {dst_email} を作成",
                        impersonate_sa=dst_sa, allow_fail=True,
                    )
                    if out is None and not (self.dry_run or self.mock):
                        # 作成失敗（権限不足等）。既定 SA で起動にフォールバック
                        self.dst_logger.warning(
                            f"  [{label}] {dst_email} を作成できませんでした。"
                            f"dst の既定 SA で起動します"
                        )
                    else:
                        # 新規 SA は伝播に数秒かかることがある。実行モードでは
                        # instances create が NOT_FOUND にならないよう可視化を待つ。
                        if not (self.dry_run or self.mock):
                            for _ in range(6):
                                if self._gcloud_exists(
                                    f"gcloud iam service-accounts describe {dst_email} "
                                    f"--project={dst_proj_for_sa} --format='value(email)'",
                                    dst_sa,
                                ):
                                    break
                                time.sleep(5)
                        # ロール複製の担当は Step 5.7 (step_iam_sync)。無効なら
                        # 空 SA のままになるので手動付与を案内する。
                        if step_enabled(self.config.get('steps', {}), 'iam_sync'):
                            self.dst_logger.info(
                                f"  [{label}] {dst_email} を新規作成しました。"
                                f"IAM ロールは Step 5.7 で src SA '{sa_email}' から複製します"
                            )
                        else:
                            self.dst_logger.warning(
                                f"  [{label}] {dst_email} を新規作成しました。"
                                f"steps.iam_sync.enabled=false のため src SA '{sa_email}' の "
                                f"IAM ロールは複製していません。必要な権限は dst で手動付与してください"
                            )
                        result = dst_email

            self._vm_sa_resolved[sa_email] = result
            return result

    def _build_vm_create_extra_args(
        self, vm: Dict, tmpdir: str,
        proj_map: Dict[str, str], dst_sa: Optional[str],
    ) -> str:
        """src VM の追加属性（metadata/tags/labels/SA/scheduling 等）を
        gcloud compute instances create の引数文字列に変換する。

        - metadata は値に , や = や改行を含むため `--metadata-from-file key=path` で渡す
        - compute 既定 SA（プロジェクト番号始まり）は dst で別 ID になるため SA 指定しない
        - user-managed SA は _resolve_dst_vm_service_account で dst 側 email に解決する
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
            # user-managed SA は project スコープ。src email のままでは
            # cross-project attach となり org policy で拒否されるため dst へ解決する。
            dst_sa_email = self._resolve_dst_vm_service_account(sa_email, proj_map, dst_sa)
            if dst_sa_email:
                args.append(f"--service-account={shlex.quote(dst_sa_email)}")
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

    # ==========================================================
    # Step 5.7: IAM ロール複製 (src SA → dst SA)
    # ==========================================================
    def _iam_excluded_sa_emails(self) -> set:
        """複製対象から外す SA（移行オーケストレータ自身が使う借用 SA）。

        bootstrap_src_sa.sh / bootstrap_dst_sa.sh が作る移行専用 SA は
        移行対象のワークロードではないため、dst に複製しない。
        """
        out = set()
        for _src, _dst, src_sa, dst_sa in self._iter_project_pairs(include_skipped=True):
            for sa in (src_sa, dst_sa):
                if sa:
                    out.add(str(sa).strip().lower())
        return out

    def _fetch_src_iam_policies(self) -> Dict[str, Dict[str, Any]]:
        """src 各プロジェクトの IAM ポリシーを並列取得する（read-only）。"""
        policies: Dict[str, Dict[str, Any]] = {}
        lock = threading.Lock()
        targets = list(self._iter_src_projects())

        def worker(item):
            src_proj, src_sa = item
            raw = self.run_command(
                f"gcloud projects get-iam-policy {src_proj} --format=json",
                side="src", logger=self.org_logger,
                desc=f"IAM policy {src_proj}",
                explanation=f"src {src_proj} の IAM ポリシーを取得（read-only）",
                impersonate_sa=src_sa, allow_fail=True,
            )
            obj = _parse_gcloud_describe_json(raw)
            if not obj:
                self.org_logger.warning(
                    f"  [IAM] {src_proj}: IAM ポリシーを取得できませんでした。"
                    f"このプロジェクトのロール複製はスキップされます"
                )
            with lock:
                policies[src_proj] = obj

        self._parallel_for_each(targets, worker, "iam-scan")
        # DIFF.md の分類（カスタムロールが実際に付与されているか）で再利用する。
        self._src_iam_policies = policies
        return policies

    def _gcloud_json(self, cmd: str, impersonate_sa: Optional[str]) -> Optional[Any]:
        """read-only な JSON 取得（stats を汚さない）。失敗 / mock / dry-run は None。

        _gcloud_exists と同じ理由で subprocess を直接叩く（存在確認や差分取得の
        失敗を run 全体の失敗として数えたくない）。
        """
        if self.mock or self.dry_run:
            return None
        env = os.environ.copy()
        if impersonate_sa:
            env['CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT'] = impersonate_sa
        try:
            res = subprocess.run(
                cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, env=env, timeout=120,
            )
            if res.returncode != 0 or not res.stdout.strip():
                return None
            return json.loads(res.stdout)
        except Exception:
            return None

    def _access_token_for(self, impersonate_sa: Optional[str]) -> Optional[str]:
        """権限確認用のアクセストークンを取得する（read-only / stats を汚さない）。"""
        if not impersonate_sa:
            return self._local_access_token()
        rc, out, _err = self._sa_preflight_run(
            f"gcloud auth print-access-token --impersonate-service-account={impersonate_sa}"
        )
        return out.strip() if rc == 0 and out.strip() else None

    def _dst_can_set_iam_policy(
        self, dst_proj: str, dst_sa: Optional[str],
    ) -> Optional[bool]:
        """dst プロジェクトへ setIamPolicy できるかを事前確認する。

        True/False の他に **None（判定不能）** を返す。mock / dry-run、トークン取得
        失敗、testIamPermissions 不通はいずれも None で、呼び出し側は「あるものとして
        続行」する（判定できないことを理由に移行を止めない）。
        """
        if self.mock or self.dry_run:
            return None
        token = self._access_token_for(dst_sa)
        if not token:
            return None
        perm = "resourcemanager.projects.setIamPolicy"
        granted = self._test_iam_permissions(token, dst_proj, {perm})
        if granted is None:
            return None
        return perm in granted

    def _dst_existing_bindings(
        self, dst_proj: str, dst_sa: Optional[str],
    ) -> Optional[set]:
        """dst の現行ポリシーから (member, role) の集合を返す。取得不能なら None。"""
        obj = self._gcloud_json(
            f"gcloud projects get-iam-policy {dst_proj} --format=json", dst_sa)
        if not isinstance(obj, dict):
            return None
        out = set()
        for b in (obj.get('bindings') or []):
            if not isinstance(b, dict) or b.get('condition'):
                continue
            role = b.get('role') or ''
            for m in (b.get('members') or []):
                out.add((m, role))
        return out

    @staticmethod
    def _iam_grant_command(g: Dict[str, Any]) -> str:
        """付与コマンド。手動案内にもそのまま流用できる形にしておく。"""
        return (
            f"gcloud projects add-iam-policy-binding {g['dst_project']} "
            f"--member={shlex.quote(g['dst_member'])} "
            f"--role={shlex.quote(g['dst_role'])} "
            f"--condition=None --quiet --format=none"
        )

    def step_iam_sync(self):
        """Step 5.7: src の SA に付いている IAM ロールを dst の同名 SA へ複製する。

        設定不要で動く（`steps.iam_sync.enabled` 未指定なら有効）。手順:

        1. src 各プロジェクトの project IAM ポリシーを read-only で取得
        2. build_iam_replication_plan() で dst 用のバインディング一覧に変換
           （ロール ID / SA email を proj_map で読み替え、読み替え不能なものは
             スキップ + WARNING に倒す）
        3. 付与先の dst SA が無ければ空 SA を冪等作成
        4. dst プロジェクト単位で付与

        意図的に対象外にしているもの:
        - 条件付きバインディング（条件式が src のリソース名を参照しうる）
        - ORG カスタムロール / project_mapping 外のプロジェクトのカスタムロール
        - default compute / appspot / Google 管理 service agent（dst に同等物がある）
        - SA リソース自身の IAM ポリシー（= 誰がその SA を借用できるか）
        - プロジェクト以外のリソース（バケット / データセット等）のバインディング

        並列化: src ポリシー取得はプロジェクト単位で並列。付与は **dst プロジェクト
        単位で並列 + プロジェクト内は直列**。同一プロジェクトへの
        add-iam-policy-binding は read-modify-write なので並列化すると etag 競合
        （ABORTED）になる。
        """
        log_stage_header(self.dst_logger, 57, "IAM ロール複製 (src SA → dst SA)")
        proj_map = self._build_proj_id_map()
        if not proj_map:
            self.dst_logger.warning("  対象プロジェクトがありません。スキップします")
            return

        # Cloud Run サービス個別の公開設定（allUsers → run.invoker）を先に複製する。
        # SA 複製の対象有無に関わらず必要なので、早期 return より前に呼ぶ。
        self._sync_run_service_invokers()

        src_policies = self._fetch_src_iam_policies()
        grants, warnings = build_iam_replication_plan(
            src_policies, proj_map, self._iam_excluded_sa_emails())
        for w in warnings:
            self.dst_logger.warning(f"  [IAM] {w}")
        if not grants:
            self.dst_logger.info("  複製対象の IAM バインディングはありません")
            return

        src_members = sorted({g['src_member'] for g in grants})
        self.dst_logger.info(
            f"  複製候補: {len(grants)} バインディング / SA {len(src_members)} 件"
        )

        # --- 付与先の dst SA を用意（無ければ空 SA を冪等作成）---
        # _resolve_dst_vm_service_account は内部で SA 単位のロックとキャッシュを持つ。
        # Step 5 で VM 用に作成済みの SA はここで再利用される。
        dst_sa_by_src = self._build_dst_sa_map()
        usable: Dict[str, bool] = {}
        for src_member in src_members:
            src_email = src_member.split(':', 1)[1]
            parsed = parse_user_managed_sa(src_email)
            sa_proj = parsed[1] if parsed else ''
            resolved = self._resolve_dst_vm_service_account(
                src_email, proj_map, dst_sa_by_src.get(sa_proj), label="SA")
            usable[src_member] = bool(resolved)
            if not resolved:
                self.dst_logger.warning(
                    f"  [IAM] {src_member} に対応する dst SA を用意できなかったため、"
                    f"この SA のロール複製をスキップします"
                )
        grants = [g for g in grants if usable.get(g['src_member'])]
        if not grants:
            self.dst_logger.warning("  付与可能なバインディングが残りませんでした")
            return

        by_project: Dict[str, List[Dict[str, Any]]] = {}
        for g in grants:
            by_project.setdefault(g['dst_project'], []).append(g)
        dst_sa_by_dst = {dst: dst_sa_by_src.get(src) for src, dst in proj_map.items()}

        applied: List[Dict[str, Any]] = []
        applied_lock = threading.Lock()

        def worker(dst_proj: str):
            items = by_project[dst_proj]
            dst_sa = dst_sa_by_dst.get(dst_proj)

            # setIamPolicy が無いと全件失敗するため、プロジェクト単位で先に確認する。
            # 無い場合はエラーにせず、手動付与コマンドを案内してスキップに倒す。
            if self._dst_can_set_iam_policy(dst_proj, dst_sa) is False:
                self.dst_logger.warning(
                    f"  [IAM] {dst_proj}: 実行主体に resourcemanager.projects.setIamPolicy が"
                    f"ないため {len(items)} 件の付与をスキップします。"
                    f"`scripts/bootstrap_dst_sa.sh --apply` を再実行して "
                    f"roles/resourcemanager.projectIamAdmin を付与するか、"
                    f"以下を手動実行してください:"
                )
                for g in items:
                    self.dst_logger.warning(f"      {self._iam_grant_command(g)}")
                    self.stats.incr("skipped")
                return

            existing = self._dst_existing_bindings(dst_proj, dst_sa)
            for g in items:
                if existing is not None and (g['dst_member'], g['dst_role']) in existing:
                    self.dst_logger.info(
                        f"  [IAM] {dst_proj}: {g['dst_role']} は {g['dst_member']} に"
                        f"付与済み。スキップ"
                    )
                    self.stats.incr("skipped")
                    continue
                # カスタムロールは Step 4 の terraform が dst に作る想定。無いまま
                # 付与すると cryptic な API エラーになるので存在確認してから付与する。
                if g['dst_role'].startswith("projects/") and not (self.dry_run or self.mock):
                    role_id = g['dst_role'].rsplit('/', 1)[1]
                    if not self._gcloud_exists(
                        f"gcloud iam roles describe {role_id} "
                        f"--project={dst_proj} --format='value(name)'", dst_sa,
                    ):
                        self.dst_logger.warning(
                            f"  [IAM] {dst_proj}: カスタムロール {g['dst_role']} が dst に"
                            f"存在しないため {g['dst_member']} への付与をスキップします"
                            f"（Step 4 terraform_apply で複製されたか確認してください）"
                        )
                        self.stats.incr("skipped")
                        continue
                out = self.run_command(
                    self._iam_grant_command(g),
                    side="dst", logger=self.dst_logger,
                    desc=f"IAM grant {g['dst_role']}",
                    explanation=(
                        f"dst {dst_proj} の {g['dst_member']} に {g['dst_role']} を付与"
                        f"（src {g['src_project']} の {g['src_member']} が持つ "
                        f"{g['src_role']} を複製）"
                    ),
                    impersonate_sa=dst_sa, allow_fail=True, retries=2,
                )
                if out is None:
                    continue
                with applied_lock:
                    applied.append(g)

        self._parallel_for_each(sorted(by_project), worker, "iam-grant")

        self.dst_logger.info(
            f"  IAM 複製: 付与 {len(applied)} 件 / 候補 {len(grants)} 件"
        )
        self._warn_high_privilege_grants(applied)

    def _sync_run_service_invokers(self):
        """Cloud Run サービス個別の公開設定（allUsers 等 → run.invoker）を複製する。

        「未認証アクセスを許可」は**サービスリソース個別の IAM** で、bulk-export
        （IAM を出力しない）にも project IAM 複製にも乗らないため、放置すると
        src では公開のサービスが dst では認証必須になる（regression: any-method-api）。
        allUsers / allAuthenticatedUsers の invoker だけを忠実複製し、付与したら
        末尾に警告でまとめて見せる（roles/owner の複製と同じ「忠実再現 + 警告」方針）。
        dst に同名サービスがまだ無い場合（bulk-export 未出力の www-1 等）は
        スキップ + WARNING（作成後の再実行で付与される）。soft fail。
        """
        pairs = list(self._iter_project_pairs())
        applied: List[Tuple[str, str, str, str]] = []  # (dst_proj, svc, region, member)
        applied_lock = threading.Lock()

        def worker(item):
            src_proj, dst_proj, src_sa, dst_sa = item
            rc, out, err = self._soft_run(
                f"gcloud run services list --project={src_proj} "
                f"--format=json --quiet",
                "src", self.org_logger, impersonate_sa=src_sa, timeout=300,
                skip_on_dry_run=False,
            )
            if rc != 0:
                if not is_api_disabled_error(f"{err or ''}\n{out or ''}"):
                    self.dst_logger.warning(
                        f"  [Run IAM] {src_proj}: サービス一覧を取得できませんでした: "
                        f"{_first_meaningful_line(err, out)}"
                    )
                return
            for name, region in parse_run_services_list(out):
                rc_p, pol, err_p = self._soft_run(
                    f"gcloud run services get-iam-policy {name} --region={region} "
                    f"--project={src_proj} --format=json --quiet",
                    "src", self.org_logger, impersonate_sa=src_sa, timeout=120,
                    skip_on_dry_run=False,
                )
                if rc_p != 0:
                    self.dst_logger.warning(
                        f"  [Run IAM] {src_proj}/{name}: IAM ポリシーを取得できません"
                        f"でした: {_first_meaningful_line(err_p, pol)}"
                    )
                    continue
                members = run_service_public_invoker_members(pol)
                if not members:
                    continue
                # dst に同名サービスが無ければ付与できない（bulk-export 未出力の
                # サービス等）。dry_run は「付与予定」を出すため存在すると見なす。
                if not self.dry_run:
                    rc_d, out_d, _ed = self._soft_run(
                        f"gcloud run services describe {name} --region={region} "
                        f"--project={dst_proj} --format='value(metadata.name)' --quiet",
                        "dst", self.dst_logger, impersonate_sa=dst_sa, timeout=120,
                    )
                    if rc_d != 0 or not (out_d or "").strip():
                        self.dst_logger.warning(
                            f"  [Run IAM] {dst_proj}/{name} が dst に無いため公開設定"
                            f"（{', '.join(members)} → run.invoker）を付与できません。"
                            f" サービス作成後に make run を再実行してください"
                        )
                        continue
                for m in members:
                    rc_a, out_a, err_a = self._soft_run(
                        f"gcloud run services add-iam-policy-binding {name} "
                        f"--region={region} --project={dst_proj} "
                        f"--member={m} --role=roles/run.invoker --quiet",
                        "dst", self.dst_logger, impersonate_sa=dst_sa, timeout=180,
                    )
                    if rc_a == 0:
                        if not self.mock and not self.dry_run:
                            self.stats.incr("executed")
                        with applied_lock:
                            applied.append((dst_proj, name, region, m))
                    else:
                        self.dst_logger.warning(
                            f"  [Run IAM] {dst_proj}/{name}: {m} の付与に失敗: "
                            f"{_first_meaningful_line(err_a, out_a)}。手動で: "
                            f"gcloud run services add-iam-policy-binding {name} "
                            f"--region={region} --project={dst_proj} "
                            f"--member={m} --role=roles/run.invoker"
                        )

        self._parallel_for_each(pairs, worker, "run-invoker")
        if not applied:
            return
        # 公開＝インターネットに開くことなので、何を開いたか必ずまとめて見せる。
        self.dst_logger.warning(
            f"  ⚠ [Run IAM] 公開アクセスを src と同じに複製しました "
            f"({len(applied)} 件)。不要なら取り消してください:"
        )
        for dst_proj, name, region, m in sorted(applied):
            self.dst_logger.warning(
                f"      {dst_proj}/{name} ({region}): {m} → roles/run.invoker"
                f"  取消: gcloud run services remove-iam-policy-binding {name} "
                f"--region={region} --project={dst_proj} --member={m} "
                f"--role=roles/run.invoker"
            )

    def _warn_high_privilege_grants(self, applied: List[Dict[str, Any]]):
        """owner 等の超高権限ロールを付与した場合、最後にまとめて警告する。

        src と同じ権限を再現した結果であっても、別 ORG に owner 相当の SA が
        無審査で生えるのは事故のもと。ログの最後で人間がレビューできるように
        「何を・どこに・なぜ」付与したかと取り消しコマンドを併記する。
        """
        high = [g for g in applied if g.get('high_privilege')]
        if not high:
            return
        bar = "!" * 56
        self.dst_logger.warning("")
        self.dst_logger.warning(f"  {bar}")
        self.dst_logger.warning(
            f"  [IAM] 超高権限ロールを {len(high)} 件付与しました。"
            f"src と同じ権限を再現した結果です。dst で本当に必要かレビューしてください:"
        )
        for g in high:
            self.dst_logger.warning(
                f"    - {g['dst_role']} → {g['dst_member']} (project {g['dst_project']})"
            )
            self.dst_logger.warning(
                f"        理由: src {g['src_project']} の {g['src_member']} に "
                f"{g['src_role']} が付与されていたため"
            )
            self.dst_logger.warning(
                f"        取消: gcloud projects remove-iam-policy-binding {g['dst_project']} "
                f"--member={shlex.quote(g['dst_member'])} "
                f"--role={shlex.quote(g['dst_role'])} --condition=None"
            )
        self.dst_logger.warning(f"  {bar}")

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
        for ent in self._standalone_entries():
            if ent.get('dst'):
                dst_projects.append(ent['dst'])

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
        if self._host_skipped():
            self.dst_logger.info(
                "  [Network] host_project.skip=true のため host VPC 複製をスキップ（既存 dst host を利用）"
            )
            return
        self._replicate_project_networks(src_host, dst_host, src_sa, dst_sa)

    def _replicate_standalone_networks(self):
        """standalone_projects の VPC/subnet を各 dst に複製する（冪等）。

        standalone プロジェクトの VPC は bulk-export → terraform apply (Step 4) が
        作成することもあるが、network_firewall/gce_restore 単体実行や export 漏れに
        備え、host と同じ gcloud ベースの複製を describe ガード付きで通しておく。
        """
        for ent in self._standalone_entries():
            src, dst = ent.get('src'), ent.get('dst')
            if not src or not dst:
                continue
            self._replicate_project_networks(
                src, dst,
                ent.get('src_impersonate_service_account'),
                ent.get('dst_impersonate_service_account'),
            )

    def _replicate_project_networks(
        self, src_host: str, dst_host: str,
        src_sa: Optional[str], dst_sa: Optional[str],
    ):
        """src プロジェクトの custom VPC/subnet を dst プロジェクトへ冪等複製する本体。"""
        self.dst_logger.info(f"  [Network] src '{src_host}' → dst '{dst_host}' VPC 複製")

        nets_json = self.run_command(
            f"gcloud compute networks list --project={src_host} --format=json",
            side="src", logger=self.org_logger,
            desc=f"List Src Networks {src_host}",
            explanation=f"{src_host} の VPC 一覧を取得（dst に複製）",
            impersonate_sa=src_sa, allow_fail=True,
        )
        subs_json = self.run_command(
            f"gcloud compute networks subnets list --project={src_host} --format=json",
            side="src", logger=self.org_logger,
            desc=f"List Src Subnets {src_host}",
            explanation=f"{src_host} のサブネット一覧を取得（dst に複製）",
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
                self.dst_logger.info(f"    VPC {name} は dst {dst_host} に既存。再利用")
            else:
                self.run_command(
                    f"gcloud compute networks create {name} --subnet-mode={mode} "
                    f"--project={dst_host} --quiet",
                    side="dst", logger=self.dst_logger,
                    desc=f"Create Network {name}",
                    explanation=f"dst {dst_host} に VPC {name}（{mode}）を作成",
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
                    explanation=f"dst {dst_host} に サブネット {sname}（{region},{cidr}）を作成",
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
        # AR イメージは Step 3.7（terraform より前）で複製済み。ここでは扱わない。
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
            # overrides は src 実名 (orig) でも置換後名 (base) でも引ける
            if orig in overrides:
                dst_bucket = overrides[orig]
            elif base in overrides:
                dst_bucket = overrides[base]
            elif '.' in orig:
                # ドット入り（ドメイン形式）バケットはドメイン検証済み TLD 配下でないと
                # dst に作成できない（HTTP 400）。特に *.appspot.com（us.artifacts.* =
                # Container Registry レイヤー / staging.* = App Engine）は Google 管理の
                # システムバケットで別プロジェクトへの複製自体が不可能。
                # 意図せず失敗を量産しないよう skip + WARNING（secure_tag_map と同パターン）。
                self.dst_logger.warning(
                    f"    gs://{orig}: ドット入り（ドメイン形式）バケットは dst に作成"
                    f"できないためスキップ。データを移行する場合は rename_rules.gcs."
                    f"overrides で '{orig}' にドット無しの dst バケット名を指定してください"
                )
                self.stats.incr("skipped")
                return
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

    # ============================================================
    # Step 3.7: Artifact Registry イメージ複製（terraform より前）
    # ============================================================
    def step_artifact_registry(self):
        """src の AR イメージを dst に複製する（Step 4 の前に必ず実行する）。

        **Step 4 より前でなければならない**: Cloud Run は
        `image = "...@sha256:<digest>"` を **revision 作成時に解決する**ため、
        イメージが dst に無いと terraform apply が
        `Error code 5, message: Image '...' not found.` で失敗する
        （regression: my-argolis の Cloud Run 3 件。Step 6 data_sync に置いていたため
        apply の後になっていて永久に間に合わなかった）。

        dst リポジトリは通常 terraform (Step 4) が作るが、ここが先に走るので
        `_sync_artifact_registry` 側で無ければ作る（冪等）。terraform は
        `# terraform import` コメント経由で既存リポジトリを adopt する。

        設定は data_sync 配下（`steps.data_sync.artifact_registry`）のまま。
        実行位置だけが前倒しされている。
        """
        pairs = list(self._iter_project_pairs())
        log_stage_header(
            self.dst_logger, 37,
            "Artifact Registry イメージ複製 (terraform より前に実施)", len(pairs),
        )
        cfg = (self.config.get('steps', {}).get('data_sync', {}) or {}).get(
            'artifact_registry', {})
        if isinstance(cfg, dict) and cfg.get('enabled') is False:
            self.dst_logger.info("  artifact_registry.enabled=false のためスキップ")
            self.dst_logger.info("  ✓ Step 3.7 完了")
            return

        # 1. 列挙フェーズ: プロジェクト単位で並列に走査し、複製対象を flat に集める。
        work: List[Dict[str, Any]] = []
        work_lock = threading.Lock()

        def collect(item):
            src_proj, dst_proj, src_sa, dst_sa = item
            self.dst_logger.info(f"  → {src_proj} → {dst_proj}")
            items = self._collect_ar_copy_work(src_proj, dst_proj, src_sa, dst_sa)
            if items:
                with work_lock:
                    work.extend(items)

        self._parallel_for_each(pairs, collect, "ar-list")
        if not work:
            self.dst_logger.info("  複製対象イメージなし")
            self.dst_logger.info("  ✓ Step 3.7 完了")
            return
        tool = self._ar_copy_tool()
        if not tool:
            # 実行前の check_prerequisites が同条件で止めるので通常は到達しない。
            # スキップ（soft fail）にすると「イメージが無いまま apply して
            # Image not found」になるだけなので、ここでは失敗として記録する。
            self.dst_logger.warning(
                f"  ✗ gcrane / crane が PATH にありません。"
                f"イメージ {len(work)} 件を複製できません。"
                f"インストール: `go install "
                f"github.com/google/go-containerregistry/cmd/gcrane@latest`"
            )
            self.stats.add_failure(
                "AR イメージ複製",
                f"gcrane / crane が未インストール（対象 {len(work)} 件）")
            self.stats.incr("failed")
            return

        # 2. レジストリ認証: ~/.docker/config.json への書き込みは並列安全でないため、
        #    コピー開始前に対象ホスト分を直列でまとめて済ませる。
        #    crane も docker の資格情報ヘルパ設定を読むので gcrane 利用時も実施する。
        for host in sorted({it["dst_pkg"].split("/", 1)[0] for it in work}):
            self._soft_run(
                f"gcloud auth configure-docker {host} --quiet",
                "dst", self.dst_logger, timeout=120,
            )

        # 3. コピーフェーズ: 全プロジェクト・全リポジトリのイメージを
        #    flat な単位で並列コピー（プロジェクト間の直列待ちを無くす）。
        self.dst_logger.info(
            f"  複製対象: {len(work)} イメージ"
            f" (tool={tool}, parallel_jobs={self.parallel_jobs})"
        )
        self._parallel_for_each(
            work, lambda it: self._copy_ar_image(it, it.get("dst_sa")), "ar-copy",
        )
        self.dst_logger.info("  ✓ Step 3.7 完了")

    def _collect_ar_copy_work(self, src_proj, dst_proj, src_sa, dst_sa
                              ) -> List[Dict[str, Any]]:
        """src の AR (DOCKER) を走査し、複製すべきイメージ item のリストを返す。

        実コピーはしない（列挙 + dst リポジトリの補完作成のみ）。呼び出し側が
        全プロジェクト分を集めて **flat な (project × repo × image) 単位で**
        並列コピーする（プロジェクトごとに直列だと parallel_jobs が
        1 リポジトリ内でしか効かず、小さいリポジトリの間で遊んでしまう）。

        soft fail に徹する（`stats.failed` に積まない）: イメージが無くて困るのは
        参照している側で、そちらが本来のエラーで気付かせてくれる。
        """
        cfg = (self.config.get('steps', {}).get('data_sync', {}) or {}).get(
            'artifact_registry', {})
        if not isinstance(cfg, dict):
            cfg = {}
        skip_repos = {str(r).strip() for r in (cfg.get('skip_repos') or []) if str(r).strip()}
        scope = str(cfg.get('scope') or 'all').strip().lower()
        # `.tf` の digest 固定参照は scope に関わらず必ず複製する（落とすと
        # Step 4 が `Image ... not found` で落ちる）。Step 3 の後に走るので
        # active/<src> は「これから apply される内容」で確定している。
        keep_digests = tf_referenced_image_digests(
            os.path.join(self._tf_base_dir(), "active", src_proj))

        # src の一覧取得は soft fail に徹する（`stats.failed` に積まない）。
        # AR を使っていない src では API 自体が無効で 403 になるが、それは
        # 「対象なし」であって移行の失敗ではない（regression: AR 未使用の
        # サービスプロジェクト 2 件で `make run` が exit 1 になっていた）。
        rc, repos_out, repos_err = self._soft_run(
            f"gcloud artifacts repositories list --project={src_proj} "
            f"--format=json --quiet",
            "src", self.org_logger, impersonate_sa=src_sa, timeout=300,
            skip_on_dry_run=False,
        )
        if rc != 0:
            detail = f"{repos_err or ''}\n{repos_out or ''}"
            if is_api_disabled_error(detail):
                self.dst_logger.info(
                    f"    src '{src_proj}' は Artifact Registry API が無効"
                    f"（= AR 未使用）。複製対象なし"
                )
            else:
                self.dst_logger.warning(
                    f"    AR リポジトリ一覧を取得できませんでした ({src_proj}): "
                    f"{_first_meaningful_line(repos_err, repos_out)}"
                )
            return []
        repos = parse_ar_repositories(repos_out)
        if not repos:
            self.dst_logger.info(f"    {src_proj}: DOCKER リポジトリ無し")
            return []

        work: List[Dict[str, Any]] = []
        for repo in repos:
            if repo["repo"] in skip_repos:
                self.dst_logger.info(
                    f"    skip_repos 指定によりスキップ: {repo['repo']}")
                self.stats.incr("skipped")
                continue
            loc, name = repo["location"], repo["repo"]
            host = f"{loc}-docker.pkg.dev"
            src_path = f"{host}/{src_proj}/{name}"
            rc_i, images_json, images_err = self._soft_run(
                f"gcloud artifacts docker images list {src_path} "
                f"--include-tags --format=json --quiet",
                "src", self.org_logger, impersonate_sa=src_sa, timeout=600,
                skip_on_dry_run=False,
            )
            if rc_i != 0:
                self.dst_logger.warning(
                    f"    {src_path}: イメージ一覧を取得できませんでした: "
                    f"{_first_meaningful_line(images_err, images_json)}"
                )
                continue
            plan = build_ar_image_copy_plan(images_json, src_proj, dst_proj)
            if not plan:
                self.dst_logger.info(f"    {src_path}: イメージ 0 件")
                continue
            # scope による間引きは「黙って減らさない」= 落とした件数と理由を必ず出す。
            plan, dropped = filter_ar_plan_by_scope(plan, scope, keep_digests)
            if dropped:
                for _ in dropped:
                    self.stats.incr("skipped")
                self.dst_logger.info(
                    f"    {src_path}: scope=tagged により tag 無し {len(dropped)} 件を"
                    f"除外（新しいビルドに tag を奪われた過去ビルド。"
                    f"`.tf` が digest 固定で参照するものは除外していません）"
                )
            if not plan:
                self.dst_logger.info(f"    {src_path}: 複製対象 0 件")
                continue
            # dst リポジトリは Terraform が作る想定。取りこぼし時のみ補う（冪等）。
            if not self._gcloud_exists(
                f"gcloud artifacts repositories describe {name} "
                f"--location={loc} --project={dst_proj} --format='value(name)' --quiet",
                dst_sa,
            ):
                rc_c, out_c, err_c = self._soft_run(
                    f"gcloud artifacts repositories create {name} "
                    f"--repository-format=docker --location={loc} "
                    f"--project={dst_proj} --quiet",
                    "dst", self.dst_logger, impersonate_sa=dst_sa, timeout=300,
                )
                if rc_c != 0:
                    self.dst_logger.warning(
                        f"    dst リポジトリ {name} を作成できませんでした: "
                        f"{_first_meaningful_line(err_c, out_c)}"
                    )
                    continue
            # dst の既存 digest を**リポジトリ単位で 1 回だけ**取得して差分を出す。
            # 従来はイメージごとに describe（1 件 1 API 呼び出し）していて、
            # 再実行時に件数分の往復が積み上がっていた。
            have: Set[str] = set()
            rc_d, out_d, _e = self._soft_run(
                f"gcloud artifacts docker images list "
                f"{host}/{dst_proj}/{name} --format='value(version)' --quiet",
                "dst", self.dst_logger, impersonate_sa=dst_sa, timeout=600,
                skip_on_dry_run=False,
            )
            if rc_d == 0:
                have = {ln.strip() for ln in (out_d or "").splitlines()
                        if _AR_DIGEST_RE.match(ln.strip())}
            already = [e for e in plan if e["digest"] in have]
            todo = [e for e in plan if e["digest"] not in have]
            for _ in already:
                self.stats.incr("skipped")
            self.dst_logger.info(
                f"    {src_path}: イメージ {len(plan)} 件"
                f"（既存 {len(already)} 件 / 複製対象 {len(todo)} 件）"
            )
            for e in todo:
                e["dst_sa"] = dst_sa
            work.extend(todo)
        return work

    def _ar_copy_tool(self) -> Optional[str]:
        """イメージ複製に使うツールを返す（gcrane 優先、無ければ crane）。

        **docker は使わない**。pull → push はマルチアーキイメージを単一
        プラットフォームに落として **digest が変わる**ことがあり、そうなると
        (1) Cloud Run の `@sha256:` 固定参照が解決できない
        (2) 「dst に既存」判定も一致せず**毎回同じイメージを再送する**
        （実測: gcf-artifacts の 4 件が毎回再送されていた）。
        gcrane/crane は registry → registry でマニフェストリストごと転送するため
        digest が保たれ、dst に既にあるレイヤも blob mount で再送されない。

        どちらも無ければ None。実行前の `check_prerequisites` が同じ条件で
        fail-fast するので通常ここには来ない（mock / チェック省略時の保険）。
        """
        if self.mock:
            return "gcrane"
        for tool in ("gcrane", "crane"):
            if shutil.which(tool):
                return tool
        return None

    def _copy_ar_image(self, item: Dict[str, Any], dst_sa: Optional[str]):
        """イメージ 1 件を gcrane / crane で registry → registry 複製する。

        digest はそのまま保たれるので「push できたが digest が変わった」検証は
        不要（変わらないことが保証される）。ローカルにイメージを作らないので
        後片付けも要らない。
        """
        tool = self._ar_copy_tool()
        if not tool:
            # 通常は check_prerequisites が実行前に止めるので到達しない。
            self.dst_logger.warning(
                f"      ✗ gcrane / crane が見つからないため複製できません: "
                f"{item.get('src_ref')}"
            )
            self.stats.add_failure("AR イメージ複製", "gcrane / crane が未インストール")
            self.stats.incr("failed")
            return
        src_ref, dst_pkg = item["src_ref"], item["dst_pkg"]
        digest, tags = item["digest"], item["tags"]
        # 宛先はタグ参照で指定する（digest は内容から決まるので宛先には書けない）。
        # tag が無い src（digest 参照専用）は digest 由来の一意なタグを合成する。
        push_tags = tags or [f"migrated-{digest.split(':', 1)[1][:12]}"]
        pushed = False
        for tag in push_tags:
            dst_tagged = f"{dst_pkg}:{tag}"
            rc, out, err = self._soft_run(
                f"{tool} cp {src_ref} {dst_tagged}", "dst", self.dst_logger,
                impersonate_sa=dst_sa, timeout=1800,
            )
            if rc == 0:
                pushed = True
                if not self.mock and not self.dry_run:
                    self.stats.incr("executed")
                continue
            detail = f"{err or ''}\n{out or ''}".lower()
            # docker 経路と同じ扱い: attestation / SBOM は実行可能イメージでは
            # ないので複製不要（失敗ではなくスキップ）。
            if "unsupported media type" in detail:
                self.dst_logger.info(
                    f"      非イメージ成果物（attestation/SBOM 等）のためスキップ: "
                    f"{src_ref}"
                )
                self.stats.incr("skipped")
                return
            self.dst_logger.warning(
                f"      ✗ {tool} cp 失敗 {dst_tagged}: "
                f"{_first_meaningful_line(err, out)}"
            )
        if pushed:
            self.dst_logger.info(
                f"      ✓ 複製 {dst_pkg}@{digest[:19]}… ({tool})")

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


def _mapping_pairs(config: Dict) -> List[Tuple[str, str]]:
    """config の project_mapping から (src, dst) 一覧を返す（host → service → standalone の順）。"""
    mapping = config.get('project_mapping') or {}
    pairs: List[Tuple[str, str]] = []
    host = mapping.get('host_project') or {}
    if host.get('src'):
        pairs.append((host['src'], host.get('dst') or ''))
    for svc in mapping.get('service_projects') or []:
        if svc.get('src'):
            pairs.append((svc['src'], svc.get('dst') or ''))
    for ent in mapping.get('standalone_projects') or []:
        if isinstance(ent, dict) and ent.get('src'):
            pairs.append((ent['src'], ent.get('dst') or ''))
    return pairs


def resolve_clean_targets(
    config: Dict, ids: List[str], tf_base: str
) -> Tuple[List[str], List[str]]:
    """--clean-state 対象の削除ディレクトリを解決する。

    各 id は src ID / config の dst ID / active/<src>/.dst_project マーカー値
    （config から消えた旧 dst の掃除用）のいずれでもマッチする。
    戻り値: (削除対象ディレクトリ list, 解決できなかった id list)。
    """
    active_dir = os.path.join(tf_base, 'active')
    raw_dir = os.path.join(tf_base, 'raw')
    dir_srcs: List[str] = []
    if os.path.isdir(active_dir):
        dir_srcs = [n for n in os.listdir(active_dir)
                    if os.path.isdir(os.path.join(active_dir, n))]

    def marker_of(src: str) -> str:
        try:
            with open(os.path.join(active_dir, src, '.dst_project'),
                      encoding='utf-8') as f:
                return f.read().strip()
        except OSError:
            return ''

    pairs = _mapping_pairs(config)
    targets: List[str] = []
    unresolved: List[str] = []
    seen = set()
    for pid in ids:
        pid = (pid or '').strip()
        if not pid:
            unresolved.append("(空)")
            continue
        srcs = {s for s, d in pairs if pid in (s, d)}
        srcs |= {s for s in dir_srcs if s == pid or marker_of(s) == pid}
        if not srcs:
            unresolved.append(pid)
            continue
        for s in sorted(srcs):
            for d in (os.path.join(active_dir, s), os.path.join(raw_dir, s)):
                if d not in seen and os.path.isdir(d):
                    seen.add(d)
                    targets.append(d)
    return targets, unresolved


def run_clean_state(config_path: str, ids: List[str]) -> int:
    """指定プロジェクトの terraform 生成物 (active/<src>・raw/<src>) だけ削除する。

    他プロジェクトの state と terraform/.gcs_rename_value には触れない。
    GCP には接続しない（ローカル生成物の削除のみ）。
    1 つでも解決できない id があれば何も削除せず中止する。
    """
    try:
        with open(config_path, encoding='utf-8') as f:
            config = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as e:
        print(f"config を読み込めません: {config_path}: {e}", file=sys.stderr)
        return 1
    tf_base = ((config.get('steps') or {}).get('bulk_export') or {}) \
        .get('output_dir', './terraform')
    targets, unresolved = resolve_clean_targets(config, ids, tf_base)
    if unresolved:
        print("解決できないプロジェクト ID があるため何も削除しません: "
              + ", ".join(unresolved), file=sys.stderr)
        known = [x for s, d in _mapping_pairs(config) for x in (s, d) if x]
        if known:
            print("  指定可能な ID (config): " + ", ".join(known), file=sys.stderr)
        return 1
    if not targets:
        print("削除対象の生成物が見つかりません（既にクリーンです）")
        return 0
    for d in targets:
        shutil.rmtree(d, ignore_errors=True)
        print(f"削除: {d}")
    print(f"✓ {len(targets)} ディレクトリを削除しました"
          "（他プロジェクトの state と .gcs_rename_value は温存）")
    return 0


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
    parser.add_argument(
        "-y", "--yes", action="store_true", default=False,
        help="SA 事前チェックの続行確認 ([y/N]) を自動承認する（非対話 / CI 用）",
    )
    parser.add_argument(
        "--skip-on-run", action="store_true", dest="skip_on_run", default=None,
        help="bulk_export.skip_on_run を今回だけ有効にする（config より優先）",
    )
    parser.add_argument(
        "--no-skip-on-run", action="store_false", dest="skip_on_run",
        help="bulk_export.skip_on_run を今回だけ無効にする＝export/customize を必ず再実行"
             "（config より優先。make run SKIP_ON_RUN=0）",
    )
    parser.add_argument(
        "--clean-state", action="append", metavar="PROJECT_ID",
        help="指定プロジェクトの terraform 生成物 (active/raw) と state だけ削除して終了。"
             "src / dst どちらの ID でも可。複数指定可",
    )
    args = parser.parse_args()

    if args.clean_state:
        sys.exit(run_clean_state(args.config, args.clean_state))

    orchestrator = MigrationOrchestrator(
        config_path=args.config,
        dry_run_override=args.dry_run,
        verbose_override=args.verbose,
        mock_override=args.mock,
        auto_approve=args.yes,
        skip_on_run_override=args.skip_on_run,
    )
    orchestrator.execute()
    if orchestrator.stats.failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
