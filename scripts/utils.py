import sys
import subprocess
import logging
import os
import shlex
from typing import List, Union

# ロガーの設定
def setup_logger(log_dir: str, log_file: str) -> logging.Logger:
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    
    log_path = os.path.join(log_dir, log_file)
    
    logger = logging.getLogger("migration")
    logger.setLevel(logging.DEBUG)
    
    # 既にハンドラが設定されている場合は重複を避ける
    if not logger.handlers:
        # ファイルハンドラ
        file_handler = logging.FileHandler(log_path, encoding='utf-8')
        file_formatter = logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s')
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)
        
        # コンソールハンドラ
        console_handler = logging.StreamHandler(sys.stdout)
        console_formatter = logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s')
        console_handler.setFormatter(console_formatter)
        logger.addHandler(console_handler)
        
    return logger

# グローバル設定ホルダー
class Context:
    def __init__(self):
        self.dry_run = True
        self.verbose_logging = True
        self.logger = None

context = Context()

def log_step(step_name: str, target: str, message: str, level: int = logging.INFO):
    """識別タグ付きでログを出力する"""
    if not context.logger:
        return
    
    # タグのフォーマット: [ステップ名] [対象] メッセージ
    formatted_msg = f"[{step_name}] [{target}] {message}"
    context.logger.log(level, formatted_msg)

def run_external_command(
    cmd: Union[str, List[str]],
    step_name: str,
    target: str,
    explanation: str,
    impersonate_sa: str = None
) -> subprocess.CompletedProcess:
    """
    生の外部コマンドを実行し、詳細ログと解説を出力する。
    gcloud コマンドの場合、自動的に Impersonation フラグを付与する。
    """
    # リスト形式に統一
    if isinstance(cmd, str):
        cmd_list = shlex.split(cmd)
    else:
        cmd_list = list(cmd)
        
    # gcloud コマンドかつ Impersonation 指定がある場合、フラグを自動挿入
    if cmd_list and cmd_list[0] == "gcloud" and impersonate_sa:
        # すでにフラグが含まれていないか確認
        if "--impersonate-service-account" not in cmd_list:
            # gcloud の直後に挿入
            cmd_list.insert(1, f"--impersonate-service-account={impersonate_sa}")

    raw_cmd_str = shlex.join(cmd_list)

    # 1. 人間用解説メッセージの出力
    log_step(step_name, target, f"[実行内容] {explanation}")
    
    # 2. 生コマンド文字列の出力 (verbose_logging が有効な場合)
    if context.verbose_logging:
        log_step(step_name, target, f"-> {raw_cmd_str}", logging.DEBUG)

    # ドライランモードの場合、実行をスキップ
    if context.dry_run:
        log_step(step_name, target, "[DRY RUN] コマンドの実行をスキップしました。", logging.INFO)
        return subprocess.CompletedProcess(
            args=cmd_list,
            returncode=0,
            stdout="[DRY RUN] OK\n",
            stderr=""
        )

    try:
        # コマンド実行
        result = subprocess.run(
            cmd_list,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False # エラーハンドリングは呼び出し側で行う
        )
        
        # 実行結果の詳細ログ
        if context.verbose_logging:
            if result.stdout:
                log_step(step_name, target, f"[STDOUT]\n{result.stdout.strip()}", logging.DEBUG)
            if result.stderr:
                log_step(step_name, target, f"[STDERR]\n{result.stderr.strip()}", logging.DEBUG)
                
        if result.returncode != 0:
            log_step(step_name, target, f"コマンドがエラーで終了しました (code: {result.returncode})", logging.ERROR)
        else:
            log_step(step_name, target, "コマンドが正常に完了しました。", logging.DEBUG)
            
        return result

    except Exception as e:
        log_step(step_name, target, f"コマンド実行中に例外が発生しました: {str(e)}", logging.ERROR)
        raise e
