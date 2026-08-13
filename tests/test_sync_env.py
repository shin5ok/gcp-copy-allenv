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
    classify_missing_asset,
    bound_custom_role_ids,
    _parse_gcloud_describe_json,
    resolve_clean_targets,
    run_clean_state,
    step_enabled,
    parse_user_managed_sa,
    remap_sa_email,
    remap_iam_role,
    build_iam_replication_plan,
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
        o = self._setup(temp_dir, cai_scan={"enabled": True},
                        network_firewall={"enabled": False},
                        iam_sync={"enabled": False})
        o._sa_preflight_run = self._fake_token()
        o._test_iam_permissions = self._fake_perms(granted=granted)
        o.check_service_accounts()  # 例外なし

    def test_default_enabled_steps_perms_are_required(self, temp_dir):
        """キー未指定でも有効なステップ (iam_sync / network_firewall) の権限も要求する。

        execute() と preflight の enabled 既定値は step_enabled() で共通化されている。
        片方だけ既定が違うと「preflight は通るのに本体で権限エラー」になる。
        """
        granted = {
            "resourcemanager.projects.get",
            "cloudasset.assets.searchAllResources",
            "resourcemanager.projects.getIamPolicy",
            "iam.serviceAccounts.create",
            "compute.firewalls.list",
            "compute.networkFirewallPolicies.list",
            "compute.firewalls.create",
            "compute.networkFirewallPolicies.create",
        }
        o = self._setup(temp_dir, cai_scan={"enabled": True})
        o._sa_preflight_run = self._fake_token()
        checker = self._fake_perms(granted=granted)
        o._test_iam_permissions = checker
        o.check_service_accounts()  # 例外なし
        assert any("resourcemanager.projects.getIamPolicy" in perms
                   for _proj, perms in checker.calls)

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
        o = self._setup(temp_dir, cai_scan={"enabled": True},
                        data_sync={"enabled": False},
                        network_firewall={"enabled": False},
                        iam_sync={"enabled": False})
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
        # ADC が src に書込権を持つ + 非対話 + --yes 未指定 → 中断
        o = self._setup_adc(temp_dir, cai_scan={"enabled": True})
        o._sa_preflight_run = self._fake_local_runner()
        o._test_iam_permissions = self._fake_perms(granted={"compute.instances.create"})
        monkeypatch.setattr("scripts.sync_env.sys.stdin.isatty", lambda: False)
        with pytest.raises(SystemExit) as ei:
            o.check_service_accounts()
        assert ei.value.code == 1

    def test_adc_auto_approve_flag_skips_prompt(self, temp_dir, monkeypatch):
        # --yes が指定されていれば非対話でも続行
        o = self._setup_adc(temp_dir, cai_scan={"enabled": True})
        o.auto_approve = True
        o._sa_preflight_run = self._fake_local_runner()
        o._test_iam_permissions = self._fake_perms(granted={"compute.instances.create"})
        monkeypatch.setattr("scripts.sync_env.sys.stdin.isatty", lambda: False)
        o.check_service_accounts()  # 例外なし

    def test_adc_interactive_yes_continues(self, temp_dir, monkeypatch):
        # 対話セッションで "y" を返したら続行
        o = self._setup_adc(temp_dir, cai_scan={"enabled": True})
        o._sa_preflight_run = self._fake_local_runner()
        o._test_iam_permissions = self._fake_perms(granted={"compute.instances.create"})
        monkeypatch.setattr("scripts.sync_env.sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("builtins.input", lambda _prompt="": "y")
        o.check_service_accounts()  # 例外なし

    def test_adc_interactive_no_aborts(self, temp_dir, monkeypatch):
        # 対話セッションで "n" を返したら中断
        o = self._setup_adc(temp_dir, cai_scan={"enabled": True})
        o._sa_preflight_run = self._fake_local_runner()
        o._test_iam_permissions = self._fake_perms(granted={"compute.instances.create"})
        monkeypatch.setattr("scripts.sync_env.sys.stdin.isatty", lambda: True)
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

    def test_non_tty_without_yes_exits(self, temp_dir, monkeypatch):
        o = self._orch(temp_dir)
        monkeypatch.setattr("scripts.sync_env.sys.stdin.isatty", lambda: False)
        with pytest.raises(SystemExit) as ei:
            o._confirm_adc_src_write_or_abort(["src 'p': compute.instances.create"])
        assert ei.value.code == 1

    def test_yes_flag_overrides_non_tty(self, temp_dir, monkeypatch):
        o = self._orch(temp_dir)
        o.auto_approve = True
        monkeypatch.setattr("scripts.sync_env.sys.stdin.isatty", lambda: False)
        monkeypatch.setattr("builtins.input", lambda _p="": (_ for _ in ()).throw(AssertionError("called")))
        o._confirm_adc_src_write_or_abort(["src 'p': storage.buckets.delete"])  # 例外なし

    def test_yes_flag_skips_prompt_on_tty(self, temp_dir, monkeypatch):
        # 対話セッションでも --yes があればプロンプトを出さない
        o = self._orch(temp_dir)
        o.auto_approve = True
        monkeypatch.setattr("scripts.sync_env.sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("builtins.input", lambda _p="": (_ for _ in ()).throw(AssertionError("called")))
        o._confirm_adc_src_write_or_abort(["src 'p': storage.buckets.delete"])  # 例外なし

    def test_env_var_is_not_honored(self, temp_dir, monkeypatch):
        # 環境変数による暗黙承認は廃止（気付かないまま承認される事故を防ぐ）
        o = self._orch(temp_dir)
        monkeypatch.setattr("scripts.sync_env.sys.stdin.isatty", lambda: False)
        monkeypatch.setenv("COPY_ALL_ENV_AUTO_APPROVE", "1")
        with pytest.raises(SystemExit) as ei:
            o._confirm_adc_src_write_or_abort(["src 'p': storage.buckets.delete"])
        assert ei.value.code == 1

    def test_interactive_yes(self, temp_dir, monkeypatch):
        o = self._orch(temp_dir)
        monkeypatch.setattr("scripts.sync_env.sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("builtins.input", lambda _p="": "yes")
        o._confirm_adc_src_write_or_abort(["src 'p': compute.disks.delete"])  # 例外なし

    def test_interactive_blank_aborts(self, temp_dir, monkeypatch):
        # Enter キーのみ（空文字）はデフォルト N 扱いで中断
        o = self._orch(temp_dir)
        monkeypatch.setattr("scripts.sync_env.sys.stdin.isatty", lambda: True)
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

    def test_captures_state_and_ip_address(self, temp_dir):
        sample = (
            "---\n"
            "additionalAttributes:\n"
            "  address: 10.100.1.203\n"
            "assetType: compute.googleapis.com/Address\n"
            "location: asia-northeast1\n"
            "name: //compute.googleapis.com/projects/src-host/regions/"
            "asia-northeast1/addresses/svc1-fix1\n"
            "project: projects/100\n"
            "state: RESERVED\n"
        )
        path = os.path.join(temp_dir, "cai_addr.txt")
        _write(path, sample)
        rs = parse_cai_resources(path)
        assert len(rs) == 1
        assert rs[0]["short_name"] == "svc1-fix1"
        assert rs[0]["state"] == "RESERVED"
        assert rs[0]["ip_address"] == "10.100.1.203"


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
        # 先頭に WHAT / WHY / HOW テーブルが来る（詳細より前）
        assert "## 要対応" in md
        assert "| WHAT（何が dst に無いか） | WHY（なぜ対応が必要か） | HOW（どう対応するか） |" in md
        assert md.index("## 要対応") < md.index("## プロジェクト別 詳細")
        assert "## 参考（実害なしと判定したもの / 優先度順）" in md


class TestDiffReferencePrioritySort:
    def _entry(self, kind, name, priority, why):
        return {
            "asset_type": f"x.googleapis.com/{kind}", "short_name": name,
            "full_name": f"//x/{name}", "location": "global",
            "state": "", "ip_address": "",
            "tf_resource_type": None, "coverage_step": "terraform_apply",
            "reason": "r", "commands": ["echo x"],
            "level": "reference", "kind": kind, "why": why, "how": "h",
            "priority": priority,
        }

    def test_reference_rows_sorted_by_priority(self):
        report = {
            "src_project": "s", "dst_project": "d",
            "cai_total": 3, "tf_total": 0, "covered": 0, "auto_handled": 0,
            "missing": [
                self._entry("KindC", "c1", 3, "why-p3"),
                self._entry("KindA", "a1", 1, "why-p1"),
                self._entry("KindB", "b1", 2, "why-p2"),
            ],
            "action_total": 0, "unknown_types": [],
        }
        md = format_diff_report([report])
        assert "| 優先度 | WHAT |" in md
        # 検出順 (3,1,2) ではなく優先度昇順 (1,2,3) で並ぶ
        assert md.index("why-p1") < md.index("why-p2") < md.index("why-p3")
        assert "1: 確認推奨" in md
        assert "2: 条件付き" in md
        assert "3: 対応不要" in md


class TestClassifyMissingAsset:
    def _item(self, atype, short, full="", coverage_step="terraform_apply",
              state="", ip_address=""):
        return {
            "asset_type": atype, "short_name": short,
            "full_name": full or f"//x/{short}", "location": "global",
            "coverage_step": coverage_step, "reason": "bulk-export が出力しなかった",
            "state": state, "ip_address": ip_address,
        }

    def test_user_managed_sa_is_reference_when_iam_sync_enabled(self):
        c = classify_missing_asset(
            self._item("iam.googleapis.com/ServiceAccount",
                       "editor@src-svc.iam.gserviceaccount.com"),
            iam_sync_enabled=True)
        assert c["level"] == "reference"
        assert "iam_sync" in c["why"]

    def test_user_managed_sa_is_action_when_iam_sync_disabled(self):
        c = classify_missing_asset(
            self._item("iam.googleapis.com/ServiceAccount",
                       "editor@src-svc.iam.gserviceaccount.com"),
            iam_sync_enabled=False)
        assert c["level"] == "action"

    def test_default_compute_sa_is_reference(self):
        c = classify_missing_asset(
            self._item("iam.googleapis.com/ServiceAccount",
                       "1234567890-compute@developer.gserviceaccount.com"))
        assert c["level"] == "reference"

    def test_migration_tool_role_is_reference(self):
        c = classify_missing_asset(self._item(
            "iam.googleapis.com/Role", "migrationSrcReader",
            "//iam.googleapis.com/projects/src-svc/roles/migrationSrcReader"))
        assert c["level"] == "reference"

    def test_unbound_custom_role_is_reference_bound_one_is_action(self):
        bound = {"projects/src-svc/roles/Incre"}
        unbound = classify_missing_asset(
            self._item("iam.googleapis.com/Role", "incre2",
                       "//iam.googleapis.com/projects/src-svc/roles/incre2"),
            bound_custom_roles=bound)
        assert unbound["level"] == "reference"
        used = classify_missing_asset(
            self._item("iam.googleapis.com/Role", "Incre",
                       "//iam.googleapis.com/projects/src-svc/roles/Incre"),
            bound_custom_roles=bound)
        assert used["level"] == "action"

    def test_custom_role_is_action_when_bindings_unknown(self):
        # 判定材料が無い場合は安全側 (要対応) に倒す
        c = classify_missing_asset(
            self._item("iam.googleapis.com/Role", "incre2",
                       "//iam.googleapis.com/projects/src-svc/roles/incre2"),
            bound_custom_roles=None)
        assert c["level"] == "action"

    def test_default_log_resources_are_reference_custom_is_action(self):
        for name in ("_Default", "_Required"):
            for atype in ("logging.googleapis.com/LogBucket",
                          "logging.googleapis.com/LogSink"):
                assert classify_missing_asset(
                    self._item(atype, name))["level"] == "reference"
        assert classify_missing_asset(self._item(
            "logging.googleapis.com/LogSink", "audit-to-bq"))["level"] == "action"

    def test_address_without_state_and_unknown_type_are_action(self):
        # state 不明の Address は判定材料なし → 安全側 (action)
        assert classify_missing_asset(self._item(
            "compute.googleapis.com/Address", "svc1-ip"))["level"] == "action"
        assert classify_missing_asset(self._item(
            "fake.googleapis.com/Unknown", "x",
            coverage_step="<unknown>"))["level"] == "action"

    def test_nat_auto_ip_address_is_reference(self):
        c = classify_missing_asset(self._item(
            "compute.googleapis.com/Address", "nat-auto-ip-10281266-0-178655",
            state="IN_USE", ip_address="34.84.246.19"))
        assert c["level"] == "reference"
        assert c["priority"] == 3

    def test_reserved_address_is_reference(self):
        # 内部 / 外部どちらも RESERVED（未使用の取り置き）なら実害なし
        for ip in ("10.100.1.203", "34.84.204.112"):
            c = classify_missing_asset(self._item(
                "compute.googleapis.com/Address", "parked", state="RESERVED",
                ip_address=ip))
            assert c["level"] == "reference"
            assert c["priority"] == 2

    def test_in_use_internal_address_follows_gce_restore(self):
        item = self._item("compute.googleapis.com/Address", "vm1-ip",
                          state="IN_USE", ip_address="10.100.1.11")
        c = classify_missing_asset(item, gce_restore_enabled=True)
        assert c["level"] == "reference"
        assert c["priority"] == 1
        assert "mig-" in c["why"]
        # gce_restore 無効なら内部 IP を予約するステップが無い → action
        assert classify_missing_asset(
            item, gce_restore_enabled=False)["level"] == "action"

    def test_in_use_external_address_is_action(self):
        # 使用中の外部 IP（nat-auto 以外）は静的 IP 前提が崩れるので action のまま
        c = classify_missing_asset(self._item(
            "compute.googleapis.com/Address", "lb-ip",
            state="IN_USE", ip_address="34.84.204.112"))
        assert c["level"] == "action"

    def test_reference_priorities_of_existing_branches(self):
        # iam_sync が対応する SA = 1 / 既定ログリソース・未付与ロール = 2 / 完全不要 = 3
        sa = classify_missing_asset(self._item(
            "iam.googleapis.com/ServiceAccount",
            "editor@src-svc.iam.gserviceaccount.com"), iam_sync_enabled=True)
        assert sa["priority"] == 1
        log = classify_missing_asset(self._item(
            "logging.googleapis.com/LogBucket", "_Default"))
        assert log["priority"] == 2
        unbound = classify_missing_asset(self._item(
            "iam.googleapis.com/Role", "incre2",
            "//iam.googleapis.com/projects/src-svc/roles/incre2"),
            bound_custom_roles={"projects/src-svc/roles/Other"})
        assert unbound["priority"] == 2
        default_sa = classify_missing_asset(self._item(
            "iam.googleapis.com/ServiceAccount",
            "1234567890-compute@developer.gserviceaccount.com"))
        assert default_sa["priority"] == 3
        tool_role = classify_missing_asset(self._item(
            "iam.googleapis.com/Role", "migrationSrcReader",
            "//iam.googleapis.com/projects/src-svc/roles/migrationSrcReader"))
        assert tool_role["priority"] == 3


class TestBoundCustomRoleIds:
    def test_collects_only_custom_roles_from_bindings(self):
        policies = {
            "src-svc": {"bindings": [
                {"role": "roles/viewer", "members": ["user:a@example.com"]},
                {"role": "projects/src-svc/roles/Incre", "members": ["user:a@example.com"]},
                {"role": "organizations/123/roles/OrgRole", "members": []},
                "not-a-dict",
            ]},
            "src-empty": {},
        }
        out = bound_custom_role_ids(policies)
        assert out == {"projects/src-svc/roles/Incre", "organizations/123/roles/OrgRole"}

    def test_empty_input(self):
        assert bound_custom_role_ids({}) == set()


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


# ============================================================
# standalone_projects: 共有 VPC 非所属プロジェクトの移行
# ============================================================
_STANDALONE_ENTRY = {
    "src": "src-alone-1",
    "dst": "dst-alone-1",
    "src_impersonate_service_account": "",
    "dst_impersonate_service_account": "",
}


class TestValidateConfigStandalone:
    def test_standalone_with_shared_vpc_ok(self, temp_dir):
        cfg = _full_config(temp_dir)
        cfg["project_mapping"]["standalone_projects"] = [dict(_STANDALONE_ENTRY)]
        assert validate_config(cfg) == []

    def test_standalone_only_ok(self, temp_dir):
        """standalone のみなら host_project / service_projects を省略できる。"""
        cfg = _full_config(temp_dir)
        cfg["project_mapping"] = {
            "standalone_projects": [dict(_STANDALONE_ENTRY)],
        }
        assert validate_config(cfg) == []

    def test_services_without_host_still_rejected(self, temp_dir):
        """service_projects がある（Shared VPC 構成）なら host は必須のまま。"""
        cfg = _full_config(temp_dir)
        del cfg["project_mapping"]["host_project"]
        cfg["project_mapping"]["standalone_projects"] = [dict(_STANDALONE_ENTRY)]
        errors = validate_config(cfg)
        assert any("host_project" in e for e in errors)

    def test_all_empty_rejected(self, temp_dir):
        cfg = _full_config(temp_dir)
        cfg["project_mapping"] = {"standalone_projects": []}
        errors = validate_config(cfg)
        assert errors  # host / service いずれかのエラーが出る

    def test_standalone_src_eq_dst_rejected(self, temp_dir):
        cfg = _full_config(temp_dir)
        ent = dict(_STANDALONE_ENTRY)
        ent["dst"] = ent["src"]
        cfg["project_mapping"]["standalone_projects"] = [ent]
        errors = validate_config(cfg)
        assert any("standalone_projects[0]" in e and "同一" in e for e in errors)

    def test_standalone_dst_collides_with_src_rejected(self, temp_dir):
        cfg = _full_config(temp_dir)
        ent = dict(_STANDALONE_ENTRY)
        ent["dst"] = "src-host"
        cfg["project_mapping"]["standalone_projects"] = [ent]
        errors = validate_config(cfg)
        assert any("standalone_projects[0]" in e for e in errors)

    def test_standalone_not_a_list_rejected(self, temp_dir):
        cfg = _full_config(temp_dir)
        cfg["project_mapping"]["standalone_projects"] = {"src": "a", "dst": "b"}
        errors = validate_config(cfg)
        assert any("リスト" in e for e in errors)


class TestStandaloneProjects:
    def _orch(self, temp_dir, standalone_only=False):
        cfg = _full_config(temp_dir)
        if standalone_only:
            cfg["project_mapping"] = {
                "standalone_projects": [dict(_STANDALONE_ENTRY)],
            }
        else:
            cfg["project_mapping"]["standalone_projects"] = [dict(_STANDALONE_ENTRY)]
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        return o

    def test_iterators_include_standalone(self, temp_dir):
        o = self._orch(temp_dir)
        assert [s for s, _ in o._iter_src_projects()] == [
            "src-host", "src-svc-1", "src-alone-1"]
        assert [p[0] for p in o._iter_project_pairs()] == [
            "src-host", "src-svc-1", "src-alone-1"]

    def test_proj_id_map_includes_standalone(self, temp_dir):
        o = self._orch(temp_dir)
        assert o._build_proj_id_map() == {
            "src-host": "dst-host",
            "src-svc-1": "dst-svc-1",
            "src-alone-1": "dst-alone-1",
        }

    def test_standalone_only_iterators(self, temp_dir):
        o = self._orch(temp_dir, standalone_only=True)
        assert [p[0] for p in o._iter_project_pairs()] == ["src-alone-1"]
        assert o._build_proj_id_map() == {"src-alone-1": "dst-alone-1"}

    def test_network_firewall_syncs_standalone(self, temp_dir):
        """Step 4.5 は host に加えて standalone の FW も src→dst で同期する。"""
        from unittest.mock import patch
        o = self._orch(temp_dir)
        with patch.object(o, "_replicate_project_networks") as rep, \
             patch.object(o, "_replicate_host_networks") as rep_host, \
             patch.object(o, "_sync_classic_firewall_rules") as classic, \
             patch.object(o, "_sync_network_firewall_policies") as pol:
            o.step_network_firewall()
        rep_host.assert_called_once()
        rep.assert_called_once_with("src-alone-1", "dst-alone-1", "", "")
        assert ("src-host", "dst-host") in [
            (c.args[0], c.args[1]) for c in classic.call_args_list]
        assert ("src-alone-1", "dst-alone-1") in [
            (c.args[0], c.args[1]) for c in classic.call_args_list]
        assert ("src-alone-1", "dst-alone-1") in [
            (c.args[0], c.args[1]) for c in pol.call_args_list]

    def test_network_firewall_standalone_only_skips_host(self, temp_dir):
        """host 未定義でも standalone があれば Step 4.5 は WARNING 終了しない。"""
        from unittest.mock import patch
        o = self._orch(temp_dir, standalone_only=True)
        with patch.object(o, "_replicate_project_networks") as rep, \
             patch.object(o, "_replicate_host_networks") as rep_host, \
             patch.object(o, "_sync_classic_firewall_rules") as classic, \
             patch.object(o, "_sync_network_firewall_policies") as pol:
            o.step_network_firewall()
        rep_host.assert_not_called()
        rep.assert_called_once_with("src-alone-1", "dst-alone-1", "", "")
        classic.assert_called_once()
        pol.assert_called_once()

    def test_replicate_standalone_networks(self, temp_dir):
        from unittest.mock import patch
        o = self._orch(temp_dir)
        with patch.object(o, "_replicate_project_networks") as rep:
            o._replicate_standalone_networks()
        rep.assert_called_once_with("src-alone-1", "dst-alone-1", "", "")

    def test_host_skip_and_standalone_coexist(self, temp_dir):
        """host_project.skip=true でも standalone は処理対象に残る。"""
        cfg = _full_config(temp_dir)
        cfg["project_mapping"]["host_project"]["skip"] = True
        cfg["project_mapping"]["standalone_projects"] = [dict(_STANDALONE_ENTRY)]
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        assert [p[0] for p in o._iter_project_pairs()] == [
            "src-svc-1", "src-alone-1"]

    def test_resolve_clean_targets_includes_standalone(self, temp_dir):
        cfg = _full_config(temp_dir)
        cfg["project_mapping"]["standalone_projects"] = [dict(_STANDALONE_ENTRY)]
        tf_base = os.path.join(temp_dir, "tf")
        for sub in ("active", "raw"):
            os.makedirs(os.path.join(tf_base, sub, "src-alone-1"), exist_ok=True)
        targets, unresolved = resolve_clean_targets(cfg, ["dst-alone-1"], tf_base)
        assert unresolved == []
        assert os.path.join(tf_base, "active", "src-alone-1") in targets


# ============================================================
# VM user-managed SA の dst remap（cross-project attach 回避）
# ============================================================
class TestVmServiceAccountRemap:
    def _orch(self, temp_dir):
        cfg = _full_config(temp_dir)
        cfg["project_mapping"]["standalone_projects"] = [dict(_STANDALONE_ENTRY)]
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        return o

    def test_mapped_sa_reused_when_exists(self, temp_dir):
        from unittest.mock import patch
        o = self._orch(temp_dir)
        pm = o._build_proj_id_map()
        with patch.object(o, "_gcloud_exists", return_value=True), \
             patch.object(o, "run_command") as rc:
            got = o._resolve_dst_vm_service_account(
                "editor@src-alone-1.iam.gserviceaccount.com", pm, None)
        assert got == "editor@dst-alone-1.iam.gserviceaccount.com"
        rc.assert_not_called()

    def test_mapped_sa_created_when_missing(self, temp_dir):
        from unittest.mock import patch
        o = self._orch(temp_dir)
        pm = o._build_proj_id_map()
        with patch.object(o, "_gcloud_exists", return_value=False), \
             patch.object(o, "run_command", return_value="") as rc:
            got = o._resolve_dst_vm_service_account(
                "editor@src-alone-1.iam.gserviceaccount.com", pm, None)
        assert got == "editor@dst-alone-1.iam.gserviceaccount.com"
        cmd = rc.call_args.args[0]
        assert "gcloud iam service-accounts create editor" in cmd
        assert "--project=dst-alone-1" in cmd

    def test_unmapped_project_falls_back_to_default(self, temp_dir):
        from unittest.mock import patch
        o = self._orch(temp_dir)
        pm = o._build_proj_id_map()
        with patch.object(o, "run_command") as rc:
            got = o._resolve_dst_vm_service_account(
                "editor@unrelated-proj.iam.gserviceaccount.com", pm, None)
        assert got is None
        rc.assert_not_called()

    def test_result_cached_across_calls(self, temp_dir):
        from unittest.mock import patch
        o = self._orch(temp_dir)
        pm = o._build_proj_id_map()
        with patch.object(o, "_gcloud_exists", return_value=True) as ge, \
             patch.object(o, "run_command"):
            a = o._resolve_dst_vm_service_account(
                "editor@src-alone-1.iam.gserviceaccount.com", pm, None)
            b = o._resolve_dst_vm_service_account(
                "editor@src-alone-1.iam.gserviceaccount.com", pm, None)
        assert a == b
        assert ge.call_count == 1

    def test_extra_args_remaps_user_sa(self, temp_dir):
        from unittest.mock import patch
        o = self._orch(temp_dir)
        pm = o._build_proj_id_map()
        vm = {
            "serviceAccounts": [{
                "email": "editor@src-alone-1.iam.gserviceaccount.com",
                "scopes": ["https://www.googleapis.com/auth/cloud-platform"],
            }],
        }
        with patch.object(o, "_gcloud_exists", return_value=True):
            extra = o._build_vm_create_extra_args(vm, temp_dir, pm, None)
        assert "--service-account=editor@dst-alone-1.iam.gserviceaccount.com" in extra
        assert "src-alone-1.iam.gserviceaccount.com" not in extra

    def test_extra_args_drops_default_compute_sa(self, temp_dir):
        o = self._orch(temp_dir)
        pm = o._build_proj_id_map()
        vm = {"serviceAccounts": [{
            "email": "123456789-compute@developer.gserviceaccount.com",
            "scopes": ["x"]}]}
        extra = o._build_vm_create_extra_args(vm, temp_dir, pm, None)
        assert "--service-account" not in extra

    def test_extra_args_drops_unmapped_sa_email(self, temp_dir):
        from unittest.mock import patch
        o = self._orch(temp_dir)
        pm = o._build_proj_id_map()
        vm = {"serviceAccounts": [{
            "email": "editor@unrelated.iam.gserviceaccount.com", "scopes": ["x"]}]}
        with patch.object(o, "run_command"):
            extra = o._build_vm_create_extra_args(vm, temp_dir, pm, None)
        assert "--service-account" not in extra


# ============================================================
# data_sync GCS: ドット入り（ドメイン形式）バケットの扱い
# ============================================================
class TestSyncGcsDotBuckets:
    def _orch(self, temp_dir, overrides=None):
        cfg = _full_config(temp_dir)
        cfg["rename_rules"]["gcs"]["overrides"] = overrides or {}
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        return o

    def _run_sync(self, o, buckets, overrides):
        """_sync_gcs を run_command/_gcloud_exists を patch して実行し、
        発行された dst 側コマンド一覧を返す。"""
        from unittest.mock import patch
        issued = []

        def fake_run(cmd, **kw):
            if cmd.startswith("gcloud storage buckets list"):
                return json.dumps(buckets)
            issued.append(cmd)
            return ""

        with patch.object(o, "run_command", side_effect=fake_run), \
             patch.object(o, "_gcloud_exists", return_value=False):
            o._sync_gcs("src-svc-1", "dst-svc-1", None, None,
                        "suffix", "-dst-x", overrides)
        return issued

    def test_dot_bucket_skipped_with_warning(self, temp_dir):
        """appspot.com 等のドメイン形式バケットは create/rsync とも実行しない。"""
        o = self._orch(temp_dir)
        issued = self._run_sync(
            o, [{"name": "us.artifacts.src-svc-1.appspot.com", "location": "US"}], {})
        assert issued == []

    def test_dot_bucket_with_override_synced(self, temp_dir):
        """overrides に src 実名で dst 名を指定すればドット入りでも同期する。"""
        o = self._orch(temp_dir)
        ov = {"us.artifacts.src-svc-1.appspot.com": "artifacts-dst-svc-1"}
        issued = self._run_sync(
            o, [{"name": "us.artifacts.src-svc-1.appspot.com", "location": "US"}], ov)
        assert any("buckets create gs://artifacts-dst-svc-1" in c for c in issued)
        assert any(
            "rsync gs://us.artifacts.src-svc-1.appspot.com gs://artifacts-dst-svc-1" in c
            for c in issued)

    def test_override_by_projmapped_name_still_works(self, temp_dir):
        """従来互換: proj_map 置換後の名前をキーにした overrides も引き続き有効。"""
        o = self._orch(temp_dir)
        ov = {"us.artifacts.dst-svc-1.appspot.com": "artifacts-dst-svc-1"}
        issued = self._run_sync(
            o, [{"name": "us.artifacts.src-svc-1.appspot.com", "location": "US"}], ov)
        assert any("buckets create gs://artifacts-dst-svc-1" in c for c in issued)

    def test_normal_bucket_renamed_and_synced(self, temp_dir):
        o = self._orch(temp_dir)
        issued = self._run_sync(
            o, [{"name": "my-data-bucket", "location": "US"}], {})
        assert any("buckets create gs://my-data-bucket-dst-x-" in c for c in issued)
        assert any("rsync gs://my-data-bucket gs://my-data-bucket-dst-x-" in c
                   for c in issued)


# ============================================================
# Step 5.7: IAM ロール複製 (src SA → dst SA)
# ============================================================
_PM = {"src-host": "dst-host", "src-svc-1": "dst-svc-1"}


class TestStepEnabledDefaults:
    def test_missing_key_uses_per_step_default(self):
        assert step_enabled({}, "iam_sync") is True
        assert step_enabled({}, "network_firewall") is True
        assert step_enabled({}, "data_sync") is False

    def test_explicit_value_wins(self):
        assert step_enabled({"iam_sync": {"enabled": False}}, "iam_sync") is False
        assert step_enabled({"data_sync": {"enabled": True}}, "data_sync") is True

    def test_non_dict_falls_back_to_default(self):
        assert step_enabled({"iam_sync": None}, "iam_sync") is True


class TestParseUserManagedSa:
    def test_user_managed_sa(self):
        assert parse_user_managed_sa(
            "editor@my-argolis.iam.gserviceaccount.com") == ("editor", "my-argolis")

    @pytest.mark.parametrize("email", [
        "123456789-compute@developer.gserviceaccount.com",
        "my-proj@appspot.gserviceaccount.com",
        "service-123@gcp-sa-pubsub.iam.gserviceaccount.com",
        "alice@example.com",
        "",
        None,
    ])
    def test_non_user_managed_rejected(self, email):
        assert parse_user_managed_sa(email) is None

    def test_remap_uses_proj_map(self):
        assert remap_sa_email("editor@src-svc-1.iam.gserviceaccount.com", _PM) == \
            "editor@dst-svc-1.iam.gserviceaccount.com"

    def test_remap_unmapped_project_is_none(self):
        assert remap_sa_email("editor@other.iam.gserviceaccount.com", _PM) is None


class TestRemapIamRole:
    def test_predefined_role_passes_through(self):
        assert remap_iam_role("roles/storage.admin", _PM) == ("roles/storage.admin", "")

    def test_owner_is_not_special_cased_here(self):
        assert remap_iam_role("roles/owner", _PM)[0] == "roles/owner"

    def test_project_custom_role_remapped(self):
        got, reason = remap_iam_role("projects/src-svc-1/roles/customViewer", _PM)
        assert got == "projects/dst-svc-1/roles/customViewer"
        assert reason == ""

    def test_unmapped_project_custom_role_skipped(self):
        got, reason = remap_iam_role("projects/other/roles/customViewer", _PM)
        assert got is None
        assert "project_mapping" in reason

    def test_org_custom_role_skipped(self):
        got, reason = remap_iam_role("organizations/1234/roles/orgRole", _PM)
        assert got is None
        assert "ORG" in reason

    def test_garbage_role_skipped(self):
        assert remap_iam_role("nonsense", _PM)[0] is None
        assert remap_iam_role("", _PM)[0] is None


def _policy(*bindings):
    return {"bindings": list(bindings), "etag": "BwX=="}


class TestBuildIamReplicationPlan:
    def test_basic_predefined_role(self):
        pol = {"src-svc-1": _policy({
            "role": "roles/storage.admin",
            "members": ["serviceAccount:editor@src-svc-1.iam.gserviceaccount.com"],
        })}
        grants, warns = build_iam_replication_plan(pol, _PM)
        assert warns == []
        assert grants == [{
            "dst_project": "dst-svc-1",
            "dst_member": "serviceAccount:editor@dst-svc-1.iam.gserviceaccount.com",
            "dst_role": "roles/storage.admin",
            "src_project": "src-svc-1",
            "src_member": "serviceAccount:editor@src-svc-1.iam.gserviceaccount.com",
            "src_role": "roles/storage.admin",
            "high_privilege": False,
        }]

    def test_owner_is_replicated_and_flagged(self):
        pol = {"src-svc-1": _policy({
            "role": "roles/owner",
            "members": ["serviceAccount:editor@src-svc-1.iam.gserviceaccount.com"],
        })}
        grants, _ = build_iam_replication_plan(pol, _PM)
        assert len(grants) == 1
        assert grants[0]["dst_role"] == "roles/owner"
        assert grants[0]["high_privilege"] is True

    def test_cross_project_sa_binding_uses_binding_project(self):
        """src-host のポリシーに src-svc-1 の SA が居たら dst-host に付与する。"""
        pol = {"src-host": _policy({
            "role": "roles/compute.viewer",
            "members": ["serviceAccount:app@src-svc-1.iam.gserviceaccount.com"],
        })}
        grants, _ = build_iam_replication_plan(pol, _PM)
        assert grants[0]["dst_project"] == "dst-host"
        assert grants[0]["dst_member"] == "serviceAccount:app@dst-svc-1.iam.gserviceaccount.com"

    def test_google_managed_members_ignored_without_warning(self):
        pol = {"src-svc-1": _policy({
            "role": "roles/editor",
            "members": [
                "serviceAccount:123456789-compute@developer.gserviceaccount.com",
                "serviceAccount:src-svc-1@appspot.gserviceaccount.com",
                "user:alice@example.com",
                "group:team@example.com",
            ],
        })}
        grants, warns = build_iam_replication_plan(pol, _PM)
        assert grants == []
        assert warns == []

    def test_conditional_binding_skipped_with_warning(self):
        pol = {"src-svc-1": _policy({
            "role": "roles/bigquery.dataViewer",
            "members": ["serviceAccount:editor@src-svc-1.iam.gserviceaccount.com"],
            "condition": {"title": "expire", "expression": "request.time < timestamp('2030-01-01T00:00:00Z')"},
        })}
        grants, warns = build_iam_replication_plan(pol, _PM)
        assert grants == []
        assert any("条件付き" in w and "expire" in w for w in warns)

    def test_org_custom_role_skipped_with_warning(self):
        pol = {"src-svc-1": _policy({
            "role": "organizations/1234/roles/orgRole",
            "members": ["serviceAccount:editor@src-svc-1.iam.gserviceaccount.com"],
        })}
        grants, warns = build_iam_replication_plan(pol, _PM)
        assert grants == []
        assert any("organizations/1234/roles/orgRole" in w for w in warns)

    def test_unmapped_sa_project_skipped_with_warning(self):
        pol = {"src-svc-1": _policy({
            "role": "roles/storage.admin",
            "members": ["serviceAccount:editor@unrelated.iam.gserviceaccount.com"],
        })}
        grants, warns = build_iam_replication_plan(pol, _PM)
        assert grants == []
        assert any("unrelated" in w for w in warns)

    def test_excluded_migration_sa_ignored(self):
        pol = {"src-svc-1": _policy({
            "role": "roles/viewer",
            "members": ["serviceAccount:viewer@src-svc-1.iam.gserviceaccount.com"],
        })}
        grants, warns = build_iam_replication_plan(
            pol, _PM, {"VIEWER@src-svc-1.iam.gserviceaccount.com"})
        assert grants == []
        assert warns == []

    def test_policy_of_unmapped_project_ignored(self):
        pol = {"src-other": _policy({
            "role": "roles/storage.admin",
            "members": ["serviceAccount:editor@src-svc-1.iam.gserviceaccount.com"],
        })}
        grants, _ = build_iam_replication_plan(pol, _PM)
        assert grants == []

    def test_duplicates_collapsed_and_sorted(self):
        binding = {
            "role": "roles/storage.admin",
            "members": ["serviceAccount:editor@src-svc-1.iam.gserviceaccount.com"],
        }
        pol = {"src-svc-1": _policy(binding, dict(binding))}
        grants, _ = build_iam_replication_plan(pol, _PM)
        assert len(grants) == 1

    def test_deterministic_order(self):
        pol = {
            "src-svc-1": _policy(
                {"role": "roles/storage.admin",
                 "members": ["serviceAccount:zz@src-svc-1.iam.gserviceaccount.com"]},
                {"role": "roles/compute.viewer",
                 "members": ["serviceAccount:aa@src-svc-1.iam.gserviceaccount.com"]},
            ),
            "src-host": _policy(
                {"role": "roles/viewer",
                 "members": ["serviceAccount:aa@src-host.iam.gserviceaccount.com"]},
            ),
        }
        got = [(g["dst_project"], g["dst_member"], g["dst_role"])
               for g in build_iam_replication_plan(pol, _PM)[0]]
        assert got == sorted(got)

    def test_malformed_policy_tolerated(self):
        for pol in ({"src-svc-1": {}}, {"src-svc-1": None},
                    {"src-svc-1": {"bindings": None}},
                    {"src-svc-1": {"bindings": ["junk"]}}):
            assert build_iam_replication_plan(pol, _PM) == ([], [])


class TestStepIamSync:
    def _orch(self, temp_dir, **cfg_over):
        cfg = _full_config(temp_dir)
        cfg["steps"]["iam_sync"] = {"enabled": True}
        cfg.update(cfg_over)
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        return o

    def _policies(self, **extra):
        pol = {
            "src-host": _policy(),
            "src-svc-1": _policy({
                "role": "roles/storage.admin",
                "members": ["serviceAccount:editor@src-svc-1.iam.gserviceaccount.com"],
            }),
        }
        pol.update(extra)
        return pol

    def test_grants_are_issued_per_binding(self, temp_dir):
        from unittest.mock import patch
        o = self._orch(temp_dir)
        issued = []
        with patch.object(o, "_fetch_src_iam_policies", return_value=self._policies()), \
             patch.object(o, "_resolve_dst_vm_service_account",
                          return_value="editor@dst-svc-1.iam.gserviceaccount.com"), \
             patch.object(o, "_dst_can_set_iam_policy", return_value=True), \
             patch.object(o, "_dst_existing_bindings", return_value=set()), \
             patch.object(o, "run_command",
                          side_effect=lambda cmd, **kw: issued.append(cmd) or ""):
            o.step_iam_sync()
        assert len(issued) == 1
        assert "add-iam-policy-binding dst-svc-1" in issued[0]
        assert "--member=serviceAccount:editor@dst-svc-1.iam.gserviceaccount.com" in issued[0]
        assert "--role=roles/storage.admin" in issued[0]
        assert "--condition=None" in issued[0]

    def test_existing_binding_skipped(self, temp_dir):
        from unittest.mock import patch
        o = self._orch(temp_dir)
        existing = {("serviceAccount:editor@dst-svc-1.iam.gserviceaccount.com",
                     "roles/storage.admin")}
        with patch.object(o, "_fetch_src_iam_policies", return_value=self._policies()), \
             patch.object(o, "_resolve_dst_vm_service_account",
                          return_value="editor@dst-svc-1.iam.gserviceaccount.com"), \
             patch.object(o, "_dst_can_set_iam_policy", return_value=True), \
             patch.object(o, "_dst_existing_bindings", return_value=existing), \
             patch.object(o, "run_command") as rc:
            o.step_iam_sync()
        rc.assert_not_called()

    def test_missing_set_iam_policy_skips_without_failure(self, temp_dir):
        """setIamPolicy が無い場合はエラーにせず手動コマンド案内でスキップする。"""
        from unittest.mock import patch
        o = self._orch(temp_dir)
        with patch.object(o, "_fetch_src_iam_policies", return_value=self._policies()), \
             patch.object(o, "_resolve_dst_vm_service_account",
                          return_value="editor@dst-svc-1.iam.gserviceaccount.com"), \
             patch.object(o, "_dst_can_set_iam_policy", return_value=False), \
             patch.object(o, "run_command") as rc:
            o.step_iam_sync()
        rc.assert_not_called()
        assert o.stats.failed == 0

    def test_unresolvable_dst_sa_skips_grant(self, temp_dir):
        from unittest.mock import patch
        o = self._orch(temp_dir)
        with patch.object(o, "_fetch_src_iam_policies", return_value=self._policies()), \
             patch.object(o, "_resolve_dst_vm_service_account", return_value=None), \
             patch.object(o, "run_command") as rc:
            o.step_iam_sync()
        rc.assert_not_called()
        assert o.stats.failed == 0

    def test_owner_grant_emits_final_warning(self, temp_dir):
        from unittest.mock import patch
        o = self._orch(temp_dir)
        pol = self._policies(**{"src-svc-1": _policy({
            "role": "roles/owner",
            "members": ["serviceAccount:editor@src-svc-1.iam.gserviceaccount.com"],
        })})
        warned = []
        with patch.object(o, "_fetch_src_iam_policies", return_value=pol), \
             patch.object(o, "_resolve_dst_vm_service_account",
                          return_value="editor@dst-svc-1.iam.gserviceaccount.com"), \
             patch.object(o, "_dst_can_set_iam_policy", return_value=True), \
             patch.object(o, "_dst_existing_bindings", return_value=set()), \
             patch.object(o, "run_command", return_value=""), \
             patch.object(o.dst_logger, "warning", side_effect=warned.append):
            o.step_iam_sync()
        joined = "\n".join(warned)
        assert "超高権限ロール" in joined
        assert "roles/owner" in joined
        assert "remove-iam-policy-binding" in joined

    def test_no_owner_no_warning(self, temp_dir):
        from unittest.mock import patch
        o = self._orch(temp_dir)
        warned = []
        with patch.object(o, "_fetch_src_iam_policies", return_value=self._policies()), \
             patch.object(o, "_resolve_dst_vm_service_account",
                          return_value="editor@dst-svc-1.iam.gserviceaccount.com"), \
             patch.object(o, "_dst_can_set_iam_policy", return_value=True), \
             patch.object(o, "_dst_existing_bindings", return_value=set()), \
             patch.object(o, "run_command", return_value=""), \
             patch.object(o.dst_logger, "warning", side_effect=warned.append):
            o.step_iam_sync()
        assert "超高権限ロール" not in "\n".join(warned)

    def test_src_policy_read_is_read_only(self, temp_dir):
        """src へ発行するコマンドが ORG 保護（read-only）を通ること。"""
        from unittest.mock import patch
        o = self._orch(temp_dir)
        issued = []
        with patch.object(o, "run_command",
                          side_effect=lambda cmd, **kw: issued.append(cmd) or "{}"):
            o._fetch_src_iam_policies()
        assert issued
        for cmd in issued:
            assert cmd.startswith("gcloud projects get-iam-policy ")
            assert is_src_read_only(cmd)
            assert is_known_mock_command(cmd)

    def test_grant_command_is_mock_known(self):
        cmd = MigrationOrchestrator._iam_grant_command({
            "dst_project": "dst-svc-1",
            "dst_member": "serviceAccount:editor@dst-svc-1.iam.gserviceaccount.com",
            "dst_role": "roles/storage.admin",
        })
        assert is_known_mock_command(cmd)
        assert not is_src_read_only(cmd)  # add は書き込み動詞 = src では必ず拒否される
