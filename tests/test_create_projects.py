import pytest
import os
import tempfile
import shutil
import yaml
from unittest.mock import MagicMock, patch
from scripts.create_projects import ProjectProvisioner

@pytest.fixture
def temp_dir():
    dirpath = tempfile.mkdtemp()
    yield dirpath
    shutil.rmtree(dirpath)

def test_load_config_and_parse_projects(temp_dir):
    config_data = {
        "global": {
            "log_dir": os.path.join(temp_dir, "logs"),
            "dry_run": False,
            "verbose_logging": True,
            "dst_log_file": "test_dst.log"
        },
        "bootstrap": {
            "org_id": "111122223333",
            "billing_account": "AAAA-BBBB-CCCC"
        },
        "project_mapping": {
            "host_project": {
                "src": "src-host",
                "dst": "dst-host"
            },
            "service_projects": [
                {
                    "src": "src-svc-1",
                    "dst": "dst-svc-1"
                },
                {
                    "src": "src-svc-2",
                    "dst": "dst-svc-2"
                }
            ]
        }
    }
    config_path = os.path.join(temp_dir, "config.yaml")
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config_data, f)
        
    provisioner = ProjectProvisioner(config_path)
    provisioner.load_config()
    
    assert provisioner.dry_run is False
    assert provisioner.verbose is True
    
    # Internal parsing check (simulating provision logic)
    bootstrap = provisioner.config.get('bootstrap', {})
    assert bootstrap.get('org_id') == "111122223333"
    assert bootstrap.get('billing_account') == "AAAA-BBBB-CCCC"
    
    mapping = provisioner.config.get('project_mapping', {})
    projects = []
    if mapping.get('host_project', {}).get('dst'):
        projects.append(mapping['host_project']['dst'])
    for svc in mapping.get('service_projects', []):
        if svc.get('dst'):
            projects.append(svc['dst'])
            
    assert projects == ["dst-host", "dst-svc-1", "dst-svc-2"]

@patch('subprocess.run')
def test_provision_dry_run(mock_run, temp_dir):
    # Setup config
    config_data = {
        "global": {
            "log_dir": os.path.join(temp_dir, "logs"),
            "dry_run": True # DRY RUN
        },
        "bootstrap": {
            "org_id": "111122223333",
            "billing_account": "AAAA-BBBB-CCCC"
        },
        "project_mapping": {
            "host_project": {
                "dst": "dst-host"
            }
        }
    }
    config_path = os.path.join(temp_dir, "config.yaml")
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config_data, f)
        
    # Mock subprocess for read-only existence check (gcloud projects describe)
    # We simulate that the project does NOT exist (describe fails)
    mock_run.return_value = MagicMock(returncode=1, stderr="Not found")
    
    provisioner = ProjectProvisioner(config_path, dry_run_override=True)
    provisioner.provision()
    
    # In dry-run:
    # 1. Describe command (read_only=True) SHOULD be executed
    # 2. Create, Link, Enable commands SHOULD NOT be executed (only logged)
    
    # Find all calls to subprocess.run
    called_cmds = [call[0][0] for call in mock_run.call_args_list]
    
    # describe command should be run
    assert any("gcloud projects describe dst-host" in cmd for cmd in called_cmds)
    
    # mutating commands should NOT be run
    assert not any("gcloud projects create" in cmd for cmd in called_cmds)
    assert not any("billing projects link" in cmd for cmd in called_cmds)
    assert not any("services enable" in cmd for cmd in called_cmds)

@patch('subprocess.run')
def test_provision_production(mock_run, temp_dir):
    config_data = {
        "global": {
            "log_dir": os.path.join(temp_dir, "logs"),
            "dry_run": False # PRODUCTION
        },
        "bootstrap": {
            "org_id": "111122223333",
            "billing_account": "AAAA-BBBB-CCCC"
        },
        "project_mapping": {
            "host_project": {
                "dst": "dst-host"
            }
        }
    }
    config_path = os.path.join(temp_dir, "config.yaml")
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config_data, f)
        
    # Mock subprocess:
    # First call (describe): return 1 (not exists)
    # Second call (create): return 0 (success)
    # Third call (link): return 0 (success)
    # Fourth call (enable): return 0 (success)
    mock_run.side_effect = [
        MagicMock(returncode=1, stderr="Not found"), # Describe fails
        MagicMock(returncode=0, stdout="Created"),   # Create succeeds
        MagicMock(returncode=0, stdout="Linked"),    # Link succeeds
        MagicMock(returncode=0, stdout="Enabled")    # Enable succeeds
    ]
    
    provisioner = ProjectProvisioner(config_path, dry_run_override=False)
    provisioner.provision()
    
    called_cmds = [call[0][0] for call in mock_run.call_args_list]
    
    # All commands should be executed in order
    assert "gcloud projects describe dst-host --format=json" in called_cmds[0]
    assert "gcloud projects create dst-host --organization=111122223333" in called_cmds[1]
    assert "gcloud beta billing projects link dst-host --billing-account=AAAA-BBBB-CCCC" in called_cmds[2]
    assert "gcloud services enable compute.googleapis.com dns.googleapis.com --project=dst-host" in called_cmds[3]
