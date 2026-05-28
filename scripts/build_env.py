#!/usr/bin/env python3
import argparse
import re
import sys
import subprocess
import logging
import io
import json
import os
import threading
import concurrent.futures
import shlex
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple

@dataclass
class SubnetConfig:
    name: str
    ip_range: str
    project: str

@dataclass
class VMConfig:
    name: str
    machine_type: str
    image: str
    zone: str
    subnet: str
    ip_address: str

@dataclass
class EnvConfig:
    host_project: str = ""
    service_projects: List[str] = field(default_factory=list)
    subnets: List[SubnetConfig] = field(default_factory=list)
    vms: Dict[str, List[VMConfig]] = field(default_factory=dict)

@dataclass
class DeployStep:
    resource_type: str
    resource_name: str
    check_cmd: str
    create_cmd: str # In case of destroy, this holds the delete command
    project: str = "" # Associated project ID for state tracking

@dataclass
class Stage:
    name: str
    steps: List[DeployStep]
    is_parallel: bool


class StateManager:
    def __init__(self, filepath: str = "state.json"):
        self.filepath = filepath
        self.lock = threading.Lock()

    def load_state(self) -> List[Dict]:
        with self.lock:
            if not os.path.exists(self.filepath):
                return []
            try:
                with open(self.filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    return data.get("resources", [])
            except json.JSONDecodeError:
                logging.warning(f"Failed to parse state file {self.filepath}. Returning empty state.")
                return []

    def add_resource(self, resource_type: str, resource_name: str, project: str, check_cmd: str, delete_cmd: str):
        with self.lock:
            state = {"resources": []}
            if os.path.exists(self.filepath):
                try:
                    with open(self.filepath, 'r', encoding='utf-8') as f:
                        state = json.load(f)
                except json.JSONDecodeError:
                    pass
            
            # Check for duplicates
            resources = state.setdefault("resources", [])
            exists = any(
                r["resource_type"] == resource_type and r["resource_name"] == resource_name and r["project"] == project
                for r in resources
            )
            
            if not exists:
                resources.append({
                    "resource_type": resource_type,
                    "resource_name": resource_name,
                    "project": project,
                    "check_cmd": check_cmd,
                    "delete_cmd": delete_cmd
                })
                
                with open(self.filepath, 'w', encoding='utf-8') as f:
                    json.dump(state, f, indent=2)

    def remove_resource(self, resource_type: str, resource_name: str, project: str):
        with self.lock:
            if not os.path.exists(self.filepath):
                return
            
            state = {"resources": []}
            try:
                with open(self.filepath, 'r', encoding='utf-8') as f:
                    state = json.load(f)
            except json.JSONDecodeError:
                return
                
            resources = state.get("resources", [])
            new_resources = [
                r for r in resources
                if not (r["resource_type"] == resource_type and r["resource_name"] == resource_name and r["project"] == project)
            ]
            
            if len(new_resources) != len(resources):
                state["resources"] = new_resources
                if not new_resources:
                    try:
                        os.remove(self.filepath)
                    except OSError:
                        pass
                else:
                    with open(self.filepath, 'w', encoding='utf-8') as f:
                        json.dump(state, f, indent=2)


class OrgParser:
    def __init__(self, filepath: str):
        self.filepath = filepath
        self.config = EnvConfig()

    def parse(self) -> EnvConfig:
        with open(self.filepath, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        current_section = ""
        current_subsection = ""
        current_project_id = ""
        table_lines = []
        in_table = False

        for line in lines:
            line = line.strip()
            
            if line.startswith("## "):
                self._process_table(current_section, current_subsection, current_project_id, table_lines)
                table_lines = []
                in_table = False
                
                current_section = line[3:].strip()
                current_subsection = ""
                continue
            elif line.startswith("### "):
                self._process_table(current_section, current_subsection, current_project_id, table_lines)
                table_lines = []
                in_table = False

                current_subsection = line[4:].strip()
                match = re.search(r'\(([^)]+)\)', current_subsection)
                if match:
                    current_project_id = match.group(1).replace("`", "").strip()
                continue

            if line.startswith("|"):
                in_table = True
                table_lines.append(line)
            elif in_table:
                self._process_table(current_section, current_subsection, current_project_id, table_lines)
                table_lines = []
                in_table = False

        if table_lines:
            self._process_table(current_section, current_subsection, current_project_id, table_lines)

        return self.config

    def _process_table(self, section: str, subsection: str, project_id: str, table_lines: List[str]):
        if not table_lines or len(table_lines) < 3:
            return

        header = [cell.strip() for cell in table_lines[0].split('|')[1:-1]]
        data_rows = []
        for row in table_lines[2:]:
            if row.strip().startswith("|"):
                cells = [cell.strip() for cell in row.split('|')[1:-1]]
                data_rows.append(cells)

        if "1. プロジェクト構造" in section:
            self._parse_project_structure(header, data_rows)
        elif "2. ネットワーク構成" in section:
            if not subsection or "サブネット定義" in subsection:
                self._parse_network_config(header, data_rows)
        elif "3. VMインスタンス構成" in section:
            if project_id:
                self._parse_vm_config(project_id, header, data_rows)

    def _parse_project_structure(self, header: List[str], rows: List[str]):
        for row in rows:
            if len(row) < 2:
                continue
            role = row[0].replace("**", "").strip()
            project_id = row[1].replace("`", "").strip()
            
            if "Host Project" in role:
                self.config.host_project = project_id
            elif "Service Project" in role:
                self.config.service_projects.append(project_id)

    def _parse_network_config(self, header: List[str], rows: List[str]):
        for row in rows:
            if len(row) < 3:
                continue
            subnet_name = row[0].replace("`", "").strip()
            ip_range = row[1].replace("`", "").strip()
            project_id = row[2].replace("`", "").strip()
            
            self.config.subnets.append(SubnetConfig(
                name=subnet_name,
                ip_range=ip_range,
                project=project_id
            ))

    def _parse_vm_config(self, project_id: str, header: List[str], rows: List[str]):
        header_map = {col: idx for idx, col in enumerate(header)}
        
        vms = []
        for row in rows:
            try:
                name = row[header_map["インスタンス名"]].replace("`", "").strip()
                machine_type = row[header_map["マシンタイプ"]].replace("`", "").strip()
                os_image = row[header_map["OSイメージ"]].replace("`", "").strip()
                zone = row[header_map["ゾーン"]].replace("`", "").strip()
                subnet = row[header_map["サブネット"]].replace("`", "").strip()
                ip_address = row[header_map["内部固定IPアドレス"]].replace("`", "").strip()
                
                image_path = os_image
                if os_image == "debian-12":
                    image_path = "projects/debian-cloud/global/images/family/debian-12"
                elif os_image == "ubuntu-2204-lts":
                    image_path = "projects/ubuntu-os-cloud/global/images/family/ubuntu-2204-lts"

                vms.append(VMConfig(
                    name=name,
                    machine_type=machine_type,
                    image=image_path,
                    zone=zone,
                    subnet=subnet,
                    ip_address=ip_address
                ))
            except (KeyError, IndexError) as e:
                print(f"Warning: Failed to parse row {row} due to missing column or index error: {e}", file=sys.stderr)
                continue
        
        if vms:
            if project_id not in self.config.vms:
                self.config.vms[project_id] = []
            self.config.vms[project_id].extend(vms)


STARTUP_SCRIPT = r"""#!/bin/bash
# Install Nginx and curl
apt-get update
apt-get install -y nginx curl

# Get host info
HOSTNAME=$(hostname)
IP=$(hostname -I | awk '{print $1}')

# Configure default index page
echo -e "Hostname: ${HOSTNAME}\nIP: ${IP}" > /var/www/html/index.html

# Overwrite Nginx configuration for /json
cat <<'EOF' > /etc/nginx/sites-available/default
server {
    listen 80 default_server;
    listen [::]:80 default_server;

    root /var/www/html;
    index index.html index.htm;

    server_name _;

    location / {
        default_type text/plain;
        try_files $uri $uri/ =404;
    }

    location /json {
        default_type application/json;
        return 200 '{"hostname": "HOSTNAME_PLACEHOLDER", "ip": "IP_PLACEHOLDER"}\n';
    }
}
EOF

# Replace placeholders with actual values
sed -i "s/HOSTNAME_PLACEHOLDER/${HOSTNAME}/" /etc/nginx/sites-available/default
sed -i "s/IP_PLACEHOLDER/${IP}/" /etc/nginx/sites-available/default

# Apply configuration
systemctl restart nginx
"""


class GcloudCommandGenerator:
    def __init__(self, config: EnvConfig, network_name: str = "shared-vpc", region: str = "asia-northeast1", startup_script_path: str = "nginx_startup.sh"):
        self.config = config
        self.network_name = network_name
        self.region = region
        self.startup_script_path = startup_script_path

    def generate_stages(self) -> List[Stage]:
        stages = []
        
        # Stage 1: Host Setup (Sequential)
        host_setup_steps = []
        host_setup_steps.append(DeployStep(
            resource_type="VPC Network",
            resource_name=self.network_name,
            check_cmd=f"gcloud compute networks describe {self.network_name} --project={self.config.host_project} --format='value(name)'",
            create_cmd=f"gcloud compute networks create {self.network_name} --subnet-mode=custom --project={self.config.host_project}",
            project=self.config.host_project
        ))
        host_setup_steps.append(DeployStep(
            resource_type="Shared VPC Host",
            resource_name=self.config.host_project,
            check_cmd=f"gcloud compute shared-vpc get-host-project --project={self.config.host_project} --format='value(name)'",
            create_cmd=f"gcloud compute shared-vpc enable {self.config.host_project}",
            project=self.config.host_project
        ))
        for svc_proj in self.config.service_projects:
            host_setup_steps.append(DeployStep(
                resource_type="Shared VPC Associated Project",
                resource_name=f"{svc_proj} in {self.config.host_project}",
                check_cmd=f"gcloud compute shared-vpc associated-projects list --project={self.config.host_project} --format='value(id)' | grep -w {svc_proj}",
                create_cmd=f"gcloud compute shared-vpc associated-projects add {svc_proj} --host-project={self.config.host_project}",
                project=self.config.host_project
            ))
        # Cloud Router Creation
        host_setup_steps.append(DeployStep(
            resource_type="Cloud Router",
            resource_name="shared-router",
            check_cmd=f"gcloud compute routers describe shared-router --region={self.region} --project={self.config.host_project} --format='value(name)'",
            create_cmd=f"gcloud compute routers create shared-router --network={self.network_name} --region={self.region} --project={self.config.host_project}",
            project=self.config.host_project
        ))
        # Cloud NAT Creation
        host_setup_steps.append(DeployStep(
            resource_type="Cloud NAT",
            resource_name="shared-nat in shared-router",
            check_cmd=f"gcloud compute routers describe shared-router --region={self.region} --project={self.config.host_project} --format='value(nats.name)' | grep -w shared-nat",
            create_cmd=f"gcloud compute routers nats create shared-nat --router=shared-router --region={self.region} --auto-allocate-nat-external-ips --nat-all-subnet-ip-ranges --project={self.config.host_project}",
            project=self.config.host_project
        ))
        # IAP SSH Firewall Rule Creation (Host Project)
        host_setup_steps.append(DeployStep(
            resource_type="Firewall Rule",
            resource_name="allow-shared-iap-ssh",
            check_cmd=f"gcloud compute firewall-rules describe allow-shared-iap-ssh --project={self.config.host_project} --format='value(name)'",
            create_cmd=f"gcloud compute firewall-rules create allow-shared-iap-ssh --network={self.network_name} --allow=tcp:22 --source-ranges=35.235.240.0/20 --direction=INGRESS --project={self.config.host_project}",
            project=self.config.host_project
        ))
        stages.append(Stage(name="VPC & Host Setup", steps=host_setup_steps, is_parallel=False))

        # Stage 2: Subnets (Parallelized)
        subnet_steps = []
        for subnet in self.config.subnets:
            subnet_steps.append(DeployStep(
                resource_type="Subnet",
                resource_name=subnet.name,
                check_cmd=f"gcloud compute networks subnets describe {subnet.name} --region={self.region} --project={self.config.host_project} --format='value(name)'",
                create_cmd=f"gcloud compute networks subnets create {subnet.name} --network={self.network_name} --range={subnet.ip_range} --region={self.region} --project={self.config.host_project}",
                project=self.config.host_project
            ))
        stages.append(Stage(name="Subnet Creation", steps=subnet_steps, is_parallel=True))

        # Stage 3: Static IPs (Parallelized)
        ip_steps = []
        for proj_id, vms in self.config.vms.items():
            for vm in vms:
                subnet_path = f"projects/{self.config.host_project}/regions/{self.region}/subnetworks/{vm.subnet}"
                ip_steps.append(DeployStep(
                    resource_type="Static Private IP Address",
                    resource_name=f"{vm.name}-ip",
                    check_cmd=f"gcloud compute addresses describe {vm.name}-ip --region={self.region} --project={proj_id} --format='value(name)'",
                    create_cmd=f"gcloud compute addresses create {vm.name}-ip --addresses={vm.ip_address} --subnet={subnet_path} --region={self.region} --project={proj_id}",
                    project=proj_id
                ))
        stages.append(Stage(name="IP Reservation", steps=ip_steps, is_parallel=True))

        # Stage 4: VM Instances (Parallelized) with --metadata-from-file
        vm_steps = []
        for proj_id, vms in self.config.vms.items():
            for vm in vms:
                subnet_path = f"projects/{self.config.host_project}/regions/{self.region}/subnetworks/{vm.subnet}"
                vm_steps.append(DeployStep(
                    resource_type="VM Instance",
                    resource_name=vm.name,
                    check_cmd=f"gcloud compute instances describe {vm.name} --zone={vm.zone} --project={proj_id} --format='value(name)'",
                    # FIXED: Use --metadata-from-file to avoid shell comma escape parser bugs in gcloud dict flags
                    create_cmd=f"gcloud compute instances create {vm.name} --machine-type={vm.machine_type} --image={vm.image} --subnet={subnet_path} --private-network-ip={vm.ip_address} --zone={vm.zone} --project={proj_id} --no-address --metadata-from-file=startup-script={self.startup_script_path}",
                    project=proj_id
                ))
        stages.append(Stage(name="VM Provisioning", steps=vm_steps, is_parallel=True))

        return stages

    def get_delete_cmd_for_resource(self, resource_type: str, resource_name: str, project: str, zone_or_region: str = "") -> Tuple[str, str]:
        z_r = zone_or_region if zone_or_region else self.region
        if resource_type == "VPC Network":
            return (
                f"gcloud compute networks describe {resource_name} --project={project} --format='value(name)'",
                f"gcloud compute networks delete {resource_name} --project={project} --quiet"
            )
        elif resource_type == "Shared VPC Host":
            return (
                f"gcloud compute shared-vpc get-host-project --project={project} --format='value(name)'",
                f"gcloud compute shared-vpc disable {project} --quiet"
            )
        elif resource_type == "Shared VPC Associated Project":
            svc_proj = resource_name.split(" in ")[0]
            return (
                f"gcloud compute shared-vpc associated-projects list --project={project} --format='value(id)' | grep -w {svc_proj}",
                f"gcloud compute shared-vpc associated-projects remove {svc_proj} --host-project={project} --quiet"
            )
        elif resource_type == "Subnet":
            return (
                f"gcloud compute networks subnets describe {resource_name} --region={z_r} --project={project} --format='value(name)'",
                f"gcloud compute networks subnets delete {resource_name} --region={z_r} --project={project} --quiet"
            )
        elif resource_type == "Static Private IP Address":
            return (
                f"gcloud compute addresses describe {resource_name} --region={z_r} --project={project} --format='value(name)'",
                f"gcloud compute addresses delete {resource_name} --region={z_r} --project={project} --quiet"
            )
        elif resource_type == "VM Instance":
            return (
                f"gcloud compute instances describe {resource_name} --zone={z_r} --project={project} --format='value(name)'",
                f"gcloud compute instances delete {resource_name} --zone={z_r} --project={project} --quiet"
            )
        elif resource_type == "Cloud Router":
            return (
                f"gcloud compute routers describe {resource_name} --region={z_r} --project={project} --format='value(name)'",
                f"gcloud compute routers delete {resource_name} --region={z_r} --project={project} --quiet"
            )
        elif resource_type == "Cloud NAT":
            nat_name, router_name = resource_name.split(" in ")
            return (
                f"gcloud compute routers describe {router_name} --region={z_r} --project={project} --format='value(nats.name)' | grep -w {nat_name}",
                f"gcloud compute routers nats delete {nat_name} --router={router_name} --region={z_r} --project={project} --quiet"
            )
        elif resource_type == "Firewall Rule":
            return (
                f"gcloud compute firewall-rules describe {resource_name} --project={project} --format='value(name)'",
                f"gcloud compute firewall-rules delete {resource_name} --project={project} --quiet"
            )
        return ("", "")


def build_destroy_stages_from_state(state_resources: List[Dict]) -> List[Stage]:
    vm_steps = []
    ip_steps = []
    nat_steps = []
    router_steps = []
    firewall_steps = []
    subnet_steps = []
    host_steps = []
    
    for r in reversed(state_resources):
        step = DeployStep(
            resource_type=r["resource_type"],
            resource_name=r["resource_name"],
            check_cmd=r["check_cmd"],
            create_cmd=r["delete_cmd"],
            project=r["project"]
        )
        
        if r["resource_type"] == "VM Instance":
            vm_steps.append(step)
        elif r["resource_type"] == "Static Private IP Address":
            ip_steps.append(step)
        elif r["resource_type"] == "Cloud NAT":
            nat_steps.append(step)
        elif r["resource_type"] == "Cloud Router":
            router_steps.append(step)
        elif r["resource_type"] == "Firewall Rule":
            firewall_steps.append(step)
        elif r["resource_type"] == "Subnet":
            subnet_steps.append(step)
        else:
            host_steps.append(step)
            
    stages = []
    if vm_steps:
        stages.append(Stage(name="VM Destruction", steps=vm_steps, is_parallel=True))
    if ip_steps:
        stages.append(Stage(name="IP Release", steps=ip_steps, is_parallel=True))
    if nat_steps:
        stages.append(Stage(name="NAT Destruction", steps=nat_steps, is_parallel=True))
    if router_steps:
        stages.append(Stage(name="Router Destruction", steps=router_steps, is_parallel=True))
    if firewall_steps:
        stages.append(Stage(name="Firewall Destruction", steps=firewall_steps, is_parallel=True))
    if subnet_steps:
        stages.append(Stage(name="Subnet Deletion", steps=subnet_steps, is_parallel=True))
    if host_steps:
        stages.append(Stage(name="VPC & Host Cleanup", steps=host_steps, is_parallel=False))
        
    return stages


def setup_logging():
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    
    formatter = logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    file_handler = logging.FileHandler('build.log', mode='a', encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)


def execute_single_step_thread(step: DeployStep, prefix: str, is_destroy: bool, state_mgr: StateManager, generator: GcloudCommandGenerator) -> Tuple[bool, str]:
    log_stream = io.StringIO()
    t_logger = logging.getLogger(f"thread_{step.resource_name}")
    t_logger.setLevel(logging.INFO)
    t_logger.handlers.clear()
    
    formatter = logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    handler = logging.StreamHandler(log_stream)
    handler.setFormatter(formatter)
    t_logger.addHandler(handler)

    t_logger.info(f"{prefix} Checking existence...")
    check_result = subprocess.run(step.check_cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    exists = (check_result.returncode == 0)
    
    success = True
    if is_destroy:
        if not exists:
            t_logger.info(f"{prefix} -> [SKIP] Already deleted (not found).")
            state_mgr.remove_resource(step.resource_type, step.resource_name, step.project)
            return True, log_stream.getvalue()
        t_logger.info(f"{prefix} -> [DELETE] Found. Deleting...")
    else:
        if exists:
            t_logger.info(f"{prefix} -> [SKIP] Already exists.")
            zone_or_region = ""
            if step.resource_type == "VM Instance":
                match = re.search(r'--zone=([^\s]+)', step.check_cmd)
                if match: zone_or_region = match.group(1)
            check_c, delete_c = generator.get_delete_cmd_for_resource(step.resource_type, step.resource_name, step.project, zone_or_region)
            if delete_c:
                state_mgr.add_resource(step.resource_type, step.resource_name, step.project, check_c, delete_c)
            return True, log_stream.getvalue()
        t_logger.info(f"{prefix} -> [CREATE] Not found. Creating...")
        
    t_logger.info(f"  Executing: {step.create_cmd}")
    
    try:
        result = subprocess.run(step.create_cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        success_verb = "Deleted" if is_destroy else "Created"
        t_logger.info(f"{prefix} -> [SUCCESS] {success_verb} successfully.")
        
        if is_destroy:
            state_mgr.remove_resource(step.resource_type, step.resource_name, step.project)
        else:
            zone_or_region = ""
            if step.resource_type == "VM Instance":
                match = re.search(r'--zone=([^\s]+)', step.create_cmd)
                if match: zone_or_region = match.group(1)
            check_c, delete_c = generator.get_delete_cmd_for_resource(step.resource_type, step.resource_name, step.project, zone_or_region)
            if delete_c:
                state_mgr.add_resource(step.resource_type, step.resource_name, step.project, check_c, delete_c)

        if result.stdout:
            t_logger.debug(f"Output:\n{result.stdout}")
    except subprocess.CalledProcessError as e:
        t_logger.error(f"{prefix} -> [FAILED] Command failed with exit code {e.returncode}.")
        t_logger.error(f"Command: {e.cmd}")
        if e.stdout:
            t_logger.error(f"Stdout:\n{e.stdout}")
        if e.stderr:
            t_logger.error(f"Stderr:\n{e.stderr}")
        success = False
        
    return success, log_stream.getvalue()


def execute_stages(stages: List[Stage], dry_run: bool, auto_approve: bool, state_mgr: StateManager, generator: GcloudCommandGenerator, is_destroy: bool = False):
    action_label = "Destroy" if is_destroy else "Deployment"
    
    if dry_run:
        logging.info(f"=== [DRY RUN] Planned {action_label} Stages ===")
        for s_idx, stage in enumerate(stages, 1):
            mode = "PARALLEL" if stage.is_parallel else "SEQUENTIAL"
            logging.info(f"\n--- Stage {s_idx:02d}: {stage.name} ({mode}) ---")
            for i, step in enumerate(stage.steps, 1):
                logging.info(f"  Step {i:02d}: [{step.resource_type}] {step.resource_name}")
                logging.info(f"    Check:  {step.check_cmd}")
                logging.info(f"    Action: {step.create_cmd}")
        logging.info("=============================================")
        return

    logging.info(f"=== Prepared {action_label} Stages ===")
    for s_idx, stage in enumerate(stages, 1):
        mode = "PARALLEL" if stage.is_parallel else "SEQUENTIAL"
        logging.info(f"Stage {s_idx:02d}: {stage.name} ({len(stage.steps)} steps, {mode})")
    logging.info("=======================================")

    # Confirmation prompt
    if not auto_approve:
        if is_destroy:
            logging.warning("!!! WARNING: This operation will DESTROY all resources recorded in state.json !!!")
            host_proj_id = ""
            for s in stages:
                for step in s.steps:
                    if step.resource_type == "Shared VPC Host":
                        host_proj_id = step.resource_name
                        break
            if not host_proj_id:
                host_proj_id = "confirm"
            try:
                user_input = input(f"\nTo confirm deletion, please type '{host_proj_id}': ").strip()
                if user_input != host_proj_id:
                    logging.error("Confirmation failed. Aborting.")
                    sys.exit(1)
            except KeyboardInterrupt:
                logging.warning("Cancelled by user.")
                sys.exit(1)
        else:
            try:
                response = input("\nDo you want to proceed with the deployment? [y/N]: ").strip().lower()
            except KeyboardInterrupt:
                logging.warning("Cancelled by user.")
                sys.exit(1)
            if response != 'y':
                logging.info("Cancelled.")
                return
    else:
        logging.info("Auto-approve enabled. Skipping confirmation.")

    logging.info(f"Starting {action_label.lower()}...")

    for s_idx, stage in enumerate(stages, 1):
        mode = "PARALLEL" if stage.is_parallel else "SEQUENTIAL"
        logging.info(f"\n=== [Stage {s_idx}/{len(stages)}] {stage.name} ({mode}) ===")
        
        if not stage.steps:
            logging.info("No steps in this stage. Skipping.")
            continue
            
        if stage.is_parallel:
            logging.info(f"Spawning {len(stage.steps)} threads for parallel execution...")
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                futures = {}
                for i, step in enumerate(stage.steps, 1):
                    prefix = f"[Stage {s_idx}][Thread-{i}/{len(stage.steps)}] ({step.resource_type}: {step.resource_name})"
                    future = executor.submit(execute_single_step_thread, step, prefix, is_destroy, state_mgr, generator)
                    futures[future] = step
                
                stage_failed = False
                for future in concurrent.futures.as_completed(futures):
                    success, buffered_log = future.result()
                    print(buffered_log, end="")
                    if not success:
                        stage_failed = True
                        
                if stage_failed:
                    logging.error(f"Stage '{stage.name}' failed. Stopping execution. Remaining stages preserved.")
                    sys.exit(1)
                    
        else:
            for i, step in enumerate(stage.steps, 1):
                prefix = f"[Stage {s_idx}][Seq-{i}/{len(stage.steps)}] ({step.resource_type}: {step.resource_name})"
                success, buffered_log = execute_single_step_thread(step, prefix, is_destroy, state_mgr, generator)
                print(buffered_log, end="")
                
                if not success:
                    logging.error(f"Step failed. Stopping execution. Remaining stages preserved.")
                    sys.exit(1)
                    
    logging.info(f"\n{action_label} completed successfully (or no changes needed).")

def main():
    parser = argparse.ArgumentParser(description="Build or Destroy GCP environment from ORG.md with State Management")
    parser.add_argument("--config", required=True, help="Path to ORG.md")
    parser.add_argument("--dry-run", action="store_true", help="Show commands without executing")
    parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt (auto-approve)")
    parser.add_argument("--destroy", action="store_true", help="Destroy all resources tracked in state.json instead of creating them")
    args = parser.parse_args()

    setup_logging()

    state_mgr = StateManager()

    # Temporary file setup for startup script to bypass shell comma escaping parsing bugs in gcloud
    startup_script_path = "nginx_startup.sh"
    if not args.dry_run and not args.destroy:
        logging.info(f"Writing temporary startup script file: {startup_script_path}")
        with open(startup_script_path, "w", encoding="utf-8") as f:
            f.write(STARTUP_SCRIPT)
        os.chmod(startup_script_path, 0o755)

    try:
        if args.destroy:
            logging.info("Loading deployed resources from state.json...")
            state_resources = state_mgr.load_state()
            if not state_resources:
                logging.info("No resources found in state.json to destroy. Nothing to do.")
                sys.exit(0)
                
            org_parser = OrgParser(args.config)
            config = org_parser.parse()
            generator = GcloudCommandGenerator(config, startup_script_path=startup_script_path)
            
            stages = build_destroy_stages_from_state(state_resources)
            execute_stages(stages, args.dry_run, args.yes, state_mgr=state_mgr, generator=generator, is_destroy=True)
        else:
            logging.info(f"Parsing config: {args.config}")
            org_parser = OrgParser(args.config)
            config = org_parser.parse()
            
            generator = GcloudCommandGenerator(config, startup_script_path=startup_script_path)
            stages = generator.generate_stages()
            execute_stages(stages, args.dry_run, args.yes, state_mgr=state_mgr, generator=generator, is_destroy=False)
    finally:
        # Cleanup temporary startup script file safely
        if os.path.exists(startup_script_path):
            try:
                logging.info(f"Cleaning up temporary startup script file: {startup_script_path}")
                os.remove(startup_script_path)
            except OSError as e:
                logging.warning(f"Failed to remove temporary file {startup_script_path}: {e}")

if __name__ == "__main__":
    main()
