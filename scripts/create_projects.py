#!/usr/bin/env python3
import argparse
import sys
import os
import yaml
import logging
import subprocess
from typing import Dict, Any, List, Optional

class ProjectProvisioner:
    def __init__(self, config_path: str, dry_run_override: Optional[bool] = None, verbose_override: Optional[bool] = None):
        self.config_path = config_path
        self.config: Dict[str, Any] = {}
        self.logger: Optional[logging.Logger] = None
        self.dry_run = True
        self.verbose = True
        self.dry_run_override = dry_run_override
        self.verbose_override = verbose_override

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

        if self.dry_run_override is not None:
            self.dry_run = self.dry_run_override
        if self.verbose_override is not None:
            self.verbose = self.verbose_override

        log_dir = global_cfg.get('log_dir', './logs')
        os.makedirs(log_dir, exist_ok=True)
        
        # Output to dst.log as this script performs changes on the destination
        dst_log_name = global_cfg.get('dst_log_file', 'dst.log')
        self.logger = self._setup_logger('dst_provision', os.path.join(log_dir, dst_log_name))

    def _setup_logger(self, name: str, filepath: str) -> logging.Logger:
        logger = logging.getLogger(name)
        logger.setLevel(logging.INFO)
        logger.handlers.clear()
        formatter = logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
        
        file_handler = logging.FileHandler(filepath, mode='a', encoding='utf-8')
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
        
        return logger

    def run_command(self, cmd: str, desc: str = "", allow_fail: bool = False, read_only: bool = False) -> Optional[str]:
        log_msg = f"[{desc}] " if desc else ""
        
        if self.dry_run and not read_only:
            self.logger.info(f"{log_msg}[DRY RUN] Planned: {cmd}")
            return ""
        
        if self.verbose:
            self.logger.info(f"{log_msg}Executing: {cmd}")
        else:
            self.logger.info(f"{log_msg}Running task...")

        try:
            result = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if result.returncode != 0:
                if not allow_fail:
                    self.logger.error(f"{log_msg}Failed with exit code {result.returncode}")
                    self.logger.error(f"Command: {cmd}")
                    if result.stderr:
                        self.logger.error(f"Stderr: {result.stderr.strip()}")
                    sys.exit(result.returncode)
                else:
                    if self.verbose and result.stderr:
                        self.logger.info(f"{log_msg}Command failed (allowed): {result.stderr.strip()}")
                    return None
            return result.stdout.strip()
        except Exception as e:
            self.logger.error(f"{log_msg}Exception occurred: {e}")
            if not allow_fail:
                sys.exit(1)
            return None

    def project_exists(self, project_id: str) -> bool:
        # Run describe command as read_only to check existence
        cmd = f"gcloud projects describe {project_id} --format=json"
        res = self.run_command(cmd, desc=f"Check Project {project_id}", allow_fail=True, read_only=True)
        return res is not None

    def provision(self):
        self.load_config()
        self.logger.info("=== Destination Project Provisioning Started ===")
        self.logger.info(f"Dry-run Mode: {self.dry_run}")
        
        bootstrap = self.config.get('bootstrap', {})
        org_id = bootstrap.get('org_id')
        folder_id = bootstrap.get('folder_id')
        billing_account = bootstrap.get('billing_account')
        
        if not billing_account:
            self.logger.error("Error: 'bootstrap.billing_account' is required in config.yaml.")
            sys.exit(1)
            
        if not org_id and not folder_id:
            self.logger.error("Error: Either 'bootstrap.org_id' or 'bootstrap.folder_id' is required.")
            sys.exit(1)

        mapping = self.config.get('project_mapping', {})
        projects: List[str] = []
        
        # Host project
        host_proj = mapping.get('host_project', {})
        if host_proj.get('dst'):
            projects.append(host_proj['dst'])
            
        # Service projects
        for svc_proj in mapping.get('service_projects', []):
            if svc_proj.get('dst'):
                projects.append(svc_proj['dst'])

        if not projects:
            self.logger.info("No destination projects found in mapping. Nothing to do.")
            return

        for proj_id in projects:
            self.logger.info(f"Processing destination project: {proj_id}")
            
            # 1. Check if project exists
            exists = self.project_exists(proj_id)
            if exists:
                self.logger.info(f"  Project '{proj_id}' already exists. Skipping creation.")
            else:
                # 2. Create Project
                self.logger.info(f"  Project '{proj_id}' not found. Creating...")
                create_cmd = f"gcloud projects create {proj_id}"
                if folder_id:
                    create_cmd += f" --folder={folder_id}"
                elif org_id:
                    create_cmd += f" --organization={org_id}"
                
                self.run_command(create_cmd, desc=f"Create Project {proj_id}")

            # 3. Link Billing (Even if project exists, ensure billing is linked)
            self.logger.info(f"  Linking billing account '{billing_account}' to project '{proj_id}'...")
            billing_cmd = f"gcloud beta billing projects link {proj_id} --billing-account={billing_account}"
            self.run_command(billing_cmd, desc=f"Link Billing {proj_id}")

            # 4. Enable Required Services
            required_services = ["compute.googleapis.com", "dns.googleapis.com"]
            services_str = " ".join(required_services)
            self.logger.info(f"  Enabling required services ({services_str}) for '{proj_id}'...")
            enable_cmd = f"gcloud services enable {services_str} --project={proj_id}"
            self.run_command(enable_cmd, desc=f"Enable APIs {proj_id}")

        self.logger.info("=== Destination Project Provisioning Finished ===")

def main():
    parser = argparse.ArgumentParser(description="GCP Destination Project Provisioner")
    parser.add_argument("--config", default="dst/config.yaml", help="Path to config.yaml")
    parser.add_argument("--dry-run", action="store_true", default=None, help="Enable dry-run mode")
    parser.add_argument("--no-dry-run", action="store_false", dest="dry_run", help="Disable dry-run mode")
    parser.add_argument("--verbose", action="store_true", default=None, help="Enable verbose logging")
    parser.add_argument("--no-verbose", action="store_false", dest="verbose", help="Disable verbose logging")
    args = parser.parse_args()

    provisioner = ProjectProvisioner(
        config_path=args.config,
        dry_run_override=args.dry_run,
        verbose_override=args.verbose
    )
    provisioner.provision()

if __name__ == "__main__":
    main()
