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
    validate_steps_config,
    diff_coverage,
    _ASSET_COVERAGE,
    fw_policy_rule_layer4,
    fw_policy_rule_flags,
    fw_policy_rule_secure_tags,
    fw_rule_scope_flag,
    parse_cai_resources,
    parse_tf_resources,
    gcloud_recreate_command,
    analyze_cai_tf_diff,
    format_diff_report,
    _parse_gcloud_describe_json,
    resolve_clean_targets,
    run_clean_state,
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

    def test_missing_impersonate_sa_allowed(self, temp_dir):
        # `*_impersonate_service_account` 未指定はエラーにしない（ローカル認証フォールバック）。
        # 実際に src 書込権を持っていれば check_service_accounts が警告 + 続行確認する。
        cfg = _full_config(temp_dir)
        cfg["project_mapping"]["host_project"]["src_impersonate_service_account"] = ""
        cfg["project_mapping"]["host_project"]["dst_impersonate_service_account"] = ""
        cfg["project_mapping"]["service_projects"][0]["src_impersonate_service_account"] = ""
        cfg["project_mapping"]["service_projects"][0]["dst_impersonate_service_account"] = ""
        assert validate_config(cfg) == []

    def test_empty_service_projects_rejected(self, temp_dir):
        cfg = _full_config(temp_dir)
        cfg["project_mapping"]["service_projects"] = []
        errors = validate_config(cfg)
        assert any("service_projects" in e for e in errors)


# ============================================================
# validate_steps_config: 有効ステップの設定不備を実行前に検出
# ============================================================
class TestValidateStepsConfig:
    def test_empty_steps_ok(self):
        assert validate_steps_config({"steps": {}}) == []

    def test_disabled_vpc_sc_not_checked(self):
        cfg = {"steps": {"vpc_sc": {"enabled": False}}}
        assert validate_steps_config(cfg) == []

    def test_vpc_sc_enabled_requires_all_fields(self):
        cfg = {"steps": {"vpc_sc": {"enabled": True}}}
        errors = validate_steps_config(cfg)
        assert any("access_policy" in e for e in errors)
        assert any("perimeter" in e for e in errors)
        assert any("billing_project" in e for e in errors)

    def test_vpc_sc_missing_only_billing(self):
        cfg = {"steps": {"vpc_sc": {
            "enabled": True, "access_policy": "1", "perimeter": "p",
        }}}
        errors = validate_steps_config(cfg)
        assert any("billing_project" in e for e in errors)
        assert not any("access_policy" in e for e in errors)

    def test_vpc_sc_complete_ok(self):
        cfg = {"steps": {"vpc_sc": {
            "enabled": True, "access_policy": "1", "perimeter": "p",
            "billing_project": "host-proj",
        }}}
        assert validate_steps_config(cfg) == []

    def test_vpc_sc_whitespace_treated_as_empty(self):
        cfg = {"steps": {"vpc_sc": {
            "enabled": True, "access_policy": "  ", "perimeter": "p",
            "billing_project": "host-proj",
        }}}
        assert any("access_policy" in e for e in validate_steps_config(cfg))

    def test_rename_method_invalid_rejected(self):
        cfg = {
            "steps": {"bulk_export": {"enabled": True}},
            "rename_rules": {"gcs": {"method": "sufix", "value": "x"}},
        }
        assert any("rename_rules.gcs.method" in e for e in validate_steps_config(cfg))

    def test_rename_value_empty_with_suffix_rejected(self):
        cfg = {
            "steps": {"data_sync": {"enabled": True}},
            "rename_rules": {"gcs": {"method": "suffix", "value": ""}},
        }
        assert any("rename_rules.gcs.value" in e for e in validate_steps_config(cfg))

    def test_rename_auto_value_ok(self):
        cfg = {
            "steps": {"bulk_export": {"enabled": True}},
            "rename_rules": {"gcs": {"method": "prefix", "value": "auto"}},
        }
        assert validate_steps_config(cfg) == []

    def test_rename_custom_no_value_ok(self):
        # custom は overrides で個別指定するため value 空でも可
        cfg = {
            "steps": {"bulk_export": {"enabled": True}},
            "rename_rules": {"gcs": {"method": "custom"}},
        }
        assert validate_steps_config(cfg) == []

    def test_rename_not_checked_when_steps_disabled(self):
        cfg = {
            "steps": {"bulk_export": {"enabled": False}, "data_sync": {"enabled": False}},
            "rename_rules": {"gcs": {"method": "bogus"}},
        }
        assert validate_steps_config(cfg) == []

    def test_snapshot_bad_max_age_rejected(self):
        cfg = {"steps": {"gce_snapshot": {"enabled": True, "max_age_days": "thirty"}}}
        assert any("max_age_days" in e for e in validate_steps_config(cfg))

    def test_snapshot_zero_max_age_rejected(self):
        cfg = {"steps": {"gce_snapshot": {"enabled": True, "max_age_days": 0}}}
        assert any("max_age_days" in e for e in validate_steps_config(cfg))

    def test_snapshot_default_max_age_ok(self):
        cfg = {"steps": {"gce_snapshot": {"enabled": True}}}
        assert validate_steps_config(cfg) == []


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

    # ---- ローカル認証フォールバック（*_impersonate_service_account 未指定） ----
    def _setup_adc(self, temp_dir, **steps):
        """src/dst の SA を空にして ADC フォールバック経路を強制する setup。"""
        cfg = _full_config(temp_dir)
        for entry in (
            cfg["project_mapping"]["host_project"],
            *cfg["project_mapping"]["service_projects"],
        ):
            entry["src_impersonate_service_account"] = ""
            entry["dst_impersonate_service_account"] = ""
        cfg["steps"] = steps
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        return o

    def _fake_local_runner(self, *, token="ya29.fake-local", account="dev@example.com",
                           token_rc=0):
        """`_sa_preflight_run` の差し替え（ローカル認証用）。"""
        calls = []

        def runner(cmd):
            calls.append(cmd)
            if "print-access-token" in cmd:
                return (token_rc, "" if token_rc else token, "" if token_rc else "denied")
            if "config get-value account" in cmd:
                return (0, account, "")
            return (0, "", "")

        runner.calls = calls
        return runner

    def test_adc_no_dangerous_perms_passes(self, temp_dir, monkeypatch):
        # ADC が src に書込相当の権限を持たない → 警告ゼロ、続行確認も呼ばれない
        o = self._setup_adc(temp_dir, cai_scan={"enabled": True})
        o._sa_preflight_run = self._fake_local_runner()
        o._test_iam_permissions = self._fake_perms(granted=set())
        o.check_service_accounts()  # 例外なし

    def test_adc_with_dangerous_perms_aborts_non_tty(self, temp_dir, monkeypatch):
        # ADC が src に書込権を持つ + 非対話 + AUTO_APPROVE 未指定 → 中断
        o = self._setup_adc(temp_dir, cai_scan={"enabled": True})
        o._sa_preflight_run = self._fake_local_runner()
        o._test_iam_permissions = self._fake_perms(granted={"compute.instances.create"})
        monkeypatch.setattr("scripts.sync_env.sys.stdin.isatty", lambda: False)
        monkeypatch.delenv("COPY_ALL_ENV_AUTO_APPROVE", raising=False)
        with pytest.raises(SystemExit) as ei:
            o.check_service_accounts()
        assert ei.value.code == 1

    def test_adc_auto_approve_env_skips_prompt(self, temp_dir, monkeypatch):
        # AUTO_APPROVE=1 が指定されていれば非対話でも続行
        o = self._setup_adc(temp_dir, cai_scan={"enabled": True})
        o._sa_preflight_run = self._fake_local_runner()
        o._test_iam_permissions = self._fake_perms(granted={"compute.instances.create"})
        monkeypatch.setattr("scripts.sync_env.sys.stdin.isatty", lambda: False)
        monkeypatch.setenv("COPY_ALL_ENV_AUTO_APPROVE", "1")
        o.check_service_accounts()  # 例外なし

    def test_adc_interactive_yes_continues(self, temp_dir, monkeypatch):
        # 対話セッションで "y" を返したら続行
        o = self._setup_adc(temp_dir, cai_scan={"enabled": True})
        o._sa_preflight_run = self._fake_local_runner()
        o._test_iam_permissions = self._fake_perms(granted={"compute.instances.create"})
        monkeypatch.setattr("scripts.sync_env.sys.stdin.isatty", lambda: True)
        monkeypatch.delenv("COPY_ALL_ENV_AUTO_APPROVE", raising=False)
        monkeypatch.setattr("builtins.input", lambda _prompt="": "y")
        o.check_service_accounts()  # 例外なし

    def test_adc_interactive_no_aborts(self, temp_dir, monkeypatch):
        # 対話セッションで "n" を返したら中断
        o = self._setup_adc(temp_dir, cai_scan={"enabled": True})
        o._sa_preflight_run = self._fake_local_runner()
        o._test_iam_permissions = self._fake_perms(granted={"compute.instances.create"})
        monkeypatch.setattr("scripts.sync_env.sys.stdin.isatty", lambda: True)
        monkeypatch.delenv("COPY_ALL_ENV_AUTO_APPROVE", raising=False)
        monkeypatch.setattr("builtins.input", lambda _prompt="": "n")
        with pytest.raises(SystemExit) as ei:
            o.check_service_accounts()
        assert ei.value.code == 1

    def test_adc_token_unavailable_exits(self, temp_dir, monkeypatch):
        # `gcloud auth print-access-token` が失敗したら fail-fast
        o = self._setup_adc(temp_dir, cai_scan={"enabled": True})
        o._sa_preflight_run = self._fake_local_runner(token_rc=1)
        o._test_iam_permissions = self._fake_perms(granted=set())
        with pytest.raises(SystemExit) as ei:
            o.check_service_accounts()
        assert ei.value.code == 1


# ============================================================
# _confirm_adc_src_write_or_abort: 続行確認の単体動作
# ============================================================
class TestConfirmAdcSrcWriteOrAbort:
    def _orch(self, temp_dir):
        cfg = _full_config(temp_dir)
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        return o

    def test_empty_warnings_is_noop(self, temp_dir, monkeypatch):
        o = self._orch(temp_dir)
        # input が呼ばれないこと（呼ばれたら raise で失敗させる）
        monkeypatch.setattr("builtins.input", lambda _p="": (_ for _ in ()).throw(AssertionError("called")))
        o._confirm_adc_src_write_or_abort([])  # 例外なし

    def test_non_tty_without_env_exits(self, temp_dir, monkeypatch):
        o = self._orch(temp_dir)
        monkeypatch.setattr("scripts.sync_env.sys.stdin.isatty", lambda: False)
        monkeypatch.delenv("COPY_ALL_ENV_AUTO_APPROVE", raising=False)
        with pytest.raises(SystemExit) as ei:
            o._confirm_adc_src_write_or_abort(["src 'p': compute.instances.create"])
        assert ei.value.code == 1

    def test_env_overrides_non_tty(self, temp_dir, monkeypatch):
        o = self._orch(temp_dir)
        monkeypatch.setattr("scripts.sync_env.sys.stdin.isatty", lambda: False)
        monkeypatch.setenv("COPY_ALL_ENV_AUTO_APPROVE", "1")
        o._confirm_adc_src_write_or_abort(["src 'p': storage.buckets.delete"])  # 例外なし

    def test_interactive_yes(self, temp_dir, monkeypatch):
        o = self._orch(temp_dir)
        monkeypatch.setattr("scripts.sync_env.sys.stdin.isatty", lambda: True)
        monkeypatch.delenv("COPY_ALL_ENV_AUTO_APPROVE", raising=False)
        monkeypatch.setattr("builtins.input", lambda _p="": "yes")
        o._confirm_adc_src_write_or_abort(["src 'p': compute.disks.delete"])  # 例外なし

    def test_interactive_blank_aborts(self, temp_dir, monkeypatch):
        # Enter キーのみ（空文字）はデフォルト N 扱いで中断
        o = self._orch(temp_dir)
        monkeypatch.setattr("scripts.sync_env.sys.stdin.isatty", lambda: True)
        monkeypatch.delenv("COPY_ALL_ENV_AUTO_APPROVE", raising=False)
        monkeypatch.setattr("builtins.input", lambda _p="": "")
        with pytest.raises(SystemExit) as ei:
            o._confirm_adc_src_write_or_abort(["src 'p': iam.serviceAccountKeys.create"])
        assert ei.value.code == 1


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

    def test_src_without_sa_allows_read_only(self, temp_dir, monkeypatch):
        # 旧仕様: src + impersonate_sa=None は即停止。
        # 新仕様: ローカル認証フォールバックを許容するので、read-only コマンドは通る。
        o = self._setup(temp_dir)

        class _FakeProc:
            returncode = 0
            stdout = ""
            stderr = ""

        monkeypatch.setattr(
            "scripts.sync_env.subprocess.run",
            lambda *a, **kw: _FakeProc(),
        )
        # SystemExit が出ないこと（is_src_read_only ガードは通過、impersonate 無くても可）
        ret = o.run_command(
            "gcloud compute instances list --project=src-host",
            side="src", logger=o.org_logger,
            impersonate_sa=None,
        )
        assert ret == ""

    def test_src_without_sa_still_blocks_write_verb(self, temp_dir):
        # impersonate の有無に関わらず書込動詞は拒否される（is_src_read_only ガード）
        o = self._setup(temp_dir)
        with pytest.raises(SystemExit):
            o.run_command(
                "gcloud compute instances create vm --project=src-host",
                side="src", logger=o.org_logger,
                impersonate_sa=None,
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
# step_vpc_sc: 既存ペリメタへ dst プロジェクトを追加（org は触らない）
# ============================================================
class TestVpcSc:
    def _setup(self, temp_dir, dry_run=False, **vpc_sc):
        cfg = _full_config(temp_dir)
        cfg["global"]["dry_run"] = dry_run
        # billing_project は必須・明示指定（自動補完しない）。テストの既定として明示。
        vpc_sc.setdefault("billing_project", "dst-host")
        cfg["steps"] = {"vpc_sc": {"enabled": True, **vpc_sc}}
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        return o

    def _capture(self, o):
        calls = []
        o.run_command = lambda cmd, **kw: calls.append((cmd, kw)) or ""
        return calls

    def test_mock_command_is_known(self):
        assert is_known_mock_command(
            "gcloud access-context-manager perimeters update p --policy=1 --add-resources=projects/9"
        )
        assert is_known_mock_command(
            "gcloud access-context-manager perimeters describe p --policy=1"
        )

    def test_load_config_rejects_enabled_vpc_sc_missing_policy(self, temp_dir):
        # enabled なのに access_policy 未設定 → load_config が実行前に fail-fast
        with pytest.raises(SystemExit):
            self._setup(temp_dir, access_policy="", perimeter="dst_perimeter")

    def test_runtime_skip_defense_when_required_blanked(self, temp_dir):
        # 検証を通った後に必須項目が消えても step は誤実行せず skip（多層防御）
        o = self._setup(temp_dir, access_policy="111", perimeter="dst_perimeter")
        o.config["steps"]["vpc_sc"]["billing_project"] = ""
        o._get_project_number = lambda p, impersonate_sa=None: "1001"
        calls = self._capture(o)
        o.step_vpc_sc()
        assert [c for c, _ in calls if "perimeters update" in c] == []
        assert o.stats.skipped >= 1

    def test_adds_resolved_dst_numbers_not_src(self, temp_dir):
        o = self._setup(temp_dir, access_policy="111", perimeter="dst_perimeter")
        nums = {"dst-host": "1001", "dst-svc-1": "1002"}
        o._get_project_number = lambda p, impersonate_sa=None: nums.get(p)
        o._get_perimeter_resources = lambda *a, **k: set()
        calls = self._capture(o)
        o.step_vpc_sc()
        update = [c for c, _ in calls if "perimeters update" in c]
        assert len(update) == 1
        cmd = update[0]
        assert "--policy=111" in cmd and "perimeters update dst_perimeter" in cmd
        assert "projects/1001" in cmd and "projects/1002" in cmd
        # resources は番号で渡す（src/dst のプロジェクト ID を resources に使わない）
        assert "src-host" not in cmd and "projects/dst-host" not in cmd

    def test_load_config_rejects_enabled_vpc_sc_missing_billing(self, temp_dir):
        # billing_project は自動補完しない → 未設定なら load_config が実行前に fail-fast
        with pytest.raises(SystemExit):
            self._setup(
                temp_dir, access_policy="111", perimeter="dst_perimeter",
                billing_project="",
            )

    def test_billing_project_flows_to_commands(self, temp_dir):
        o = self._setup(temp_dir, access_policy="111", perimeter="dst_perimeter")
        o._get_project_number = lambda p, impersonate_sa=None: "1001"
        seen = {}
        o._get_perimeter_resources = (
            lambda *a, **k: seen.update(args=a, kwargs=k) or set()
        )
        calls = self._capture(o)
        o.step_vpc_sc()
        # describe 側へ明示 billing が渡る
        assert seen["args"][-1] == "dst-host" or seen["kwargs"].get("billing") == "dst-host"
        update = [c for c, _ in calls if "perimeters update" in c][0]
        assert "--billing-project=dst-host" in update
        # quota project の API 有効化が走る
        assert any(
            "services enable accesscontextmanager.googleapis.com" in c
            and "--project=dst-host" in c
            for c, _ in calls
        )

    def test_billing_project_override(self, temp_dir):
        o = self._setup(
            temp_dir, access_policy="111", perimeter="dst_perimeter",
            billing_project="quota-proj",
        )
        o._get_project_number = lambda p, impersonate_sa=None: "1001"
        o._get_perimeter_resources = lambda *a, **k: set()
        calls = self._capture(o)
        o.step_vpc_sc()
        update = [c for c, _ in calls if "perimeters update" in c][0]
        assert "--billing-project=quota-proj" in update
        assert any(
            "services enable accesscontextmanager.googleapis.com" in c
            and "--project=quota-proj" in c
            for c, _ in calls
        )

    def test_only_missing_resources_added(self, temp_dir):
        o = self._setup(temp_dir, access_policy="111", perimeter="dst_perimeter")
        nums = {"dst-host": "1001", "dst-svc-1": "1002"}
        o._get_project_number = lambda p, impersonate_sa=None: nums.get(p)
        o._get_perimeter_resources = lambda *a, **k: {"projects/1001"}
        calls = self._capture(o)
        o.step_vpc_sc()
        cmd = [c for c, _ in calls if "perimeters update" in c][0]
        assert "projects/1002" in cmd and "projects/1001" not in cmd

    def test_noop_when_all_present(self, temp_dir):
        o = self._setup(temp_dir, access_policy="111", perimeter="dst_perimeter")
        nums = {"dst-host": "1001", "dst-svc-1": "1002"}
        o._get_project_number = lambda p, impersonate_sa=None: nums.get(p)
        o._get_perimeter_resources = lambda *a, **k: {"projects/1001", "projects/1002"}
        calls = self._capture(o)
        o.step_vpc_sc()
        assert [c for c, _ in calls if "perimeters update" in c] == []
        assert o.stats.skipped >= 1

    def test_exclude_host_project(self, temp_dir):
        o = self._setup(
            temp_dir, access_policy="111", perimeter="dst_perimeter",
            include_host_project=False,
        )
        nums = {"dst-host": "1001", "dst-svc-1": "1002"}
        o._get_project_number = lambda p, impersonate_sa=None: nums.get(p)
        o._get_perimeter_resources = lambda *a, **k: set()
        calls = self._capture(o)
        o.step_vpc_sc()
        cmd = [c for c, _ in calls if "perimeters update" in c][0]
        assert "projects/1002" in cmd and "projects/1001" not in cmd

    def test_update_is_dst_side(self, temp_dir):
        o = self._setup(temp_dir, access_policy="111", perimeter="dst_perimeter")
        o._get_project_number = lambda p, impersonate_sa=None: "1001"
        o._get_perimeter_resources = lambda *a, **k: set()
        calls = self._capture(o)
        o.step_vpc_sc()
        update = [(c, kw) for c, kw in calls if "perimeters update" in c]
        assert update and update[0][1].get("side") == "dst"


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

    def test_bigquery_dataset_and_table_skipped(self, temp_dir):
        # BQ dataset / table は Step6 (data_sync) が管理する。
        # terraform_apply に残すと「table を先に作って 404」する不具合が起きる。
        o = self._setup(temp_dir)
        raw = os.path.join(temp_dir, "raw")
        active = os.path.join(temp_dir, "active")
        os.makedirs(raw)
        ds_sample = '''
resource "google_bigquery_dataset" "d" {
  dataset_id = "dataset_foo"
  project    = "src-svc-1"
  location   = "asia-northeast1"
}
'''
        tbl_sample = '''
resource "google_bigquery_table" "t" {
  dataset_id = "dataset_foo"
  table_id   = "tbl1"
  project    = "src-svc-1"
}
'''
        with open(os.path.join(raw, "google_bigquery_dataset.tf"), "w", encoding="utf-8") as f:
            f.write(ds_sample)
        with open(os.path.join(raw, "google_bigquery_table.tf"), "w", encoding="utf-8") as f:
            f.write(tbl_sample)
        o.customize_hcl(raw, active)
        # 両方 active に出力されない（Step 6 が担当する所有モデル）
        assert not os.path.exists(os.path.join(active, "google_bigquery_dataset.tf"))
        assert not os.path.exists(os.path.join(active, "google_bigquery_table.tf"))


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


# ============================================================
# ISSUE-01: CAI カバレッジマップと突合せ
# ============================================================
class TestAssetCoverage:
    def test_known_assets_have_coverage_entry(self):
        # 実環境 (cai_export/*) で観測された主要 assetType がマップに登録済みであること
        must_have = [
            "compute.googleapis.com/Firewall",
            "compute.googleapis.com/Instance",
            "compute.googleapis.com/Subnetwork",
            "compute.googleapis.com/Router",
            "iam.googleapis.com/Role",
        ]
        for t in must_have:
            assert t in _ASSET_COVERAGE, f"{t} が _ASSET_COVERAGE に未登録"

    def test_diff_coverage_detects_unknown(self):
        # 未登録 type は uncovered に出る、既知 type は出ない
        observed = [
            "compute.googleapis.com/Firewall",       # known
            "compute.googleapis.com/Instance",       # known
            "fake.googleapis.com/UnknownThing",      # unknown
            "another.googleapis.com/Mystery",        # unknown
        ]
        uncovered, _ = diff_coverage(observed)
        assert "fake.googleapis.com/UnknownThing" in uncovered
        assert "another.googleapis.com/Mystery" in uncovered
        assert "compute.googleapis.com/Firewall" not in uncovered

    def test_diff_coverage_empty_input(self):
        uncovered, _ = diff_coverage([])
        assert uncovered == []

    def test_report_cai_coverage_warns_uncovered(self, temp_dir):
        cfg = _full_config(temp_dir)
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()

        # mock の CAI 出力ファイルを作る
        out_dir = os.path.join(temp_dir, "cai_out")
        os.makedirs(out_dir)
        sample = (
            "assetType: compute.googleapis.com/Firewall\n"
            "displayName: x\n"
            "---\n"
            "assetType: fake.googleapis.com/Mystery\n"
            "displayName: y\n"
        )
        with open(os.path.join(out_dir, "cai_resources_src-host.txt"), "w") as f:
            f.write(sample)

        # org_logger は propagate=False なので caplog ではなく自前ハンドラで捕捉
        records = []

        class _Capture(logging.Handler):
            def emit(self, record):
                records.append(record)

        h = _Capture(level=logging.WARNING)
        o.org_logger.addHandler(h)
        try:
            o._report_cai_coverage(
                [("src-host", None)], out_dir, fail_on_uncovered=False,
            )
        finally:
            o.org_logger.removeHandler(h)

        msgs = [r.getMessage() for r in records]
        assert any("fake.googleapis.com/Mystery" in m for m in msgs)

    def test_fail_on_uncovered_exits(self, temp_dir):
        cfg = _full_config(temp_dir)
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        out_dir = os.path.join(temp_dir, "cai_out")
        os.makedirs(out_dir)
        with open(os.path.join(out_dir, "cai_resources_src-host.txt"), "w") as f:
            f.write("assetType: unknown.googleapis.com/Type\n")
        with pytest.raises(SystemExit):
            o._report_cai_coverage(
                [("src-host", None)], out_dir, fail_on_uncovered=True,
            )


# ============================================================
# ISSUE-02: FW policy rule の gcloud フラグ変換
# ============================================================
class TestFwPolicyRuleConversion:
    def test_layer4_single_port(self):
        rule = {"match": {"layer4Configs": [{"ipProtocol": "tcp", "ports": ["22"]}]}}
        assert fw_policy_rule_layer4(rule) == "tcp:22"

    def test_layer4_multiple_ports_same_protocol(self):
        # 旧コードはここで先頭ポートしか拾わなかった ('tcp:80' になっていた)
        rule = {"match": {"layer4Configs": [{"ipProtocol": "tcp", "ports": ["80", "443"]}]}}
        assert fw_policy_rule_layer4(rule) == "tcp:80,tcp:443"

    def test_layer4_multiple_protocols(self):
        rule = {"match": {"layer4Configs": [
            {"ipProtocol": "tcp", "ports": ["80", "443"]},
            {"ipProtocol": "udp", "ports": ["53"]},
            {"ipProtocol": "icmp"},
        ]}}
        assert fw_policy_rule_layer4(rule) == "tcp:80,tcp:443,udp:53,icmp"

    def test_layer4_no_ports(self):
        rule = {"match": {"layer4Configs": [{"ipProtocol": "all"}]}}
        assert fw_policy_rule_layer4(rule) == "all"

    def test_layer4_empty_falls_back_to_all(self):
        assert fw_policy_rule_layer4({}) == "all"
        assert fw_policy_rule_layer4({"match": {"layer4Configs": []}}) == "all"

    def test_flags_src_dest_ip_ranges(self):
        rule = {"match": {"srcIpRanges": ["10.0.0.0/8", "192.168.0.0/16"], "destIpRanges": ["172.16.0.0/12"]}}
        flags = fw_policy_rule_flags(rule, {})
        assert "--src-ip-ranges=10.0.0.0/8,192.168.0.0/16" in flags
        assert "--dest-ip-ranges=172.16.0.0/12" in flags

    def test_flags_threat_intelligence(self):
        rule = {
            "match": {
                "srcThreatIntelligences": ["iplist-known-malicious-ips", "iplist-tor-exit-nodes"],
                "destThreatIntelligences": ["iplist-known-malicious-ips"],
            },
        }
        flags = fw_policy_rule_flags(rule, {})
        assert "--src-threat-intelligence=iplist-known-malicious-ips,iplist-tor-exit-nodes" in flags
        assert "--dest-threat-intelligence=iplist-known-malicious-ips" in flags

    def test_flags_address_groups_and_fqdns(self):
        rule = {
            "match": {
                "srcAddressGroups": ["projects/p/locations/global/addressGroups/g1"],
                "destAddressGroups": ["projects/p/locations/global/addressGroups/g2"],
                "srcFqdns": ["example.com"],
                "destFqdns": ["evil.example"],
            },
        }
        flags = fw_policy_rule_flags(rule, {})
        assert "--src-address-groups=projects/p/locations/global/addressGroups/g1" in flags
        assert "--dest-address-groups=projects/p/locations/global/addressGroups/g2" in flags
        assert "--src-fqdns=example.com" in flags
        assert "--dest-fqdns=evil.example" in flags

    def test_flags_network_scope_and_spg(self):
        rule = {
            "match": {"srcNetworkScope": "INTERNET"},
            "securityProfileGroup": "//networksecurity.googleapis.com/orgs/123/locations/global/securityProfileGroups/spg",
            "tlsInspect": True,
        }
        flags = fw_policy_rule_flags(rule, {})
        assert "--src-network-context=INTERNET" in flags
        assert any("--security-profile-group=" in f for f in flags)
        assert "--tls-inspect" in flags

    def test_flags_disabled_and_logging(self):
        rule = {"disabled": True, "enableLogging": True}
        flags = fw_policy_rule_flags(rule, {})
        assert "--disabled" in flags
        assert "--enable-logging" in flags

    def test_flags_omits_disabled_when_false(self):
        rule = {"disabled": False, "enableLogging": False}
        flags = fw_policy_rule_flags(rule, {})
        assert "--disabled" not in flags
        assert "--enable-logging" not in flags

    def test_flags_description_is_shell_quoted(self):
        rule = {"description": "allow web; multi word"}
        flags = fw_policy_rule_flags(rule, {})
        joined = " ".join(flags)
        assert joined.startswith("--description=")
        # shlex.quote によりスペースを含む文字列はクォートされる
        assert "'allow web; multi word'" in joined

    def test_flags_target_sa_project_remap(self):
        rule = {"targetServiceAccounts": ["app@src-proj.iam.gserviceaccount.com"]}
        flags = fw_policy_rule_flags(rule, {"src-proj": "dst-proj"})
        assert "--target-service-accounts=app@dst-proj.iam.gserviceaccount.com" in flags

    def test_flags_secure_tags(self):
        rule = {
            "match": {"srcSecureTags": [{"name": "tagValues/111"}, {"name": "tagValues/222"}]},
            "targetSecureTags": [{"name": "tagValues/333"}],
        }
        # secure_tag_map=None (default) は変換せずそのまま（同一 ORG 後方互換）
        flags = fw_policy_rule_flags(rule, {})
        assert "--src-secure-tags=tagValues/111,tagValues/222" in flags
        assert "--target-secure-tags=tagValues/333" in flags

    def test_flags_secure_tags_remapped_via_map(self):
        rule = {
            "match": {"srcSecureTags": [{"name": "tagValues/111"}, {"name": "tagValues/222"}]},
            "targetSecureTags": [{"name": "tagValues/333"}],
        }
        m = {
            "tagValues/111": "tagValues/aaa",
            "tagValues/222": "tagValues/bbb",
            "tagValues/333": "tagValues/ccc",
        }
        flags = fw_policy_rule_flags(rule, {}, m)
        assert "--src-secure-tags=tagValues/aaa,tagValues/bbb" in flags
        assert "--target-secure-tags=tagValues/ccc" in flags

    def test_flags_secure_tags_unmapped_dropped(self):
        # map にあるタグだけ残り、無いタグは落ちる
        rule = {"match": {"srcSecureTags": [{"name": "tagValues/111"}, {"name": "tagValues/999"}]}}
        flags = fw_policy_rule_flags(rule, {}, {"tagValues/111": "tagValues/aaa"})
        assert "--src-secure-tags=tagValues/aaa" in flags

    def test_secure_tags_collector(self):
        rule = {
            "match": {"srcSecureTags": [{"name": "tagValues/111"}]},
            "targetSecureTags": [{"name": "tagValues/333"}],
        }
        assert fw_policy_rule_secure_tags(rule) == ["tagValues/111", "tagValues/333"]
        assert fw_policy_rule_secure_tags({}) == []

    def test_flags_empty_rule_returns_empty(self):
        assert fw_policy_rule_flags({}, {}) == []


# ============================================================
# gcloud describe --format=json の出力形状ゆれ吸収
# ============================================================
class TestParseGcloudDescribeJson:
    def test_object_passthrough(self):
        assert _parse_gcloud_describe_json('{"name":"p","rules":[1,2]}') == \
            {"name": "p", "rules": [1, 2]}

    def test_single_element_list_unwraps(self):
        # 一部 gcloud バージョンで describe が [{...}] を返すケース
        assert _parse_gcloud_describe_json('[{"name":"p","rules":[]}]') == \
            {"name": "p", "rules": []}

    def test_empty_input_returns_empty_dict(self):
        assert _parse_gcloud_describe_json("") == {}
        assert _parse_gcloud_describe_json(None) == {}

    def test_invalid_json_returns_empty_dict(self):
        assert _parse_gcloud_describe_json("not json") == {}

    def test_unexpected_shape_returns_empty_dict(self):
        # 複数要素 / 空配列 / スカラはいずれも安全側で空 dict 化
        assert _parse_gcloud_describe_json('[{"a":1},{"a":2}]') == {}
        assert _parse_gcloud_describe_json("[]") == {}
        assert _parse_gcloud_describe_json("123") == {}


# ============================================================
# CAI ↔ TF 差分解析 (make plan 後の DIFF.md 生成)
# ============================================================
_CAI_SAMPLE = """\
---
assetType: compute.googleapis.com/Subnetwork
location: asia-northeast1
name: //compute.googleapis.com/projects/src-host/regions/asia-northeast1/subnetworks/subnet-svc1
project: projects/100
---
assetType: compute.googleapis.com/Subnetwork
location: asia-northeast1
name: //compute.googleapis.com/projects/src-host/regions/asia-northeast1/subnetworks/subnet-svc-missing
project: projects/100
---
assetType: compute.googleapis.com/Router
location: asia-northeast1
name: //compute.googleapis.com/projects/src-host/regions/asia-northeast1/routers/nat-router
project: projects/100
---
assetType: storage.googleapis.com/Bucket
location: us-central1
name: //storage.googleapis.com/org-bucket-shared-data
project: projects/100
---
assetType: fake.googleapis.com/Unknown
location: global
name: //fake.googleapis.com/projects/src-host/unknowns/x
project: projects/100
"""

_TF_SAMPLE_SUBNET = '''
resource "google_compute_subnetwork" "subnet-svc1" {
  name = "subnet-svc1"
  region = "asia-northeast1"
}
'''

_TF_SAMPLE_BUCKET = '''
resource "google_storage_bucket" "bucket1" {
  name = "org-bucket-shared-data"
  location = "US"
}
'''


def _write(p, body):
    with open(p, "w", encoding="utf-8") as f:
        f.write(body)


class TestParseCaiResources:
    def test_parses_basic_records(self, temp_dir):
        path = os.path.join(temp_dir, "cai.txt")
        _write(path, _CAI_SAMPLE)
        rs = parse_cai_resources(path)
        types = sorted({r["asset_type"] for r in rs})
        assert "compute.googleapis.com/Subnetwork" in types
        assert "storage.googleapis.com/Bucket" in types
        # short_name は full name の末尾セグメント
        names = {r["short_name"] for r in rs}
        assert "subnet-svc1" in names
        assert "nat-router" in names
        assert "org-bucket-shared-data" in names

    def test_missing_file_returns_empty(self, temp_dir):
        assert parse_cai_resources(os.path.join(temp_dir, "nope.txt")) == []


class TestParseTfResources:
    def test_parses_resource_blocks(self, temp_dir):
        d = os.path.join(temp_dir, "tf")
        os.makedirs(d)
        _write(os.path.join(d, "google_compute_subnetwork.tf"), _TF_SAMPLE_SUBNET)
        _write(os.path.join(d, "google_storage_bucket.tf"), _TF_SAMPLE_BUCKET)
        out = parse_tf_resources(d)
        assert "google_compute_subnetwork" in out
        assert "subnet-svc1" in out["google_compute_subnetwork"]
        assert "google_storage_bucket" in out
        assert "org-bucket-shared-data" in out["google_storage_bucket"]

    def test_missing_dir_returns_empty(self, temp_dir):
        assert parse_tf_resources(os.path.join(temp_dir, "nope")) == {}


class TestGcloudRecreateCommand:
    def test_subnet_command_includes_region_and_dst(self):
        cmds = gcloud_recreate_command(
            "compute.googleapis.com/Subnetwork", "subnet-x", "asia-northeast1",
            "dst-host",
            "//compute.googleapis.com/projects/src-host/regions/asia-northeast1/subnetworks/subnet-x",
        )
        joined = " ".join(cmds)
        assert "gcloud compute networks subnets create subnet-x" in joined
        assert "--region=asia-northeast1" in joined
        assert "--project=dst-host" in joined
        # read 操作 (describe) と src 参照は載せない
        assert "describe" not in joined
        assert "--project=src-host" not in joined

    def test_bucket_command_uses_gs_prefix(self):
        cmds = gcloud_recreate_command(
            "storage.googleapis.com/Bucket", "my-bkt", "us-central1",
            "dst-host", "//storage.googleapis.com/my-bkt",
        )
        joined = " ".join(cmds)
        assert "gcloud storage buckets create gs://" in joined
        assert "--location=us-central1" in joined
        assert "--project=dst-host" in joined
        assert "describe" not in joined

    def test_service_account_extracts_account_id(self):
        cmds = gcloud_recreate_command(
            "iam.googleapis.com/ServiceAccount",
            "myacct@src-host.iam.gserviceaccount.com", "global",
            "dst-host",
            "//iam.googleapis.com/projects/src-host/serviceAccounts/"
            "myacct@src-host.iam.gserviceaccount.com",
        )
        joined = " ".join(cmds)
        # create は accountId（email の @ より前）のみを使う
        assert "create myacct " in joined or "create myacct\t" in joined
        # read 操作は載せない / src の email 全体も出さない
        assert "describe" not in joined
        assert "myacct@src-host.iam.gserviceaccount.com" not in joined

    def test_unknown_type_falls_back_to_comment(self):
        cmds = gcloud_recreate_command(
            "fake.googleapis.com/Unknown", "x", "global",
            "dst-host", "//fake.googleapis.com/projects/src-host/unknowns/x",
        )
        # read 操作は載せず、手動対応を促すコメントのみ返す
        assert any(c.lstrip().startswith("#") for c in cmds)
        assert any("自動補完対象外" in c for c in cmds)
        assert all("describe" not in c for c in cmds)


class TestAnalyzeCaiTfDiff:
    def _setup(self, temp_dir):
        cai_path = os.path.join(temp_dir, "cai.txt")
        _write(cai_path, _CAI_SAMPLE)
        tf_dir = os.path.join(temp_dir, "tf_raw")
        os.makedirs(tf_dir)
        _write(os.path.join(tf_dir, "google_compute_subnetwork.tf"), _TF_SAMPLE_SUBNET)
        _write(os.path.join(tf_dir, "google_storage_bucket.tf"), _TF_SAMPLE_BUCKET)
        return cai_path, tf_dir

    def test_detects_missing_subnet_and_router(self, temp_dir):
        cai_path, tf_dir = self._setup(temp_dir)
        report = analyze_cai_tf_diff(
            cai_path, [tf_dir],
            src_project="src-host", dst_project="dst-host",
        )
        missing_names = {(m["asset_type"], m["short_name"]) for m in report["missing"]}
        # subnet-svc1 と bucket は TF にあるので covered
        assert ("compute.googleapis.com/Subnetwork", "subnet-svc1") not in missing_names
        assert ("storage.googleapis.com/Bucket", "org-bucket-shared-data") not in missing_names
        # subnet-svc-missing は gce_restore 担当、nat-router(Router) は None 指定 →
        # 自動処理/対象外なので要手動対応 (missing) には載らない
        assert ("compute.googleapis.com/Subnetwork", "subnet-svc-missing") not in missing_names
        assert ("compute.googleapis.com/Router", "nat-router") not in missing_names
        # 未登録 type (fake) は複製漏れの可能性 → 要手動対応として残る
        assert ("fake.googleapis.com/Unknown", "x") in missing_names
        # 未登録 type は unknown_types にも集計
        assert "fake.googleapis.com/Unknown" in report["unknown_types"]
        # CAI 5 件 / 一致 2 件 / 自動処理・対象外 2 件 (subnet-missing, nat-router)
        assert report["cai_total"] == 5
        assert report["covered"] == 2
        assert report["auto_handled"] == 2

    def test_missing_entries_have_recreate_commands(self, temp_dir):
        cai_path, tf_dir = self._setup(temp_dir)
        report = analyze_cai_tf_diff(
            cai_path, [tf_dir],
            src_project="src-host", dst_project="dst-host",
        )
        for m in report["missing"]:
            assert m["commands"], f"{m['short_name']} に推奨コマンドが無い"
            assert any(c.strip() for c in m["commands"])

    def test_format_diff_report_markdown(self, temp_dir):
        cai_path, tf_dir = self._setup(temp_dir)
        report = analyze_cai_tf_diff(
            cai_path, [tf_dir],
            src_project="src-host", dst_project="dst-host",
        )
        md = format_diff_report([report])
        assert "CAI ↔ Terraform" in md
        assert "src-host" in md and "dst-host" in md
        # 要手動対応 (未登録 type) のみ掲載
        assert "fake.googleapis.com/Unknown" in md
        # 自動処理/対象外 (gce_restore, None) は本文に列挙しない
        assert "subnet-svc-missing" not in md
        assert "```bash" in md  # コマンドが fenced コードブロックで提示される


class TestEmitCaiTfDiff:
    def _orch(self, temp_dir):
        cfg = _full_config(temp_dir, steps={
            "cai_scan": {"enabled": True, "output_dir": os.path.join(temp_dir, "cai_export")},
            "bulk_export": {"enabled": True, "output_dir": os.path.join(temp_dir, "tf")},
        })
        # _iter_src_projects は host_project (src-host) と service_projects (src-svc-1) を
        # 両方返す。テストでは host だけに CAI ファイルを用意して、svc 側は
        # "出力が無いためスキップ" 経路を踏ませる（warning が出れば OK）。
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        return o, cfg

    def test_writes_diff_md_and_logs(self, temp_dir, monkeypatch):
        o, _ = self._orch(temp_dir)
        # CAI 出力を src-host 向けに用意
        cai_dir = os.path.join(temp_dir, "cai_export")
        os.makedirs(cai_dir)
        _write(os.path.join(cai_dir, "cai_resources_src-host.txt"), _CAI_SAMPLE)
        # tf raw を一部だけ用意（subnet-svc1 と bucket は出ている）
        tf_dir = os.path.join(temp_dir, "tf", "raw", "src-host")
        os.makedirs(tf_dir)
        _write(os.path.join(tf_dir, "google_compute_subnetwork.tf"), _TF_SAMPLE_SUBNET)
        _write(os.path.join(tf_dir, "google_storage_bucket.tf"), _TF_SAMPLE_BUCKET)

        # 実体は logs/<timestamp>/DIFF.md、cwd の DIFF.md は symlink
        # → cwd を temp_dir に切り替えて symlink を生成させる
        monkeypatch.chdir(temp_dir)
        o._emit_cai_tf_diff()
        # 実体は self.run_dir 配下にある
        real_path = os.path.join(o.run_dir, "DIFF.md")
        assert os.path.isfile(real_path)
        # cwd の DIFF.md は symlink で、最新の実体を指している
        symlink_path = os.path.join(temp_dir, "DIFF.md")
        assert os.path.islink(symlink_path)
        assert os.path.realpath(symlink_path) == os.path.realpath(real_path)
        body = open(symlink_path, encoding="utf-8").read()
        # 要手動対応 (未登録 type) のみ掲載
        assert "fake.googleapis.com/Unknown" in body
        # 自動処理/対象外 (gce_restore, None 指定) は載らない
        assert "subnet-svc-missing" not in body
        assert "nat-router" not in body

    def test_symlink_replaces_existing_regular_file(self, temp_dir, monkeypatch):
        """cwd に既存の通常ファイル DIFF.md があっても上書きして symlink に張り替える。"""
        o, _ = self._orch(temp_dir)
        cai_dir = os.path.join(temp_dir, "cai_export")
        os.makedirs(cai_dir)
        _write(os.path.join(cai_dir, "cai_resources_src-host.txt"), _CAI_SAMPLE)
        tf_dir = os.path.join(temp_dir, "tf", "raw", "src-host")
        os.makedirs(tf_dir)
        _write(os.path.join(tf_dir, "google_compute_subnetwork.tf"), _TF_SAMPLE_SUBNET)

        # 事前に通常ファイルとして DIFF.md を置いておく（旧コミットの状態を再現）
        monkeypatch.chdir(temp_dir)
        legacy_diff = os.path.join(temp_dir, "DIFF.md")
        _write(legacy_diff, "OLD CONTENT")
        assert not os.path.islink(legacy_diff)

        o._emit_cai_tf_diff()

        # symlink に張り替わり、実体は run_dir 側
        assert os.path.islink(legacy_diff)
        assert os.path.realpath(legacy_diff) == os.path.realpath(
            os.path.join(o.run_dir, "DIFF.md")
        )


class TestFwRuleScopeFlag:
    """rules / associations サブコマンド用の scope flag 変換。"""

    def test_global(self):
        assert fw_rule_scope_flag("--global") == "--global-firewall-policy"

    def test_region(self):
        assert (
            fw_rule_scope_flag("--region=asia-northeast1")
            == "--firewall-policy-region=asia-northeast1"
        )

    def test_already_rule_scope_passthrough(self):
        assert fw_rule_scope_flag("--global-firewall-policy") == "--global-firewall-policy"
        assert (
            fw_rule_scope_flag("--firewall-policy-region=us-central1")
            == "--firewall-policy-region=us-central1"
        )


# ============================================================
# 並列実行 (_parallel_for_each) と Step 5 並列化の安全性
# ============================================================
import threading as _threading_mod
import time as _time_mod


class TestParallelForEach:
    """_parallel_for_each: parallel_jobs=1 は直列、>1 は並列実行で全要素を処理。"""

    def _orchestrator(self, temp_dir, parallel_jobs: int):
        cfg = _full_config(temp_dir)
        cfg["global"]["parallel_jobs"] = parallel_jobs
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        return o

    def test_serial_runs_all_items_in_order(self, temp_dir):
        o = self._orchestrator(temp_dir, parallel_jobs=1)
        seen: list = []
        o._parallel_for_each([1, 2, 3, 4, 5], seen.append, "test-serial")
        assert seen == [1, 2, 3, 4, 5]

    def test_parallel_runs_all_items(self, temp_dir):
        o = self._orchestrator(temp_dir, parallel_jobs=4)
        seen: list = []
        lock = _threading_mod.Lock()

        def worker(x):
            with lock:
                seen.append(x)

        items = list(range(20))
        o._parallel_for_each(items, worker, "test-parallel")
        assert sorted(seen) == items  # 全要素実行、順序不問

    def test_parallel_actually_overlaps(self, temp_dir):
        """並列実行がシリアルより速いことを確認（タイミングテスト）。"""
        o_serial = self._orchestrator(temp_dir, parallel_jobs=1)
        o_parallel = self._orchestrator(temp_dir, parallel_jobs=8)

        def slow_work(_x):
            _time_mod.sleep(0.05)

        items = list(range(8))

        t0 = _time_mod.monotonic()
        o_serial._parallel_for_each(items, slow_work, "serial")
        serial_dt = _time_mod.monotonic() - t0

        t0 = _time_mod.monotonic()
        o_parallel._parallel_for_each(items, slow_work, "parallel")
        parallel_dt = _time_mod.monotonic() - t0

        # 並列は直列の半分以下になるはず（8並列で 8*50ms vs 50ms）
        assert parallel_dt < serial_dt / 2, (
            f"parallel={parallel_dt:.3f}s, serial={serial_dt:.3f}s — 並列化が効いていない"
        )

    def test_parallel_propagates_worker_exception(self, temp_dir):
        """worker が例外を投げたら _parallel_for_each は再 raise する。"""
        o = self._orchestrator(temp_dir, parallel_jobs=4)

        def bad_worker(x):
            if x == 3:
                raise RuntimeError(f"boom at {x}")

        with pytest.raises(RuntimeError, match="boom"):
            o._parallel_for_each(list(range(10)), bad_worker, "test-exc")


class TestRestoreOneVmFailureHandling:
    """_restore_one_vm: snapshot 未検出時に sys.exit せず stats.failed に記録する。

    並列モードで sys.exit すると他 VM の処理が巻き添えで止まるため、
    failure を記録して return する（最終的に main() で exit 1）。
    """

    def _orchestrator(self, temp_dir):
        cfg = _full_config(temp_dir)
        cfg["global"]["mock"] = False
        cfg["global"]["dry_run"] = False
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        # logger を仮設定（実ファイルは作らない）
        o.dst_logger = logging.getLogger("test-dst")
        o.org_logger = logging.getLogger("test-org")
        return o

    def test_snapshot_missing_records_failure_no_exit(self, temp_dir):
        o = self._orchestrator(temp_dir)
        vm = {
            "name": "vm1",
            "zone": "projects/p/zones/asia-northeast1-a",
            "machineType": "projects/p/zones/z/machineTypes/e2-micro",
            "disks": [{"boot": True, "source": "projects/p/zones/z/disks/disk1"}],
        }
        # snapshots 空: _find_valid_snapshot が None を返す
        before_failed = o.stats.failed
        o._restore_one_vm(
            vm, snapshots=[],
            src_proj="src-p", dst_proj="dst-p",
            src_sa=None, dst_sa=None,
            proj_map={}, max_age_days=30,
        )
        # sys.exit が起きないことを確認、failed が +1 されることを確認
        assert o.stats.failed == before_failed + 1
        assert any("Restore VM vm1" in d for d, _ in o.stats.failures)

    def test_no_boot_disk_silently_skips(self, temp_dir):
        o = self._orchestrator(temp_dir)
        vm = {"name": "vm-noboot", "zone": "z", "disks": [{"boot": False}]}
        before_failed = o.stats.failed
        o._restore_one_vm(
            vm, snapshots=[],
            src_proj="s", dst_proj="d",
            src_sa=None, dst_sa=None,
            proj_map={}, max_age_days=30,
        )
        # boot disk が無い → 何もしない（failed カウントも増えない）
        assert o.stats.failed == before_failed

    def test_no_vm_name_silently_skips(self, temp_dir):
        o = self._orchestrator(temp_dir)
        vm = {"disks": [{"boot": True, "source": "/disks/d"}]}
        before_failed = o.stats.failed
        o._restore_one_vm(
            vm, snapshots=[],
            src_proj="s", dst_proj="d",
            src_sa=None, dst_sa=None,
            proj_map={}, max_age_days=30,
        )
        assert o.stats.failed == before_failed


class TestRestoreOneVmEndsRunning:
    """_restore_one_vm: 復元チェーンは常に VM を RUNNING で残す。

    電源状態の TERMINATED / SUSPENDED への反映は Step 5 の最終フェーズ
    (_finalize_vm_power_states) で実施するため、ここでは src.status に
    関わらず stop / suspend は発行されないことを確認。
    """
    def _orchestrator(self, temp_dir):
        cfg = _full_config(temp_dir)
        cfg["global"]["mock"] = False
        cfg["global"]["dry_run"] = False
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        o.dst_logger = logging.getLogger("test-dst")
        o.org_logger = logging.getLogger("test-org")
        return o

    @staticmethod
    def _vm(status: str):
        return {
            "name": "vm1",
            "zone": "projects/p/zones/asia-northeast1-a",
            "machineType": "projects/p/zones/z/machineTypes/e2-micro",
            "disks": [{"boot": True, "source": "projects/p/zones/z/disks/disk1"}],
            "status": status,
        }

    @staticmethod
    def _snapshots():
        import datetime as dt
        ts = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)).isoformat()
        return [{
            "name": "snap-1",
            "sourceDisk": "projects/p/zones/z/disks/disk1",
            "creationTimestamp": ts,
        }]

    def _capture(self, o, vm, vm_exists):
        from unittest.mock import patch
        gcloud_seq = [vm_exists, False]
        def gcloud_exists_side(*_a, **_kw):
            return gcloud_seq.pop(0) if gcloud_seq else False
        with patch.object(o, "run_command", return_value="") as mock_run, \
             patch.object(o, "_gcloud_exists", side_effect=gcloud_exists_side), \
             patch.object(o, "_attach_secondary_disks", return_value=None):
            o._restore_one_vm(
                vm, self._snapshots(),
                src_proj="src-p", dst_proj="dst-p",
                src_sa=None, dst_sa=None,
                proj_map={}, max_age_days=30,
            )
        return [c.args[0] for c in mock_run.call_args_list]

    @pytest.mark.parametrize("status", ["RUNNING", "TERMINATED", "SUSPENDED"])
    def test_new_vm_never_stops_or_suspends(self, temp_dir, status):
        o = self._orchestrator(temp_dir)
        calls = self._capture(o, self._vm(status), vm_exists=False)
        assert any("instances create vm1" in c for c in calls)
        assert not any("instances stop vm1" in c for c in calls)
        assert not any("instances suspend vm1" in c for c in calls)

    @pytest.mark.parametrize("status", ["RUNNING", "TERMINATED", "SUSPENDED"])
    def test_existing_vm_always_starts_at_end(self, temp_dir, status):
        o = self._orchestrator(temp_dir)
        calls = self._capture(o, self._vm(status), vm_exists=True)
        attach_idx = next(
            i for i, c in enumerate(calls)
            if "instances attach-disk vm1" in c and "--boot" in c
        )
        start_idx = next(i for i, c in enumerate(calls) if "instances start vm1" in c)
        assert start_idx > attach_idx
        assert not any("instances suspend vm1" in c for c in calls)


class TestFinalizeVmPowerStates:
    """_finalize_vm_power_states: Step 5 の最終フェーズで電源状態を反映する。

    - dry_run/mock では sleep をスキップ
    - TERMINATED 目標 → run_command で `instances stop` 発行
    - SUSPENDED 目標 → _try_dst_suspend を呼ぶ
    - suspend 失敗 (subprocess returncode!=0) は stats.failed を増やさない
    """
    def _orchestrator(self, temp_dir, dry_run=False, mock=False):
        cfg = _full_config(temp_dir)
        cfg["global"]["mock"] = mock
        cfg["global"]["dry_run"] = dry_run
        cfg["global"]["parallel_jobs"] = 1
        cfg.setdefault("steps", {})
        cfg["steps"]["gce_restore"] = {"enabled": True, "power_state_wait_seconds": 0}
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        o.dst_logger = logging.getLogger("test-dst")
        o.org_logger = logging.getLogger("test-org")
        return o

    def test_dry_run_skips_sleep_and_real_commands(self, temp_dir):
        from unittest.mock import patch
        o = self._orchestrator(temp_dir, dry_run=True)
        pending = [("vm-t", "zone-a", "dst-p", None, "TERMINATED")]
        with patch("time.sleep") as mock_sleep, \
             patch.object(o, "run_command", return_value="") as mock_run:
            o._finalize_vm_power_states(pending)
        mock_sleep.assert_not_called()
        # dry_run でも run_command は呼ばれる（中で [DRY RUN] プレフィクスを出すだけ）
        assert any("instances stop vm-t" in c.args[0] for c in mock_run.call_args_list)

    def test_terminated_calls_stop(self, temp_dir):
        from unittest.mock import patch
        o = self._orchestrator(temp_dir)
        pending = [("vm-t", "zone-a", "dst-p", None, "TERMINATED")]
        with patch("time.sleep"), \
             patch.object(o, "run_command", return_value="") as mock_run:
            o._finalize_vm_power_states(pending)
        cmds = [c.args[0] for c in mock_run.call_args_list]
        assert any("instances stop vm-t" in c for c in cmds)
        assert not any("instances suspend vm-t" in c for c in cmds)

    def test_suspended_calls_try_suspend(self, temp_dir):
        from unittest.mock import patch
        o = self._orchestrator(temp_dir)
        pending = [("vm-s", "zone-a", "dst-p", None, "SUSPENDED")]
        with patch("time.sleep"), \
             patch.object(o, "_try_dst_suspend", return_value=True) as mock_susp, \
             patch.object(o, "run_command", return_value=""):
            o._finalize_vm_power_states(pending)
        mock_susp.assert_called_once_with("vm-s", "zone-a", "dst-p", None)

    def test_suspend_failure_does_not_increment_stats_failed(self, temp_dir):
        from unittest.mock import patch, MagicMock
        o = self._orchestrator(temp_dir)
        before = o.stats.failed
        fail = MagicMock(returncode=1, stderr="UNSUPPORTED_OPERATION", stdout="")
        with patch("subprocess.run", return_value=fail):
            ok = o._try_dst_suspend("vm-s", "zone-a", "dst-p", None)
        assert ok is False
        assert o.stats.failed == before  # run 全体を落とさない

    def test_suspend_success(self, temp_dir):
        from unittest.mock import patch, MagicMock
        o = self._orchestrator(temp_dir)
        ok_res = MagicMock(returncode=0, stderr="", stdout="")
        with patch("subprocess.run", return_value=ok_res):
            ok = o._try_dst_suspend("vm-s", "zone-a", "dst-p", None)
        assert ok is True


# ============================================================
# host_project.skip: host を処理対象から外す
# ============================================================
class TestHostSkip:
    def _orch(self, temp_dir, skip):
        cfg = _full_config(temp_dir)
        cfg["global"]["dry_run"] = False
        if skip is not None:
            cfg["project_mapping"]["host_project"]["skip"] = skip
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        return o

    def test_iterators_include_host_by_default(self, temp_dir):
        o = self._orch(temp_dir, skip=None)
        assert [s for s, _ in o._iter_src_projects()] == ["src-host", "src-svc-1"]
        assert [p[0] for p in o._iter_project_pairs()] == ["src-host", "src-svc-1"]

    def test_iterators_exclude_skipped_host(self, temp_dir):
        o = self._orch(temp_dir, skip=True)
        assert [s for s, _ in o._iter_src_projects()] == ["src-svc-1"]
        assert [p[0] for p in o._iter_project_pairs()] == ["src-svc-1"]

    def test_id_and_number_maps_keep_skipped_host(self, temp_dir):
        """service .tf 内の host 参照置換に必要なため、マップからは外さない。"""
        o = self._orch(temp_dir, skip=True)
        assert o._build_proj_id_map() == {
            "src-host": "dst-host", "src-svc-1": "dst-svc-1"}
        assert [p[0] for p in o._iter_project_pairs(include_skipped=True)] == [
            "src-host", "src-svc-1"]

    def test_collect_terraform_roots_excludes_skipped_host(self, temp_dir):
        o = self._orch(temp_dir, skip=True)
        active = os.path.join(temp_dir, "active")
        for name in ("src-host", "src-svc-1"):
            os.makedirs(os.path.join(active, name))
            with open(os.path.join(active, name, "main.tf"), "w", encoding="utf-8") as f:
                f.write("# tf\n")
        assert o._collect_terraform_roots(active) == [
            os.path.join(active, "src-svc-1")]

    def _customize(self, o, temp_dir):
        raw = os.path.join(temp_dir, "raw")
        active = os.path.join(temp_dir, "active")
        os.makedirs(os.path.join(raw, "src-svc-1"), exist_ok=True)
        with open(os.path.join(raw, "src-svc-1", "b.tf"), "w", encoding="utf-8") as f:
            f.write('resource "google_storage_bucket" "b" {\n'
                    '  name = "src-bucket"\n  project = "src-svc-1"\n}\n')
        host_dir = os.path.join(active, "src-host")
        os.makedirs(host_dir, exist_ok=True)
        with open(os.path.join(host_dir, "terraform.tfstate"), "w", encoding="utf-8") as f:
            f.write("{}")
        with open(os.path.join(host_dir, "old.tf"), "w", encoding="utf-8") as f:
            f.write("# old\n")
        o.customize_hcl(raw, active)
        return host_dir

    def test_customize_hcl_preserves_skipped_host_dir(self, temp_dir):
        """raw に無い skip host の active/ を孤児扱いで消さない（state 温存）。"""
        o = self._orch(temp_dir, skip=True)
        host_dir = self._customize(o, temp_dir)
        assert os.path.exists(os.path.join(host_dir, "terraform.tfstate"))
        assert os.path.exists(os.path.join(host_dir, "old.tf"))
        assert not os.path.exists(os.path.join(host_dir, ".dst_project"))

    def test_customize_hcl_removes_orphan_host_dir_without_skip(self, temp_dir):
        o = self._orch(temp_dir, skip=False)
        host_dir = self._customize(o, temp_dir)
        assert not os.path.exists(host_dir)


# ============================================================
# --clean-state: 特定プロジェクトの生成物だけ削除
# ============================================================
class TestCleanState:
    def _prep(self, temp_dir):
        tf_base = os.path.join(temp_dir, "terraform")
        cfg = _full_config(
            temp_dir, steps={"bulk_export": {"output_dir": tf_base}})
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        for sub in (os.path.join("active", "src-host"),
                    os.path.join("active", "src-svc-1"),
                    os.path.join("raw", "src-svc-1")):
            os.makedirs(os.path.join(tf_base, sub))
        with open(os.path.join(tf_base, "active", "src-svc-1", "terraform.tfstate"),
                  "w", encoding="utf-8") as f:
            f.write("{}")
        with open(os.path.join(tf_base, ".gcs_rename_value"), "w", encoding="utf-8") as f:
            f.write("-dst-x")
        return path, tf_base, cfg

    def test_resolve_by_src_and_dst_id(self, temp_dir):
        _path, tf_base, cfg = self._prep(temp_dir)
        for pid in ("src-svc-1", "dst-svc-1"):
            targets, unresolved = resolve_clean_targets(cfg, [pid], tf_base)
            assert unresolved == []
            assert targets == [
                os.path.join(tf_base, "active", "src-svc-1"),
                os.path.join(tf_base, "raw", "src-svc-1"),
            ]

    def test_resolve_by_marker_for_removed_project(self, temp_dir):
        """config から消えた旧プロジェクトも .dst_project マーカーで解決できる。"""
        _path, tf_base, cfg = self._prep(temp_dir)
        old_dir = os.path.join(tf_base, "active", "src-old")
        os.makedirs(old_dir)
        with open(os.path.join(old_dir, ".dst_project"), "w", encoding="utf-8") as f:
            f.write("dst-old\n")
        targets, unresolved = resolve_clean_targets(cfg, ["dst-old"], tf_base)
        assert unresolved == []
        assert targets == [old_dir]

    def test_unknown_id_is_unresolved(self, temp_dir):
        _path, tf_base, cfg = self._prep(temp_dir)
        targets, unresolved = resolve_clean_targets(cfg, ["nope"], tf_base)
        assert targets == []
        assert unresolved == ["nope"]

    def test_run_clean_state_removes_only_target(self, temp_dir):
        path, tf_base, _cfg = self._prep(temp_dir)
        assert run_clean_state(path, ["dst-svc-1"]) == 0
        assert not os.path.isdir(os.path.join(tf_base, "active", "src-svc-1"))
        assert not os.path.isdir(os.path.join(tf_base, "raw", "src-svc-1"))
        assert os.path.isdir(os.path.join(tf_base, "active", "src-host"))
        assert os.path.exists(os.path.join(tf_base, ".gcs_rename_value"))

    def test_run_clean_state_aborts_on_unknown_id(self, temp_dir):
        """1 つでも解決できない ID があれば何も削除しない。"""
        path, tf_base, _cfg = self._prep(temp_dir)
        assert run_clean_state(path, ["dst-svc-1", "typo"]) == 1
        assert os.path.isdir(os.path.join(tf_base, "active", "src-svc-1"))
