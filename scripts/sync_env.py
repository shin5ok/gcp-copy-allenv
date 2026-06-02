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
    ) -> Optional[str]:
        """外部コマンドを安全に実行する。

        Args:
            side: "src" (ORG = read-only 必須) / "dst" (コピー先) / "local" (terraform 等)
            impersonate_sa: 借用 SA。side="src" では必須。
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

        try:
            result = subprocess.run(
                cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, cwd=cwd, env=env,
            )
            if result.returncode != 0:
                logger.error(f"{tag}✗ 失敗 (exit={result.returncode})")
                if result.stderr:
                    logger.error(f"      理由: {result.stderr.strip()[:600]}")
                if not allow_fail:
                    self.stats.incr("failed")
                    sys.exit(result.returncode)
                self.stats.incr("failed")
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
                cmd = (
                    f"gcloud beta resource-config bulk-export "
                    f"--project={proj_id} --resource-format=terraform --path={proj_raw_dir}"
                )
                self.run_command(
                    cmd, side="src", logger=self.org_logger,
                    desc=f"Bulk Export {proj_id}",
                    explanation=f"{proj_id} のリソース定義を Terraform HCL としてエクスポート",
                    impersonate_sa=sa, allow_fail=True,
                )

        self.customize_hcl(raw_dir, active_dir)
        self.org_logger.info("  ✓ Step 3 完了")

    # ----- HCL のカスタマイズ（バグ修正版） -----
    def customize_hcl(self, raw_dir: str, active_dir: str):
        self.org_logger.info(f"  HCL カスタマイズ: {raw_dir} → {active_dir}")

        mapping = self.config.get('project_mapping', {})
        proj_map: Dict[str, str] = {}
        host = mapping.get('host_project', {})
        if host.get('src') and host.get('dst'):
            proj_map[host['src']] = host['dst']
        for svc in mapping.get('service_projects', []):
            if svc.get('src') and svc.get('dst'):
                proj_map[svc['src']] = svc['dst']

        rename_gcs = self.config.get('rename_rules', {}).get('gcs', {})
        gcs_method = rename_gcs.get('method')
        gcs_val = rename_gcs.get('value', '')
        gcs_overrides = rename_gcs.get('overrides', {}) or {}

        if not os.path.isdir(raw_dir):
            self.org_logger.warning(f"  raw_dir が存在しないため HCL カスタマイズをスキップ: {raw_dir}")
            return

        for root, _, files in os.walk(raw_dir):
            for file in files:
                if not file.endswith('.tf'):
                    continue
                raw_path = os.path.join(root, file)
                rel = os.path.relpath(raw_path, raw_dir)
                active_path = os.path.join(active_dir, rel)
                if not self.dry_run:
                    os.makedirs(os.path.dirname(active_path), exist_ok=True)

                self.org_logger.info(f"    処理中: {rel}")
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

                    # 2. google_storage_bucket リソース「ブロック内」の name のみリネーム。
                    #    以前のバグ: ファイル単位で全 name を書き換えていたため VM/FW 等も
                    #    suffix が付与されてしまっていた。
                    content = self._rename_bucket_names_in_blocks(
                        content, gcs_method, gcs_val, gcs_overrides,
                    )

                    # 3. VM の boot_disk.source 行を削除（スナップショット復元前提）
                    content = self._strip_boot_disk_source(content, rel)

                    with open(active_path, 'w', encoding='utf-8') as f:
                        f.write(content)
                except Exception as e:
                    self.org_logger.error(f"    HCL カスタマイズ失敗 {raw_path}: {e}")
                    sys.exit(1)

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
            else:
                self.dst_logger.error(f"  active_dir が無いため停止: {active_dir}")
                sys.exit(1)

        self.run_command(
            "terraform init", side="local", logger=self.dst_logger,
            desc="TF Init", explanation="Terraform 初期化",
            cwd=active_dir,
        )
        self.run_command(
            "terraform plan -out=tfplan", side="local", logger=self.dst_logger,
            desc="TF Plan", explanation="差分を tfplan に保存して内容を確認可能に",
            cwd=active_dir,
        )
        self.dst_logger.info("  → tfplan を生成しました。dry_run でない場合のみ apply します。")
        if not self.dry_run:
            self.run_command(
                "terraform apply -auto-approve tfplan", side="local", logger=self.dst_logger,
                desc="TF Apply", explanation="先ほど作成した tfplan を適用",
                cwd=active_dir,
            )
        self.dst_logger.info("  ✓ Step 4 完了")

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
        for orig in [b.strip() for b in buckets_str.split('\n') if b.strip()]:
            if orig in overrides:
                dst_bucket = overrides[orig]
            elif method == 'suffix':
                dst_bucket = f"{orig}{value}"
            elif method == 'prefix':
                dst_bucket = f"{value}{orig}"
            else:
                dst_bucket = orig
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
