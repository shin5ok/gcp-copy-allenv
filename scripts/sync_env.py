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
import time
import datetime
import threading
import concurrent.futures
from typing import Dict, List, Optional, Any

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
    "bq ls",
    "bq show",
    "bq mk",
    "bq cp",
    "terraform init",
    "terraform plan",
    "terraform apply",
)


# ---------------------------------------------------------------------------
# SA プリフライト: 借用 SA に必要な代表権限（有効ステップごと）
# ---------------------------------------------------------------------------
# test-iam-permissions で検査する代表的な権限。全リソース種は網羅しないが、
# 「Viewer/Editor 相当のロールが付いていない」ケースを実行前に検出するための
# 最小セット。baseline は常に付与し、有効ステップ分を union する。
_SRC_BASELINE_PERMS = ("resourcemanager.projects.get",)
_SRC_PERMS_BY_STEP = {
    "cai_scan":     ("cloudasset.assets.searchAllResources",),
    "gce_snapshot": ("compute.instances.list", "compute.snapshots.list"),
    "bulk_export":  ("cloudasset.assets.searchAllResources",
                     "compute.instances.list", "storage.buckets.list"),
    "gce_restore":  ("compute.instances.list", "compute.snapshots.list"),
    "data_sync":    ("storage.buckets.list", "bigquery.datasets.get"),
}
_DST_BASELINE_PERMS = ("resourcemanager.projects.get",)
_DST_PERMS_BY_STEP = {
    "terraform_apply": ("compute.instances.create", "storage.buckets.create"),
    "gce_restore":     ("compute.instances.start", "compute.instances.stop",
                        "compute.disks.create", "compute.disks.delete",
                        "compute.instances.attachDisk", "compute.instances.detachDisk"),
    "data_sync":       ("storage.objects.create",
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
    return any(cmd.strip().startswith(p) for p in _MOCK_KNOWN_PATTERNS)


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
    ) -> Optional[str]:
        """外部コマンドを安全に実行する。

        Args:
            side: "src" (ORG = read-only 必須) / "dst" (コピー先) / "local" (terraform 等)
            impersonate_sa: 借用 SA。side="src" では必須。
            retries: 失敗時の追加リトライ回数（config-connector 等のフレーキー対策）。
                     リトライ中の失敗は失敗カウントに含めない。
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
                    logger.error(f"{tag}✗ 失敗 (exit={result.returncode})")
                    if result.stderr:
                        logger.error(f"      理由: {result.stderr.strip()[:600]}")
                    self.stats.incr("failed")
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

        if cmd.strip().startswith("gcloud storage buckets list"):
            logger.info(f"{tag}[MOCK] バケット一覧をシミュレート ({proj_id})")
            return "org-bucket-shared-data\norg-assets-bucket\n"

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

        output_dir = self.config.get('steps', {}).get('cai_scan', {}).get('output_dir', './cai_export')
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
        self.org_logger.info("  ✓ Step 1 完了")

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

        output_dir_base = self.config.get('steps', {}).get('bulk_export', {}).get('output_dir', './terraform')
        raw_dir = os.path.join(output_dir_base, 'raw')
        active_dir = os.path.join(output_dir_base, 'active')

        if not self.dry_run:
            # raw 全体を作り直す。過去の mock ダミーや別 config の export 残骸
            # （現 config に無い孤児プロジェクト dir 等）が混ざり、customize/terraform
            # を汚すのを防ぐ。raw は毎回 bulk-export で再生成される派生物。
            if os.path.isdir(raw_dir):
                self.org_logger.info(f"  既存の raw を作り直し: {raw_dir}")
                shutil.rmtree(raw_dir, ignore_errors=True)
            os.makedirs(raw_dir, exist_ok=True)
            os.makedirs(active_dir, exist_ok=True)

        for proj_id, sa in projects:
            self.org_logger.info(f"  → src '{proj_id}' をエクスポート")
            proj_raw_dir = os.path.join(raw_dir, proj_id)
            if not self.dry_run:
                os.makedirs(proj_raw_dir, exist_ok=True)

            if self.mock and not self.dry_run:
                self._write_dummy_tf_files(proj_raw_dir, proj_id)
            else:
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

    # ----- HCL のカスタマイズ（バグ修正版） -----
    def customize_hcl(self, raw_dir: str, active_dir: str):
        self.org_logger.info(f"  HCL カスタマイズ: {raw_dir} → {active_dir}")

        proj_map = self._build_proj_id_map()

        rename_gcs = self.config.get('rename_rules', {}).get('gcs', {})
        gcs_method = rename_gcs.get('method')
        gcs_val = rename_gcs.get('value', '')
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
        """`resource "google_storage_bucket" ...` ブロックの内側にある name 値だけを置換する。"""
        lines = content.split('\n')
        out: List[str] = []
        in_bucket_block = False
        depth = 0
        bucket_block_re = re.compile(r'^\s*resource\s+"google_storage_bucket"')
        name_re = re.compile(r'^(\s*name\s*=\s*)"([^"]+)"\s*$')

        for line in lines:
            if not in_bucket_block:
                if bucket_block_re.search(line):
                    in_bucket_block = True
                    depth = line.count('{') - line.count('}')
                    out.append(line)
                    continue
                out.append(line)
                continue

            # ブロック内
            m = name_re.match(line)
            if m:
                prefix, orig_name = m.group(1), m.group(2)
                if orig_name in overrides:
                    new_name = overrides[orig_name]
                elif method == 'suffix':
                    new_name = f"{orig_name}{value}"
                elif method == 'prefix':
                    new_name = f"{value}{orig_name}"
                else:
                    new_name = orig_name
                self.org_logger.info(f"      bucket リネーム: {orig_name} → {new_name}")
                out.append(f'{prefix}"{new_name}"')
            else:
                out.append(line)

            depth += line.count('{') - line.count('}')
            if depth <= 0:
                in_bucket_block = False

        return '\n'.join(out)

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

        self.dst_logger.info(f"  対象 Terraform ルート: {len(project_dirs)} 件")
        for proj_dir in project_dirs:
            self.dst_logger.info(f"  → Terraform ルート: {proj_dir}")
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

    def _terraform_import_existing(self, proj_dir: str):
        """apply 前に既存リソースを terraform state へ取り込み、再実行を冪等にする。

        bulk-export は各 .tf に `# terraform import <addr> <id>` コメントを残すため、
        それを使って「dst に既に存在するリソース」を adopt する。既に state にある／
        実在しないリソースの import 失敗は無視（best-effort）。
        - 一部リソースは comment の id 形式が古い/不正なので補正する:
          * google_project_iam_custom_role は `proj##role` → `projects/proj/roles/role`
        - google_storage_bucket は名前変更しており comment の id が一致しないため除外
          （存在しなければ新規作成、存在すれば apply で衝突しないよう別途リネーム済み）。
        """
        import glob
        pairs: List[tuple] = []
        for tf in sorted(glob.glob(os.path.join(proj_dir, "*.tf"))):
            try:
                with open(tf, encoding="utf-8") as f:
                    for line in f:
                        m = re.match(r'#\s*terraform import\s+(\S+)\s+(.+?)\s*$', line)
                        if m:
                            pairs.append((m.group(1), m.group(2)))
            except OSError:
                continue
        if not pairs:
            return
        self.dst_logger.info(f"    既存リソースの取り込みを試行: {len(pairs)} 件")
        imported = 0
        skipped_already = 0
        failed: List[tuple] = []
        for addr, imp_id in pairs:
            if addr.startswith("google_storage_bucket."):
                continue  # リネーム済みのため comment id は不一致。新規作成に任せる。
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
    def step_gce_restore(self):
        pairs = list(self._iter_project_pairs())
        log_stage_header(self.dst_logger, 5, "GCE VM 復元（スナップショット → ディスク差し替え）", len(pairs))

        max_age_days = self.config.get('steps', {}).get('gce_snapshot', {}).get('max_age_days', 30)

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
                if not vm_name or not vm_name.startswith('org-'):
                    self.dst_logger.info(f"    管理対象外 VM をスキップ: {vm_name}")
                    continue
                zone = vm.get('zone', '').split('/')[-1]
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
                self.dst_logger.info(
                    f"    {vm_name} を復元 (zone={zone}, snap={snap_name})"
                )

                # 既存 VM の停止・既存ディスクのデタッチと削除
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
                    explanation="Terraform で作成した空ディスクを削除",
                    impersonate_sa=dst_sa, allow_fail=True,
                )

                # スナップショットからディスクを復元（src snapshot を read で参照）
                snap_path = f"projects/{src_proj}/global/snapshots/{snap_name}"
                self.run_command(
                    f"gcloud compute disks create {dst_disk_name} "
                    f"--source-snapshot={snap_path} --zone={zone} --project={dst_proj} --quiet",
                    side="dst", logger=self.dst_logger,
                    desc=f"Create disk {dst_disk_name}",
                    explanation=f"src snapshot {snap_name} から dst にクローンディスクを作成",
                    impersonate_sa=dst_sa,
                )
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

        self.dst_logger.info("  ✓ Step 5 完了")

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
        gcs_val = rename_gcs.get('value', '')
        gcs_overrides = rename_gcs.get('overrides', {}) or {}

        for src_proj, dst_proj, src_sa, dst_sa in pairs:
            self.dst_logger.info(f"  → {src_proj} → {dst_proj}")
            self._sync_gcs(src_proj, dst_proj, src_sa, dst_sa, gcs_method, gcs_val, gcs_overrides)
            self._sync_bq(src_proj, dst_proj, src_sa, dst_sa)
        self.dst_logger.info("  ✓ Step 6 完了")

    def _sync_gcs(self, src_proj, dst_proj, src_sa, dst_sa, method, value, overrides):
        self.dst_logger.info("  [GCS] バケット同期")
        buckets_str = self.run_command(
            f"gcloud storage buckets list --project={src_proj} --format='value(name)'",
            side="src", logger=self.org_logger,
            desc=f"List Src Buckets {src_proj}",
            explanation=f"{src_proj} のバケット一覧を取得",
            impersonate_sa=src_sa, allow_fail=True,
        )
        if not buckets_str:
            self.dst_logger.info("    バケット無し / 取得失敗")
            return
        # customize_hcl と同じ規則で dst バケット名を算出する:
        #   1) バケット名に含まれる src プロジェクト ID を dst ID に置換（単語境界）
        #   2) override / suffix / prefix のリネーム規則を適用
        # （Step 4 が作る dst バケット名と一致させないと rsync 先が 404 になる）
        proj_map = self._build_proj_id_map()
        for orig in [b.strip() for b in buckets_str.split('\n') if b.strip()]:
            base = orig
            for s in sorted(proj_map.keys(), key=len, reverse=True):
                base = re.sub(
                    rf'(?<![A-Za-z0-9_-]){re.escape(s)}(?![A-Za-z0-9_-])',
                    proj_map[s], base,
                )
            if base in overrides:
                dst_bucket = overrides[base]
            elif method == 'suffix':
                dst_bucket = f"{base}{value}"
            elif method == 'prefix':
                dst_bucket = f"{value}{base}"
            else:
                dst_bucket = base
            self.dst_logger.info(f"    gs://{orig} → gs://{dst_bucket}")
            self.run_command(
                f"gcloud storage rsync gs://{orig} gs://{dst_bucket} --recursive --project={dst_proj}",
                side="dst", logger=self.dst_logger,
                desc=f"GCS Rsync {orig}",
                explanation=f"src バケットから dst バケットにデータ同期",
                impersonate_sa=dst_sa,
            )

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
            for t in tables:
                t_id = t.get('tableReference', {}).get('tableId')
                if not t_id:
                    continue
                self.dst_logger.info(f"      Table: {ds_id}.{t_id}")
                self.run_command(
                    f"bq cp --force {src_proj}:{ds_id}.{t_id} {dst_proj}:{ds_id}.{t_id}",
                    side="dst", logger=self.dst_logger,
                    desc=f"BQ Cp {ds_id}.{t_id}",
                    explanation=f"テーブルを src → dst にコピー（同一 location 必要）",
                    impersonate_sa=dst_sa,
                )


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


if __name__ == "__main__":
    main()
