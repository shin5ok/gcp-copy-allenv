import pytest
import os
import json
import tempfile
import shutil
import logging
import yaml
from scripts.sync_env import (
    MigrationOrchestrator,
    is_src_read_only,
    is_known_mock_command,
    validate_config,
)


@pytest.fixture
def temp_dir():
    dirpath = tempfile.mkdtemp()
    yield dirpath
    shutil.rmtree(dirpath)


def _full_config(temp_dir, **overrides):
    """テスト用に最小限正しい config を生成。"""
    base = {
        "global": {
            "log_dir": os.path.join(temp_dir, "logs"),
            "dry_run": True,
            "verbose_logging": True,
            "mock": False,
            "parallel_jobs": 1,
        },
        "project_mapping": {
            "host_project": {
                "src": "src-host",
                "dst": "dst-host",
                "src_impersonate_service_account": "viewer@src-host.iam.gserviceaccount.com",
                "dst_impersonate_service_account": "owner@dst-host.iam.gserviceaccount.com",
            },
            "service_projects": [
                {
                    "src": "src-svc-1",
                    "dst": "dst-svc-1",
                    "src_impersonate_service_account": "viewer@src-svc-1.iam.gserviceaccount.com",
                    "dst_impersonate_service_account": "owner@dst-svc-1.iam.gserviceaccount.com",
                },
            ],
        },
        "rename_rules": {"gcs": {"method": "suffix", "value": "-dst-0602"}},
        "steps": {},
    }
    # overrides で深いマージ
    for k, v in overrides.items():
        base[k] = v
    return base


def _write_yaml(path, data):
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f)


# ============================================================
# ORG 保護: is_src_read_only
# ============================================================
class TestSrcReadOnlyGuard:
    def test_list_describe_get_pass(self):
        assert is_src_read_only("gcloud compute instances list --project=p")
        assert is_src_read_only("gcloud compute snapshots describe foo --project=p")
        assert is_src_read_only("gcloud asset search-all-resources --scope=projects/p")
        assert is_src_read_only("bq ls --project_id=p")
        assert is_src_read_only("bq show --project_id=p ds")

    def test_bulk_export_is_read_only(self):
        # bulk-export はローカルに HCL を書くだけで src は変更しない
        assert is_src_read_only(
            "gcloud beta resource-config bulk-export --project=p --path=./tf"
        )

    def test_write_verbs_blocked(self):
        assert not is_src_read_only("gcloud compute instances create vm --project=p")
        assert not is_src_read_only("gcloud compute disks delete d --project=p")
        assert not is_src_read_only("gcloud compute instances stop vm --project=p")
        assert not is_src_read_only("gcloud services enable compute.googleapis.com --project=p")
        assert not is_src_read_only("bq mk --project_id=p ds")
        assert not is_src_read_only("bq cp src:ds.t dst:ds.t")
        assert not is_src_read_only("terraform apply -auto-approve")
        assert not is_src_read_only("gcloud storage rsync gs://a gs://b")

    def test_flag_values_not_false_positive(self):
        # --format=value(creationTimestamp) には create が含まれるが、フラグ値は無視されるべき
        assert is_src_read_only(
            "gcloud compute snapshots list --project=p "
            "--format='value(name,creationTimestamp)'"
        )


# ============================================================
# Mock: 既知コマンドの判定
# ============================================================
class TestMockKnownCommand:
    def test_known(self):
        assert is_known_mock_command("gcloud compute instances list --project=p")
        assert is_known_mock_command("bq mk --project_id=p ds")
        assert is_known_mock_command("terraform apply -auto-approve")

    def test_unknown_is_blocked(self):
        # mock 時はこの種のコマンドは fail-closed されるべき
        assert not is_known_mock_command("gcloud projects describe p")
        assert not is_known_mock_command("gcloud iam policy-bindings list")


# ============================================================
# validate_config
# ============================================================
class TestValidateConfig:
    def test_ok(self, temp_dir):
        cfg = _full_config(temp_dir)
        assert validate_config(cfg) == []

    def test_missing_mapping(self):
        assert validate_config({}) == ["project_mapping が定義されていません"]

    def test_src_eq_dst_rejected(self, temp_dir):
        cfg = _full_config(temp_dir)
        cfg["project_mapping"]["host_project"]["dst"] = cfg["project_mapping"]["host_project"]["src"]
        errors = validate_config(cfg)
        assert any("src と dst が同一" in e for e in errors)

    def test_dst_collides_with_src_rejected(self, temp_dir):
        cfg = _full_config(temp_dir)
        cfg["project_mapping"]["service_projects"][0]["dst"] = "src-host"  # host の src と衝突
        errors = validate_config(cfg)
        assert any("dst" in e and "src" in e for e in errors)

    def test_missing_impersonate_sa_rejected(self, temp_dir):
        cfg = _full_config(temp_dir)
        cfg["project_mapping"]["host_project"]["src_impersonate_service_account"] = ""
        errors = validate_config(cfg)
        assert any("src_impersonate_service_account" in e for e in errors)

    def test_empty_service_projects_rejected(self, temp_dir):
        cfg = _full_config(temp_dir)
        cfg["project_mapping"]["service_projects"] = []
        errors = validate_config(cfg)
        assert any("service_projects" in e for e in errors)


# ============================================================
# load_config: バリデーション失敗で sys.exit
# ============================================================
class TestLoadConfigFailsFast:
    def test_load_config_fails_on_bad_mapping(self, temp_dir):
        cfg = _full_config(temp_dir)
        cfg["project_mapping"]["host_project"]["dst"] = cfg["project_mapping"]["host_project"]["src"]
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        with pytest.raises(SystemExit) as ei:
            o.load_config()
        assert ei.value.code == 1


# ============================================================
# check_prerequisites: 有効ステップが必要とする CLI を実行前に検査
# ============================================================
class TestCheckPrerequisites:
    def _setup(self, temp_dir, **steps):
        cfg = _full_config(temp_dir)
        cfg["steps"] = steps
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        return o

    def test_missing_config_connector_exits(self, temp_dir, monkeypatch):
        o = self._setup(temp_dir, bulk_export={"enabled": True})
        monkeypatch.setattr("scripts.sync_env.shutil.which", lambda name: None)
        with pytest.raises(SystemExit) as ei:
            o.check_prerequisites()
        assert ei.value.code == 1

    def test_config_connector_present_passes(self, temp_dir, monkeypatch):
        o = self._setup(temp_dir, bulk_export={"enabled": True})
        monkeypatch.setattr(
            "scripts.sync_env.shutil.which",
            lambda name: "/path/to/config-connector",
        )
        o.check_prerequisites()  # 例外なし

    def test_skipped_when_bulk_export_disabled(self, temp_dir, monkeypatch):
        o = self._setup(temp_dir, bulk_export={"enabled": False})
        monkeypatch.setattr("scripts.sync_env.shutil.which", lambda name: None)
        o.check_prerequisites()  # bulk-export 無効なら未インストールでも通る

    def test_skipped_in_mock_mode(self, temp_dir, monkeypatch):
        o = self._setup(temp_dir, bulk_export={"enabled": True})
        o.mock = True
        monkeypatch.setattr("scripts.sync_env.shutil.which", lambda name: None)
        o.check_prerequisites()  # Mock では実コマンドを叩かないのでスキップ

    def test_missing_terraform_exits_when_apply_enabled(self, temp_dir, monkeypatch):
        o = self._setup(temp_dir, terraform_apply={"enabled": True})
        # terraform だけ無い、他はある
        monkeypatch.setattr(
            "scripts.sync_env.shutil.which",
            lambda name: None if name == "terraform" else f"/usr/bin/{name}",
        )
        with pytest.raises(SystemExit) as ei:
            o.check_prerequisites()
        assert ei.value.code == 1

    def test_terraform_not_required_when_apply_disabled(self, temp_dir, monkeypatch):
        # cai_scan のみ有効。terraform が無くても止まらない
        o = self._setup(temp_dir, cai_scan={"enabled": True})
        monkeypatch.setattr(
            "scripts.sync_env.shutil.which",
            lambda name: None if name == "terraform" else f"/usr/bin/{name}",
        )
        o.check_prerequisites()  # 例外なし

    def test_missing_gcloud_exits(self, temp_dir, monkeypatch):
        o = self._setup(temp_dir, cai_scan={"enabled": True})
        monkeypatch.setattr(
            "scripts.sync_env.shutil.which",
            lambda name: None if name == "gcloud" else f"/usr/bin/{name}",
        )
        with pytest.raises(SystemExit) as ei:
            o.check_prerequisites()
        assert ei.value.code == 1


# ============================================================
# check_service_accounts: SA の実在・借用可否・代表権限を実行前に検証
# ============================================================
class TestCheckServiceAccounts:
    def _setup(self, temp_dir, **steps):
        cfg = _full_config(temp_dir)
        cfg["steps"] = steps
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        return o

    def _fake_token(self, *, token_rc=0):
        """_sa_preflight_run（print-access-token）の差し替え。"""
        calls = []

        def runner(cmd):
            calls.append(cmd)
            if "print-access-token" in cmd:
                return (token_rc, "" if token_rc else "ya29.fake-token", "" if token_rc else "denied")
            return (0, "", "")

        runner.calls = calls
        return runner

    def _fake_perms(self, *, granted=None, unverifiable=False):
        """_test_iam_permissions の差し替え。granted は付与済み権限の集合。
        unverifiable=True なら検証不能(None)を返す。"""
        granted = set(granted or [])
        calls = []

        def checker(token, project, perms):
            calls.append((project, set(perms)))
            if unverifiable:
                return None
            return {p for p in perms if p in granted}

        checker.calls = calls
        return checker

    def test_token_failure_exits(self, temp_dir, monkeypatch):
        o = self._setup(temp_dir, cai_scan={"enabled": True})
        o._sa_preflight_run = self._fake_token(token_rc=1)
        o._test_iam_permissions = self._fake_perms(granted=[])
        with pytest.raises(SystemExit) as ei:
            o.check_service_accounts()
        assert ei.value.code == 1

    def test_missing_permission_exits(self, temp_dir, monkeypatch):
        # cai_scan のみ有効。borrow は成功するが必要権限を一切返さない → 停止
        o = self._setup(temp_dir, cai_scan={"enabled": True})
        o._sa_preflight_run = self._fake_token()
        o._test_iam_permissions = self._fake_perms(granted=[])
        with pytest.raises(SystemExit) as ei:
            o.check_service_accounts()
        assert ei.value.code == 1

    def test_all_permissions_present_passes(self, temp_dir, monkeypatch):
        # src: projects.get + cloudasset、dst: projects.get（terraform/data_sync 無効）
        granted = {
            "resourcemanager.projects.get",
            "cloudasset.assets.searchAllResources",
        }
        o = self._setup(temp_dir, cai_scan={"enabled": True})
        o._sa_preflight_run = self._fake_token()
        o._test_iam_permissions = self._fake_perms(granted=granted)
        o.check_service_accounts()  # 例外なし

    def test_unverifiable_permission_warns_and_continues(self, temp_dir, monkeypatch):
        # 借用は成功、権限は検証不能(None) → 警告のみで停止しない
        o = self._setup(temp_dir, cai_scan={"enabled": True})
        o._sa_preflight_run = self._fake_token()
        o._test_iam_permissions = self._fake_perms(unverifiable=True)
        o.check_service_accounts()  # 例外なし

    def test_skipped_in_mock_mode(self, temp_dir, monkeypatch):
        o = self._setup(temp_dir, cai_scan={"enabled": True})
        o.mock = True
        runner = self._fake_token(token_rc=1)  # 呼ばれたら失敗するはず
        o._sa_preflight_run = runner
        o._test_iam_permissions = self._fake_perms(granted=[])
        o.check_service_accounts()  # スキップされ例外なし
        assert runner.calls == []  # ランナーは一度も呼ばれない

    def test_disabled_step_perms_not_required(self, temp_dir, monkeypatch):
        # data_sync 無効なので bigquery/storage 権限は要求されない。
        # baseline(projects.get) と cai_scan の権限だけで通る。
        granted = {
            "resourcemanager.projects.get",
            "cloudasset.assets.searchAllResources",
        }
        o = self._setup(temp_dir, cai_scan={"enabled": True}, data_sync={"enabled": False})
        o._sa_preflight_run = self._fake_token()
        checker = self._fake_perms(granted=granted)
        o._test_iam_permissions = checker
        o.check_service_accounts()  # 例外なし
        # bigquery 権限が要求されていないことを確認
        assert checker.calls and all(
            not any("bigquery" in p for p in perms) for _proj, perms in checker.calls
        )


# ============================================================
# run_command: ORG 保護
# ============================================================
class TestRunCommandSafety:
    def _setup(self, temp_dir):
        cfg = _full_config(temp_dir)
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        return o

    def test_src_without_sa_exits(self, temp_dir):
        o = self._setup(temp_dir)
        with pytest.raises(SystemExit):
            o.run_command(
                "gcloud compute instances list --project=src-host",
                side="src", logger=o.org_logger,
                impersonate_sa=None,  # ← これが原因で停止すべき
            )

    def test_src_write_verb_exits(self, temp_dir):
        o = self._setup(temp_dir)
        with pytest.raises(SystemExit):
            o.run_command(
                "gcloud compute instances create vm --project=src-host",
                side="src", logger=o.org_logger,
                impersonate_sa="viewer@src-host.iam.gserviceaccount.com",
            )

    def test_invalid_side_exits(self, temp_dir):
        o = self._setup(temp_dir)
        with pytest.raises(SystemExit):
            o.run_command(
                "anything", side="hacker", logger=o.org_logger,
            )

    def test_dst_dry_run_returns_empty(self, temp_dir):
        o = self._setup(temp_dir)
        # dry_run=True かつ side=dst なら実行されず空文字
        ret = o.run_command(
            "gcloud compute instances create vm --project=dst-host",
            side="dst", logger=o.dst_logger,
            impersonate_sa="owner@dst-host.iam.gserviceaccount.com",
        )
        assert ret == ""

    def test_mock_unknown_command_exits(self, temp_dir):
        cfg = _full_config(temp_dir, **{"global": {
            "log_dir": os.path.join(temp_dir, "logs"),
            "dry_run": False, "verbose_logging": False, "mock": True, "parallel_jobs": 1,
        }})
        # 必須キーを足す（_full_config が上書きされた global を更に補完）
        cfg["global"]["org_log_file"] = "org.log"
        cfg["global"]["dst_log_file"] = "dst.log"
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        assert o.mock is True
        with pytest.raises(SystemExit):
            o.run_command(
                # _MOCK_KNOWN_PATTERNS にない gcloud projects describe
                "gcloud projects describe dst-host",
                side="dst", logger=o.dst_logger,
                impersonate_sa="owner@dst-host.iam.gserviceaccount.com",
            )


# ============================================================
# customize_hcl: name 置換が bucket ブロック内に限定されるか
# ============================================================
class TestCustomizeHcl:
    def _setup(self, temp_dir):
        cfg = _full_config(temp_dir)
        cfg["global"]["dry_run"] = False
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        return o

    def test_only_bucket_name_renamed_not_vm_name(self, temp_dir):
        o = self._setup(temp_dir)
        raw = os.path.join(temp_dir, "raw")
        active = os.path.join(temp_dir, "active")
        os.makedirs(raw)
        # VM は Step5 が管理するため terraform からスキップ → 別ファイルにする
        vm_sample = """
resource "google_compute_instance" "vm" {
  name = "org-vm-01"
  project = "src-host"
}
"""
        bucket_sample = """
resource "google_storage_bucket" "b" {
  name = "src-bucket-data"
  project = "src-svc-1"
}
"""
        with open(os.path.join(raw, "vm.tf"), "w", encoding="utf-8") as f:
            f.write(vm_sample)
        with open(os.path.join(raw, "bucket.tf"), "w", encoding="utf-8") as f:
            f.write(bucket_sample)
        o.customize_hcl(raw, active)
        # VM ファイルは Step5 管理のためスキップ（active に生成されない）
        assert not os.path.exists(os.path.join(active, "vm.tf"))
        # bucket は生成される
        with open(os.path.join(active, "bucket.tf"), "r", encoding="utf-8") as f:
            out = f.read()
        # bucket name は suffix + dst プロジェクト固有ハッシュで一意化される
        import hashlib
        h = hashlib.sha1("dst-svc-1".encode("utf-8")).hexdigest()[:6]
        assert f'name = "src-bucket-data-dst-0602-{h}"' in out
        # project ID 置換
        assert 'project = "dst-svc-1"' in out
        assert "src-svc-1" not in out

    def test_project_id_word_boundary(self, temp_dir):
        """src ID が他の単語と prefix 重複した場合に誤置換しないこと。"""
        # config を src='proj' / dst='dest' に上書き
        cfg = _full_config(temp_dir)
        cfg["global"]["dry_run"] = False
        cfg["project_mapping"]["host_project"]["src"] = "proj"
        cfg["project_mapping"]["host_project"]["dst"] = "dest"
        cfg["project_mapping"]["service_projects"] = [
            {"src": "proj-1", "dst": "dest-1",
             "src_impersonate_service_account": "a@x", "dst_impersonate_service_account": "b@x"},
        ]
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()

        raw = os.path.join(temp_dir, "raw")
        active = os.path.join(temp_dir, "active")
        os.makedirs(raw)
        sample = '''
project_a = "proj"
project_b = "proj-1"
unrelated = "main-proj-2"
'''
        with open(os.path.join(raw, "ids.tf"), "w", encoding="utf-8") as f:
            f.write(sample)
        o.customize_hcl(raw, active)
        with open(os.path.join(active, "ids.tf"), "r", encoding="utf-8") as f:
            out = f.read()
        assert 'project_a = "dest"' in out
        assert 'project_b = "dest-1"' in out
        # 'main-proj-2' の中の 'proj' は置換されないこと
        assert 'unrelated = "main-proj-2"' in out

    def test_boot_disk_source_removed(self, temp_dir):
        # VM は Step5 管理のため customize でスキップされる。
        # boot_disk.source 除去のロジックは _strip_boot_disk_source で引き続き存在するが
        # VM ファイル自体が active に書き出されないことを確認する。
        o = self._setup(temp_dir)
        raw = os.path.join(temp_dir, "raw")
        active = os.path.join(temp_dir, "active")
        os.makedirs(raw)
        sample = """
resource "google_compute_instance" "v" {
  name = "vm"
  boot_disk {
    auto_delete = true
    device_name = "disk-0"
    initialize_params {
      image = "debian-12"
    }
    source = "https://www.googleapis.com/compute/v1/projects/src-host/zones/asia-northeast1-a/disks/vm"
  }
}
"""
        with open(os.path.join(raw, "vm.tf"), "w", encoding="utf-8") as f:
            f.write(sample)
        o.customize_hcl(raw, active)
        # VM は Step5 管理のためスキップ → active に出力されない
        assert not os.path.exists(os.path.join(active, "vm.tf"))


# ============================================================
# ログディレクトリ: 実行ごとに新規作成
# ============================================================
class TestLogging:
    def test_per_run_dir_created(self, temp_dir):
        cfg = _full_config(temp_dir)
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        assert os.path.isdir(o.run_dir)
        assert os.path.exists(os.path.join(o.run_dir, "org.log"))
        assert os.path.exists(os.path.join(o.run_dir, "dst.log"))
