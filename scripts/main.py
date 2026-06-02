#!/usr/bin/env python3
import argparse
import yaml
import os
import sys
from typing import Dict, Any

# scripts ディレクトリをパスに追加して自作モジュールをロード可能にする
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import utils
from utils import log_step, context
import cai_scan
import gce_snapshot

def load_config(config_path: str) -> Dict[str, Any]:
    """YAML設定ファイルを読み込む"""
    if not os.path.exists(config_path):
        print(f"エラー: 設定ファイル {config_path} が存在しません。", file=sys.stderr)
        print("dst/config.yaml.template からコピーして作成してください。", file=sys.stderr)
        sys.exit(1)
        
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
            if not config:
                raise ValueError("ファイルが空です")
            return config
    except Exception as e:
        print(f"エラー: 設定ファイルの読み込みに失敗しました ({config_path}): {str(e)}", file=sys.stderr)
        sys.exit(1)

def validate_config(config: Dict[str, Any]):
    """設定の最低限のバリデーション"""
    # project_mapping の必須チェック
    mapping = config.get("project_mapping")
    if not mapping:
        print("エラー: 設定ファイルに 'project_mapping' が定義されていません。", file=sys.stderr)
        sys.exit(1)
        
    host = mapping.get("host_project")
    if not host or not host.get("src") or not host.get("dst"):
        print("エラー: 'project_mapping.host_project' の定義が不完全です。", file=sys.stderr)
        sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description="GCP Project Clone Tool (Terraform Base)")
    parser.add_argument(
        "--config",
        default="dst/config.yaml",
        help="設定ファイル (config.yaml) のパス (デフォルト: dst/config.yaml)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="ドライランモードを実行します (実際のリソース変更を行いません)"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="詳細な外部コマンド実行ログを出力します"
    )
    args = parser.parse_args()

    # 1. 設定の読み込みと検証
    config = load_config(args.config)
    validate_config(config)

    # 2. グローバル設定およびロガーの初期化
    global_cfg = config.get("global", {})
    log_dir = global_cfg.get("log_dir", "./logs")
    
    # デフォルトの基本ログファイル
    log_file = "migration.log"
    logger = utils.setup_logger(log_dir, log_file)

    # コンテキストのセットアップ
    # コマンドライン引数を設定ファイルより優先する
    context.dry_run = args.dry_run if args.dry_run else global_cfg.get("dry_run", True)
    context.verbose_logging = args.verbose if args.verbose else global_cfg.get("verbose_logging", True)
    context.logger = logger

    log_step("main", "init", "==================================================")
    log_step("main", "init", "GCP Project Clone Tool (Terraform Base) 起動")
    log_step("main", "init", "==================================================")
    log_step("main", "init", f"設定ファイル: {args.config}")
    log_step("main", "init", f"ドライランモード: {context.dry_run}")
    log_step("main", "init", f"詳細ログ出力: {context.verbose_logging}")

    # 3. 移行ステップの順次実行制御
    steps_cfg = config.get("steps", {})
    
    # [Step 1] CAI Scan (現状確認)
    if steps_cfg.get("cai_scan", {}).get("enabled", True):
        log_step("main", "step_control", ">>> [Step 1] CAI現状確認 を開始します...")
        cai_scan.run_cai_scan(config)
        log_step("main", "step_control", "<<< [Step 1] CAI現状確認 完了")
    else:
        log_step("main", "step_control", "--- [Step 1] CAI現状確認 はスキップされました")

    # [Step 2] GCE Snapshot (バックアップ検証)
    if steps_cfg.get("gce_snapshot", {}).get("enabled", True):
        log_step("main", "step_control", ">>> [Step 2] GCEスナップショット検証 を開始します...")
        gce_snapshot.run_gce_snapshot_check(config)
        log_step("main", "step_control", "<<< [Step 2] GCEスナップショット検証 完了")
    else:
        log_step("main", "step_control", "--- [Step 2] GCEスナップショット検証 はスキップされました")

    # [Step 3] Bulk Export (Terraformコード生成)
    if steps_cfg.get("bulk_export", {}).get("enabled", True):
        log_step("main", "step_control", ">>> [Step 3] Terraformエクスポート を開始します...")
        # TODO: bulk_export の呼び出し
        log_step("main", "step_control", "<<< [Step 3] Terraformエクスポート プレースホルダー完了")
    
    # [Step 4] Terraform Apply (コピー先インフラ再現)
    if steps_cfg.get("terraform_apply", {}).get("enabled", True):
        log_step("main", "step_control", ">>> [Step 4] Terraform適用 を開始します...")
        # TODO: terraform_apply の呼び出し
        log_step("main", "step_control", "<<< [Step 4] Terraform適用 プレースホルダー完了")

    # [Step 5] GCE VM 復元 (ディスク紐付け)
    if steps_cfg.get("gce_restore", {}).get("enabled", True):
        log_step("main", "step_control", ">>> [Step 5] GCE VMデータ復元 を開始します...")
        # TODO: gce_restore の呼び出し
        log_step("main", "step_control", "<<< [Step 5] GCE VMデータ復元 プレースホルダー完了")

    # [Step 6] データ同期 (GCS, BQ)
    if steps_cfg.get("data_sync", {}).get("enabled", True):
        log_step("main", "step_control", ">>> [Step 6] データ移行同期 を開始します...")
        # TODO: data_sync の呼び出し
        log_step("main", "step_control", "<<< [Step 6] データ移行同期 プレースホルダー完了")

    log_step("main", "finish", "==================================================")
    log_step("main", "finish", "すべての処理が完了しました。")
    log_step("main", "finish", "==================================================")

if __name__ == "__main__":
    main()
