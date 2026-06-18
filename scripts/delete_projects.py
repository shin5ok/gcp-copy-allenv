#!/usr/bin/env python3
"""copy-all-env が作成した dst プロジェクトのみを対象に一括削除する。

削除対象の決定:
- `--config` (既定 `dst/config.yaml`) の `project_mapping.host_project.dst` と
  `project_mapping.service_projects[].dst` を母集団とする。
- `--pattern` でその母集団をさらに project_id 部分一致で絞り込む（3 文字以上必須）。
- このため、config に無い「他人のプロジェクト」「無関係なプロジェクト」は誤って消えない。

安全策:
- パターンは 3 文字以上必須（誤爆防止）
- `--no-dry-run` 時は 6 桁ランダムコードを端末に表示し、ユーザー入力と一致しないと進まない
- lien が付いている場合は先に削除してから `projects delete` を行う
- 削除前に対象一覧をテーブル形式で出力（src→dst 対応・lien 数・状態）

ログは scripts/create_projects.py と揃え、logs/<ts>_delete-projects/dst.log に書く。
"""
import argparse
import concurrent.futures
import datetime
import json
import logging
import os
import secrets
import string
import subprocess
import sys
import threading
from typing import Any, Dict, List, Optional, Tuple

import yaml


MIN_PATTERN_LEN = 3


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
        "[%(asctime)s] [%(levelname)s] [%(thread_tag)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh = logging.FileHandler(filepath, mode="w", encoding="utf-8")
    fh.setFormatter(fmt)
    fh.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.addHandler(fh)

    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    ch.setLevel(logging.INFO)
    logger.addHandler(ch)
    return logger


def _load_config(config_path: str) -> Dict[str, Any]:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _gen_confirmation_code() -> str:
    return "".join(secrets.choice(string.digits) for _ in range(6))


def _collect_dst_entries(config: Dict[str, Any]) -> List[Dict[str, str]]:
    mapping = config.get("project_mapping", {}) or {}
    out: List[Dict[str, str]] = []
    host = mapping.get("host_project", {}) or {}
    if isinstance(host, dict) and host.get("dst"):
        out.append({"kind": "host", "dst": host["dst"], "src": host.get("src", "")})
    for svc in mapping.get("service_projects", []) or []:
        if isinstance(svc, dict) and svc.get("dst"):
            out.append({"kind": "svc", "dst": svc["dst"], "src": svc.get("src", "")})
    return out


class ProjectDeleter:
    def __init__(
        self,
        pattern: str,
        dry_run: bool,
        config_path: str,
        verbose: bool = True,
        yes_code: Optional[str] = None,
        stdin=sys.stdin,
    ):
        self.pattern = pattern
        self.dry_run = dry_run
        self.config_path = config_path
        self.verbose = verbose
        self.yes_code = yes_code
        self.stdin = stdin

        self.config: Dict[str, Any] = {}
        self.logger: Optional[logging.Logger] = None
        self.run_dir: str = ""
        self.parallel_jobs: int = 8

        self._lock = threading.Lock()
        self.deleted = 0
        self.liens_removed = 0
        self.failed = 0

    def _setup(self) -> None:
        global_cfg = self.config.get("global", {}) or {}
        base_log_dir = global_cfg.get("log_dir", "./logs")
        self.parallel_jobs = int(global_cfg.get("parallel_jobs", 8))

        timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        self.run_dir = os.path.join(base_log_dir, f"{timestamp}_delete-projects")
        os.makedirs(self.run_dir, exist_ok=True)
        log_name = global_cfg.get("dst_log_file", "dst.log")
        self.logger = setup_logger(
            "dst_delete_projects",
            os.path.join(self.run_dir, log_name),
            self.verbose,
        )

    def _run(
        self,
        argv: List[str],
        desc: str,
        explanation: str = "",
        allow_fail: bool = False,
        read_only: bool = False,
    ) -> Tuple[int, str, str]:
        tag = f"[{desc}] " if desc else ""
        if explanation:
            self.logger.info(f"{tag}[実行内容] {explanation}")
        if self.dry_run and not read_only:
            self.logger.info(f"{tag}[DRY RUN] 予定: {' '.join(argv)}")
            return 0, "", ""
        if self.verbose:
            self.logger.info(f"{tag}実行: {' '.join(argv)}")
        try:
            res = subprocess.run(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                shell=False,
            )
        except Exception as e:
            self.logger.error(f"{tag}例外: {e}")
            if allow_fail:
                return 1, "", str(e)
            raise
        if res.returncode != 0 and not allow_fail:
            self.logger.error(f"{tag}✗ 失敗 (exit={res.returncode}) {res.stderr.strip()}")
        return res.returncode, (res.stdout or "").strip(), (res.stderr or "").strip()

    def _describe_project(self, pid: str) -> Optional[Dict[str, Any]]:
        rc, out, err = self._run(
            ["gcloud", "projects", "describe", pid, "--format=json"],
            desc=f"describe:{pid}",
            explanation=f"{pid} の存在 / 状態を確認",
            allow_fail=True,
            read_only=True,
        )
        if rc != 0:
            return None
        try:
            return json.loads(out) if out else None
        except json.JSONDecodeError:
            return None

    def _list_liens(self, project_id: str) -> List[str]:
        for track in ("alpha", "beta"):
            rc, out, _ = self._run(
                [
                    "gcloud", track, "resource-manager", "liens", "list",
                    f"--project={project_id}",
                    "--format=json",
                ],
                desc=f"liens-list:{project_id}",
                explanation=f"{project_id} の lien を列挙",
                allow_fail=True,
                read_only=True,
            )
            if rc == 0:
                try:
                    data = json.loads(out) if out else []
                except json.JSONDecodeError:
                    return []
                names = []
                for item in data:
                    if not isinstance(item, dict):
                        continue
                    name = item.get("name") or ""
                    if name.startswith("liens/"):
                        name = name[len("liens/"):]
                    if name:
                        names.append(name)
                return names
        self.logger.warning(f"[{project_id}] lien の確認に失敗。lien なしとみなして続行します。")
        return []

    def _delete_one(self, entry: Dict[str, Any], liens: List[str]) -> bool:
        pid = entry["dst"]
        ok = True
        for lien in liens:
            rc, _, err = self._run(
                ["gcloud", "alpha", "resource-manager", "liens", "delete", lien],
                desc=f"lien-del:{pid}",
                explanation=f"{pid} の lien {lien} を削除",
                allow_fail=True,
            )
            if rc != 0:
                self.logger.error(f"[{pid}] lien 削除失敗 ({lien}): {err}")
                ok = False
            else:
                with self._lock:
                    self.liens_removed += 1
        if not ok:
            return False
        rc, _, err = self._run(
            ["gcloud", "projects", "delete", pid, "--quiet"],
            desc=f"proj-del:{pid}",
            explanation=f"{pid} を削除",
            allow_fail=True,
        )
        if rc != 0:
            self.logger.error(f"[{pid}] 削除失敗: {err}")
            return False
        with self._lock:
            self.deleted += 1
        return True

    def _print_table(
        self,
        rows: List[Dict[str, Any]],
        total_config: int,
    ) -> None:
        headers = ["#", "kind", "project_id (dst)", "name", "state", "lien", "src project"]
        widths = [len(h) for h in headers]
        body: List[List[str]] = []
        for i, r in enumerate(rows, 1):
            body.append([
                str(i),
                r["kind"],
                r["dst"],
                r.get("name", "") or "",
                r.get("state", "") or "",
                str(r.get("liens_count", 0)),
                r.get("src", "") or "",
            ])
        for row in body:
            for i, cell in enumerate(row):
                widths[i] = max(widths[i], len(cell))

        def fmt(cells: List[str]) -> str:
            parts = []
            for i, c in enumerate(cells):
                if i in (0, 5):
                    parts.append(c.rjust(widths[i]))
                else:
                    parts.append(c.ljust(widths[i]))
            return "  " + " | ".join(parts)

        bar = "━" * 78
        self.logger.info("")
        self.logger.info(bar)
        self.logger.info(f"  削除対象プロジェクト ({len(rows)} 件 / config dst {total_config} 件中)")
        self.logger.info(f"  config  = {self.config_path}")
        self.logger.info(f"  pattern = '{self.pattern}'")
        self.logger.info(bar)
        self.logger.info(fmt(headers))
        self.logger.info("  " + "-+-".join("-" * w for w in widths))
        for row in body:
            self.logger.info(fmt(row))
        self.logger.info(bar)

    def _confirm(self) -> bool:
        code = _gen_confirmation_code()
        self.logger.info("")
        self.logger.info("================================================================")
        self.logger.info("  ⚠️  上記プロジェクトを削除します。続行するには下のコードを入力してください。")
        self.logger.info(f"  確認コード: {code}")
        self.logger.info("================================================================")
        if self.yes_code is not None:
            entered = self.yes_code.strip()
            self.logger.info(f"--yes で渡されたコード: {'*' * len(entered)}")
        else:
            try:
                entered = self.stdin.readline().strip()
            except (EOFError, KeyboardInterrupt):
                entered = ""
        if entered != code:
            self.logger.error("✗ コード不一致。削除を中止します。")
            return False
        self.logger.info("✓ コード一致。削除を実行します。")
        return True

    def run(self) -> int:
        if not self.pattern or len(self.pattern) < MIN_PATTERN_LEN:
            print(
                f"ERROR: --pattern は {MIN_PATTERN_LEN} 文字以上を指定してください (与えられた値: '{self.pattern}')",
                file=sys.stderr,
            )
            return 2

        if not os.path.exists(self.config_path):
            print(
                f"ERROR: config が見つかりません: {self.config_path}\n"
                "  copy-all-env が作成した dst プロジェクト一覧を取得するため config が必須です。",
                file=sys.stderr,
            )
            return 2
        try:
            self.config = _load_config(self.config_path)
        except Exception as e:
            print(f"ERROR: config 読み込み失敗: {e}", file=sys.stderr)
            return 2

        all_dst = _collect_dst_entries(self.config)
        if not all_dst:
            print(
                f"ERROR: config の project_mapping に dst が定義されていません: {self.config_path}",
                file=sys.stderr,
            )
            return 2

        self._setup()
        self.logger.info("=" * 60)
        self.logger.info(" copy-all-env  delete-projects  開始")
        self.logger.info("=" * 60)
        self.logger.info(f"  config       = {self.config_path}")
        self.logger.info(f"  pattern      = '{self.pattern}'")
        self.logger.info(f"  config dst   = {len(all_dst)} 件")
        self.logger.info(f"  dry_run      = {self.dry_run}")
        self.logger.info(f"  parallel     = {self.parallel_jobs}")
        self.logger.info(f"  ログ         = {self.run_dir}")

        candidates = [e for e in all_dst if self.pattern in e["dst"]]
        skipped_no_match = [e for e in all_dst if self.pattern not in e["dst"]]

        rows: List[Dict[str, Any]] = []
        skipped: List[Tuple[str, str]] = [(e["dst"], "pattern に一致しません") for e in skipped_no_match]

        for e in candidates:
            pid = e["dst"]
            desc = self._describe_project(pid)
            if desc is None:
                skipped.append((pid, "存在しない / アクセス不可"))
                continue
            state = desc.get("lifecycleState", "ACTIVE")
            if state != "ACTIVE":
                skipped.append((pid, f"lifecycleState={state}"))
                continue
            liens = self._list_liens(pid)
            rows.append({
                **e,
                "name": desc.get("name", ""),
                "state": state,
                "liens": liens,
                "liens_count": len(liens),
            })

        if not rows:
            self.logger.info("")
            self.logger.info(
                f"削除対象は 0 件です（pattern '{self.pattern}' に一致する config dst が無い、"
                "または既に削除済 / アクセス不可）。"
            )
            if skipped:
                self.logger.info("内訳:")
                for pid, reason in skipped:
                    self.logger.info(f"  - {pid}  ({reason})")
            return 0

        self._print_table(rows, total_config=len(all_dst))
        if skipped:
            self.logger.info("")
            self.logger.info(f"スキップ ({len(skipped)} 件):")
            for pid, reason in skipped:
                self.logger.info(f"  - {pid}  ({reason})")

        if self.dry_run:
            self.logger.info("")
            self.logger.info("[DRY RUN] 実削除は行いません。--no-dry-run を付けて再実行してください。")
            return 0

        if not self._confirm():
            return 1

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=max(1, self.parallel_jobs),
            thread_name_prefix="del",
        ) as ex:
            list(ex.map(lambda r: self._delete_one(r, r["liens"]), rows))

        with self._lock:
            self.failed = len(rows) - self.deleted

        bar = "━" * 60
        self.logger.info("")
        self.logger.info(bar)
        self.logger.info(" サマリー")
        self.logger.info(bar)
        self.logger.info(f"  削除済   : {self.deleted} 件")
        self.logger.info(f"  lien 解除: {self.liens_removed} 件")
        self.logger.info(f"  失敗     : {self.failed} 件")
        self.logger.info(f"  ログ     : {self.run_dir}")
        self.logger.info(bar)
        return 0 if self.failed == 0 else 1


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="copy-all-env (dst/config.yaml) が作成した dst プロジェクトのみを 6 桁コード確認のうえ削除",
    )
    parser.add_argument("--pattern", required=True, help=f"project_id 部分一致 ({MIN_PATTERN_LEN} 文字以上)")
    parser.add_argument("--config", default="dst/config.yaml", help="dst 一覧 / log_dir / parallel_jobs を読む config")
    parser.add_argument("--dry-run", action="store_true", default=True, help="一覧表示のみ (default)")
    parser.add_argument("--no-dry-run", action="store_false", dest="dry_run", help="実削除")
    parser.add_argument("--yes", default=None, help="対話コード入力の代替（表示コードと一致する必要あり）")
    parser.add_argument("--no-verbose", action="store_false", dest="verbose", default=True, help="詳細ログ無効")
    args = parser.parse_args(argv)

    d = ProjectDeleter(
        pattern=args.pattern,
        dry_run=args.dry_run,
        config_path=args.config,
        verbose=args.verbose,
        yes_code=args.yes,
    )
    return d.run()


if __name__ == "__main__":
    sys.exit(main())
