import os
import tempfile
import pytest
from scripts.build_env import OrgParser, GcloudCommandGenerator, EnvConfig, SubnetConfig, VMConfig

# Dummy ORG.md content for testing
DUMMY_ORG_CONTENT = """# オリジナル環境構成定義 (ORG)

## 1. プロジェクト構造

| ロール | プロジェクトID | 備考 |
| :--- | :--- | :--- |
| **Host Project** | `test-host-proj` | 共有VPCホスト |
| **Service Project 1** | `test-svc-proj-1` | Debian |

---

## 2. ネットワーク構成 (共有VPC)

| サブネット名 | IP範囲 | 共有先プロジェクト | 備考 |
| :--- | :--- | :--- | :--- |
| `subnet-1` | `10.0.1.0/24` | `test-svc-proj-1` | Debian VM用 |

---

## 3. VMインスタンス構成 (固定IP割り当て)

### 3.1. Service Project 1 (`test-svc-proj-1`)

OSはすべて **Debian 12** (`debian-12`) を使用します。

| インスタンス名 | マシンタイプ | OSイメージ | ゾーン | サブネット | 内部固定IPアドレス |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `vm-deb-01` | `e2-standard-4` | `debian-12` | `asia-northeast1-a` | `subnet-1` | `10.0.1.11` |
"""

@pytest.fixture
def temp_org_file():
    with tempfile.NamedTemporaryFile(mode='w+', suffix='.md', delete=False, encoding='utf-8') as tmp:
        tmp.write(DUMMY_ORG_CONTENT)
        tmp_path = tmp.name
    yield tmp_path
    os.remove(tmp_path)

def test_parser_success(temp_org_file):
    parser = OrgParser(temp_org_file)
    config = parser.parse()

    assert config.host_project == "test-host-proj"
    assert config.service_projects == ["test-svc-proj-1"]
    
    assert len(config.subnets) == 1
    assert config.subnets[0] == SubnetConfig(name="subnet-1", ip_range="10.0.1.0/24", project="test-svc-proj-1")
    
    assert "test-svc-proj-1" in config.vms
    assert len(config.vms["test-svc-proj-1"]) == 1
    
    expected_vm = VMConfig(
        name="vm-deb-01",
        machine_type="e2-standard-4",
        image="projects/debian-cloud/global/images/family/debian-12",
        zone="asia-northeast1-a",
        subnet="subnet-1",
        ip_address="10.0.1.11"
    )
    assert config.vms["test-svc-proj-1"][0] == expected_vm

def test_command_generator_stages(temp_org_file):
    parser = OrgParser(temp_org_file)
    config = parser.parse()
    
    generator = GcloudCommandGenerator(config, network_name="test-vpc", region="asia-northeast1")
    stages = generator.generate_stages()
    
    # Expected 4 stages
    assert len(stages) == 4
    
    # Stage 1: VPC & Host Setup (Sequential)
    assert stages[0].name == "VPC & Host Setup"
    assert not stages[0].is_parallel
    assert len(stages[0].steps) == 6 # VPC, Shared VPC, Associated Project, Router, NAT, Firewall
    
    # VPC Network
    assert stages[0].steps[0].resource_type == "VPC Network"
    assert stages[0].steps[0].check_cmd == "gcloud compute networks describe test-vpc --project=test-host-proj --format='value(name)'"
    assert stages[0].steps[0].create_cmd == "gcloud compute networks create test-vpc --subnet-mode=custom --project=test-host-proj"
    
    # Shared VPC Host Enable
    assert stages[0].steps[1].resource_type == "Shared VPC Host"
    assert stages[0].steps[1].check_cmd == "gcloud compute shared-vpc associated-projects list --project=test-host-proj"
    assert stages[0].steps[1].create_cmd == "gcloud compute shared-vpc enable test-host-proj"
    
    # Associated Project
    assert stages[0].steps[2].resource_type == "Shared VPC Associated Project"
    assert stages[0].steps[2].check_cmd == "gcloud compute shared-vpc associated-projects list --project=test-host-proj --format='value(id)' | grep -w test-svc-proj-1"
    assert stages[0].steps[2].create_cmd == "gcloud compute shared-vpc associated-projects add test-svc-proj-1 --host-project=test-host-proj"

    # Cloud Router
    assert stages[0].steps[3].resource_type == "Cloud Router"
    assert stages[0].steps[3].check_cmd == "gcloud compute routers describe shared-router --region=asia-northeast1 --project=test-host-proj --format='value(name)'"
    assert stages[0].steps[3].create_cmd == "gcloud compute routers create shared-router --network=test-vpc --region=asia-northeast1 --project=test-host-proj"

    # Cloud NAT
    assert stages[0].steps[4].resource_type == "Cloud NAT"
    assert stages[0].steps[4].check_cmd == "gcloud compute routers describe shared-router --region=asia-northeast1 --project=test-host-proj --format='value(nats.name)' | grep -w shared-nat"
    assert stages[0].steps[4].create_cmd == "gcloud compute routers nats create shared-nat --router=shared-router --region=asia-northeast1 --auto-allocate-nat-external-ips --nat-all-subnet-ip-ranges --project=test-host-proj"

    # Firewall Rule
    assert stages[0].steps[5].resource_type == "Firewall Rule"
    assert stages[0].steps[5].check_cmd == "gcloud compute firewall-rules describe allow-shared-iap-ssh --project=test-host-proj --format='value(name)'"
    assert stages[0].steps[5].create_cmd == "gcloud compute firewall-rules create allow-shared-iap-ssh --network=test-vpc --allow=tcp:22 --source-ranges=35.235.240.0/20 --direction=INGRESS --project=test-host-proj"

    # Stage 2: Subnets (Parallel)
    assert stages[1].name == "Subnet Creation"
    assert stages[1].is_parallel
    assert len(stages[1].steps) == 1
    assert stages[1].steps[0].resource_type == "Subnet"
    assert stages[1].steps[0].check_cmd == "gcloud compute networks subnets describe subnet-1 --region=asia-northeast1 --project=test-host-proj --format='value(name)'"
    assert stages[1].steps[0].create_cmd == "gcloud compute networks subnets create subnet-1 --network=test-vpc --range=10.0.1.0/24 --region=asia-northeast1 --project=test-host-proj"

    # Stage 3: Static IPs (Parallel)
    assert stages[2].name == "IP Reservation"
    assert stages[2].is_parallel
    assert len(stages[2].steps) == 1
    assert stages[2].steps[0].resource_type == "Static Private IP Address"
    subnet_path = "projects/test-host-proj/regions/asia-northeast1/subnetworks/subnet-1"
    assert stages[2].steps[0].check_cmd == "gcloud compute addresses describe vm-deb-01-ip --region=asia-northeast1 --project=test-svc-proj-1 --format='value(name)'"
    assert stages[2].steps[0].create_cmd == f"gcloud compute addresses create vm-deb-01-ip --addresses=10.0.1.11 --subnet={subnet_path} --region=asia-northeast1 --project=test-svc-proj-1"

    # Stage 4: VMs (Parallel)
    assert stages[3].name == "VM Provisioning"
    assert stages[3].is_parallel
    assert len(stages[3].steps) == 1
    assert stages[3].steps[0].resource_type == "VM Instance"
    assert stages[3].steps[0].check_cmd == "gcloud compute instances describe vm-deb-01 --zone=asia-northeast1-a --project=test-svc-proj-1 --format='value(name)'"
    assert stages[3].steps[0].create_cmd == f"gcloud compute instances create vm-deb-01 --machine-type=e2-standard-4 --image=projects/debian-cloud/global/images/family/debian-12 --subnet={subnet_path} --private-network-ip=10.0.1.11 --zone=asia-northeast1-a --project=test-svc-proj-1 --no-address --metadata-from-file=startup-script=nginx_startup.sh"

def test_command_generator_destroy_stages_from_state():
    from scripts.build_env import build_destroy_stages_from_state
    
    # Mock state data representing resources in creation order (VPC -> Router -> NAT -> Firewall -> Subnet -> IP -> VM)
    dummy_state = [
        {
            "resource_type": "VPC Network",
            "resource_name": "test-vpc",
            "project": "test-host-proj",
            "check_cmd": "gcloud compute networks describe test-vpc --project=test-host-proj --format='value(name)'",
            "delete_cmd": "gcloud compute networks delete test-vpc --project=test-host-proj --quiet"
        },
        {
            "resource_type": "Cloud Router",
            "resource_name": "shared-router",
            "project": "test-host-proj",
            "check_cmd": "gcloud compute routers describe shared-router --region=asia-northeast1 --project=test-host-proj --format='value(name)'",
            "delete_cmd": "gcloud compute routers delete shared-router --region=asia-northeast1 --project=test-host-proj --quiet"
        },
        {
            "resource_type": "Cloud NAT",
            "resource_name": "shared-nat in shared-router",
            "project": "test-host-proj",
            "check_cmd": "gcloud compute routers describe shared-router --region=asia-northeast1 --project=test-host-proj --format='value(nats.name)' | grep -w shared-nat",
            "delete_cmd": "gcloud compute routers nats delete shared-nat --router=shared-router --region=asia-northeast1 --project=test-host-proj --quiet"
        },
        {
            "resource_type": "Firewall Rule",
            "resource_name": "allow-shared-iap-ssh",
            "project": "test-host-proj",
            "check_cmd": "gcloud compute firewall-rules describe allow-shared-iap-ssh --project=test-host-proj --format='value(name)'",
            "delete_cmd": "gcloud compute firewall-rules delete allow-shared-iap-ssh --project=test-host-proj --quiet"
        },
        {
            "resource_type": "Subnet",
            "resource_name": "subnet-1",
            "project": "test-host-proj",
            "check_cmd": "gcloud compute networks subnets describe subnet-1 --region=asia-northeast1 --project=test-host-proj --format='value(name)'",
            "delete_cmd": "gcloud compute networks subnets delete subnet-1 --region=asia-northeast1 --project=test-host-proj --quiet"
        },
        {
            "resource_type": "Static Private IP Address",
            "resource_name": "vm-deb-01-ip",
            "project": "test-svc-proj-1",
            "check_cmd": "gcloud compute addresses describe vm-deb-01-ip --region=asia-northeast1 --project=test-svc-proj-1 --format='value(name)'",
            "delete_cmd": "gcloud compute addresses delete vm-deb-01-ip --region=asia-northeast1 --project=test-svc-proj-1 --quiet"
        },
        {
            "resource_type": "VM Instance",
            "resource_name": "vm-deb-01",
            "project": "test-svc-proj-1",
            "check_cmd": "gcloud compute instances describe vm-deb-01 --zone=asia-northeast1-a --project=test-svc-proj-1 --format='value(name)'",
            "delete_cmd": "gcloud compute instances delete vm-deb-01 --zone=asia-northeast1-a --project=test-svc-proj-1 --quiet"
        }
    ]
    
    stages = build_destroy_stages_from_state(dummy_state)
    
    # Expected 7 stages (reverse order: VM -> IP -> NAT -> Router -> Firewall -> Subnet -> VPC)
    assert len(stages) == 7
    
    # Stage 1: VM Instances (Parallel)
    assert stages[0].name == "VM Destruction"
    assert stages[0].is_parallel
    assert len(stages[0].steps) == 1
    assert stages[0].steps[0].resource_type == "VM Instance"
    assert stages[0].steps[0].check_cmd == "gcloud compute instances describe vm-deb-01 --zone=asia-northeast1-a --project=test-svc-proj-1 --format='value(name)'"
    assert stages[0].steps[0].create_cmd == "gcloud compute instances delete vm-deb-01 --zone=asia-northeast1-a --project=test-svc-proj-1 --quiet"

    # Stage 2: Static IPs (Parallel)
    assert stages[1].name == "IP Release"
    assert stages[1].is_parallel
    assert len(stages[1].steps) == 1
    assert stages[1].steps[0].resource_type == "Static Private IP Address"
    assert stages[1].steps[0].check_cmd == "gcloud compute addresses describe vm-deb-01-ip --region=asia-northeast1 --project=test-svc-proj-1 --format='value(name)'"
    assert stages[1].steps[0].create_cmd == "gcloud compute addresses delete vm-deb-01-ip --region=asia-northeast1 --project=test-svc-proj-1 --quiet"

    # Stage 3: Cloud NAT (Parallel)
    assert stages[2].name == "NAT Destruction"
    assert stages[2].is_parallel
    assert len(stages[2].steps) == 1
    assert stages[2].steps[0].resource_type == "Cloud NAT"
    assert stages[2].steps[0].check_cmd == "gcloud compute routers describe shared-router --region=asia-northeast1 --project=test-host-proj --format='value(nats.name)' | grep -w shared-nat"
    assert stages[2].steps[0].create_cmd == "gcloud compute routers nats delete shared-nat --router=shared-router --region=asia-northeast1 --project=test-host-proj --quiet"

    # Stage 4: Cloud Router (Parallel)
    assert stages[3].name == "Router Destruction"
    assert stages[3].is_parallel
    assert len(stages[3].steps) == 1
    assert stages[3].steps[0].resource_type == "Cloud Router"
    assert stages[3].steps[0].check_cmd == "gcloud compute routers describe shared-router --region=asia-northeast1 --project=test-host-proj --format='value(name)'"
    assert stages[3].steps[0].create_cmd == "gcloud compute routers delete shared-router --region=asia-northeast1 --project=test-host-proj --quiet"

    # Stage 5: Firewall Rule (Parallel)
    assert stages[4].name == "Firewall Destruction"
    assert stages[4].is_parallel
    assert len(stages[4].steps) == 1
    assert stages[4].steps[0].resource_type == "Firewall Rule"
    assert stages[4].steps[0].check_cmd == "gcloud compute firewall-rules describe allow-shared-iap-ssh --project=test-host-proj --format='value(name)'"
    assert stages[4].steps[0].create_cmd == "gcloud compute firewall-rules delete allow-shared-iap-ssh --project=test-host-proj --quiet"

    # Stage 6: Subnets (Parallel)
    assert stages[5].name == "Subnet Deletion"
    assert stages[5].is_parallel
    assert len(stages[5].steps) == 1
    assert stages[5].steps[0].resource_type == "Subnet"
    assert stages[5].steps[0].check_cmd == "gcloud compute networks subnets describe subnet-1 --region=asia-northeast1 --project=test-host-proj --format='value(name)'"
    assert stages[5].steps[0].create_cmd == "gcloud compute networks subnets delete subnet-1 --region=asia-northeast1 --project=test-host-proj --quiet"

    # Stage 7: VPC & Host Cleanup (Sequential)
    assert stages[6].name == "VPC & Host Cleanup"
    assert not stages[6].is_parallel
    assert len(stages[6].steps) == 1
    
    # VPC Network delete
    assert stages[6].steps[0].resource_type == "VPC Network"
    assert stages[6].steps[0].check_cmd == "gcloud compute networks describe test-vpc --project=test-host-proj --format='value(name)'"
    assert stages[6].steps[0].create_cmd == "gcloud compute networks delete test-vpc --project=test-host-proj --quiet"

def test_build_snapshot_stage_from_state():
    from scripts.build_env import build_snapshot_stage_from_state
    
    # Dummy state representing mixed resources
    dummy_state = [
        {
            "resource_type": "VPC Network",
            "resource_name": "test-vpc",
            "project": "test-host-proj",
            "check_cmd": "...",
            "delete_cmd": "..."
        },
        {
            "resource_type": "VM Instance",
            "resource_name": "vm-deb-01",
            "project": "test-svc-proj-1",
            "check_cmd": "gcloud compute instances describe vm-deb-01 --zone=asia-northeast1-a --project=test-svc-proj-1 --format='value(name)'",
            "delete_cmd": "..."
        },
        {
            "resource_type": "Static Private IP Address",
            "resource_name": "vm-deb-01-ip",
            "project": "test-svc-proj-1",
            "check_cmd": "...",
            "delete_cmd": "..."
        }
    ]
    
    stage = build_snapshot_stage_from_state(dummy_state)
    
    # Verify that ONLY the VM Instance is selected for snapshotting
    assert stage.name == "Snapshot Creation"
    assert stage.is_parallel
    assert len(stage.steps) == 1
    
    assert stage.steps[0].resource_type == "Snapshot"
    assert stage.steps[0].resource_name == "vm-deb-01"
    assert stage.steps[0].project == "test-svc-proj-1"
    assert stage.steps[0].check_cmd == "gcloud compute snapshots describe vm-deb-01 --project=test-svc-proj-1 --format='value(name)'"
    assert stage.steps[0].create_cmd == "gcloud compute snapshots create vm-deb-01 --source-disk=vm-deb-01 --source-disk-zone=asia-northeast1-a --project=test-svc-proj-1 --quiet"

