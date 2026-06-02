#!/usr/bin/env python3
import argparse
import sys
import os
import yaml
import logging
import subprocess
import json
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

class MigrationOrchestrator:
    def __init__(self, config_path: str):
        self.config_path = config_path
        self.config = {}
        self.org_logger = None
        self.dst_logger = None
        self.dry_run = True
        self.verbose = True

    def load_config(self):
        if not os.path.exists(self.config_path):
            print(f"Error: Configuration file {self.config_path} not found.", file=sys.stderr)
            sys.exit(1)
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                self.config = yaml.safe_load(f)
        except Exception as e:
            print(f"Error parsing configuration file: {e}", file=sys.stderr)
            sys.exit(1)

        global_cfg = self.config.get('global', {})
        self.dry_run = global_cfg.get('dry_run', True)
        self.verbose = global_cfg.get('verbose_logging', True)
        
        log_dir = global_cfg.get('log_dir', './logs')
        os.makedirs(log_dir, exist_ok=True)
        
        org_log_name = global_cfg.get('org_log_file', 'org.log')
        dst_log_name = global_cfg.get('dst_log_file', 'dst.log')
        
        self.org_logger = self._setup_logger('org', os.path.join(log_dir, org_log_name))
        self.dst_logger = self._setup_logger('dst', os.path.join(log_dir, dst_log_name))

    def _setup_logger(self, name: str, filepath: str) -> logging.Logger:
        logger = logging.getLogger(name)
        logger.setLevel(logging.INFO)
        logger.handlers.clear()
        formatter = logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
        
        # File handler
        file_handler = logging.FileHandler(filepath, mode='a', encoding='utf-8')
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        
        # Console handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
        
        return logger

    def run_command(self, cmd: str, logger: logging.Logger, desc: str = "", allow_fail: bool = False) -> Optional[str]:
        log_msg = f"[{desc}] " if desc else ""
        if self.dry_run:
            logger.info(f"{log_msg}[DRY RUN] Planned: {cmd}")
            return ""
        
        if self.verbose:
            logger.info(f"{log_msg}Executing: {cmd}")
        else:
            logger.info(f"{log_msg}Running task...")

        try:
            result = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if result.returncode != 0:
                logger.error(f"{log_msg}Failed with exit code {result.returncode}")
                logger.error(f"Command: {cmd}")
                if result.stderr:
                    logger.error(f"Stderr: {result.stderr.strip()}")
                if not allow_fail:
                    sys.exit(result.returncode)
            else:
                if result.stdout and self.verbose:
                    logger.debug(f"Stdout:\n{result.stdout.strip()}")
            return result.stdout.strip()
        except Exception as e:
            logger.error(f"{log_msg}Exception occurred: {e}")
            if not allow_fail:
                sys.exit(1)
            return None

    def execute(self):
        self.load_config()
        self.org_logger.info("=== Migration Started ===")
        self.dst_logger.info("=== Migration Started ===")
        
        steps = self.config.get('steps', {})
        
        # Step 1: CAI Scan
        if steps.get('cai_scan', {}).get('enabled', False):
            self.step_cai_scan()
            
        # Step 2: GCE Snapshot Check
        if steps.get('gce_snapshot', {}).get('enabled', False):
            self.step_gce_snapshot()
            
        # Step 3: Bulk Export
        if steps.get('bulk_export', {}).get('enabled', False):
            self.step_bulk_export()
            
        # Step 4: Terraform Apply
        if steps.get('terraform_apply', {}).get('enabled', False):
            self.step_terraform_apply()
            
        # Step 5: GCE Restore
        if steps.get('gce_restore', {}).get('enabled', False):
            self.step_gce_restore()
            
        # Step 6: Data Sync
        if steps.get('data_sync', {}).get('enabled', False):
            self.step_data_sync()

        self.org_logger.info("=== Migration Finished ===")
        self.dst_logger.info("=== Migration Finished ===")

    def step_cai_scan(self):
        self.org_logger.info("--- [Step 1] Starting CAI Scan ---")
        
        mapping = self.config.get('project_mapping', {})
        projects = []
        
        # Host project
        host_proj = mapping.get('host_project', {})
        if host_proj.get('src'):
            projects.append((host_proj['src'], host_proj.get('src_impersonate_service_account')))
            
        # Service projects
        for svc_proj in mapping.get('service_projects', []):
            if svc_proj.get('src'):
                projects.append((svc_proj['src'], svc_proj.get('src_impersonate_service_account')))
                
        for proj_id, sa in projects:
            self.org_logger.info(f"Scanning resources in source project: {proj_id}")
            cmd = f"gcloud asset search-all-resources --scope=projects/{proj_id}"
            if sa:
                cmd += f" --impersonate-service-account={sa}"
            
            # output_dir
            output_dir = self.config.get('steps', {}).get('cai_scan', {}).get('output_dir', './cai_export')
            if not self.dry_run:
                os.makedirs(output_dir, exist_ok=True)
            
            output_file = os.path.join(output_dir, f"cai_resources_{proj_id}.txt")
            cmd += f" > {output_file}"
            
            self.run_command(cmd, self.org_logger, desc=f"CAI Scan {proj_id}")
            
        self.org_logger.info("--- [Step 1] CAI Scan Completed ---")

    def step_gce_snapshot(self):
        self.org_logger.info("--- [Step 2] Starting GCE Snapshot Check ---")
        
        mapping = self.config.get('project_mapping', {})
        projects = []
        
        # Host project
        host_proj = mapping.get('host_project', {})
        if host_proj.get('src'):
            projects.append((host_proj['src'], host_proj.get('src_impersonate_service_account')))
            
        # Service projects
        for svc_proj in mapping.get('service_projects', []):
            if svc_proj.get('src'):
                projects.append((svc_proj['src'], svc_proj.get('src_impersonate_service_account')))
                
        max_age_days = self.config.get('steps', {}).get('gce_snapshot', {}).get('max_age_days', 30)
        
        for proj_id, sa in projects:
            self.org_logger.info(f"Checking GCE Snapshots in source project: {proj_id}")
            
            # 1. Get VMs
            vm_cmd = f"gcloud compute instances list --project={proj_id} --format=json"
            if sa:
                vm_cmd += f" --impersonate-service-account={sa}"
                
            vm_json = self.run_command(vm_cmd, self.org_logger, desc=f"List VMs {proj_id}", allow_fail=True)
            if self.dry_run:
                self.org_logger.info(f"[DRY RUN] Skip VM snapshot check for {proj_id}")
                continue
                
            if not vm_json:
                self.org_logger.info(f"No VMs found or failed to list VMs in {proj_id}")
                continue
                
            try:
                vms = json.loads(vm_json)
            except Exception as e:
                self.org_logger.error(f"Failed to parse VM list JSON: {e}")
                sys.exit(1)
                
            if not vms:
                self.org_logger.info(f"No VMs found in {proj_id}")
                continue

            # 2. Get Snapshots
            snap_cmd = f"gcloud compute snapshots list --project={proj_id} --format=json"
            if sa:
                snap_cmd += f" --impersonate-service-account={sa}"
                
            snap_json = self.run_command(snap_cmd, self.org_logger, desc=f"List Snapshots {proj_id}")
            try:
                snapshots = json.loads(snap_json) if snap_json else []
            except Exception as e:
                self.org_logger.error(f"Failed to parse Snapshot list JSON: {e}")
                sys.exit(1)

            # 3. Verify each VM has a fresh snapshot for its boot disk
            for vm in vms:
                vm_name = vm.get('name')
                disks = vm.get('disks', [])
                boot_disk = next((d for d in disks if d.get('boot')), None)
                if not boot_disk:
                    self.org_logger.warning(f"VM {vm_name} has no boot disk. Skipping.")
                    continue
                    
                source_disk_name = boot_disk.get('source', '').split('/')[-1]
                
                valid_snapshot = None
                for snap in snapshots:
                    snap_source_disk = snap.get('sourceDisk', '').split('/')[-1]
                    if snap_source_disk == source_disk_name:
                        creation_time_str = snap.get('creationTimestamp')
                        if creation_time_str:
                            try:
                                creation_time = datetime.fromisoformat(creation_time_str)
                                age = datetime.now(timezone.utc) - creation_time
                                if age <= timedelta(days=max_age_days):
                                    valid_snapshot = snap
                                    break
                            except Exception as e:
                                self.org_logger.warning(f"Failed to parse creationTimestamp '{creation_time_str}': {e}")
                                
                if valid_snapshot:
                    self.org_logger.info(f"  VM {vm_name} (boot disk: {source_disk_name}) has valid snapshot: {valid_snapshot['name']} (created: {valid_snapshot['creationTimestamp']})")
                else:
                    self.org_logger.error(f"  [ERROR] VM {vm_name} (boot disk: {source_disk_name}) DOES NOT have a valid snapshot within the last {max_age_days} days!")
                    self.org_logger.error("  Please create a snapshot manually in the Original environment first.")
                    sys.exit(1)
                    
        self.org_logger.info("--- [Step 2] GCE Snapshot Check Completed ---")

    def step_bulk_export(self):
        self.org_logger.info("--- [Step 3] Starting Bulk Export & HCL Customization ---")
        # TODO: Implement Bulk Export and HCL Customization
        self.org_logger.info("--- [Step 3] Bulk Export & HCL Customization Completed ---")

    def step_terraform_apply(self):
        self.dst_logger.info("--- [Step 4] Starting Terraform Apply ---")
        # TODO: Implement Terraform Apply
        self.dst_logger.info("--- [Step 4] Terraform Apply Completed ---")

    def step_gce_restore(self):
        self.dst_logger.info("--- [Step 5] Starting GCE VM Restore ---")
        # TODO: Implement GCE VM Restore
        self.dst_logger.info("--- [Step 5] GCE VM Restore Completed ---")

    def step_data_sync(self):
        self.dst_logger.info("--- [Step 6] Starting Data Sync (GCS/BQ) ---")
        # TODO: Implement Data Sync
        self.dst_logger.info("--- [Step 6] Data Sync Completed ---")

def main():
    parser = argparse.ArgumentParser(description="GCP Project 'Marugoto Copy' Orchestrator")
    parser.add_argument("--config", default="dst/config.yaml", help="Path to config.yaml")
    args = parser.parse_args()

    orchestrator = MigrationOrchestrator(args.config)
    orchestrator.execute()

if __name__ == "__main__":
    main()
