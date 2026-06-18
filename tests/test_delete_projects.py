import json
import os
import shutil
import tempfile
from unittest.mock import patch, MagicMock

import pytest
import yaml

from scripts.delete_projects import ProjectDeleter, main


@pytest.fixture
def temp_dir():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d)


def _completed(rc=0, stdout="", stderr=""):
    m = MagicMock()
    m.returncode = rc
    m.stdout = stdout
    m.stderr = stderr
    return m


def _write_cfg(path: str, dsts):
    """dsts = [(kind, src, dst), ...]; kind in {'host','svc'}"""
    host_entry = None
    svc_entries = []
    for kind, src, dst in dsts:
        ent = {"src": src, "dst": dst}
        if kind == "host":
            host_entry = ent
        else:
            svc_entries.append(ent)
    data = {
        "global": {"log_dir": os.path.join(os.path.dirname(path), "logs")},
        "project_mapping": {
            "host_project": host_entry or {},
            "service_projects": svc_entries,
        },
    }
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f)


def test_pattern_too_short_returns_error(temp_dir, capsys):
    cfg = os.path.join(temp_dir, "config.yaml")
    _write_cfg(cfg, [("host", "src-host", "dst-host")])
    rc = main(["--pattern", "ab", "--config", cfg])
    assert rc == 2
    assert "3 文字以上" in capsys.readouterr().err


def test_pattern_empty_returns_error(temp_dir, capsys):
    cfg = os.path.join(temp_dir, "config.yaml")
    _write_cfg(cfg, [("host", "src-host", "dst-host")])
    rc = main(["--pattern", "", "--config", cfg])
    assert rc == 2
    assert "3 文字以上" in capsys.readouterr().err


def test_missing_config_returns_error(temp_dir, capsys):
    rc = main(["--pattern", "foo", "--config", os.path.join(temp_dir, "nope.yaml")])
    assert rc == 2
    assert "config が見つかりません" in capsys.readouterr().err


def test_config_without_mapping_returns_error(temp_dir, capsys):
    cfg = os.path.join(temp_dir, "config.yaml")
    with open(cfg, "w", encoding="utf-8") as f:
        yaml.dump({"global": {}}, f)
    rc = main(["--pattern", "foo", "--config", cfg])
    assert rc == 2
    assert "project_mapping" in capsys.readouterr().err


@patch("subprocess.run")
def test_dry_run_uses_config_dst_only(mock_run, temp_dir):
    cfg = os.path.join(temp_dir, "config.yaml")
    _write_cfg(cfg, [
        ("host", "foo-src-host", "foo-dst-host"),
        ("svc", "foo-src-1", "foo-dst-svc-1"),
        ("svc", "bar-src-2", "bar-dst-svc-2"),
    ])
    mock_run.side_effect = [
        _completed(0, json.dumps({"projectId": "foo-dst-host", "name": "h", "lifecycleState": "ACTIVE"})),
        _completed(0, "[]"),
        _completed(0, json.dumps({"projectId": "foo-dst-svc-1", "name": "s1", "lifecycleState": "ACTIVE"})),
        _completed(0, "[]"),
    ]
    d = ProjectDeleter(pattern="foo", dry_run=True, config_path=cfg)
    rc = d.run()
    assert rc == 0

    cmds = [c.args[0] for c in mock_run.call_args_list]
    describe_pids = [c[3] for c in cmds if c[:3] == ["gcloud", "projects", "describe"]]
    assert describe_pids == ["foo-dst-host", "foo-dst-svc-1"]
    assert all(c[:3] != ["gcloud", "projects", "list"] for c in cmds)
    assert all(c[:3] != ["gcloud", "projects", "delete"] for c in cmds)


@patch("subprocess.run")
def test_skips_non_active_and_missing(mock_run, temp_dir):
    cfg = os.path.join(temp_dir, "config.yaml")
    _write_cfg(cfg, [
        ("host", "s1", "foo-dst-host"),
        ("svc", "s2", "foo-dst-svc-1"),
        ("svc", "s3", "foo-dst-svc-2"),
    ])
    mock_run.side_effect = [
        _completed(1, "", "not found"),  # foo-dst-host: missing
        _completed(0, json.dumps({"projectId": "foo-dst-svc-1", "name": "s1", "lifecycleState": "DELETE_REQUESTED"})),
        _completed(0, json.dumps({"projectId": "foo-dst-svc-2", "name": "s2", "lifecycleState": "ACTIVE"})),
        _completed(0, "[]"),
    ]
    d = ProjectDeleter(pattern="foo", dry_run=True, config_path=cfg)
    rc = d.run()
    assert rc == 0


@patch("subprocess.run")
def test_no_match_returns_zero_no_delete(mock_run, temp_dir):
    cfg = os.path.join(temp_dir, "config.yaml")
    _write_cfg(cfg, [("host", "s", "foo-dst-host")])
    d = ProjectDeleter(pattern="zzz", dry_run=False, config_path=cfg, yes_code="000000")
    rc = d.run()
    assert rc == 0
    cmds = [c.args[0] for c in mock_run.call_args_list]
    assert cmds == []


@patch("subprocess.run")
def test_wrong_code_aborts_no_delete(mock_run, temp_dir):
    cfg = os.path.join(temp_dir, "config.yaml")
    _write_cfg(cfg, [("host", "s", "foo-dst-host")])
    mock_run.side_effect = [
        _completed(0, json.dumps({"projectId": "foo-dst-host", "name": "h", "lifecycleState": "ACTIVE"})),
        _completed(0, "[]"),
    ]
    d = ProjectDeleter(pattern="foo", dry_run=False, config_path=cfg, yes_code="000000")
    with patch("scripts.delete_projects._gen_confirmation_code", return_value="123456"):
        rc = d.run()
    assert rc == 1
    cmds = [c.args[0] for c in mock_run.call_args_list]
    assert not any(c[:3] == ["gcloud", "projects", "delete"] for c in cmds)


@patch("subprocess.run")
def test_correct_code_deletes_with_liens_first(mock_run, temp_dir):
    cfg = os.path.join(temp_dir, "config.yaml")
    _write_cfg(cfg, [("host", "src-host", "foo-dst-host")])
    mock_run.side_effect = [
        _completed(0, json.dumps({"projectId": "foo-dst-host", "name": "h", "lifecycleState": "ACTIVE"})),
        _completed(0, json.dumps([{"name": "liens/p123-l456"}])),
        _completed(0, ""),  # lien delete
        _completed(0, ""),  # projects delete
    ]
    d = ProjectDeleter(pattern="foo", dry_run=False, config_path=cfg, yes_code="424242")
    with patch("scripts.delete_projects._gen_confirmation_code", return_value="424242"):
        rc = d.run()
    assert rc == 0
    assert d.deleted == 1
    assert d.liens_removed == 1

    cmds = [c.args[0] for c in mock_run.call_args_list]
    lien_del_idx = next(
        i for i, c in enumerate(cmds)
        if c[:5] == ["gcloud", "alpha", "resource-manager", "liens", "delete"]
    )
    proj_del_idx = next(
        i for i, c in enumerate(cmds)
        if c[:3] == ["gcloud", "projects", "delete"]
    )
    assert lien_del_idx < proj_del_idx
    assert cmds[lien_del_idx][5] == "p123-l456"
    assert cmds[proj_del_idx][3] == "foo-dst-host"


@patch("subprocess.run")
def test_lien_list_failure_falls_back_to_no_liens(mock_run, temp_dir):
    cfg = os.path.join(temp_dir, "config.yaml")
    _write_cfg(cfg, [("host", "s", "foo-dst-host")])
    mock_run.side_effect = [
        _completed(0, json.dumps({"projectId": "foo-dst-host", "name": "h", "lifecycleState": "ACTIVE"})),
        _completed(1, "", "alpha not installed"),
        _completed(1, "", "beta not installed"),
    ]
    d = ProjectDeleter(pattern="foo", dry_run=True, config_path=cfg)
    rc = d.run()
    assert rc == 0
