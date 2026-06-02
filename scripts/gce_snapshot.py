import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List
import utils
from utils import log_step, run_external_command

def parse_timestamp(ts_str: str) -> datetime:
    """GCPのタイムスタンプ文字列 (ISO 8601) を datetime オブジェクトにパースする"""
    try:
        # Python 3.7以降の fromisoformat
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except Exception as e:
        raise ValueError(f"タイムスタンプのパースに失敗しました ({ts_str}): {str(e)}")

def check_vm_snapshot(
    project_id: str,
    vm_name: str,
    impersonate_sa: str,
    max_age_days: int = 30,
    step_name: str = "gce_snapshot"
) -> bool:
    """
    特定のVMのブートディスクに対して、指定された日数以内に作成された有効なスナップショットが存在するか確認する。
    """
    target = f"vm:{vm_name}"
    log_step(step_name, target, f"有効なスナップショットの存在チェックを開始します (許容期限: {max_age_days} 日)。")

    # スナップショット一覧を取得するコマンド
    cmd = (
        f"gcloud compute snapshots list "
        f"--project={project_id} "
        f"--format=json"
    )
    
    explanation = f"プロジェクト {project_id} 内のスナップショット一覧を取得し、VM {vm_name} の有効なバックアップがあるか検証しています。"
    
    result = run_external_command(
        cmd=cmd,
        step_name=step_name,
        target=target,
        explanation=explanation,
        impersonate_sa=impersonate_sa
    )

    if result.returncode != 0:
        log_step(step_name, target, f"スナップショット一覧の取得に失敗しました。 (exit code: {result.returncode})", logging.ERROR)
        return False

    # ドライランモード時のモック検証
    if utils.context.dry_run:
        log_step(step_name, target, "[DRY RUN] スナップショット検証をシミュレート (成功とみなします)。", logging.INFO)
        return True

    try:
        snapshots = json.loads(result.stdout)
        now = datetime.now(timezone.utc)
        limit_date = now - timedelta(days=max_age_days)
        
        valid_snapshots = []

        for snap in snapshots:
            # sourceDisk から対象VMのディスクであるか判定
            source_disk = snap.get("sourceDisk", "")
            if f"/disks/{vm_name}" not in source_disk:
                continue
                
            creation_ts_str = snap.get("creationTimestamp", "")
            if not creation_ts_str:
                continue
                
            creation_time = parse_timestamp(creation_ts_str)
            
            # 期限内かチェック
            if creation_time >= limit_date:
                age = now - creation_time
                log_step(step_name, target, f"有効なスナップショットを検出: {snap.get('name')} (作成: {creation_ts_str}, 経過: {age.days}日)", logging.INFO)
                valid_snapshots.append(snap)
            else:
                age = now - creation_time
                log_step(step_name, target, f"期限切れのスナップショットをスキップ: {snap.get('name')} (作成: {creation_ts_str}, 経過: {age.days}日)", logging.DEBUG)

        if not valid_snapshots:
            log_step(step_name, target, f"エラー: VM {vm_name} に対する {max_age_days} 日以内の有効なスナップショットが見つかりません。", logging.ERROR)
            return False
            
        return True

    except Exception as e:
        log_step(step_name, target, f"スナップショットの検証中に例外が発生しました: {str(e)}", logging.ERROR)
        return False

def run_gce_snapshot_check(config: Dict[str, Any]) -> bool:
    """Step 2 メイン処理"""
    step_name = "gce_snapshot"
    log_step(step_name, "all", ">>> [Step 2] GCEスナップショット検証 開始")
    
    mapping = config.get("project_mapping", {})
    services = mapping.get("service_projects", [])
    steps_cfg = config.get("steps", {}).get("gce_snapshot", {})
    max_age = steps_cfg.get("max_age_days", 30)

    # 静的に定義されたVM名の一覧 (DST.md の内容と整合)
    vms_to_check = {
        "shingo-ar-sharedservice0926-1": [
            "org-svc1-deb-e2-mic-01",
            "org-svc1-deb-e2-mic-02",
            "org-svc1-deb-e2-mic-03",
            "org-svc1-deb-n2-std2-01",
            "org-svc1-deb-n2-std2-02"
        ],
        "shingo-ar-sharedservice0926-3": [
            "org-svc3-ub-e2-med-01",
            "org-svc3-ub-e2-med-02",
            "org-svc3-ub-e2-med-03",
            "org-svc3-ub-e2-mic-01",
            "org-svc3-ub-e2-mic-02",
            "org-svc3-ub-c2-std4-01"
        ]
    }

    success = True
    for svc in services:
        src_proj = svc.get("src")
        impersonate_sa = svc.get("src_impersonate_service_account")
        
        vms = vms_to_check.get(src_proj, [])
        for vm_name in vms:
            ok = check_vm_snapshot(
                project_id=src_proj,
                vm_name=vm_name,
                impersonate_sa=impersonate_sa,
                max_age_days=max_age,
                step_name=step_name
            )
            if not ok:
                success = False

    if not success:
        log_step(step_name, "all", "エラー: 1つ以上のVMで有効なスナップショットが確認できませんでした。手動でスナップショットを作成してください。", logging.ERROR)
        raise RuntimeError("スナップショット検証エラー")

    log_step(step_name, "all", "<<< [Step 2] GCEスナップショット検証 正常完了")
    return True
