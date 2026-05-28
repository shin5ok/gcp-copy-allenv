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

# Re-use core data structures
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
    create_cmd: str
    project: str = ""

@dataclass
class Stage:
    name: str
    steps: List[DeployStep]
    is_parallel: bool


class StateManager:
    def __init__(self, filepath: str = "state-sync.json"):
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
                
                vms.append(VMConfig(
                    name=name,
                    machine_type=machine_type,
                    image=os_image,
                    zone=zone,
                    subnet=subnet,
                    ip_address=ip_address
                ))
            except (KeyError, IndexError) as e:
                continue
        
        if vms:
            if project_id not in self.config.vms:
                self.config.vms[project_id] = []
            self.config.vms[project_id].extend(vms)


class GCPClonerGenerator:
    def __init__(self, config: EnvConfig, project_map: Dict[str, str], network_name: str = "shared-vpc", region: str = "asia-northeast1"):
        self.config = config
        self.project_map = project_map
        self.network_name = network_name
        self.region = region
        
        # Reverse map to find source project ID from mapped destination project ID
        self.reverse_project_map = {v: k for k, v in project_map.items()}

    def apply_project_mapping(self):
        # 1. Map Host Project
        orig_host = self.config.host_project
        self.config.host_project = self.project_map.get(orig_host, orig_host)
        
        # 2. Map Service Projects
        self.config.service_projects = [
            self.project_map.get(p, p) for p in self.config.service_projects
        ]
        
        # 3. Map Subnet projects
        for sub in self.config.subnets:
            sub.project = self.project_map.get(sub.project, sub.project)
            
        # 4. Map VMs map keys
        new_vms = {}
        for orig_proj, vms in self.config.vms.items():
            mapped_proj = self.project_map.get(orig_proj, orig_proj)
            new_vms[mapped_proj] = vms
        self.config.vms = new_vms

    def generate_sync_stages(self) -> List[Stage]:
        stages = []
        
        # Stage 1: VPC & Host Setup (Sequential)
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
            check_cmd=f"gcloud compute shared-vpc associated-projects list --project={self.config.host_project}",
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
        # IAP SSH Firewall Rule Creation
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
            if not subnet.name.startswith("subnet-"):
                continue
            subnet_steps.append(DeployStep(
                resource_type="Subnet",
                resource_name=subnet.name,
                check_cmd=f"gcloud compute networks subnets describe {subnet.name} --region={self.region} --project={self.config.host_project} --format='value(name)'",
                create_cmd=f"gcloud compute networks subnets create {subnet.name} --network={self.network_name} --range={subnet.ip_range} --region={self.region} --project={self.config.host_project}",
                project=self.config.host_project
            ))
        stages.append(Stage(name="Subnet Creation", steps=subnet_steps, is_parallel=True))

        # Stage 3: Clone Disk from Original Snapshot (Parallelized)
        disk_steps = []
        for proj_id, vms in self.config.vms.items():
            src_proj = self.reverse_project_map.get(proj_id, proj_id)
            for vm in vms:
                disk_name = f"{vm.name}-disk"
                snapshot_path = f"projects/{src_proj}/global/snapshots/{vm.name}"
                disk_steps.append(DeployStep(
                    resource_type="Disk",
                    resource_name=disk_name,
                    check_cmd=f"gcloud compute disks describe {disk_name} --zone={vm.zone} --project={proj_id} --format='value(name)'",
                    create_cmd=f"gcloud compute disks create {disk_name} --source-snapshot={snapshot_path} --zone={vm.zone} --project={proj_id} --quiet",
                    project=proj_id
                ))
        stages.append(Stage(name="Disk Cloning from Snapshot", steps=disk_steps, is_parallel=True))

        # Stage 4: Clone VMs launching from Cloned Boot Disks (Parallelized)
        vm_steps = []
        for proj_id, vms in self.config.vms.items():
            for vm in vms:
                subnet_path = f"projects/{self.config.host_project}/regions/{self.region}/subnetworks/{vm.subnet}"
                disk_name = f"{vm.name}-disk"
                vm_steps.append(DeployStep(
                    resource_type="VM Instance",
                    resource_name=vm.name,
                    check_cmd=f"gcloud compute instances describe {vm.name} --zone={vm.zone} --project={proj_id} --format='value(name)'",
                    create_cmd=f"gcloud compute instances create {vm.name} --disk=name={disk_name},boot=yes,auto-delete=yes --subnet={subnet_path} --private-network-ip={vm.ip_address} --zone={vm.zone} --project={proj_id} --no-address --quiet",
                    project=proj_id
                ))
        stages.append(Stage(name="VM Cloned Provisioning", steps=vm_steps, is_parallel=True))

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
        elif resource_type == "Disk":
            return (
                f"gcloud compute disks describe {resource_name} --zone={z_r} --project={project} --format='value(name)'",
                f"gcloud compute disks delete {resource_name} --zone={z_r} --project={project} --quiet"
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


def build_api_enablement_stage_from_projects(projects: List[str]) -> Stage:
    steps = []
    for project in projects:
        steps.append(DeployStep(
            resource_type="API",
            resource_name=project,
            check_cmd=f"gcloud services list --enabled --project={project} --format='value(config.name)' | grep -w compute.googleapis.com",
            create_cmd=f"gcloud services enable compute.googleapis.com dns.googleapis.com --project={project} --quiet",
            project=project
        ))
    return Stage(name="API Enablement", steps=steps, is_parallel=True)


def execute_single_step_thread(step: DeployStep, prefix: str, state_mgr: StateManager, cloner: GCPClonerGenerator) -> Tuple[bool, str]:
    log_stream = io.StringIO()
    t_logger = logging.getLogger(f"thread_{step.resource_name}")
    t_logger.setLevel(logging.INFO)
    t_logger.handlers.clear()
    
    formatter = logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    handler = logging.StreamHandler(log_stream)
    handler.setFormatter(formatter)
    t_logger.addHandler(handler)

    t_logger.info(f"{prefix} Checking existence in destination...")
    check_result = subprocess.run(step.check_cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    exists = (check_result.returncode == 0)
    
    success = True
    if exists:
        already_verb = "already enabled" if step.resource_type == "API" else "Already exists in destination"
        t_logger.info(f"{prefix} -> [SKIP] {already_verb}.")
        
        # Track successfully synced resource (excluding APIs)
        if step.resource_type != "API":
            zone_or_region = ""
            if step.resource_type in ["VM Instance", "Disk"]:
                match = re.search(r'--zone=([^\s]+)', step.check_cmd)
                if match: zone_or_region = match.group(1)
            check_c, delete_c = cloner.get_delete_cmd_for_resource(step.resource_type, step.resource_name, step.project, zone_or_region)
            if delete_c:
                state_mgr.add_resource(step.resource_type, step.resource_name, step.project, check_c, delete_c)
        return True, log_stream.getvalue()
        
    action_verb = "Enabling required APIs" if step.resource_type == "API" else "Replicating resource"
    t_logger.info(f"{prefix} -> [RUN] Not found. {action_verb}...")
    t_logger.info(f"  Executing: {step.create_cmd}")
    
    try:
        result = subprocess.run(step.create_cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        
        if step.resource_type == "API":
            success_verb = "Enabled"
        elif step.resource_type == "VM Instance" or step.resource_type == "Disk":
            success_verb = "Replicated"
        else:
            success_verb = "Created"
            
        t_logger.info(f"{prefix} -> [SUCCESS] {success_verb} successfully.")
        
        # Track successfully synced resource (excluding APIs)
        if step.resource_type != "API":
            zone_or_region = ""
            if step.resource_type in ["VM Instance", "Disk"]:
                match = re.search(r'--zone=([^\s]+)', step.create_cmd)
                if match: zone_or_region = match.group(1)
            check_c, delete_c = cloner.get_delete_cmd_for_resource(step.resource_type, step.resource_name, step.project, zone_or_region)
            if delete_c:
                state_mgr.add_resource(step.resource_type, step.resource_name, step.project, check_c, delete_c)

        if result.stdout:
            t_logger.debug(f"Output:\n{result.stdout}")
    except subprocess.CalledProcessError as e:
        t_logger.error(f"{prefix} -> [FAILED] Command failed with exit code {e.returncode}.")
        t_logger.error(f"Command: {e.cmd}")
        if e.stderr:
            t_logger.error(f"Stderr:\n{e.stderr}")
        success = False
        
    return success, log_stream.getvalue()


def execute_sync_stages(stages: List[Stage], dry_run: bool, auto_approve: bool, state_mgr: StateManager, cloner: GCPClonerGenerator, is_prepare: bool = False):
    action_label = "API Enablement" if is_prepare else "Environment Replication"
    
    if dry_run:
        logging.info(f"=== [DRY RUN] Planned {action_label} Stages ===")
        for s_idx, stage in enumerate(stages, 1):
            mode = "PARALLEL" if stage.is_parallel else "SEQUENTIAL"
            logging.info(f"\n--- Stage {s_idx:02d}: {stage.name} ({mode}) ---")
            for i, step in enumerate(stage.steps, 1):
                logging.info(f"  Step {i:02d}: [{step.resource_type}] {step.resource_name}")
                logging.info(f"    Check:  {step.check_cmd}")
                logging.info(f"    Action: {step.create_cmd}")
        logging.info("=======================================================")
        return

    logging.info(f"=== Prepared {action_label} Stages ===")
    for s_idx, stage in enumerate(stages, 1):
        mode = "PARALLEL" if stage.is_parallel else "SEQUENTIAL"
        logging.info(f"Stage {s_idx:02d}: {stage.name} ({len(stage.steps)} steps, {mode})")
    logging.info("====================================")

    if not auto_approve:
        try:
            prompt_str = f"Do you want to proceed with the {action_label.lower()}? [y/N]: "
            response = input(prompt_str).strip().lower()
        except KeyboardInterrupt:
            logging.warning("Cancelled by user.")
            sys.exit(1)
        if response != 'y':
            logging.info("Cancelled.")
            return
    else:
        logging.info("Auto-approve enabled. Skipping confirmation.")

    logging.info(f"Starting {action_label.lower()} to destination...")

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
                    future = executor.submit(execute_single_step_thread, step, prefix, state_mgr, cloner)
                    futures[future] = step
                
                stage_failed = False
                for future in concurrent.futures.as_completed(futures):
                    success, buffered_log = future.result()
                    print(buffered_log, end="")
                    if not success:
                        stage_failed = True
                        
                if stage_failed:
                    logging.error(f"Stage '{stage.name}' failed. Stopping. Remaining stages preserved.")
                    sys.exit(1)
        else:
            for i, step in enumerate(stage.steps, 1):
                prefix = f"[Stage {s_idx}][Seq-{i}/{len(stage.steps)}] ({step.resource_type}: {step.resource_name})"
                success, buffered_log = execute_single_step_thread(step, prefix, state_mgr, cloner)
                print(buffered_log, end="")
                if not success:
                    logging.error("Step failed. Stopping execution. Remaining stages preserved.")
                    sys.exit(1)
                    
    logging.info(f"\n{action_label} completed successfully.")


def setup_logging():
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    file_handler = logging.FileHandler('sync.log', mode='a', encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)


def parse_project_map(map_str: str) -> Dict[str, str]:
    # Expects: src_host=dst_host,src_svc1=dst_svc1,...
    project_map = {}
    if not map_str:
        return project_map
        
    pairs = map_str.split(",")
    for pair in pairs:
        if "=" not in pair:
            logging.error(f"Invalid project-map pair format: {pair}. Expected key=value.")
            sys.exit(1)
        k, v = pair.split("=", 1)
        project_map[k.strip()] = v.strip()
    return project_map


def main():
    parser = argparse.ArgumentParser(description="Sync/Replicate GCP environment from scan file (DST.md) using snapshots")
    parser.add_argument("--config", required=True, help="Path to scanned DST.md configuration file")
    parser.add_argument("--project-map", required=True, help="Comma-separated project ID mappings: src_proj=dst_proj,...")
    parser.add_argument("--prepare", action="store_true", help="Only enable required APIs (Compute Engine, DNS) in mapped projects")
    parser.add_argument("--dry-run", action="store_true", help="Show commands without executing")
    parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    args = parser.parse_args()

    setup_logging()

    project_map = parse_project_map(args.project_map)
    state_mgr = StateManager()

    logging.info(f"Parsing scanned configuration: {args.config}")
    org_parser = OrgParser(args.config)
    config = org_parser.parse()

    # Initialize cloner, map project IDs to destination mapping
    cloner = GCPClonerGenerator(config, project_map)
    cloner.apply_project_mapping()
    
    if args.prepare:
        logging.info("Preparing copy target: Enabling required GCP service APIs...")
        # Extract unique destination projects list (Host + Service projects mapped)
        dst_projects = list(set([cloner.config.host_project] + cloner.config.service_projects))
        
        # Generate api enablement stage
        api_stage = build_api_enablement_stage_from_projects(dst_projects)
        execute_sync_stages([api_stage], args.dry_run, args.yes, state_mgr=state_mgr, cloner=cloner, is_prepare=True)
    else:
        stages = cloner.generate_sync_stages()
        execute_sync_stages(stages, args.dry_run, args.yes, state_mgr=state_mgr, cloner=cloner, is_prepare=False)

if __name__ == "__main__":
    main()
