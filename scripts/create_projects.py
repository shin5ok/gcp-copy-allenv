#!/usr/bin/env python3
"""コピー先プロジェクトのプロビジョニング。

- このスクリプトは ORG プロジェクトには一切触れず、dst プロジェクトの
  作成 / billing 紐付け / API 有効化のみ行う。
- ログは sync_env.py と同じ logs/<timestamp>/dst.log 系統に書き出す。
"""
import argparse
import sys
import os
import yaml
import logging
import subprocess
import datetime
import threading
from typing import Dict, Any, List, Optional


class _ThreadTagFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        tname = threading.current_thread().name
        record.thread_tag = "main" if tname == "MainThread" else tname
        return True


def setup_logger(name: str, filepath: str, verbose: bool) -> logging.Logger:
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
    ch.setLevel(logging.INFO)
    logger.addHandler(ch)
    return logger


class ProjectProvisioner:
    def __init__(
        self,
        config_path: str,
        dry_run_override: Optional[bool] = None,
        verbose_override: Optional[bool] = None,
    ):
        self.config_path = config_path
        self.config: Dict[str, Any] = {}
        self.logger: Optional[logging.Logger] = None
        self.dry_run = True
        self.verbose = True
        self.dry_run_override = dry_run_override
        self.verbose_override = verbose_override
        self.run_dir: str = ""
        # 統計
        self.created = 0
        self.skipped = 0
        self.failed = 0

    def load_config(self):
        if not os.path.exists(self.config_path):
            print(f"エラー: 設定ファイル {self.config_path} が見つかりません。", file=sys.stderr)
            sys.exit(1)
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                self.config = yaml.safe_load(f)
        except Exception as e:
            print(f"エラー: 設定ファイル解析失敗: {e}", file=sys.stderr)
            sys.exit(1)

        global_cfg = self.config.get('global', {})
        self.dry_run = global_cfg.get('dry_run', True)
        self.verbose = global_cfg.get('verbose_logging', True)
        if self.dry_run_override is not None:
            self.dry_run = self.dry_run_override
        if self.verbose_override is not None:
            self.verbose = self.verbose_override

        # ORG 保護: dst が src と衝突していないか最低限チェック
        errors = self._validate_mapping()
        if errors:
            print("[ORG 保護] config.yaml に問題があります:", file=sys.stderr)
            for e in errors:
                print(f"  - {e}", file=sys.stderr)
            sys.exit(1)

        # 実行ごとの logs/<timestamp>/dst.log
        base_log_dir = global_cfg.get('log_dir', './logs')
        timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        self.run_dir = os.path.join(base_log_dir, f"{timestamp}_create-projects")
        os.makedirs(self.run_dir, exist_ok=True)
        dst_log_name = global_cfg.get('dst_log_file', 'dst.log')
        self.logger = setup_logger(
            'dst_provision', os.path.join(self.run_dir, dst_log_name), self.verbose,
        )

    def _validate_mapping(self) -> List[str]:
        errors: List[str] = []
        mapping = self.config.get('project_mapping', {})
        if not mapping:
            return ["project_mapping が定義されていません"]
        entries = []
        host = mapping.get('host_project', {})
        if isinstance(host, dict):
            entries.append(("host_project", host))
        for i, svc in enumerate(mapping.get('service_projects', []) or []):
            if isinstance(svc, dict):
                entries.append((f"service_projects[{i}]", svc))

        src_ids = {e.get('src') for _, e in entries if e.get('src')}
        for label, ent in entries:
            src = ent.get('src')
            dst = ent.get('dst')
            if not dst:
                errors.append(f"{label}: dst が未指定")
            if src and dst and src == dst:
                errors.append(f"{label}: src と dst が同一 ({src})")
            if dst and dst in src_ids:
                errors.append(f"{label}: dst '{dst}' が src と一致（ORG を上書きするリスク）")
        return errors

    def run_command(self, cmd: str, desc: str = "", explanation: str = "",
                    allow_fail: bool = False, read_only: bool = False) -> Optional[str]:
        tag = f"[{desc}] " if desc else ""
        if explanation:
            self.logger.info(f"{tag}[実行内容] {explanation}")

        if self.dry_run and not read_only:
            self.logger.info(f"{tag}[DRY RUN] 予定: {cmd}")
            return ""

        if self.verbose:
            self.logger.info(f"{tag}実行: {cmd}")

        try:
            result = subprocess.run(
                cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            if result.returncode != 0:
                if not allow_fail:
                    self.logger.error(f"{tag}✗ 失敗 (exit={result.returncode})")
                    if result.stderr:
                        self.logger.error(f"      理由: {result.stderr.strip()[:400]}")
                    self.failed += 1
                    sys.exit(result.returncode)
                else:
                    return None
            return result.stdout.strip()
        except Exception as e:
            self.logger.error(f"{tag}例外: {e}")
            if not allow_fail:
                sys.exit(1)
            return None

    def project_exists(self, project_id: str) -> bool:
        res = self.run_command(
            f"gcloud projects describe {project_id} --format=json",
            desc=f"Check {project_id}",
            explanation=f"プロジェクト {project_id} が既に存在するか確認",
            allow_fail=True, read_only=True,
        )
        return res is not None and res != ""

    def provision(self):
        self.load_config()
        self.logger.info("=" * 60)
        self.logger.info(" copy-all-env  create-projects  開始")
        self.logger.info("=" * 60)
        self.logger.info(f"  dry_run = {self.dry_run}")
        self.logger.info(f"  ログ出力先 = {self.run_dir}")

        bootstrap = self.config.get('bootstrap', {})
        org_id = bootstrap.get('org_id')
        folder_id = bootstrap.get('folder_id')
        billing = bootstrap.get('billing_account')
        if not billing:
            self.logger.error("bootstrap.billing_account は必須です。")
            sys.exit(1)
        if not org_id and not folder_id:
            self.logger.error("bootstrap.org_id または bootstrap.folder_id のいずれかが必須です。")
            sys.exit(1)

        mapping = self.config.get('project_mapping', {})
        projects: List[str] = []
        host = mapping.get('host_project', {})
        if host.get('dst'):
            projects.append(host['dst'])
        for svc in mapping.get('service_projects', []) or []:
            if svc.get('dst'):
                projects.append(svc['dst'])
        if not projects:
            self.logger.info("dst プロジェクトが mapping にありません。何もしません。")
            return

        for pid in projects:
            self.logger.info(f"  → {pid}")
            if self.project_exists(pid):
                self.logger.info(f"    ✓ {pid} は既存。作成をスキップ")
                self.skipped += 1
            else:
                cmd = f"gcloud projects create {pid}"
                if folder_id:
                    cmd += f" --folder={folder_id}"
                elif org_id:
                    cmd += f" --organization={org_id}"
                self.run_command(cmd, desc=f"Create {pid}",
                                 explanation=f"新規 dst プロジェクト {pid} を作成")
                self.created += 1

            self.run_command(
                f"gcloud beta billing projects link {pid} --billing-account={billing}",
                desc=f"Link billing {pid}",
                explanation=f"請求アカウント {billing} を {pid} に紐付け",
            )
            # Terraform の google_project_service / data ソースが依存する基盤 API も
            # 必ず有効化する。CRM/ServiceUsage が無効だと sync_env.py の Step 4 で
            # "Cloud Resource Manager API has not been used in project ... before" の
            # 403 が出て apply が止まる。
            prereq_apis = " ".join([
                "compute.googleapis.com",
                "dns.googleapis.com",
                "cloudresourcemanager.googleapis.com",
                "serviceusage.googleapis.com",
                "iam.googleapis.com",
                "iamcredentials.googleapis.com",
            ])
            self.run_command(
                f"gcloud services enable {prereq_apis} --project={pid}",
                desc=f"Enable APIs {pid}",
                explanation=f"{pid} で Terraform 必須 API を有効化",
            )

        # サマリ
        bar = "━" * 60
        self.logger.info("")
        self.logger.info(bar)
        self.logger.info(" サマリー")
        self.logger.info(bar)
        self.logger.info(f"  新規作成 : {self.created} 件")
        self.logger.info(f"  スキップ : {self.skipped} 件")
        self.logger.info(f"  失敗     : {self.failed} 件")
        self.logger.info(f"  ログ     : {self.run_dir}")
        self.logger.info(bar)


def main():
    parser = argparse.ArgumentParser(description="コピー先プロジェクトのプロビジョニング")
    parser.add_argument("--config", default="dst/config.yaml", help="config.yaml のパス")
    parser.add_argument("--dry-run", action="store_true", default=None, help="ドライランモード")
    parser.add_argument("--no-dry-run", action="store_false", dest="dry_run", help="本番実行")
    parser.add_argument("--verbose", action="store_true", default=None, help="詳細ログ")
    parser.add_argument("--no-verbose", action="store_false", dest="verbose", help="詳細ログ無効")
    args = parser.parse_args()

    p = ProjectProvisioner(
        config_path=args.config,
        dry_run_override=args.dry_run,
        verbose_override=args.verbose,
    )
    p.provision()


if __name__ == "__main__":
    main()
