#!/usr/bin/env python3
import argparse
import sys
import os
import re
import yaml
import logging
import subprocess
import json
import shutil
import glob
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

class MigrationOrchestrator:
    def __init__(self, config_path: str, dry_run_override: Optional[bool] = None, verbose_override: Optional[bool] = None, mock_override: Optional[bool] = None):
        self.config_path = config_path
        self.config = {}
        self.org_logger = None
        self.dst_logger = None
        self.dry_run = True
        self.verbose = True
        self.mock = False
        self.dry_run_override = dry_run_override
        self.verbose_override = verbose_override
        self.mock_override = mock_override

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
        self.mock = global_cfg.get('mock', False)
        
        if self.dry_run_override is not None:
            self.dry_run = self.dry_run_override
        if self.verbose_override is not None:
            self.verbose = self.verbose_override
        if self.mock_override is not None:
            self.mock = self.mock_override
        
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

    def run_command(self, cmd: str, logger: logging.Logger, desc: str = "", allow_fail: bool = False, cwd: Optional[str] = None, read_only: bool = False, impersonate_sa: Optional[str] = None) -> Optional[str]:
        log_msg = f"[{desc}] " if desc else ""
        
        if self.mock:
            mock_res = self._simulate_command(cmd, logger, log_msg)
            if mock_res is not None:
                return mock_res
                
        env = os.environ.copy()
        if impersonate_sa:
            env['CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT'] = impersonate_sa
            log_msg = f"{log_msg}[Impersonate: {impersonate_sa}] "

        if self.dry_run and not read_only:
            logger.info(f"{log_msg}[DRY RUN] Planned: {cmd} (in {cwd or '.'})")
            return ""
        
        if self.verbose:
            logger.info(f"{log_msg}Executing: {cmd} (in {cwd or '.'})")
        else:
            logger.info(f"{log_msg}Running task...")

        try:
            result = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=cwd, env=env)
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

    def _simulate_command(self, cmd: str, logger: logging.Logger, log_msg: str) -> Optional[str]:
        import json
        from datetime import datetime, timezone
        
        proj_match = re.search(r'--project=([^\s]+)', cmd) or re.search(r'--project_id=([^\s]+)', cmd)
        proj_id = proj_match.group(1) if proj_match else "unknown-project"
        
        if "compute instances list" in cmd:
            logger.info(f"{log_msg}[MOCK] Simulating VM list for {proj_id}...")
            mock_vms = [
                {
                    "name": "org-svc1-deb-e2-mic-01",
                    "zone": f"https://www.googleapis.com/compute/v1/projects/{proj_id}/zones/asia-northeast1-a",
                    "disks": [
                        {
                            "boot": True,
                            "source": f"https://www.googleapis.com/compute/v1/projects/{proj_id}/zones/asia-northeast1-a/disks/org-svc1-deb-e2-mic-01"
                        }
                    ]
                },
                {
                    "name": "org-svc1-deb-n2-std2-02",
                    "zone": f"https://www.googleapis.com/compute/v1/projects/{proj_id}/zones/asia-northeast1-b",
                    "disks": [
                        {
                            "boot": True,
                            "source": f"https://www.googleapis.com/compute/v1/projects/{proj_id}/zones/asia-northeast1-b/disks/org-svc1-deb-n2-std2-02"
                        }
                    ]
                }
            ]
            return json.dumps(mock_vms)
            
        elif "compute snapshots list" in cmd:
            logger.info(f"{log_msg}[MOCK] Simulating Snapshot list for {proj_id}...")
            now_str = datetime.now(timezone.utc).isoformat()
            mock_snaps = [
                {
                    "name": "migration-snap-org-svc1-deb-e2-mic-01",
                    "sourceDisk": f"https://www.googleapis.com/compute/v1/projects/{proj_id}/zones/asia-northeast1-a/disks/org-svc1-deb-e2-mic-01",
                    "creationTimestamp": now_str
                },
                {
                    "name": "migration-snap-org-svc1-deb-n2-std2-02",
                    "sourceDisk": f"https://www.googleapis.com/compute/v1/projects/{proj_id}/zones/asia-northeast1-b/disks/org-svc1-deb-n2-std2-02",
                    "creationTimestamp": now_str
                }
            ]
            return json.dumps(mock_snaps)
            
        elif "storage buckets list" in cmd:
            logger.info(f"{log_msg}[MOCK] Simulating Bucket list for {proj_id}...")
            return "org-bucket-shared-data\norg-assets-bucket\n"
            
        elif "bq ls" in cmd:
            logger.info(f"{log_msg}[MOCK] Simulating BigQuery list for {proj_id}...")
            if "raw_logs" in cmd or "raw_dataset" in cmd:
                mock_tables = [
                    {"tableReference": {"tableId": "app_events"}},
                    {"tableReference": {"tableId": "user_logs"}}
                ]
                return json.dumps(mock_tables)
            else:
                mock_datasets = [
                    {"datasetReference": {"datasetId": "raw_logs"}},
                    {"datasetReference": {"datasetId": "raw_dataset"}}
                ]
                return json.dumps(mock_datasets)
                
        elif "bq show" in cmd:
            logger.info(f"{log_msg}[MOCK] Simulating BQ Show dataset check...")
            return "Dataset exists metadata"
            
        elif any(x in cmd for x in ["gcloud asset search-all-resources", "gcloud beta resource-config bulk-export", "terraform init", "terraform apply", "gcloud compute instances stop", "gcloud compute instances detach-disk", "gcloud compute disks delete", "gcloud compute disks create", "gcloud compute instances attach-disk", "gcloud compute instances start", "gcloud storage rsync", "bq mk", "bq cp"]):
            logger.info(f"{log_msg}[MOCK] Simulating command success: {cmd.split()[0]}...")
            return "Success"
            
        return None

    def _write_dummy_tf_files(self, proj_dir: str, proj_id: str):
        self.org_logger.info(f"  [MOCK] Writing dummy TF files to {proj_dir}...")
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
            self.org_logger.error(f"  [MOCK] Failed to write dummy TF files: {e}")

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
            
            # output_dir
            output_dir = self.config.get('steps', {}).get('cai_scan', {}).get('output_dir', './cai_export')
            if not self.dry_run:
                os.makedirs(output_dir, exist_ok=True)
            
            output_file = os.path.join(output_dir, f"cai_resources_{proj_id}.txt")
            cmd += f" > {output_file}"
            
            self.run_command(cmd, self.org_logger, desc=f"CAI Scan {proj_id}", impersonate_sa=sa)
            
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
            vm_json = self.run_command(vm_cmd, self.org_logger, desc=f"List VMs {proj_id}", allow_fail=True, read_only=True, impersonate_sa=sa)
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
            snap_json = self.run_command(snap_cmd, self.org_logger, desc=f"List Snapshots {proj_id}", read_only=True, impersonate_sa=sa)
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
                
        output_dir_base = self.config.get('steps', {}).get('bulk_export', {}).get('output_dir', './terraform')
        raw_dir = os.path.join(output_dir_base, 'raw')
        active_dir = os.path.join(output_dir_base, 'active')
        
        if not self.dry_run:
            os.makedirs(raw_dir, exist_ok=True)
            os.makedirs(active_dir, exist_ok=True)
            
        for proj_id, sa in projects:
            self.org_logger.info(f"Exporting resources from project: {proj_id}")
            proj_raw_dir = os.path.join(raw_dir, proj_id)
            if not self.dry_run:
                os.makedirs(proj_raw_dir, exist_ok=True)
                
            if self.mock and not self.dry_run:
                self._write_dummy_tf_files(proj_raw_dir, proj_id)
            else:
                cmd = f"gcloud beta resource-config bulk-export --project={proj_id} --resource-format=terraform --path={proj_raw_dir}"
                self.run_command(cmd, self.org_logger, desc=f"Bulk Export {proj_id}", impersonate_sa=sa)
            
        self.customize_hcl(raw_dir, active_dir)
        
        self.org_logger.info("--- [Step 3] Bulk Export & HCL Customization Completed ---")

    def customize_hcl(self, raw_dir: str, active_dir: str):
        self.org_logger.info(f"Customizing HCL files from {raw_dir} to {active_dir}...")
        
        mapping = self.config.get('project_mapping', {})
        proj_map = {}
        host_proj = mapping.get('host_project', {})
        if host_proj.get('src') and host_proj.get('dst'):
            proj_map[host_proj['src']] = host_proj['dst']
        for svc_proj in mapping.get('service_projects', []):
            if svc_proj.get('src') and svc_proj.get('dst'):
                proj_map[svc_proj['src']] = svc_proj['dst']
                
        rename_gcs = self.config.get('rename_rules', {}).get('gcs', {})
        gcs_method = rename_gcs.get('method')
        gcs_val = rename_gcs.get('value', '')
        gcs_overrides = rename_gcs.get('overrides', {})

        for root, dirs, files in os.walk(raw_dir):
            for file in files:
                if not file.endswith('.tf'):
                    continue
                    
                raw_file_path = os.path.join(root, file)
                rel_path = os.path.relpath(raw_file_path, raw_dir)
                active_file_path = os.path.join(active_dir, rel_path)
                
                if not self.dry_run:
                    os.makedirs(os.path.dirname(active_file_path), exist_ok=True)
                    
                self.org_logger.info(f"  Processing {rel_path}...")
                
                if self.dry_run:
                    continue
                    
                try:
                    with open(raw_file_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                        
                    # 1. Replace Project IDs
                    for src_proj, dst_proj in proj_map.items():
                        content = content.replace(src_proj, dst_proj)
                        
                    # 2. Rename GCS Buckets
                    if 'google_storage_bucket' in content:
                        def rename_bucket(match):
                            indent = match.group(1)
                            spaces = match.group(2)
                            orig_name = match.group(3)
                            
                            new_name = orig_name
                            if orig_name in gcs_overrides:
                                new_name = gcs_overrides[orig_name]
                            elif gcs_method == 'suffix':
                                new_name = f"{orig_name}{gcs_val}"
                            elif gcs_method == 'prefix':
                                new_name = f"{gcs_val}{orig_name}"
                                
                            return f'{indent}name{spaces}= "{new_name}"'
                            
                        content = re.sub(r'(\s*)name(\s*)=\s*"([^"]+)"', rename_bucket, content)

                    # 3. VM boot disk source removal
                    if 'google_compute_instance' in content and 'boot_disk' in content:
                        lines = content.split('\n')
                        new_lines = []
                        bracket_count = 0
                        in_boot_disk = False
                        for line in lines:
                            open_count = line.count('{')
                            close_count = line.count('}')
                            
                            if 'boot_disk {' in line:
                                in_boot_disk = True
                                bracket_count = 1
                                new_lines.append(line)
                                continue
                                
                            if in_boot_disk:
                                bracket_count += open_count - close_count
                                if bracket_count <= 0:
                                    in_boot_disk = False
                                    
                                if 'source =' in line and '/disks/' in line:
                                    self.org_logger.info(f"    Removed boot_disk source line from {rel_path}: {line.strip()}")
                                    continue
                            new_lines.append(line)
                        content = '\n'.join(new_lines)

                    with open(active_file_path, 'w', encoding='utf-8') as f:
                        f.write(content)
                        
                except Exception as e:
                    self.org_logger.error(f"Failed to customize {raw_file_path}: {e}")
                    sys.exit(1)

    def step_terraform_apply(self):
        self.dst_logger.info("--- [Step 4] Starting Terraform Apply ---")
        
        output_dir_base = self.config.get('steps', {}).get('bulk_export', {}).get('output_dir', './terraform')
        active_dir = os.path.join(output_dir_base, 'active')
        
        self.run_command("terraform init", self.dst_logger, desc="TF Init", cwd=active_dir)
        self.run_command("terraform apply -auto-approve", self.dst_logger, desc="TF Apply", cwd=active_dir)
        
        self.dst_logger.info("--- [Step 4] Terraform Apply Completed ---")

    def step_gce_restore(self):
        self.dst_logger.info("--- [Step 5] Starting GCE VM Restore ---")
        
        mapping = self.config.get('project_mapping', {})
        projects = []
        
        # Host project
        host_proj = mapping.get('host_project', {})
        if host_proj.get('src') and host_proj.get('dst'):
            projects.append((host_proj['src'], host_proj['dst'], host_proj.get('src_impersonate_service_account'), host_proj.get('dst_impersonate_service_account')))
            
        # Service projects
        for svc_proj in mapping.get('service_projects', []):
            if svc_proj.get('src') and svc_proj.get('dst'):
                projects.append((svc_proj['src'], svc_proj['dst'], svc_proj.get('src_impersonate_service_account'), svc_proj.get('dst_impersonate_service_account')))
                
        max_age_days = self.config.get('steps', {}).get('gce_snapshot', {}).get('max_age_days', 30)
        
        for src_proj, dst_proj, src_sa, dst_sa in projects:
            self.dst_logger.info(f"Restoring VMs in destination project: {dst_proj} from source project: {src_proj}")
            
            # 1. Get VMs in source
            vm_cmd = f"gcloud compute instances list --project={src_proj} --format=json"
            vm_json = self.run_command(vm_cmd, self.dst_logger, desc=f"List Source VMs {src_proj}", allow_fail=True, read_only=True, impersonate_sa=src_sa)
            if not vm_json:
                self.dst_logger.info(f"No source VMs found or failed to list VMs in {src_proj}")
                continue
                
            try:
                vms = json.loads(vm_json)
            except Exception as e:
                self.dst_logger.error(f"Failed to parse VM list JSON: {e}")
                sys.exit(1)
                
            if not vms:
                self.dst_logger.info(f"No VMs found in {src_proj}")
                continue

            # 2. Get Snapshots in source
            snap_cmd = f"gcloud compute snapshots list --project={src_proj} --format=json"
            snap_json = self.run_command(snap_cmd, self.dst_logger, desc=f"List Source Snapshots {src_proj}", read_only=True, impersonate_sa=src_sa)
            try:
                snapshots = json.loads(snap_json) if snap_json else []
            except Exception as e:
                self.dst_logger.error(f"Failed to parse Snapshot list JSON: {e}")
                sys.exit(1)

            # 3. Restore each VM
            for vm in vms:
                vm_name = vm.get('name')
                if not vm_name.startswith('org-'):
                    self.dst_logger.info(f"  Skipping non-tool VM: {vm_name}")
                    continue
                    
                zone = vm.get('zone', '').split('/')[-1]
                disks = vm.get('disks', [])
                boot_disk = next((d for d in disks if d.get('boot')), None)
                if not boot_disk:
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
                                pass
                                
                if not valid_snapshot:
                    self.dst_logger.error(f"  [ERROR] No valid snapshot found for VM {vm_name} boot disk {source_disk_name}!")
                    sys.exit(1)
                    
                snap_name = valid_snapshot['name']
                dst_disk_name = vm_name
                
                self.dst_logger.info(f"  Restoring VM {vm_name} in {dst_proj} (zone: {zone}) using snapshot {snap_name}...")
                
                stop_cmd = f"gcloud compute instances stop {vm_name} --zone={zone} --project={dst_proj} --quiet"
                self.run_command(stop_cmd, self.dst_logger, desc=f"Stop VM {vm_name}", allow_fail=True, impersonate_sa=dst_sa)
                
                detach_cmd = f"gcloud compute instances detach-disk {vm_name} --disk={dst_disk_name} --zone={zone} --project={dst_proj} --quiet"
                self.run_command(detach_cmd, self.dst_logger, desc=f"Detach disk {dst_disk_name}", allow_fail=True, impersonate_sa=dst_sa)
                
                del_disk_cmd = f"gcloud compute disks delete {dst_disk_name} --zone={zone} --project={dst_proj} --quiet"
                self.run_command(del_disk_cmd, self.dst_logger, desc=f"Delete dummy disk {dst_disk_name}", allow_fail=True, impersonate_sa=dst_sa)
                
                snap_path = f"projects/{src_proj}/global/snapshots/{snap_name}"
                create_disk_cmd = f"gcloud compute disks create {dst_disk_name} --source-snapshot={snap_path} --zone={zone} --project={dst_proj} --quiet"
                self.run_command(create_disk_cmd, self.dst_logger, desc=f"Create disk {dst_disk_name} from snap", impersonate_sa=dst_sa)
                
                attach_cmd = f"gcloud compute instances attach-disk {vm_name} --disk={dst_disk_name} --boot --zone={zone} --project={dst_proj} --quiet"
                self.run_command(attach_cmd, self.dst_logger, desc=f"Attach boot disk {dst_disk_name}", impersonate_sa=dst_sa)
                
                start_cmd = f"gcloud compute instances start {vm_name} --zone={zone} --project={dst_proj} --quiet"
                self.run_command(start_cmd, self.dst_logger, desc=f"Start VM {vm_name}", impersonate_sa=dst_sa)
                
        self.dst_logger.info("--- [Step 5] GCE VM Restore Completed ---")

    def step_data_sync(self):
        self.dst_logger.info("--- [Step 6] Starting Data Sync (GCS/BQ) ---")
        
        mapping = self.config.get('project_mapping', {})
        projects = []
        
        # Host project
        host_proj = mapping.get('host_project', {})
        if host_proj.get('src') and host_proj.get('dst'):
            projects.append((host_proj['src'], host_proj.get('dst'), host_proj.get('src_impersonate_service_account'), host_proj.get('dst_impersonate_service_account')))
            
        # Service projects
        for svc_proj in mapping.get('service_projects', []):
            if svc_proj.get('src') and svc_proj.get('dst'):
                projects.append((svc_proj['src'], svc_proj.get('dst'), svc_proj.get('src_impersonate_service_account'), svc_proj.get('dst_impersonate_service_account')))
                
        rename_gcs = self.config.get('rename_rules', {}).get('gcs', {})
        gcs_method = rename_gcs.get('method')
        gcs_val = rename_gcs.get('value', '')
        gcs_overrides = rename_gcs.get('overrides', {})

        for src_proj, dst_proj, src_sa, dst_sa in projects:
            self.dst_logger.info(f"Syncing data from {src_proj} to {dst_proj}...")
            
            # === GCS Sync ===
            self.dst_logger.info("  [GCS] Syncing Buckets...")
            list_buckets_cmd = f"gcloud storage buckets list --project={src_proj} --format='value(name)'"
            buckets_str = self.run_command(list_buckets_cmd, self.dst_logger, desc=f"List Buckets {src_proj}", allow_fail=True, read_only=True, impersonate_sa=src_sa)
            if buckets_str:
                buckets = [b.strip() for b in buckets_str.split('\n') if b.strip()]
                for orig_bucket in buckets:
                    if orig_bucket in gcs_overrides:
                        dst_bucket = gcs_overrides[orig_bucket]
                    elif gcs_method == 'suffix':
                        dst_bucket = f"{orig_bucket}{gcs_val}"
                    elif gcs_method == 'prefix':
                        dst_bucket = f"{gcs_val}{orig_bucket}"
                    else:
                        dst_bucket = orig_bucket
                        
                    self.dst_logger.info(f"    Syncing bucket gs://{orig_bucket} -> gs://{dst_bucket}...")
                    
                    rsync_cmd = f"gcloud storage rsync gs://{orig_bucket} gs://{dst_bucket} --recursive --project={dst_proj}"
                    self.run_command(rsync_cmd, self.dst_logger, desc=f"GCS Rsync {orig_bucket}", impersonate_sa=dst_sa)
            else:
                self.dst_logger.info("    No GCS buckets found or failed to list.")

            # === BigQuery Sync ===
            self.dst_logger.info("  [BQ] Syncing BigQuery Datasets...")
            list_ds_cmd = f"bq ls --project_id={src_proj} --format=json"
            ds_json = self.run_command(list_ds_cmd, self.dst_logger, desc=f"List BQ Datasets {src_proj}", allow_fail=True, read_only=True, impersonate_sa=src_sa)
            if ds_json:
                try:
                    datasets = json.loads(ds_json)
                    for ds in datasets:
                        ds_ref = ds.get('datasetReference', {})
                        ds_id = ds_ref.get('datasetId')
                        if not ds_id:
                            continue
                            
                        self.dst_logger.info(f"    Processing Dataset: {ds_id}")
                        
                        check_ds_cmd = f"bq show --project_id={dst_proj} {ds_id}"
                        ds_show = self.run_command(check_ds_cmd, self.dst_logger, desc=f"Check Dataset {ds_id}", allow_fail=True, read_only=True, impersonate_sa=dst_sa)
                        ds_exists = ds_show is not None and ds_show != ""
                        
                        if not ds_exists and not self.dry_run:
                            mk_ds_cmd = f"bq mk --project_id={dst_proj} {ds_id}"
                            self.run_command(mk_ds_cmd, self.dst_logger, desc=f"BQ Mk Dataset {ds_id}", impersonate_sa=dst_sa)
                            
                        list_tables_cmd = f"bq ls --project_id={src_proj} --format=json {ds_id}"
                        tables_json = self.run_command(list_tables_cmd, self.dst_logger, desc=f"List BQ Tables {ds_id}", allow_fail=True, read_only=True, impersonate_sa=src_sa)
                        if tables_json:
                            tables = json.loads(tables_json)
                            for table in tables:
                                t_ref = table.get('tableReference', {})
                                t_id = t_ref.get('tableId')
                                if not t_id:
                                    continue
                                    
                                self.dst_logger.info(f"      Syncing Table: {ds_id}.{t_id}...")
                                cp_cmd = f"bq cp --force {src_proj}:{ds_id}.{t_id} {dst_proj}:{ds_id}.{t_id}"
                                self.run_command(cp_cmd, self.dst_logger, desc=f"BQ Cp Table {ds_id}.{t_id}", impersonate_sa=dst_sa)
                except Exception as e:
                    self.dst_logger.error(f"Failed to sync BigQuery data: {e}")
            else:
                self.dst_logger.info("    No BigQuery datasets found or failed to list.")
                
        self.dst_logger.info("--- [Step 6] Data Sync Completed ---")

def main():
    parser = argparse.ArgumentParser(description="GCP Project 'Marugoto Copy' Orchestrator")
    parser.add_argument("--config", default="dst/config.yaml", help="Path to config.yaml")
    parser.add_argument("--dry-run", action="store_true", default=None, help="Enable dry-run mode")
    parser.add_argument("--no-dry-run", action="store_false", dest="dry_run", help="Disable dry-run mode")
    parser.add_argument("--verbose", action="store_true", default=None, help="Enable verbose logging")
    parser.add_argument("--no-verbose", action="store_false", dest="verbose", help="Disable verbose logging")
    parser.add_argument("--mock", action="store_true", default=None, help="Enable mock simulation mode")
    parser.add_argument("--no-mock", action="store_false", dest="mock", help="Disable mock simulation mode")
    args = parser.parse_args()

    orchestrator = MigrationOrchestrator(
        config_path=args.config,
        dry_run_override=args.dry_run,
        verbose_override=args.verbose,
        mock_override=args.mock
    )
    orchestrator.execute()

if __name__ == "__main__":
    main()
