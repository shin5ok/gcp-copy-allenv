import pytest
from scripts.sync_env import EnvConfig, SubnetConfig, VMConfig, GCPClonerGenerator, parse_project_map

def test_project_mapping_parser():
    map_str = "src-host=dst-host,src-svc1=dst-svc1,src-svc3=dst-svc3"
    proj_map = parse_project_map(map_str)
    
    assert len(proj_map) == 3
    assert proj_map["src-host"] == "dst-host"
    assert proj_map["src-svc1"] == "dst-svc1"
    assert proj_map["src-svc3"] == "dst-svc3"

def test_generator_apply_project_mapping():
    config = EnvConfig(host_project="src-host")
    config.service_projects = ["src-svc1", "src-svc3"]
    config.subnets.append(SubnetConfig(name="subnet-1", ip_range="10.0.1.0/24", project="src-svc1"))
    config.vms["src-svc1"] = [
        VMConfig(name="vm-1", machine_type="e2-medium", image="debian-12", zone="asia-northeast1-a", subnet="subnet-1", ip_address="10.0.1.11")
    ]
    
    proj_map = {
        "src-host": "dst-host",
        "src-svc1": "dst-svc1",
        "src-svc3": "dst-svc3"
    }
    
    cloner = GCPClonerGenerator(config, proj_map)
    cloner.apply_project_mapping()
    
    # Verify mapped values
    assert cloner.config.host_project == "dst-host"
    assert cloner.config.service_projects == ["dst-svc1", "dst-svc3"]
    assert cloner.config.subnets[0].project == "dst-svc1"
    
    # Verify VM map key substitution
    assert "dst-svc1" in cloner.config.vms
    assert "src-svc1" not in cloner.config.vms
    assert cloner.config.vms["dst-svc1"][0].name == "vm-1"

def test_sync_generator_stages():
    config = EnvConfig(host_project="src-host")
    config.service_projects = ["src-svc1"]
    
    # Include both tool-defined subnet and a custom pre-existing subnet to verify filtering
    config.subnets.append(SubnetConfig(name="subnet-svc1", ip_range="10.100.1.0/24", project="src-svc1"))
    config.subnets.append(SubnetConfig(name="tokyo", ip_range="10.0.0.0/16", project="src-host"))
    
    config.vms["src-svc1"] = [
        VMConfig(
            name="org-svc1-deb-e2-std4-01",
            machine_type="e2-standard-4",
            image="debian-12",
            zone="asia-northeast1-a",
            subnet="subnet-svc1",
            ip_address="10.100.1.11"
        )
    ]
    
    proj_map = {
        "src-host": "dst-host",
        "src-svc1": "dst-svc1"
    }
    
    cloner = GCPClonerGenerator(config, proj_map, network_name="dst-vpc", region="asia-northeast1")
    cloner.apply_project_mapping()
    stages = cloner.generate_sync_stages()
    
    # Expected 4 replication stages
    assert len(stages) == 4
    
    # Stage 1: VPC & Host Setup (Sequential)
    assert stages[0].name == "VPC & Host Setup"
    assert not stages[0].is_parallel
    assert len(stages[0].steps) == 6
    # Assert destination host project substitution in VPC network step
    assert stages[0].steps[0].resource_type == "VPC Network"
    assert stages[0].steps[0].check_cmd == "gcloud compute networks describe dst-vpc --project=dst-host --format='value(name)'"
    assert stages[0].steps[0].create_cmd == "gcloud compute networks create dst-vpc --subnet-mode=custom --project=dst-host"
    
    # Stage 2: Subnet Creation (Parallel)
    assert stages[1].name == "Subnet Creation"
    assert stages[1].is_parallel
    # Verify that 'tokyo' subnet is filtered out because it doesn't start with 'subnet-'
    assert len(stages[1].steps) == 1
    assert stages[1].steps[0].resource_type == "Subnet"
    assert stages[1].steps[0].resource_name == "subnet-svc1"
    assert stages[1].steps[0].check_cmd == "gcloud compute networks subnets describe subnet-svc1 --region=asia-northeast1 --project=dst-host --format='value(name)'"
    assert stages[1].steps[0].create_cmd == "gcloud compute networks subnets create subnet-svc1 --network=dst-vpc --range=10.100.1.0/24 --region=asia-northeast1 --project=dst-host"

    # Stage 3: Disk Cloning from Snapshot (Parallel) - RESTORE STAGE
    assert stages[2].name == "Disk Cloning from Snapshot"
    assert stages[2].is_parallel
    assert len(stages[2].steps) == 1
    
    # Disk restore command must point to ORIGINAL snapshot path but create disk in DESTINATION project
    assert stages[2].steps[0].resource_type == "Disk"
    assert stages[2].steps[0].resource_name == "org-svc1-deb-e2-std4-01-disk"
    assert stages[2].steps[0].check_cmd == "gcloud compute disks describe org-svc1-deb-e2-std4-01-disk --zone=asia-northeast1-a --project=dst-svc1 --format='value(name)'"
    # source-snapshot points to global path in original 'src-svc1' project, while project is destination 'dst-svc1'
    assert stages[2].steps[0].create_cmd == "gcloud compute disks create org-svc1-deb-e2-std4-01-disk --source-snapshot=projects/src-svc1/global/snapshots/org-svc1-deb-e2-std4-01 --zone=asia-northeast1-a --project=dst-svc1 --quiet"

    # Stage 4: VM Cloned Provisioning (Parallel) - LAUNCH STAGE
    assert stages[3].name == "VM Cloned Provisioning"
    assert stages[3].is_parallel
    assert len(stages[3].steps) == 1
    
    assert stages[3].steps[0].resource_type == "VM Instance"
    assert stages[3].steps[0].resource_name == "org-svc1-deb-e2-std4-01"
    assert stages[3].steps[0].check_cmd == "gcloud compute instances describe org-svc1-deb-e2-std4-01 --zone=asia-northeast1-a --project=dst-svc1 --format='value(name)'"
    # Launches from restored boot disk 'org-svc1-deb-e2-std4-01-disk', auto-delete=yes, no metadata startup-script
    subnet_path = "projects/dst-host/regions/asia-northeast1/subnetworks/subnet-svc1"
    assert stages[3].steps[0].create_cmd == f"gcloud compute instances create org-svc1-deb-e2-std4-01 --disk=name=org-svc1-deb-e2-std4-01-disk,boot=yes,auto-delete=yes --subnet={subnet_path} --private-network-ip=10.100.1.11 --zone=asia-northeast1-a --project=dst-svc1 --no-address --quiet"

def test_build_api_enablement_stage_from_projects():
    from scripts.sync_env import build_api_enablement_stage_from_projects
    
    projects = ["dst-host", "dst-svc1"]
    stage = build_api_enablement_stage_from_projects(projects)
    
    assert stage.name == "API Enablement"
    assert stage.is_parallel
    assert len(stage.steps) == 2
    
    # Assert Step 1: dst-host
    assert stage.steps[0].resource_type == "API"
    assert stage.steps[0].resource_name == "dst-host"
    assert stage.steps[0].project == "dst-host"
    assert stage.steps[0].check_cmd == "gcloud services list --enabled --project=dst-host --format='value(config.name)' | grep -w compute.googleapis.com"
    assert stage.steps[0].create_cmd == "gcloud services enable compute.googleapis.com dns.googleapis.com --project=dst-host --quiet"
    
    # Assert Step 2: dst-svc1
    assert stage.steps[1].resource_type == "API"
    assert stage.steps[1].resource_name == "dst-svc1"
    assert stage.steps[1].project == "dst-svc1"
    assert stage.steps[1].check_cmd == "gcloud services list --enabled --project=dst-svc1 --format='value(config.name)' | grep -w compute.googleapis.com"
    assert stage.steps[1].create_cmd == "gcloud services enable compute.googleapis.com dns.googleapis.com --project=dst-svc1 --quiet"

