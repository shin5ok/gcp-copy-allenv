import json
import logging
from typing import Dict, Any, List
import utils
from utils import log_step, run_external_command

def scan_project_resources(project_id: str, impersonate_sa: str, step_name: str = "cai_scan") -> List[Dict[str, Any]]:
    """指定されたプロジェクトの全リソースを CAI を用いてスキャンする"""
    target = f"project:{project_id}"
    log_step(step_name, target, f"Cloud Asset Inventory (CAI) によるリソーススキャンを開始します。")

    # CAI 検索コマンドの組み立て
    cmd = (
        f"gcloud asset search-all-resources "
        f"--scope=projects/{project_id} "
        f"--format=json"
    )
    
    explanation = f"プロジェクト {project_id} 内のすべての有効なGCPリソースを CAI で探索しています。"
    
    # 外部コマンド実行 (Impersonationを適用)
    result = run_external_command(
        cmd=cmd,
        step_name=step_name,
        target=target,
        explanation=explanation,
        impersonate_sa=impersonate_sa
    )

    if result.returncode != 0:
        log_step(step_name, target, f"リソーススキャンに失敗しました。 (exit code: {result.returncode})", logging.ERROR)
        return []

    # ドライランモード時はモックのリソースリストを返す
    if utils.context.dry_run:
        log_step(step_name, target, "[DRY RUN] モックのリソースリストを返します。", logging.DEBUG)
        return [
            {
                "name": f"//compute.googleapis.com/projects/{project_id}/zones/asia-northeast1-a/instances/mock-vm-01",
                "assetType": "compute.googleapis.com/Instance",
                "project": f"projects/{project_id}"
            },
            {
                "name": f"//storage.googleapis.com/mock-bucket-{project_id}",
                "assetType": "storage.googleapis.com/Bucket",
                "project": f"projects/{project_id}"
            }
        ]

    try:
        resources = json.loads(result.stdout)
        log_step(step_name, target, f"探索完了: {len(resources)} 個のリソースを検出しました。")
        
        # 主要なリソースのみログに出力
        for res in resources:
            asset_type = res.get("assetType", "Unknown")
            name = res.get("name", "Unknown")
            log_step(step_name, target, f"検出リソース: {asset_type} -> {name}", logging.DEBUG)
            
        return resources
    except Exception as e:
        log_step(step_name, target, f"出力結果の解析に失敗しました: {str(e)}", logging.ERROR)
        return []

def run_cai_scan(config: Dict[str, Any]):
    """Step 1 メイン処理"""
    step_name = "cai_scan"
    log_step(step_name, "all", ">>> [Step 1] CAI現状確認 スキャン開始")
    
    mapping = config.get("project_mapping", {})
    host = mapping.get("host_project", {})
    services = mapping.get("service_projects", [])

    # 1. ホストプロジェクトのスキャン
    scan_project_resources(
        project_id=host.get("src"),
        impersonate_sa=host.get("src_impersonate_service_account"),
        step_name=step_name
    )

    # 2. サービスプロジェクトのスキャン
    for svc in services:
        scan_project_resources(
            project_id=svc.get("src"),
            impersonate_sa=svc.get("src_impersonate_service_account"),
            step_name=step_name
        )

    log_step(step_name, "all", "<<< [Step 1] CAI現状確認 スキャン完了")
