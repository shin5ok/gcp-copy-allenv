#!/usr/bin/env python3
import argparse
import re
import sys
import subprocess
import json
import logging
import os
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple

# Re-use data structures from build_env for consistency
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


class GCPScanner:
    def __init__(self, host_project: str, network_name: str, dry_run: bool = False):
        self.host_project = host_project
        self.network_name = network_name
        self.dry_run = dry_run
        self.config = EnvConfig(host_project=host_project)
        self.region = "asia-northeast1" # Default region for scoping scan, can be dynamic

    def _run_gcloud(self, cmd_str: str) -> Optional[str]:
        if self.dry_run:
            print(f"[DRY RUN] Executing: {cmd_str}")
            return None
            
        logging.debug(f"Executing scan command: {cmd_str}")
        try:
            result = subprocess.run(cmd_str, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            logging.error(f"Command failed: {cmd_str}")
            logging.error(f"Stderr: {e.stderr}")
            # Don't exit immediately, return None to allow partial scans if some projects are unreachable
            return None

    def scan(self) -> EnvConfig:
        print(f"Scanning Host Project: {self.host_project}...")
        
        # 1. Verify Shared VPC Host (list command errors out if Shared VPC is not enabled)
        host_chk_res = subprocess.run(
            f"gcloud compute shared-vpc associated-projects list --project={self.host_project}",
            shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        if not self.dry_run and host_chk_res.returncode != 0:
            logging.error(f"Project {self.host_project} is not an enabled Shared VPC Host project. Stderr: {host_chk_res.stderr.strip()}")
            sys.exit(1)

        # 2. Scan Associated Service Projects
        assoc_output = self._run_gcloud(f"gcloud compute shared-vpc associated-projects list --project={self.host_project} --format='value(id)'")
        if assoc_output:
            self.config.service_projects = [p.strip() for p in assoc_output.split("\n") if p.strip()]
        print(f"  Found associated Service Projects: {self.config.service_projects}")

        # 3. Scan Subnets in shared-vpc
        print(f"Scanning Subnets in network '{self.network_name}'...")
        subnets_json = self._run_gcloud(f"gcloud compute networks subnets list --network={self.network_name} --project={self.host_project} --format='json(name, ipCidrRange, region)'")
        if subnets_json:
            try:
                subnets_data = json.loads(subnets_json)
                for sub in subnets_data:
                    # Region is returned as a full URL path: 'https://.../regions/asia-northeast1'
                    region_name = sub["region"].split("/")[-1]
                    self.region = region_name # SCOPE: Update region to match detected VPC region
                    
                    # Find which service project this subnet belongs to or is shared with.
                    # In this simplified scan, we map subnets by parsing their name suffix or using ORG defaults.
                    # Typically Shared VPC subnets are shared to all associated service projects via IAM.
                    # For the output configuration, we map subnet-svc1 to svc1 and subnet-svc3 to svc3.
                    mapped_svc = self.host_project # Fallback
                    if "svc1" in sub["name"]:
                        mapped_svc = self.config.service_projects[0] if len(self.config.service_projects) > 0 else self.host_project
                    elif "svc3" in sub["name"]:
                        mapped_svc = self.config.service_projects[1] if len(self.config.service_projects) > 1 else self.host_project

                    self.config.subnets.append(SubnetConfig(
                        name=sub["name"],
                        ip_range=sub["ipCidrRange"],
                        project=mapped_svc
                    ))
            except json.JSONDecodeError as e:
                logging.error(f"Failed to parse subnets JSON: {e}")

        # 4. Scan VMs in each Service Project
        for svc_proj in self.config.service_projects:
            print(f"Scanning VMs in Service Project: {svc_proj}...")
            vms_json = self._run_gcloud(f"gcloud compute instances list --project={svc_proj} --format='json(name, zone, machineType, networkInterfaces)'")
            if not vms_json:
                continue
                
            try:
                vms_data = json.loads(vms_json)
                for vm in vms_data:
                    vm_name = vm["name"]
                    # Filter out non-tool VM instances (like instance-1 that blocked destroy)
                    if not vm_name.startswith("org-"):
                        print(f"  Skipping non-tool VM instance: {vm_name}")
                        continue
                        
                    zone_name = vm["zone"].split("/")[-1]
                    machine_type = vm["machineType"].split("/")[-1]
                    
                    # Parse Network Interface for Private IP and Subnet Name
                    net_inf = vm["networkInterfaces"][0]
                    private_ip = net_inf.get("networkIP", "")
                    subnet_name = net_inf.get("subnetwork", "").split("/")[-1]
                    
                    # Scan for OS image details by describing VM disk license
                    os_image = "debian-12" # Fallback
                    vm_desc_json = self._run_gcloud(f"gcloud compute instances describe {vm_name} --zone={zone_name} --project={svc_proj} --format='json(disks)'")
                    if vm_desc_json:
                        try:
                            vm_desc = json.loads(vm_desc_json)
                            disks_list = vm_desc.get("disks", [])
                            boot_disk = next((d for d in disks_list if d.get("boot")), None)
                            if boot_disk and "licenses" in boot_disk:
                                for lic in boot_disk["licenses"]:
                                    if "debian-cloud" in lic:
                                        os_image = "debian-12"
                                        break
                                    elif "ubuntu-os-cloud" in lic:
                                        os_image = "ubuntu-2204-lts"
                                        break
                        except Exception as e:
                            logging.warning(f"Failed to detect OS image for {vm_name}, defaulting to debian-12: {e}")

                    if svc_proj not in self.config.vms:
                        self.config.vms[svc_proj] = []
                        
                    self.config.vms[svc_proj].append(VMConfig(
                        name=vm_name,
                        machine_type=machine_type,
                        image=os_image,
                        zone=zone_name,
                        subnet=subnet_name,
                        ip_address=private_ip
                    ))
                    print(f"  Found VM: {vm_name} ({machine_type}, {os_image}, {private_ip} in {subnet_name})")
            except json.JSONDecodeError as e:
                logging.error(f"Failed to parse VMs JSON for project {svc_proj}: {e}")

        return self.config


class MarkdownRenderer:
    def __init__(self, config: EnvConfig, network_name: str = "shared-vpc", region: str = "asia-northeast1"):
        self.config = config
        self.network_name = network_name
        self.region = region

    def render(self) -> str:
        doc = []
        doc.append("# コピー元環境実機スキャン構成定義 (DST)")
        doc.append("\nこのファイルは、実機環境の自動スキャン結果に基づいて自動生成されました。")
        doc.append("この構成情報を複製同期先 (Destination) 環境の構築に適用します。")
        
        # 1. Projects
        doc.append("\n## 1. プロジェクト構造")
        doc.append("\n| ロール | プロジェクトID | 備考 |")
        doc.append("| :--- | :--- | :--- |")
        doc.append(f"| **Host Project** | `{self.config.host_project}` | 共有VPCホスト |")
        for i, svc_proj in enumerate(self.config.service_projects, 1):
            doc.append(f"| **Service Project {i}** | `{svc_proj}` | リソース配置先 |")

        # 2. Network
        doc.append("\n---")
        doc.append("\n## 2. ネットワーク構成 (共有VPC)")
        doc.append(f"\nホストプロジェクトで管理され、サービスプロジェクトに共有されるネットワークリソースの定義。")
        doc.append(f"\n- **共有VPCネットワーク名**: `{self.network_name}`")
        doc.append(f"- **リージョン**: `{self.region}` (東京)")
        
        doc.append("\n### 2.1. サブネット定義")
        doc.append("\n| サブネット名 | IP範囲 | 共有先プロジェクト | 備考 |")
        doc.append("| :--- | :--- | :--- | :--- |")
        for subnet in self.config.subnets:
            doc.append(f"| `{subnet.name}` | `{subnet.ip_range}` | `{subnet.project}` | 自動スキャンによる検出 |")

        doc.append("\n### 2.2. インターネット接続ゲートウェイ (Cloud NAT)")
        doc.append("\nプライベートVMが外部インターネットへ発信通信を行えるように配置するゲートウェイ。")
        doc.append("\n| ゲートウェイタイプ | リソース名 | 紐付け先ネットワーク/ルーター | 設定詳細 |")
        doc.append("| :--- | :--- | :--- | :--- |")
        doc.append(f"| **Cloud Router** | `shared-router` | `{self.network_name}` | リージョン: `{self.region}` |")
        doc.append(f"| **Cloud NAT** | `shared-nat` | `shared-router` | すべてのサブネットの全IP範囲を対象、外部IP自動割り当て |")

        # 3. VMs
        doc.append("\n---")
        doc.append("\n## 3. VMインスタンス構成 (固定IP割り当て)")
        doc.append("\n全てのインスタンスは、デフォルトで以下の設定を共有します。")
        doc.append(f"- **ゾーン (Zone)**: `{self.region}-a`")
        doc.append("- **ネットワークカード設定**: 外部IPなし（プライベートIPのみ、上記 Cloud NAT 経由でインターネット接続）")

        for proj_id, vms in self.config.vms.items():
            # Find role name
            role_name = "Service Project"
            if proj_id == self.config.host_project:
                role_name = "Host Project"
            else:
                # Service project index
                idx = self.config.service_projects.index(proj_id) + 1
                role_name = f"Service Project {idx}"
                
            doc.append(f"\n### 3.{idx}. {role_name} (`{proj_id}`)")
            
            # Find default OS from VMs
            default_os = vms[0].image if vms else "debian-12"
            os_display = "Debian 12" if default_os == "debian-12" else "Ubuntu 22.04 LTS"
            doc.append(f"\nOSはすべて **{os_display}** (`{default_os}`) を使用します。")
            
            doc.append("\n| インスタンス名 | マシンタイプ | OSイメージ | ゾーン | サブネット | 内部固定IPアドレス |")
            doc.append("| :--- | :--- | :--- | :--- | :--- | :--- |")
            for vm in vms:
                doc.append(f"| `{vm.name}` | `{vm.machine_type}` | `{vm.image}` | `{vm.zone}` | `{vm.subnet}` | `{vm.ip_address}` |")

        return "\n".join(doc) + "\n"

def main():
    parser = argparse.ArgumentParser(description="Scan GCP Shared VPC and generate DST.md config")
    parser.add_argument("--project", required=True, help="Shared VPC Host Project ID")
    parser.add_argument("--network", default="shared-vpc", help="Shared VPC Network Name")
    parser.add_argument("--output", default="dst/DST.md", help="Path to save generated DST.md config")
    parser.add_argument("--dry-run", action="store_true", help="Show gcloud commands without executing them")
    args = parser.parse_args()

    # In scan mode, we print results directly to stdout, but setup simple warning logging for CLI
    logging.basicConfig(level=logging.WARNING, format='[%(levelname)s] %(message)s')

    scanner = GCPScanner(host_project=args.project, network_name=args.network, dry_run=args.dry_run)
    config = scanner.scan()
    
    if not args.dry_run:
        renderer = MarkdownRenderer(config, network_name=args.network, region=scanner.region)
        rendered_md = renderer.render()
        
        # Ensure output directory exists
        output_dir = os.path.dirname(args.output)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir)
            
        print(f"\nSaving scanned configuration to: {args.output}...")
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(rendered_md)
        print("Save successful.")

if __name__ == "__main__":
    main()
