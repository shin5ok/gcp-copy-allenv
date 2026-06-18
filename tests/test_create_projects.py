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


def _write_yaml(path, data):
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f)


def test_load_config_and_parse_projects(temp_dir):
    config_data = {
        "global": {
            "log_dir": os.path.join(temp_dir, "logs"),
            "dry_run": False,
            "verbose_logging": True,
            "dst_log_file": "test_dst.log",
        },
        "bootstrap": {
            "org_id": "111122223333",
            "billing_account": "AAAA-BBBB-CCCC",
        },
        "project_mapping": {
            "host_project": {"src": "src-host", "dst": "dst-host"},
            "service_projects": [
                {"src": "src-svc-1", "dst": "dst-svc-1"},
                {"src": "src-svc-2", "dst": "dst-svc-2"},
            ],
        },
    }
    config_path = os.path.join(temp_dir, "config.yaml")
    _write_yaml(config_path, config_data)

    p = ProjectProvisioner(config_path)
    p.load_config()
    assert p.dry_run is False
    assert p.verbose is True


def test_validate_mapping_rejects_src_eq_dst(temp_dir):
    config_data = {
        "global": {"log_dir": os.path.join(temp_dir, "logs"), "dry_run": True},
        "bootstrap": {"org_id": "111", "billing_account": "AAAA-BBBB-CCCC"},
        "project_mapping": {
            "host_project": {"src": "same", "dst": "same"},
        },
    }
    config_path = os.path.join(temp_dir, "config.yaml")
    _write_yaml(config_path, config_data)

    p = ProjectProvisioner(config_path)
    with pytest.raises(SystemExit):
        p.load_config()


def test_validate_mapping_rejects_dst_eq_other_src(temp_dir):
    config_data = {
        "global": {"log_dir": os.path.join(temp_dir, "logs"), "dry_run": True},
        "bootstrap": {"org_id": "111", "billing_account": "AAAA-BBBB-CCCC"},
        "project_mapping": {
            "host_project": {"src": "src-host", "dst": "dst-host"},
            "service_projects": [
                {"src": "src-svc-1", "dst": "src-host"},  # dst が他の src と衝突
            ],
        },
    }
    config_path = os.path.join(temp_dir, "config.yaml")
    _write_yaml(config_path, config_data)
    p = ProjectProvisioner(config_path)
    with pytest.raises(SystemExit):
        p.load_config()


@patch("subprocess.run")
def test_provision_dry_run(mock_run, temp_dir):
    config_data = {
        "global": {"log_dir": os.path.join(temp_dir, "logs"), "dry_run": True},
        "bootstrap": {"org_id": "111122223333", "billing_account": "AAAA-BBBB-CCCC"},
        "project_mapping": {"host_project": {"src": "src-host", "dst": "dst-host"}},
    }
    config_path = os.path.join(temp_dir, "config.yaml")
    _write_yaml(config_path, config_data)
    mock_run.return_value = MagicMock(returncode=1, stderr="Not found")
    p = ProjectProvisioner(config_path, dry_run_override=True)
    p.provision()

    cmds = [call[0][0] for call in mock_run.call_args_list]
    assert any("gcloud projects describe dst-host" in c for c in cmds)
    # dry_run なので create / link / enable は実行されない
    assert not any("gcloud projects create" in c for c in cmds)
    assert not any("billing projects link" in c for c in cmds)
    assert not any("services enable" in c for c in cmds)


@patch("subprocess.run")
def test_provision_production(mock_run, temp_dir):
    config_data = {
        "global": {"log_dir": os.path.join(temp_dir, "logs"), "dry_run": False},
        "bootstrap": {"org_id": "111122223333", "billing_account": "AAAA-BBBB-CCCC"},
        "project_mapping": {"host_project": {"src": "src-host", "dst": "dst-host"}},
    }
    config_path = os.path.join(temp_dir, "config.yaml")
    _write_yaml(config_path, config_data)
    mock_run.side_effect = [
        MagicMock(returncode=1, stderr="Not found"),  # describe (read_only=True なので実行)
        MagicMock(returncode=0, stdout="Created"),
        MagicMock(returncode=0, stdout="Linked"),
        MagicMock(returncode=0, stdout="Enabled"),
    ]
    p = ProjectProvisioner(config_path, dry_run_override=False)
    p.provision()
    cmds = [call[0][0] for call in mock_run.call_args_list]
    assert "gcloud projects describe dst-host --format=json" in cmds[0]
    assert "gcloud projects create dst-host --organization=111122223333" in cmds[1]
    assert "gcloud beta billing projects link dst-host --billing-account=AAAA-BBBB-CCCC" in cmds[2]
    assert cmds[3].startswith("gcloud services enable ")
    assert "--project=dst-host" in cmds[3]
    for api in (
        "compute.googleapis.com",
        "dns.googleapis.com",
        "cloudresourcemanager.googleapis.com",
        "serviceusage.googleapis.com",
        "iam.googleapis.com",
        "iamcredentials.googleapis.com",
    ):
        assert api in cmds[3], f"missing prereq API in enable command: {api}"


@patch("subprocess.run")
def test_provision_parallel_counters(mock_run, temp_dir):
    """並列 worker から counter (created / failed) を更新しても整合する。"""
    config_data = {
        "global": {
            "log_dir": os.path.join(temp_dir, "logs"),
            "dry_run": False,
            "parallel_jobs": 4,
        },
        "bootstrap": {"org_id": "111", "billing_account": "AAAA-BBBB-CCCC"},
        "project_mapping": {
            "host_project": {"src": "s-host", "dst": "d-host"},
            "service_projects": [
                {"src": "s-1", "dst": "d-1"},
                {"src": "s-2", "dst": "d-2"},
            ],
        },
    }
    config_path = os.path.join(temp_dir, "config.yaml")
    _write_yaml(config_path, config_data)

    def side(args, **kw):
        cmd = args[0] if isinstance(args, (list, tuple)) else args
        if "projects describe" in cmd:
            return MagicMock(returncode=1, stderr="Not found")
        return MagicMock(returncode=0, stdout="OK", stderr="")

    mock_run.side_effect = side
    p = ProjectProvisioner(config_path, dry_run_override=False)
    p.provision()

    assert p.created == 3
    assert p.skipped == 0
    assert p.failed == 0
    cmds = [call[0][0] for call in mock_run.call_args_list]
    for pid in ("d-host", "d-1", "d-2"):
        assert any(f"gcloud projects create {pid} --organization=111" in c for c in cmds)
        assert any(f"billing projects link {pid}" in c for c in cmds)
        assert any(f"services enable" in c and f"--project={pid}" in c for c in cmds)


@patch("subprocess.run")
def test_provision_parallel_failure_isolated(mock_run, temp_dir):
    """1 つのプロジェクトの create が失敗しても他の worker は完走し、failed のみ加算される。"""
    config_data = {
        "global": {
            "log_dir": os.path.join(temp_dir, "logs"),
            "dry_run": False,
            "parallel_jobs": 4,
        },
        "bootstrap": {"org_id": "111", "billing_account": "AAAA-BBBB-CCCC"},
        "project_mapping": {
            "host_project": {"src": "s-host", "dst": "d-host"},
            "service_projects": [
                {"src": "s-1", "dst": "d-bad"},
                {"src": "s-2", "dst": "d-2"},
            ],
        },
    }
    config_path = os.path.join(temp_dir, "config.yaml")
    _write_yaml(config_path, config_data)

    def side(args, **kw):
        cmd = args[0] if isinstance(args, (list, tuple)) else args
        if "projects describe" in cmd:
            return MagicMock(returncode=1, stderr="Not found")
        if "projects create d-bad" in cmd:
            return MagicMock(returncode=1, stderr="API error", stdout="")
        return MagicMock(returncode=0, stdout="OK", stderr="")

    mock_run.side_effect = side
    p = ProjectProvisioner(config_path, dry_run_override=False)
    p.provision()

    assert p.failed == 1
    assert p.created == 2
    cmds = [call[0][0] for call in mock_run.call_args_list]
    # d-bad の後続 (link / enable) は呼ばれない（failed で return しているため）
    assert not any("billing projects link d-bad" in c for c in cmds)
    assert not any(f"services enable" in c and "--project=d-bad" in c for c in cmds)
    # 他プロジェクトは完走している
    for pid in ("d-host", "d-2"):
        assert any(f"billing projects link {pid}" in c for c in cmds)
