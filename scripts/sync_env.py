#!/usr/bin/env python3
"""GCP プロジェクトまるごとコピーオーケストレータ (sync_env.py)。

設計の柱:
- ORG プロジェクトに対する書き込みは絶対に行わない（コードレベルで強制）。
- すべての外部コマンドは side="src" | "dst" | "local" タグ付きで実行され、
  src 操作は impersonate_sa 必須かつ read-only パターンに限定される。
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
    "gcloud compute network-firewall-policies rules list",
    "gcloud compute network-firewall-policies rules create",
    "gcloud compute network-firewall-policies associations list",
    "gcloud compute network-firewall-policies associations create",
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


def fw_policy_rule_flags(
    rule: Dict[str, Any], proj_id_map: Dict[str, str],
) -> List[str]:
    """FW policy rule dict を gcloud `rules create` 用の追加フラグリストに変換する (ISSUE-02)。

    呼び出し側が prefix (`rules create <priority> --firewall-policy=... --action=... --direction=...
    --layer4-configs=...`) を作り、その後ろに append する想定。

    対応フィールド: srcIpRanges / destIpRanges / srcSecureTags / targetSecureTags /
    targetServiceAccounts / disabled / enableLogging / description /
    srcNetworkScope / srcRegionCodes / destRegionCodes
    SA email 中の src プロジェクト ID は proj_id_map で dst へ置換する。
    """
    flags: List[str] = []
    match = rule.get('match', {}) or {}

    def _join_or_skip(key: str, flag: str, src: Dict[str, Any]):
        vals = src.get(key) or []
        if vals:
            flags.append(f"{flag}={','.join(str(v) for v in vals)}")

    _join_or_skip('srcIpRanges',     '--src-ip-ranges', match)
    _join_or_skip('destIpRanges',    '--dest-ip-ranges', match)
    _join_or_skip('srcRegionCodes',  '--src-region-codes', match)
    _join_or_skip('destRegionCodes', '--dest-region-codes', match)

    # secure tag は name (`tagValues/...` フル形式) で渡す
    src_tags = [t.get('name') for t in match.get('srcSecureTags') or [] if t.get('name')]
    if src_tags:
        flags.append(f"--src-secure-tags={','.join(src_tags)}")

    tgt_tags = [t.get('name') for t in rule.get('targetSecureTags') or [] if t.get('name')]
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
        # description にはスペースを含むため shlex.quote で囲む
        flags.append(f"--description={shlex.quote(desc)}")

    return flags


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
    - host_project と service_projects の src/dst/impersonate_sa がすべて埋まっている
    - src と dst が同一でないこと
    - dst 側 ID が src 側 ID と重複していないこと（ORG を上書きしないため）
    - dst が複数の src にマップされていないこと
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
        src_sa = ent.get('src_impersonate_service_account')
        dst_sa = ent.get('dst_impersonate_service_account')
        if not src:
            errors.append(f"{label}: src が未指定")
        if not dst:
            errors.append(f"{label}: dst が未指定")
        if not src_sa:
            errors.append(f"{label}: src_impersonate_service_account が未指定（ORG 保護のため必須）")
        if not dst_sa:
            errors.append(f"{label}: dst_impersonate_service_account が未指定")
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

        # 厳格バリデーション（ORG 保護の最終防衛線）
        errors = validate_config(self.config)
        if errors:
            print("=" * 60, file=sys.stderr)
            print(" [ORG 保護] config.yaml にエラーがあります。処理を中止します:", file=sys.stderr)
            for e in errors:
                print(f"  - {e}", file=sys.stderr)
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

        gcloud_steps = ("cai_scan", "gce_snapshot", "bulk_export", "gce_restore", "data_sync")

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

        # 検証対象: (SA, project, side, 必要権限集合) を構築。
        targets = []
        src_perms = required_perms(_SRC_PERMS_BY_STEP, _SRC_BASELINE_PERMS)
        dst_perms = required_perms(_DST_PERMS_BY_STEP, _DST_BASELINE_PERMS)
        for src_proj, dst_proj, src_sa, dst_sa in self._iter_project_pairs():
            targets.append((src_sa, src_proj, "src", src_perms))
            targets.append((dst_sa, dst_proj, "dst", dst_perms))

        errors: List[str] = []
        ok_count = 0
        checked_sas = set()
        for sa, project, side, perms in targets:
            label = f"{side} SA '{sa}' (project={project})"

            # 1) 実在＋借用可否（アクセストークン発行）。stdout=token はログに出さない。
            rc, token, err = self._sa_preflight_run(
                f"gcloud auth print-access-token --impersonate-service-account={sa}"
            )
            if rc != 0 or not token.strip():
                reason = err.strip().splitlines()[-1] if err.strip() else "原因不明"
                errors.append(
                    f"{label}: 借用（impersonate）できません。SA が存在しないか、"
                    f"実行ユーザーに roles/iam.serviceAccountTokenCreator がありません。"
                    f" 詳細: {reason[:300]}"
                )
                continue

            # 2) 権限（借用トークンで testIamPermissions REST を実行）。
            granted = self._test_iam_permissions(token.strip(), project, perms)
            if granted is None:
                # 検証不能（API 未有効・ネットワーク等）。借用は確認済みなので警告に留める。
                self.org_logger.warning(
                    f"  [SA事前チェック] {label}: 権限を検証できませんでした"
                    f"（対象 API 未有効などの可能性）。借用は確認済みのため継続します。"
                )
                ok_count += 1
                checked_sas.add(sa)
                continue
            missing = sorted(perms - granted)
            if missing:
                errors.append(
                    f"{label}: 必要権限が不足しています: {', '.join(missing)}"
                )
                continue

            ok_count += 1
            checked_sas.add(sa)

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
                print("   まず dry-run で内容確認:  make bootstrap", file=sys.stderr)
                print("   実際に作成/付与:         make bootstrap-apply", file=sys.stderr)
                print("   個別:  make bootstrap-dst-sa-apply / bootstrap-cross-project-apply / bootstrap-shared-vpc-apply", file=sys.stderr)
                print("=" * 60, file=sys.stderr)
            sys.exit(1)

        self.org_logger.info(
            f"  [SA事前チェック] OK: {len(checked_sas)} 個の SA で借用と代表権限を確認"
            f"（検証は代表的な権限のみ）"
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
            impersonate_sa: 借用 SA。side="src" では必須。
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

        if side == "src":
            if not impersonate_sa:
                logger.error(
                    f"{tag}[ORG 保護] src 操作には impersonate_sa が必須です。"
                    f" コマンド: {cmd}"
                )
                sys.exit(1)
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

        if cmd.strip().startswith("gcloud compute network-firewall-policies rules list"):
            logger.info(f"{tag}[MOCK] ファイアウォールポリシールール一覧をシミュレート")
            return json.dumps([
                {
                    "priority": 1000, "action": "allow", "direction": "INGRESS",
                    "match": {
                        "srcIpRanges": ["10.0.0.0/8"],
                        "layer4Configs": [
                            {"ipProtocol": "tcp", "ports": ["80", "443"]},
                            {"ipProtocol": "udp", "ports": ["53"]},
                        ],
                    },
                    "targetServiceAccounts": ["app-sa@<SRC_HOST_PROJECT_ID>.iam.gserviceaccount.com"],
                    "enableLogging": True,
                    "description": "web ingress (mock)",
                },
            ])

        if cmd.strip().startswith("gcloud compute network-firewall-policies associations list"):
            logger.info(f"{tag}[MOCK] ファイアウォールポリシーアソシエーション一覧をシミュレート")
            return json.dumps([])

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
        self.org_logger.info("【ORG 保護】src 操作は read-only に強制、impersonate_sa 必須、")
        self.org_logger.info("           書き込み動詞は実行前に拒否されます。")
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
        finally:
            self._print_summary()

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
        self.dst_logger.info(f"  対象 Terraform ルート: {len(project_dirs)} 件")
        for proj_dir in project_dirs:
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
            # ローカルファイル書きのみで dst への副作用は無いため dry_run でも実施し、
            # plan が "Invalid provider configuration" で落ちるのを防ぐ。
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
            # apply 前に既存リソースを state に取り込み、再実行/汚れた dst でも
            # "already exists" にならないようにする（冪等化）。dry_run ではスキップ。
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
        self.dst_logger.info("  ✓ Step 4 完了")

    def _reset_stale_state_if_needed(self, proj_dir: str, dst_proj: str):
        """active/<src> の terraform state が現在の dst プロジェクト用でなければ破棄する。

        - `.dst_project` マーカーがあり中身が現 dst と異なる → stale。
        - マーカーが無い旧 state は、state 本文に現 dst プロジェクト ID が一度も
          現れない（=別プロジェクト用）なら stale とみなす。
        stale なら terraform.tfstate（+backup）、.terraform、lock を削除し、
        import からクリーンにやり直せるようにする。最後にマーカーを現 dst で更新。
        """
        marker = os.path.join(proj_dir, ".dst_project")
        state = os.path.join(proj_dir, "terraform.tfstate")
        stale = False
        if os.path.exists(marker):
            try:
                stale = open(marker, encoding="utf-8").read().strip() != dst_proj
            except OSError:
                stale = False
        elif os.path.exists(state):
            try:
                txt = open(state, encoding="utf-8").read()
            except OSError:
                txt = ""
            # リソースを持つ state なのに現 dst プロジェクトを一切参照していない＝旧環境用。
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

        for rule in rules:
            name = rule.get('name', '')
            if not name:
                continue

            if self._gcloud_exists(
                f"gcloud compute firewall-rules describe {name} --project={dst_host} "
                f"--format='value(name)'",
                dst_sa,
            ):
                self.dst_logger.info(f"    FW rule '{name}' は既存。スキップ")
                continue

            # ネットワーク名を src → dst に置換（URL 末尾から取得）
            net_url = rule.get('network', '')
            net_name = net_url.split('/')[-1] if net_url else 'default'

            direction = rule.get('direction', 'INGRESS')
            priority = rule.get('priority', 1000)
            disabled = rule.get('disabled', False)

            allowed = rule.get('allowed', [])
            denied = rule.get('denied', [])
            action_flag, proto_list = self._fw_action_and_rules(allowed, denied)
            if not action_flag:
                self.dst_logger.warning(f"    FW rule '{name}': allowed/denied が空のためスキップ")
                continue

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
        scopes: List[Tuple[str, str]] = [("--global", "global")]
        for region in sorted(self._discover_src_regions(src_host, src_sa)):
            scopes.append((f"--region={region}", region))

        for scope_flag, scope_label in scopes:
            raw = self.run_command(
                f"gcloud compute network-firewall-policies list "
                f"--project={src_host} {scope_flag} --format=json",
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

    def _sync_fw_policy_rules(
        self, pname: str, scope_flag: str, scope_label: str,
        src_host: str, dst_host: str,
        src_sa: Optional[str], dst_sa: Optional[str],
        proj_map: Dict[str, str],
    ):
        rules_raw = self.run_command(
            f"gcloud compute network-firewall-policies rules list "
            f"--firewall-policy={pname} --project={src_host} {scope_flag} --format=json",
            side="src", logger=self.org_logger,
            desc=f"List FW Policy Rules {pname} ({scope_label})",
            explanation=f"ポリシー '{pname}' のルール一覧取得",
            impersonate_sa=src_sa, allow_fail=True,
        )
        try:
            fw_rules = json.loads(rules_raw) if rules_raw else []
        except Exception:
            fw_rules = []

        for r in fw_rules:
            prio = r.get('priority')
            action = r.get('action', 'allow')
            direction = r.get('direction', 'INGRESS')
            if prio is None:
                continue

            if self._gcloud_exists(
                f"gcloud compute network-firewall-policies rules describe {prio} "
                f"--firewall-policy={pname} --project={dst_host} {scope_flag} "
                f"--format='value(priority)'",
                dst_sa,
            ):
                self.dst_logger.info(
                    f"      ポリシールール {pname}/{prio} は既存。スキップ"
                )
                continue

            layer4 = fw_policy_rule_layer4(r)
            extra_flags = fw_policy_rule_flags(r, proj_map)

            rule_cmd = (
                f"gcloud compute network-firewall-policies rules create {prio} "
                f"--firewall-policy={pname} --project={dst_host} {scope_flag} "
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

    def _sync_fw_policy_associations(
        self, pname: str, scope_flag: str, scope_label: str,
        src_host: str, dst_host: str,
        src_sa: Optional[str], dst_sa: Optional[str],
        proj_map: Dict[str, str],
    ):
        # src の association を取得
        assoc_raw = self.run_command(
            f"gcloud compute network-firewall-policies associations list "
            f"--firewall-policy={pname} --project={src_host} {scope_flag} --format=json",
            side="src", logger=self.org_logger,
            desc=f"List FW Policy Assoc {pname} ({scope_label}) [src]",
            explanation=f"ポリシー '{pname}' のアソシエーション一覧取得 (src)",
            impersonate_sa=src_sa, allow_fail=True,
        )
        try:
            assocs = json.loads(assoc_raw) if assoc_raw else []
        except Exception:
            assocs = []

        if not assocs:
            return

        # dst 側 association を list で取得し、name 集合で存在判定する
        # （`associations describe --name=` は gcloud バージョンによって挙動が不安定）
        dst_assoc_raw = self.run_command(
            f"gcloud compute network-firewall-policies associations list "
            f"--firewall-policy={pname} --project={dst_host} {scope_flag} --format=json",
            side="dst", logger=self.dst_logger,
            desc=f"List FW Policy Assoc {pname} ({scope_label}) [dst]",
            explanation=f"ポリシー '{pname}' の dst 側 association を取得 (冪等判定)",
            impersonate_sa=dst_sa, allow_fail=True,
        )
        try:
            dst_assoc_list = json.loads(dst_assoc_raw) if dst_assoc_raw else []
        except Exception:
            dst_assoc_list = []
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

            self.run_command(
                f"gcloud compute network-firewall-policies associations create "
                f"--firewall-policy={pname} --project={dst_host} {scope_flag} "
                f"--name={assoc_name} --network={dst_net_url}",
                side="dst", logger=self.dst_logger,
                desc=f"Create FW Policy Assoc {assoc_name} ({scope_label})",
                explanation=f"ポリシー '{pname}' をネットワーク '{net_name}' に関連付け",
                impersonate_sa=dst_sa, allow_fail=True,
            )

    def step_gce_restore(self):
        pairs = list(self._iter_project_pairs())
        log_stage_header(self.dst_logger, 5, "GCE VM 復元（スナップショット → ディスク差し替え）", len(pairs))

        max_age_days = self.config.get('steps', {}).get('gce_snapshot', {}).get('max_age_days', 30)
        proj_map = self._build_proj_id_map()

        # bulk-export は Shared VPC のネットワーク定義を出力しないため、VM を共有
        # サブネットに作成する前に src host の VPC/subnet を dst host へ複製する。
        self._replicate_host_networks()

        for src_proj, dst_proj, src_sa, dst_sa in pairs:
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
                continue
            try:
                vms = json.loads(vm_json)
            except Exception as e:
                self.dst_logger.error(f"    VM JSON 解析失敗: {e}")
                continue

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
                self.dst_logger.error(f"    Snapshot JSON 解析失敗: {e}")
                continue

            for vm in vms:
                vm_name = vm.get('name')
                if not vm_name:
                    continue
                zone = vm.get('zone', '').split('/')[-1]
                machine_type = vm.get('machineType', '').split('/')[-1] or 'e2-micro'
                boot_disk = next((d for d in vm.get('disks', []) if d.get('boot')), None)
                if not boot_disk:
                    continue
                disk_name = boot_disk.get('source', '').split('/')[-1]

                snap_name = self._find_valid_snapshot(snapshots, disk_name, max_age_days)
                if not snap_name:
                    self.dst_logger.error(
                        f"    ✗ {vm_name}: 有効スナップショットが無いため復元できません"
                    )
                    sys.exit(1)

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
                    continue

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
                    explanation="復元ディスクで VM を起動",
                    impersonate_sa=dst_sa,
                )
                self._attach_secondary_disks(
                    vm, vm_name, zone, src_proj, dst_proj, dst_sa, snapshots, max_age_days
                )

        self.dst_logger.info("  ✓ Step 5 完了")

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
        """
        for d in vm.get('disks', []) or []:
            if d.get('boot'):
                continue
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
            self.dst_logger.info(
                f"      セカンダリディスク復元: {src_disk_name} (snap={snap_name})"
            )
            self._create_disk_from_snapshot(src_disk_name, snap_path, zone, dst_proj, dst_sa)
            device_name = d.get('deviceName') or src_disk_name
            mode = d.get('mode') or 'READ_WRITE'
            mode_flag = "--mode=ro" if mode == 'READ_ONLY' else "--mode=rw"
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
