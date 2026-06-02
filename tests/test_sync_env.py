import pytest
import os
import tempfile
import shutil
import yaml
from scripts.sync_env import MigrationOrchestrator

@pytest.fixture
def temp_dir():
    dirpath = tempfile.mkdtemp()
    yield dirpath
    shutil.rmtree(dirpath)

def test_load_config(temp_dir):
    config_data = {
        "global": {
            "log_dir": os.path.join(temp_dir, "logs"),
            "dry_run": False,
            "verbose_logging": True,
            "org_log_file": "custom_org.log",
            "dst_log_file": "custom_dst.log"
        },
        "project_mapping": {
            "host_project": {
                "src": "src-host",
                "dst": "dst-host"
            }
        },
        "steps": {
            "cai_scan": {"enabled": True}
        }
    }
    config_path = os.path.join(temp_dir, "config.yaml")
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config_data, f)
        
    orchestrator = MigrationOrchestrator(config_path)
    orchestrator.load_config()
    
    assert orchestrator.dry_run is False
    assert orchestrator.verbose is True
    assert orchestrator.config["project_mapping"]["host_project"]["src"] == "src-host"
    assert os.path.exists(os.path.join(temp_dir, "logs"))

def test_customize_hcl(temp_dir):
    # Prepare directories
    raw_dir = os.path.join(temp_dir, "raw")
    active_dir = os.path.join(temp_dir, "active")
    os.makedirs(raw_dir)
    
    # Sample HCL content
    sample_vm_hcl = """
resource "google_compute_instance" "my_vm" {
  name         = "org-vm-01"
  project      = "src-service-1"
  zone         = "asia-northeast1-a"
  boot_disk {
    auto_delete = true
    device_name = "persistent-disk-0"
    initialize_params {
      image = "debian-12"
    }
    source = "https://www.googleapis.com/compute/v1/projects/src-service-1/zones/asia-northeast1-a/disks/org-vm-01"
  }
  network_interface {
    network = "https://www.googleapis.com/compute/v1/projects/src-host/global/networks/shared-vpc"
  }
}
"""
    sample_bucket_hcl = """
resource "google_storage_bucket" "my_bucket" {
  name     = "src-bucket-data"
  project  = "src-service-1"
  location = "US"
}
"""
    # Write sample files
    with open(os.path.join(raw_dir, "vm.tf"), "w", encoding="utf-8") as f:
        f.write(sample_vm_hcl)
    with open(os.path.join(raw_dir, "bucket.tf"), "w", encoding="utf-8") as f:
        f.write(sample_bucket_hcl)
        
    # Prepare config
    config_data = {
        "global": {
            "log_dir": os.path.join(temp_dir, "logs"),
            "dry_run": False # Must be False to actually write customized files
        },
        "project_mapping": {
            "host_project": {
                "src": "src-host",
                "dst": "dst-host"
            },
            "service_projects": [
                {
                    "src": "src-service-1",
                    "dst": "dst-service-1"
                }
            ]
        },
        "rename_rules": {
            "gcs": {
                "method": "suffix",
                "value": "-dst-0602"
            }
        }
    }
    config_path = os.path.join(temp_dir, "config.yaml")
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config_data, f)
        
    orchestrator = MigrationOrchestrator(config_path)
    orchestrator.load_config()
    
    # Run customization
    orchestrator.customize_hcl(raw_dir, active_dir)
    
    # Verify VM customisation
    custom_vm_path = os.path.join(active_dir, "vm.tf")
    assert os.path.exists(custom_vm_path)
    with open(custom_vm_path, "r", encoding="utf-8") as f:
        vm_content = f.read()
        
    assert "dst-service-1" in vm_content
    assert "src-service-1" not in vm_content
    assert "dst-host" in vm_content
    assert "src-host" not in vm_content
    # Boot disk source line should be removed
    assert "source =" not in vm_content
    assert "device_name =" in vm_content # Other parts preserved
    
    # Verify Bucket customisation
    custom_bucket_path = os.path.join(active_dir, "bucket.tf")
    assert os.path.exists(custom_bucket_path)
    with open(custom_bucket_path, "r", encoding="utf-8") as f:
        bucket_content = f.read()
        
    assert "dst-service-1" in bucket_content
    # Bucket name renamed with suffix
    assert 'name     = "src-bucket-data-dst-0602"' in bucket_content
