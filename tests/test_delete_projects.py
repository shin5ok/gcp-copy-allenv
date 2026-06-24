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


def _write_cfg(path: str, dsts, folder_id="111122223333"):
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
    if folder_id is not None:
        data["bootstrap"] = {"folder_id": folder_id}
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f)


def _list_response(projects):
    """projects = [(pid, name, state), ...] → JSON list response."""
    return json.dumps([
        {"projectId": pid, "name": name, "lifecycleState": state}
        for pid, name, state in projects
    ])


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


def test_missing_folder_id_returns_error(temp_dir, capsys):
    cfg = os.path.join(temp_dir, "config.yaml")
    _write_cfg(cfg, [("host", "s", "foo-dst-host")], folder_id=None)
    rc = main(["--pattern", "foo", "--config", cfg])
    assert rc == 2
    assert "folder_id" in capsys.readouterr().err


def test_folder_id_override_via_cli(temp_dir):
    cfg = os.path.join(temp_dir, "config.yaml")
    _write_cfg(cfg, [("host", "s", "foo-dst-host")], folder_id=None)
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [
            _completed(0, _list_response([("foo-dst-host", "h", "ACTIVE")])),
            _completed(0, "[]"),
        ]
        rc = main(["--pattern", "foo", "--config", cfg, "--folder-id", "999"])
        assert rc == 0
        list_cmd = mock_run.call_args_list[0].args[0]
        assert list_cmd[:3] == ["gcloud", "projects", "list"]
        assert "parent.id=999" in list_cmd[3]


@patch("subprocess.run")
def test_dry_run_lists_folder_and_filters_by_pattern(mock_run, temp_dir):
    cfg = os.path.join(temp_dir, "config.yaml")
    _write_cfg(cfg, [
        ("host", "foo-src-host", "foo-dst-host"),
        ("svc", "foo-src-1", "foo-dst-svc-1"),
    ])
    mock_run.side_effect = [
        _completed(0, _list_response([
            ("foo-dst-host", "h", "ACTIVE"),
            ("foo-dst-svc-1", "s1", "ACTIVE"),
            ("bar-dst-other", "b", "ACTIVE"),
        ])),
        _completed(0, "[]"),  # liens for foo-dst-host (alpha)
        _completed(0, "[]"),  # liens for foo-dst-svc-1 (alpha)
    ]
    d = ProjectDeleter(pattern="foo", dry_run=True, config_path=cfg)
    rc = d.run()
    assert rc == 0

    cmds = [c.args[0] for c in mock_run.call_args_list]
    assert cmds[0][:3] == ["gcloud", "projects", "list"]
    assert "parent.id=111122223333" in cmds[0][3]
    assert "parent.type=folder" in cmds[0][3]
    assert "lifecycleState:ACTIVE" in cmds[0][3]
    assert all(c[:3] != ["gcloud", "projects", "describe"] for c in cmds)
    assert all(c[:3] != ["gcloud", "projects", "delete"] for c in cmds)


@patch("subprocess.run")
def test_includes_folder_project_not_in_config(mock_run, temp_dir):
    """config から外れた過去の dst も folder にあれば削除候補に上がる。"""
    cfg = os.path.join(temp_dir, "config.yaml")
    _write_cfg(cfg, [("host", "src-host", "foo-dst-host-new")])  # config は new のみ
    mock_run.side_effect = [
        _completed(0, _list_response([
            ("foo-dst-host-new", "new", "ACTIVE"),
            ("foo-dst-host-old", "old", "ACTIVE"),  # config に無いが folder にある
        ])),
        _completed(0, "[]"),
        _completed(0, "[]"),
    ]
    d = ProjectDeleter(pattern="foo", dry_run=True, config_path=cfg)
    rc = d.run()
    assert rc == 0


@patch("subprocess.run")
def test_non_active_filtered_out_even_if_returned(mock_run, temp_dir):
    """gcloud filter で除外されるはずだが、念のため defense-in-depth で防ぐ。"""
    cfg = os.path.join(temp_dir, "config.yaml")
    _write_cfg(cfg, [("host", "s", "foo-dst-host")])
    mock_run.side_effect = [
        _completed(0, _list_response([
            ("foo-dst-host", "h", "ACTIVE"),
            ("foo-dst-deleted", "d", "DELETE_REQUESTED"),
        ])),
        _completed(0, "[]"),
    ]
    d = ProjectDeleter(pattern="foo", dry_run=True, config_path=cfg)
    rc = d.run()
    assert rc == 0


@patch("subprocess.run")
def test_no_match_returns_zero_no_delete(mock_run, temp_dir):
    cfg = os.path.join(temp_dir, "config.yaml")
    _write_cfg(cfg, [("host", "s", "foo-dst-host")])
    mock_run.side_effect = [
        _completed(0, _list_response([("foo-dst-host", "h", "ACTIVE")])),
    ]
    d = ProjectDeleter(pattern="zzz", dry_run=False, config_path=cfg, yes_code="000000")
    rc = d.run()
    assert rc == 0
    cmds = [c.args[0] for c in mock_run.call_args_list]
    assert not any(c[:3] == ["gcloud", "projects", "delete"] for c in cmds)


@patch("subprocess.run")
def test_wrong_code_aborts_no_delete(mock_run, temp_dir):
    cfg = os.path.join(temp_dir, "config.yaml")
    _write_cfg(cfg, [("host", "s", "foo-dst-host")])
    mock_run.side_effect = [
        _completed(0, _list_response([("foo-dst-host", "h", "ACTIVE")])),
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
        _completed(0, _list_response([("foo-dst-host", "h", "ACTIVE")])),
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
        _completed(0, _list_response([("foo-dst-host", "h", "ACTIVE")])),
        _completed(1, "", "alpha not installed"),
        _completed(1, "", "beta not installed"),
    ]
    d = ProjectDeleter(pattern="foo", dry_run=True, config_path=cfg)
    rc = d.run()
    assert rc == 0
