import pytest
import os
import re
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
    is_gke_node_vm,
    is_gke_managed_name,
    is_gke_managed_fw_rule,
    has_k8s_owner_marker,
    is_k8s_lb_resource_name,
    import_error_kind,
    coerce_nonneg_int,
    is_api_disabled_error,
    parse_run_services_list,
    run_service_public_invoker_members,
    tf_type_kept,
    resource_type_filter_reason,
    cai_in_use_internal_addresses,
    parse_krm_kinds,
    _first_meaningful_line,
    _CAI_TO_TF_RESOURCE,
    _BASE_DST_APIS,
    api_from_asset_type,
    cai_api_hints,
    build_api_enable_plan,
    tf_type_to_api,
    tf_blocks_of_type,
    ensure_tf_resource_arg,
    parse_ar_repositories,
    build_ar_image_copy_plan,
    filter_ar_plan_by_scope,
    tf_referenced_image_digests,
    dedupe_tf_resource_labels,
    strip_hcl_blocks,
    customize_note_row,
    load_customize_notes,
    ensure_hcl_block_arg,
    _GKE_REMOVED_TF_BLOCKS,
    tf_required_apis,
    tf_dir_has_mock_artifacts,
    _MOCK_TF_MARK,
    _DST_API_SKIP,
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
        # gcloud を要求するステップ（enable_apis は既定 true）も切って、
        # bulk-export 無効なら config-connector 未インストールでも通ることを見る
        o = self._setup(
            temp_dir,
            bulk_export={"enabled": False},
            enable_apis={"enabled": False},
        )
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

    def test_missing_gcrane_exits_when_ar_copy_enabled(self, temp_dir, monkeypatch):
        # docker があっても代替にならない（digest が変わるため）
        o = self._setup(temp_dir, data_sync={"enabled": True})
        monkeypatch.setattr(
            "scripts.sync_env.shutil.which",
            lambda name: None if name in ("gcrane", "crane") else f"/usr/bin/{name}",
        )
        with pytest.raises(SystemExit) as ei:
            o.check_prerequisites()
        assert ei.value.code == 1

    def test_crane_alone_satisfies_requirement(self, temp_dir, monkeypatch):
        o = self._setup(temp_dir, data_sync={"enabled": True})
        monkeypatch.setattr(
            "scripts.sync_env.shutil.which",
            lambda name: None if name == "gcrane" else f"/usr/bin/{name}",
        )
        o.check_prerequisites()  # crane があれば通る

    def test_gcrane_not_required_when_ar_copy_disabled(self, temp_dir, monkeypatch):
        o = self._setup(
            temp_dir,
            data_sync={"enabled": True, "artifact_registry": {"enabled": False}},
        )
        monkeypatch.setattr(
            "scripts.sync_env.shutil.which",
            lambda name: None if name in ("gcrane", "crane") else f"/usr/bin/{name}",
        )
        o.check_prerequisites()  # イメージ複製を切っていれば不要

    def test_gcrane_not_required_when_data_sync_disabled(self, temp_dir, monkeypatch):
        o = self._setup(temp_dir, cai_scan={"enabled": True})
        monkeypatch.setattr(
            "scripts.sync_env.shutil.which",
            lambda name: None if name in ("gcrane", "crane") else f"/usr/bin/{name}",
        )
        o.check_prerequisites()


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

    def test_gke_managed_resources_dropped_cluster_kept(self, temp_dir):
        """GKE 派生リソースは active に出さず、クラスタ構成は残す。"""
        o = self._setup(temp_dir)
        raw = os.path.join(temp_dir, "raw")
        active = os.path.join(temp_dir, "active")
        os.makedirs(raw)
        files = {
            "gke_template.tf": '''
resource "google_compute_instance_template" "t" {
  name    = "gke-my-cluster-default-pool-1234abcd"
  project = "src-svc-1"
}
''',
            "user_template.tf": '''
resource "google_compute_instance_template" "t" {
  name    = "my-app-template"
  project = "src-svc-1"
}
''',
            "gke_fw.tf": '''
resource "google_compute_firewall" "f" {
  name    = "k8s-fw-a1b2c3d4e5"
  project = "src-svc-1"
}
''',
            "user_fw.tf": '''
resource "google_compute_firewall" "f" {
  name    = "allow-ssh"
  project = "src-svc-1"
}
''',
            "cluster.tf": '''
resource "google_container_cluster" "c" {
  name     = "my-cluster"
  project  = "src-svc-1"
  location = "asia-northeast1-a"
}
''',
            "nodepool.tf": '''
resource "google_container_node_pool" "np" {
  name    = "default-pool"
  cluster = "my-cluster"
  project = "src-svc-1"
}
''',
        }
        for fn, body in files.items():
            with open(os.path.join(raw, fn), "w", encoding="utf-8") as f:
                f.write(body)
        o.customize_hcl(raw, active)

        # GKE / k8s 自動生成 → dst クラスタが再生成するのでスキップ
        assert not os.path.exists(os.path.join(active, "gke_template.tf"))
        assert not os.path.exists(os.path.join(active, "gke_fw.tf"))
        # ユーザー作成の同型リソースは残す
        assert os.path.exists(os.path.join(active, "user_template.tf"))
        assert os.path.exists(os.path.join(active, "user_fw.tf"))
        # GKE 構成そのもの（クラスタ / ノードプール）は複製対象
        assert os.path.exists(os.path.join(active, "cluster.tf"))
        assert os.path.exists(os.path.join(active, "nodepool.tf"))
        with open(os.path.join(active, "cluster.tf"), encoding="utf-8") as f:
            assert 'project  = "dst-svc-1"' in f.read()

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
            # GKE（構成のみ terraform で複製）とその派生 compute
            "container.googleapis.com/Cluster",
            "container.googleapis.com/NodePool",
            "compute.googleapis.com/InstanceTemplate",
            "compute.googleapis.com/InstanceGroupManager",
        ]
        for t in must_have:
            assert t in _ASSET_COVERAGE, f"{t} が _ASSET_COVERAGE に未登録"

    def test_gke_cluster_is_terraform_owned(self):
        # GKE はクラスタ構成のみ terraform (Step 3/4) で複製する
        assert _ASSET_COVERAGE["container.googleapis.com/Cluster"] == "terraform_apply"
        assert _ASSET_COVERAGE["container.googleapis.com/NodePool"] == "terraform_apply"

    def test_diff_coverage_ignores_k8s_objects(self):
        # クラスタ内 k8s オブジェクトは種類が無数にあり複製対象外。警告に出さない
        observed = [
            "k8s.io/Pod",
            "apps.k8s.io/Deployment",
            "rbac.authorization.k8s.io/ClusterRole",
            "fake.googleapis.com/UnknownThing",
        ]
        uncovered, _ = diff_coverage(observed)
        assert uncovered == ["fake.googleapis.com/UnknownThing"]

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

    def test_scans_bulk_export_nested_tree(self, temp_dir):
        """bulk-export の raw は深いツリー。フラット走査だと 0 件になる。"""
        d = os.path.join(temp_dir, "raw", "my-argolis")
        cluster_dir = os.path.join(
            d, "projects", "my-argolis", "ContainerCluster", "asia-northeast1"
        )
        pool_dir = os.path.join(
            cluster_dir, "my-ec-cluster", "ContainerNodePool", "asia-northeast1"
        )
        os.makedirs(pool_dir)
        _write(
            os.path.join(cluster_dir, "my-ec-cluster.tf"),
            'resource "google_container_cluster" "my_ec_cluster" {\n'
            '  name     = "my-ec-cluster"\n'
            '  location = "asia-northeast1"\n'
            '}\n',
        )
        _write(
            os.path.join(pool_dir, "default-pool.tf"),
            'resource "google_container_node_pool" "default_pool" {\n'
            '  name = "default-pool"\n'
            '}\n',
        )
        out = parse_tf_resources(d)
        assert out["google_container_cluster"] == ["my-ec-cluster"]
        assert out["google_container_node_pool"] == ["default-pool"]

    def test_ignores_terraform_cache_dir(self, temp_dir):
        """active/<src>/.terraform は provider / module キャッシュ。拾わない。"""
        d = os.path.join(temp_dir, "active", "my-argolis")
        cache = os.path.join(d, ".terraform", "modules", "vpc")
        os.makedirs(cache)
        _write(os.path.join(d, "google_storage_bucket.tf"), _TF_SAMPLE_BUCKET)
        _write(
            os.path.join(cache, "main.tf"),
            'resource "google_compute_network" "cached" {\n'
            '  name = "from-module-cache"\n'
            '}\n',
        )
        out = parse_tf_resources(d)
        assert "google_storage_bucket" in out
        assert "google_compute_network" not in out


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

    def test_k8s_objects_are_not_reported(self, temp_dir):
        # クラスタ内 k8s オブジェクトは差分に出さず auto_handled に数えるだけ
        cai_path = os.path.join(temp_dir, "cai_k8s.txt")
        _write(cai_path, (
            "assetType: k8s.io/Pod\n"
            "name: //container.googleapis.com/projects/p/zones/z/clusters/c/k8s/namespaces/default/pods/nginx\n"
            "---\n"
            "assetType: rbac.authorization.k8s.io/ClusterRole\n"
            "name: //container.googleapis.com/projects/p/zones/z/clusters/c/k8s/rbac/clusterroles/admin\n"
        ))
        tf_dir = os.path.join(temp_dir, "tf_empty")
        os.makedirs(tf_dir)
        report = analyze_cai_tf_diff(
            cai_path, [tf_dir],
            src_project="src-host", dst_project="dst-host",
        )
        assert report["missing"] == []
        assert report["auto_handled"] == 2
        assert report["unknown_types"] == []

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

    def test_gke_derived_instance_template_is_reference(self):
        c = classify_missing_asset(self._item(
            "compute.googleapis.com/InstanceTemplate",
            "gke-my-cluster-default-pool-1234abcd"))
        assert c["level"] == "reference"
        assert c["priority"] == 3
        assert "再生成" in c["why"]

    def test_gke_derived_mig_and_neg_are_reference(self):
        for atype, short in (
            ("compute.googleapis.com/InstanceGroupManager", "gke-my-cluster-default-pool-1234abcd-grp"),
            ("compute.googleapis.com/InstanceGroup", "gke-my-cluster-default-pool-1234abcd-grp"),
            ("compute.googleapis.com/NetworkEndpointGroup", "k8s1-abcdef-default-svc-80"),
        ):
            c = classify_missing_asset(self._item(atype, short))
            assert c["level"] == "reference", f"{atype} {short}"
            assert c["priority"] == 3

    def test_user_instance_template_is_action(self):
        # GKE 命名でないテンプレートは従来どおり要対応（判定できないものは action）
        c = classify_missing_asset(self._item(
            "compute.googleapis.com/InstanceTemplate", "my-app-template"))
        assert c["level"] == "action"

    def test_gke_cluster_itself_is_action(self):
        # クラスタ本体が terraform に出ていない = 複製漏れ。参考に落とさない
        c = classify_missing_asset(self._item(
            "container.googleapis.com/Cluster", "my-cluster"))
        assert c["level"] == "action"


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


# ============================================================
# GKE: 構成のみ複製 / ノード VM をコピー対象外にする
# ============================================================
class TestIsGkeNodeVm:
    def test_label_only_is_node(self):
        # GKE は全ノードに goog-gke-node ラベルを付ける（値は空文字）
        assert is_gke_node_vm({"name": "gke-c1-default-pool-abc-x1",
                               "labels": {"goog-gke-node": ""}})

    def test_autopilot_node_by_label(self):
        assert is_gke_node_vm({"name": "gk3-c1-nap-abc-x1",
                               "labels": {"goog-gke-node": ""}})

    def test_name_plus_kube_metadata_is_node(self):
        # labels が取れない場合の保険（名前 + ノード固有 metadata）
        assert is_gke_node_vm({
            "name": "gke-c1-default-pool-abc-x1",
            "metadata": {"items": [{"key": "kube-env", "value": "..."}]},
        })

    def test_name_only_is_not_node(self):
        # 名前だけで除外すると gke- 始まりのユーザー VM を取りこぼす
        assert not is_gke_node_vm({"name": "gke-like-user-vm"})

    def test_plain_vm_is_not_node(self):
        assert not is_gke_node_vm({"name": "web-01", "labels": {"env": "prod"}})

    def test_missing_keys_are_safe(self):
        assert not is_gke_node_vm({})
        assert not is_gke_node_vm({"name": "gke-x", "labels": None, "metadata": None})

    def test_unrelated_metadata_is_not_node(self):
        assert not is_gke_node_vm({
            "name": "gke-like-user-vm",
            "metadata": {"items": [{"key": "startup-script", "value": "x"}]},
        })


class TestIsGkeManagedName:
    def test_managed_prefixes(self):
        for n in ("gke-c1-default-pool-abc-grp", "gk3-c1-nap-abc",
                  "k8s-fw-a1b2c3", "k8s2-abcdef-default-svc",
                  "k8s1-abcdef-default-svc-80", "gkegw1-l7-default"):
            assert is_gke_managed_name(n), n

    def test_user_names(self):
        for n in ("allow-ssh", "my-template", "mygke-app", "web-01"):
            assert not is_gke_managed_name(n), n

    def test_empty_is_false(self):
        assert not is_gke_managed_name(None)
        assert not is_gke_managed_name("")


class TestGkeNodeExcludedFromCopy:
    """Step 2 / Step 5 が GKE ノード VM を対象外にすること。"""

    _GKE_VM = {
        "name": "gke-c1-default-pool-1234abcd-xyz1",
        "zone": "projects/src-svc-1/zones/asia-northeast1-a",
        "status": "RUNNING",
        "labels": {"goog-gke-node": ""},
        "disks": [{"boot": True,
                   "source": "projects/src-svc-1/zones/asia-northeast1-a/disks/gke-c1-default-pool-1234abcd-xyz1"}],
    }
    _USER_VM = {
        "name": "web-01",
        "zone": "projects/src-svc-1/zones/asia-northeast1-a",
        "status": "TERMINATED",
        "disks": [{"boot": True,
                   "source": "projects/src-svc-1/zones/asia-northeast1-a/disks/web-01"}],
    }

    def _orch(self, temp_dir):
        cfg = _full_config(temp_dir)
        cfg["global"]["dry_run"] = False
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        o.dst_logger = logging.getLogger("test-dst")
        o.org_logger = logging.getLogger("test-org")
        return o

    def _lister(self, vms):
        """instances list / snapshots list に応答する run_command の代役。

        スナップショットは常に空 = 「有効スナップショットが無い」状態。
        """
        def _run(cmd, **kwargs):
            if cmd.startswith("gcloud compute instances list"):
                return json.dumps(vms)
            if cmd.startswith("gcloud compute snapshots list"):
                return "[]"
            return ""
        return _run

    def test_step2_gke_node_does_not_fail_run(self, temp_dir):
        from unittest.mock import patch
        o = self._orch(temp_dir)
        # GKE ノードのみ: スナップショットが無くても検証は通る
        with patch.object(o, "run_command", side_effect=self._lister([self._GKE_VM])):
            o.step_gce_snapshot()  # SystemExit が出ないこと

    def test_step2_normal_vm_without_snapshot_still_fails(self, temp_dir):
        from unittest.mock import patch
        o = self._orch(temp_dir)
        with patch.object(o, "run_command",
                          side_effect=self._lister([self._GKE_VM, self._USER_VM])):
            with pytest.raises(SystemExit):
                o.step_gce_snapshot()

    def test_step5_gke_node_not_restored(self, temp_dir):
        from unittest.mock import patch
        o = self._orch(temp_dir)
        restored, pending = [], []
        with patch.object(o, "run_command",
                          side_effect=self._lister([self._GKE_VM, self._USER_VM])), \
             patch.object(o, "_replicate_host_networks"), \
             patch.object(o, "_replicate_standalone_networks"), \
             patch.object(o, "_restore_one_vm",
                          side_effect=lambda vm, *a, **kw: restored.append(vm.get("name"))), \
             patch.object(o, "_finalize_vm_power_states",
                          side_effect=lambda p: pending.extend(p)):
            o.step_gce_restore()
        # 復元対象にも電源状態調整の対象にも GKE ノードは入らない
        assert "gke-c1-default-pool-1234abcd-xyz1" not in restored
        assert restored  # ユーザー VM は復元される
        assert all(name != "gke-c1-default-pool-1234abcd-xyz1" for name, *_ in pending)
        assert any(name == "web-01" for name, *_ in pending)


class TestGkeFirewallRulesSkipped:
    """Step 4.5: GKE/k8s 自動生成の classic FW ルールを dst に作らない。"""

    def _orch(self, temp_dir):
        cfg = _full_config(temp_dir)
        cfg["global"]["dry_run"] = False
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        o.dst_logger = logging.getLogger("test-dst")
        o.org_logger = logging.getLogger("test-org")
        return o

    def test_gke_rules_are_not_created(self, temp_dir):
        from unittest.mock import patch
        o = self._orch(temp_dir)
        rules = [
            {"name": "gke-c1-1234abcd-vms", "network": "n/shared-vpc",
             "direction": "INGRESS", "allowed": [{"IPProtocol": "tcp"}]},
            {"name": "k8s-fw-a1b2c3", "network": "n/shared-vpc",
             "direction": "INGRESS", "allowed": [{"IPProtocol": "tcp"}]},
            {"name": "allow-ssh", "network": "n/shared-vpc",
             "direction": "INGRESS", "allowed": [{"IPProtocol": "tcp", "ports": ["22"]}]},
        ]
        issued = []

        def _run(cmd, **kwargs):
            if cmd.startswith("gcloud compute firewall-rules list"):
                return json.dumps(rules)
            issued.append(cmd)
            return ""

        # dst network は存在する / dst FW rule は未存在（= create パスに入る）
        def _exists(cmd, sa=None):
            return cmd.startswith("gcloud compute networks describe")

        with patch.object(o, "run_command", side_effect=_run), \
             patch.object(o, "_gcloud_exists", side_effect=_exists):
            o._sync_classic_firewall_rules("src-host", "dst-host", None, None)

        created = [c for c in issued if c.startswith("gcloud compute firewall-rules create")]
        assert len(created) == 1
        assert "allow-ssh" in created[0]
        assert not any("gke-c1-1234abcd-vms" in c or "k8s-fw-a1b2c3" in c for c in created)


# ============================================================
# Step 1.5: dst API 事前有効化
# ============================================================
class TestApiFromAssetType:
    def test_service_part_is_returned(self):
        assert api_from_asset_type(
            "container.googleapis.com/Cluster") == "container.googleapis.com"
        assert api_from_asset_type(
            "compute.googleapis.com/Instance") == "compute.googleapis.com"

    def test_k8s_objects_are_not_apis(self):
        # クラスタ内 k8s オブジェクトは API ではない
        assert api_from_asset_type("k8s.io/Pod") is None
        assert api_from_asset_type("rbac.authorization.k8s.io/ClusterRole") is None

    def test_garbage_is_none(self):
        assert api_from_asset_type("") is None
        assert api_from_asset_type("NotAnAssetType") is None


class TestCaiApiHints:
    def test_extracts_enabled_services_and_asset_types(self, temp_dir):
        path = os.path.join(temp_dir, "cai.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(
                "---\n"
                "assetType: serviceusage.googleapis.com/Service\n"
                "name: //serviceusage.googleapis.com/projects/123/services/container.googleapis.com\n"
                "---\n"
                "assetType: container.googleapis.com/Cluster\n"
                "name: //container.googleapis.com/projects/p/locations/l/clusters/c1\n"
                "---\n"
                "assetType: k8s.io/Pod\n"
                "name: //k8s.io/pods/x\n"
            )
        services, atypes = cai_api_hints(path)
        assert services == {"container.googleapis.com"}
        assert "container.googleapis.com/Cluster" in atypes
        assert "k8s.io/Pod" in atypes

    def test_missing_file_is_empty(self, temp_dir):
        services, atypes = cai_api_hints(os.path.join(temp_dir, "none.txt"))
        assert services == set() and atypes == set()


class TestBuildApiEnablePlan:
    def test_src_enabled_apis_are_replicated(self):
        plan = build_api_enable_plan(
            ["container.googleapis.com", "dns.googleapis.com"], [], {},
        )
        assert "container.googleapis.com" in plan  # GKE が本ケースの主目的
        assert "dns.googleapis.com" in plan

    def test_base_apis_always_included(self):
        plan = build_api_enable_plan([], [], {})
        for api in ("cloudresourcemanager.googleapis.com", "serviceusage.googleapis.com",
                    "iam.googleapis.com", "iamcredentials.googleapis.com"):
            assert api in plan

    def test_asset_types_imply_apis(self):
        # services list が読めなくても CAI の assetType から API を補完できる
        plan = build_api_enable_plan([], ["container.googleapis.com/Cluster", "k8s.io/Pod"], {})
        assert "container.googleapis.com" in plan
        assert not any(p.endswith("k8s.io") for p in plan)

    def test_enabled_steps_add_their_apis(self):
        steps = {
            "data_sync": {"enabled": True},
            "network_firewall": {"enabled": False},   # 既定 true なので明示 off
            "iam_sync": {"enabled": False},
        }
        plan = build_api_enable_plan([], [], steps)
        assert "bigquery.googleapis.com" in plan
        assert "storage.googleapis.com" in plan
        # compute を要求するステップ（gce_restore / network_firewall 等）が無ければ入らない
        assert "compute.googleapis.com" not in plan

    def test_default_enabled_steps_are_respected(self):
        # network_firewall / iam_sync はキーが無くても既定 true
        plan = build_api_enable_plan([], [], {})
        assert "compute.googleapis.com" in plan

    def test_skip_list_and_config_skip(self):
        plan = build_api_enable_plan(
            ["bigquery-json.googleapis.com", "dns.googleapis.com"], [], {},
            skip_apis=["dns.googleapis.com"],
        )
        assert "bigquery-json.googleapis.com" not in plan   # 既定の除外（旧エイリアス）
        assert "dns.googleapis.com" not in plan             # config の除外

    def test_extra_apis_are_added(self):
        plan = build_api_enable_plan([], [], {}, extra_apis=["notebooks.googleapis.com"])
        assert "notebooks.googleapis.com" in plan

    def test_invalid_names_are_dropped(self):
        plan = build_api_enable_plan(["", "not-an-api", "UPPER.googleapis.com"], [], {})
        assert "not-an-api" not in plan and "UPPER.googleapis.com" not in plan

    def test_result_is_sorted_and_unique(self):
        plan = build_api_enable_plan(
            ["dns.googleapis.com", "dns.googleapis.com"], ["dns.googleapis.com/ManagedZone"], {},
        )
        assert plan == sorted(set(plan))

    def test_skip_list_holds_only_non_enablable_apis(self):
        # 契約/申請が要るだけの実 API は skip に入れない（黙って消さず WARNING で見せる）
        assert "edgecache.googleapis.com" not in _DST_API_SKIP
        assert "anthos.googleapis.com" not in _DST_API_SKIP


class TestStepEnableApis:
    """Step 1.5 本体: 差分だけ有効化する / 失敗しても run を落とさない。"""

    def _orch(self, temp_dir, steps=None):
        cfg = _full_config(temp_dir, steps=steps or {})
        cfg["global"]["dry_run"] = False
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        o.dst_logger = logging.getLogger("test-dst")
        o.org_logger = logging.getLogger("test-org")
        return o

    def test_only_missing_apis_are_enabled(self, temp_dir):
        from unittest.mock import patch
        o = self._orch(temp_dir, steps={"gce_restore": {"enabled": True}})
        issued = []

        def _soft(cmd, side, logger, impersonate_sa=None, timeout=300, skip_on_dry_run=True):
            if cmd.startswith("gcloud services list"):
                if "--project=src-" in cmd:
                    return 0, "compute.googleapis.com\ncontainer.googleapis.com\n", ""
                return 0, "compute.googleapis.com\n", ""   # dst は compute だけ有効
            issued.append(cmd)
            return 0, "", ""

        with patch.object(o, "_soft_run", side_effect=_soft), \
             patch.object(o, "_wait_for_apis_enabled") as waiter:
            o.step_enable_apis()

        assert issued, "enable コマンドが発行されていない"
        joined = " ".join(issued)
        assert "container.googleapis.com" in joined       # src で有効 / dst で無効 → 追加
        assert all(c.startswith("gcloud services enable ") for c in issued)
        # 既に有効な compute は enable 対象に含めない
        assert not any(" compute.googleapis.com" in c.split("--project")[0] for c in issued)
        assert waiter.called

    def test_batch_failure_falls_back_to_one_by_one(self, temp_dir):
        from unittest.mock import patch
        o = self._orch(temp_dir)
        singles = []

        def _soft(cmd, side, logger, impersonate_sa=None, timeout=300, skip_on_dry_run=True):
            if cmd.startswith("gcloud services list"):
                return 0, "", ""
            apis = cmd.split("enable ", 1)[1].split(" --project")[0].split()
            if len(apis) > 1:
                return 1, "", "ERROR: batch failed"
            singles.append(apis[0])
            return (1, "", "ERROR: not found") if apis[0].startswith("iam.") else (0, "", "")

        with patch.object(o, "_soft_run", side_effect=_soft), \
             patch.object(o, "_wait_for_apis_enabled"):
            o.step_enable_apis()

        assert len(singles) > 1, "個別再試行が行われていない"

    def test_enable_failure_does_not_fail_the_run(self, temp_dir):
        from unittest.mock import patch
        o = self._orch(temp_dir)

        def _soft(cmd, side, logger, impersonate_sa=None, timeout=300, skip_on_dry_run=True):
            if cmd.startswith("gcloud services list"):
                return 0, "", ""
            return 1, "", "ERROR: PERMISSION_DENIED"

        with patch.object(o, "_soft_run", side_effect=_soft), \
             patch.object(o, "_wait_for_apis_enabled"):
            o.step_enable_apis()

        # soft fail: stats.failed に積まない（= make run が exit 1 にならない）
        assert o.stats.failed == 0

    def test_src_list_failure_falls_back_to_step_apis(self, temp_dir):
        from unittest.mock import patch
        o = self._orch(temp_dir, steps={"data_sync": {"enabled": True}})
        issued = []

        def _soft(cmd, side, logger, impersonate_sa=None, timeout=300, skip_on_dry_run=True):
            if cmd.startswith("gcloud services list"):
                return 1, "", "ERROR: PERMISSION_DENIED"   # src も dst も読めない
            issued.append(cmd)
            return 0, "", ""

        with patch.object(o, "_soft_run", side_effect=_soft), \
             patch.object(o, "_wait_for_apis_enabled"):
            o.step_enable_apis()

        joined = " ".join(issued)
        assert "bigquery.googleapis.com" in joined     # 有効ステップの必須 API は必ず有効化
        assert "cloudresourcemanager.googleapis.com" in joined


class TestEnableApisSrcGuard:
    def test_services_list_is_read_only(self):
        assert is_src_read_only(
            "gcloud services list --enabled --project=p --format='value(config.name)'")

    def test_services_enable_is_rejected_on_src(self):
        assert not is_src_read_only("gcloud services enable container.googleapis.com --project=p")

    def test_services_list_is_mock_known(self):
        assert is_known_mock_command(
            "gcloud services list --enabled --project=p --format='value(config.name)'")


# ============================================================
# Step 4 直前の API 有効化（.tf から必要 API を引く）
# ============================================================
class TestTfTypeToApi:
    def test_container_resources_map_to_container_api(self):
        assert tf_type_to_api("google_container_cluster") == "container.googleapis.com"
        assert tf_type_to_api("google_container_node_pool") == "container.googleapis.com"

    def test_longest_prefix_wins(self):
        # "container" より "container_registry" / "container_analysis" が優先される
        assert tf_type_to_api("google_container_registry") == "containerregistry.googleapis.com"
        assert tf_type_to_api("google_container_analysis_note") == "containeranalysis.googleapis.com"
        # "project" より "project_service"
        assert tf_type_to_api("google_project_service") == "serviceusage.googleapis.com"
        assert tf_type_to_api("google_project_iam_member") == "cloudresourcemanager.googleapis.com"

    def test_common_types(self):
        assert tf_type_to_api("google_compute_instance") == "compute.googleapis.com"
        assert tf_type_to_api("google_storage_bucket") == "storage.googleapis.com"
        assert tf_type_to_api("google_bigquery_dataset") == "bigquery.googleapis.com"
        assert tf_type_to_api("google_sql_database_instance") == "sqladmin.googleapis.com"
        assert tf_type_to_api("google_service_account") == "iam.googleapis.com"

    def test_unknown_and_non_google_are_none(self):
        # 未知の型で誤った API を有効化しない（安全側）
        assert tf_type_to_api("google_totally_unknown_thing") is None
        assert tf_type_to_api("aws_instance") is None
        assert tf_type_to_api("") is None


class TestTfRequiredApis:
    def test_resource_and_data_blocks_are_scanned(self, temp_dir):
        with open(os.path.join(temp_dir, "a.tf"), "w", encoding="utf-8") as f:
            f.write('resource "google_container_cluster" "c" {\n  name = "c1"\n}\n')
        with open(os.path.join(temp_dir, "b.tf"), "w", encoding="utf-8") as f:
            f.write('data "google_storage_bucket" "b" {\n  name = "b1"\n}\n')
        assert tf_required_apis(temp_dir) == [
            "container.googleapis.com", "storage.googleapis.com",
        ]

    def test_non_tf_files_and_missing_dir_are_ignored(self, temp_dir):
        with open(os.path.join(temp_dir, "notes.txt"), "w", encoding="utf-8") as f:
            f.write('resource "google_container_cluster" "c" {}\n')
        assert tf_required_apis(temp_dir) == []
        assert tf_required_apis(os.path.join(temp_dir, "nope")) == []


class TestTfDirHasMockArtifacts:
    def test_marked_file_is_detected(self, temp_dir):
        with open(os.path.join(temp_dir, "x.tf"), "w", encoding="utf-8") as f:
            f.write(f"# {_MOCK_TF_MARK}\nresource \"google_storage_bucket\" \"x\" {{}}\n")
        assert tf_dir_has_mock_artifacts(temp_dir)

    def test_legacy_mock_labels_are_detected(self, temp_dir):
        # マーク行が無かった頃に生成された残骸も検出する
        with open(os.path.join(temp_dir, "x.tf"), "w", encoding="utf-8") as f:
            f.write('resource "google_container_cluster" "mock_cluster" {\n}\n')
        assert tf_dir_has_mock_artifacts(temp_dir)

    def test_real_export_is_not_flagged(self, temp_dir):
        with open(os.path.join(temp_dir, "x.tf"), "w", encoding="utf-8") as f:
            f.write('resource "google_container_cluster" "prod" {\n  name = "prod"\n}\n')
        assert not tf_dir_has_mock_artifacts(temp_dir)


class TestTfBaseDirIsolation:
    def _orch(self, temp_dir, mock: bool):
        cfg = _full_config(temp_dir, steps={"bulk_export": {"output_dir": "./terraform"}})
        cfg["global"]["mock"] = mock
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path, mock_override=mock)
        o.load_config()
        return o

    def test_mock_uses_separate_dir(self, temp_dir):
        assert self._orch(temp_dir, mock=True)._tf_base_dir() == os.path.join(
            "./terraform", "mock")

    def test_real_run_uses_configured_dir(self, temp_dir):
        assert self._orch(temp_dir, mock=False)._tf_base_dir() == "./terraform"


class TestTerraformMockArtifactGuard:
    def _orch(self, temp_dir):
        cfg = _full_config(temp_dir)
        cfg["global"]["dry_run"] = False
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        o.dst_logger = logging.getLogger("test-dst")
        o.org_logger = logging.getLogger("test-org")
        return o

    def test_mock_artifacts_are_not_applied(self, temp_dir):
        from unittest.mock import patch
        o = self._orch(temp_dir)
        proj_dir = os.path.join(temp_dir, "active", "src-svc-1")
        os.makedirs(proj_dir)
        with open(os.path.join(proj_dir, "x.tf"), "w", encoding="utf-8") as f:
            f.write(f"# {_MOCK_TF_MARK}\nresource \"google_container_cluster\" \"mock_cluster\" {{}}\n")

        with patch.object(o, "run_command") as rc:
            o._terraform_one_project(proj_dir, {"src-svc-1": "dst-svc-1"}, {})

        assert rc.call_count == 0, "mock 生成物を apply しようとしている"
        assert o.stats.failed == 1

    def test_tf_derived_apis_are_enabled_before_apply(self, temp_dir):
        from unittest.mock import patch
        o = self._orch(temp_dir)
        proj_dir = os.path.join(temp_dir, "active", "src-svc-1")
        os.makedirs(proj_dir)
        with open(os.path.join(proj_dir, "gke.tf"), "w", encoding="utf-8") as f:
            f.write('resource "google_container_cluster" "prod" {\n  name = "prod"\n}\n')
        issued = []

        def _soft(cmd, side, logger, impersonate_sa=None, timeout=300, skip_on_dry_run=True):
            if cmd.startswith("gcloud services list"):
                return 0, "cloudresourcemanager.googleapis.com\n", ""
            issued.append(cmd)
            return 0, "", ""

        with patch.object(o, "_soft_run", side_effect=_soft), \
             patch.object(o, "_wait_for_apis_enabled"), \
             patch.object(o, "_write_provider_tf"), \
             patch.object(o, "_terraform_import_existing"), \
             patch.object(o, "run_command"):
            o._terraform_one_project(proj_dir, {"src-svc-1": "dst-svc-1"}, {})

        joined = " ".join(issued)
        assert "container.googleapis.com" in joined
        assert "--project=dst-svc-1" in joined

    def test_api_enable_failure_does_not_fail_the_run(self, temp_dir):
        from unittest.mock import patch
        o = self._orch(temp_dir)
        proj_dir = os.path.join(temp_dir, "active", "src-svc-1")
        os.makedirs(proj_dir)
        with open(os.path.join(proj_dir, "gke.tf"), "w", encoding="utf-8") as f:
            f.write('resource "google_container_cluster" "prod" {\n  name = "prod"\n}\n')

        def _soft(cmd, side, logger, impersonate_sa=None, timeout=300, skip_on_dry_run=True):
            return 1, "", "ERROR: PERMISSION_DENIED"

        with patch.object(o, "_soft_run", side_effect=_soft), \
             patch.object(o, "_wait_for_apis_enabled"), \
             patch.object(o, "_write_provider_tf"), \
             patch.object(o, "_terraform_import_existing"), \
             patch.object(o, "run_command"):
            o._terraform_one_project(proj_dir, {"src-svc-1": "dst-svc-1"}, {})

        assert o.stats.failed == 0


# ============================================================
# flatten 時の resource ラベル重複解消
# ============================================================
class TestDedupeTfResourceLabels:
    def test_first_occurrence_keeps_label(self):
        seen = set()
        c1, r1 = dedupe_tf_resource_labels(
            'resource "google_artifact_registry_repository" "repo" {\n}\n',
            "asia-northeast1", seen)
        assert r1 == [] and '"repo"' in c1
        assert ("resource", "google_artifact_registry_repository", "repo") in seen

    def test_collision_gets_location_suffix_and_import_comment_follows(self):
        seen = {("resource", "google_artifact_registry_repository", "repo")}
        content = (
            'resource "google_artifact_registry_repository" "repo" {\n'
            '  location = "us-central1"\n'
            '}\n'
            '# terraform import google_artifact_registry_repository.repo projects/p/locations/us-central1/repositories/repo\n'
        )
        out, renames = dedupe_tf_resource_labels(content, "us-central1", seen)
        assert renames == [("google_artifact_registry_repository", "repo", "repo_us_central1")]
        assert 'resource "google_artifact_registry_repository" "repo_us_central1"' in out
        assert "google_artifact_registry_repository.repo_us_central1 projects/p" in out
        assert ("resource", "google_artifact_registry_repository", "repo_us_central1") in seen

    def test_same_discriminator_falls_back_to_counter(self):
        seen = {("resource", "t", "x"), ("resource", "t", "x_loc")}
        out, renames = dedupe_tf_resource_labels('resource "t" "x" {}\n', "loc", seen)
        assert renames == [("t", "x", "x_loc_2")]

    def test_different_type_same_label_is_not_a_collision(self):
        seen = {("resource", "google_storage_bucket", "repo")}
        _out, renames = dedupe_tf_resource_labels(
            'resource "google_artifact_registry_repository" "repo" {}\n', "loc", seen)
        assert renames == []

    def test_empty_discriminator_uses_dup(self):
        seen = {("resource", "t", "x")}
        _out, renames = dedupe_tf_resource_labels('resource "t" "x" {}\n', "", seen)
        assert renames == [("t", "x", "x_dup")]


class TestCustomizeHclDedupesFlattenedLabels:
    """bulk-export の深いツリーを flatten した際の Duplicate resource 回避。"""

    def _setup(self, temp_dir):
        cfg = _full_config(temp_dir)
        cfg["global"]["dry_run"] = False
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        return o

    def _write_repo_tf(self, base, location):
        d = os.path.join(base, "projects", "src-svc-1", "ArtifactRegistryRepository", location)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "cloud-run-source-deploy.tf"), "w", encoding="utf-8") as f:
            f.write(
                'resource "google_artifact_registry_repository" "cloud_run_source_deploy" {\n'
                f'  location      = "{location}"\n'
                '  project       = "src-svc-1"\n'
                '  repository_id = "cloud-run-source-deploy"\n'
                '}\n'
                '# terraform import google_artifact_registry_repository.cloud_run_source_deploy '
                f'projects/src-svc-1/locations/{location}/repositories/cloud-run-source-deploy\n'
            )

    def test_same_label_in_two_locations_is_deduped(self, temp_dir):
        o = self._setup(temp_dir)
        raw = os.path.join(temp_dir, "raw", "src-svc-1")
        active = os.path.join(temp_dir, "active")
        self._write_repo_tf(raw, "asia-northeast1")
        self._write_repo_tf(raw, "us-central1")
        o.customize_hcl(os.path.join(temp_dir, "raw"), active)

        proj_root = os.path.join(active, "src-svc-1")
        contents = {}
        for name in os.listdir(proj_root):
            if name.endswith(".tf"):
                with open(os.path.join(proj_root, name), encoding="utf-8") as f:
                    contents[name] = f.read()
        labels = []
        for c in contents.values():
            labels += [m[1] for m in
                       __import__("re").findall(r'resource\s+"([^"]+)"\s+"([^"]+)"', c)]
        # ラベルは一意（Duplicate resource が出ない）
        assert len(labels) == 2 and len(set(labels)) == 2
        # ソート走査で asia が先勝ち = 元ラベル維持、us が改名される（決定的）
        assert "cloud_run_source_deploy" in labels
        assert "cloud_run_source_deploy_us_central1" in labels
        # 改名側の import コメントも追従している
        renamed = [c for c in contents.values() if "cloud_run_source_deploy_us_central1" in c][0]
        assert ("# terraform import google_artifact_registry_repository."
                "cloud_run_source_deploy_us_central1 ") in renamed


# ============================================================
# provider 非互換の吸収（廃止ブロック除去 / 必須引数の補完）
# ============================================================
class TestStripHclBlocks:
    SAMPLE = (
        'resource "google_container_cluster" "c" {\n'
        '  name = "c1"\n'
        '\n'
        '  cluster_telemetry {\n'
        '    type = "ENABLED"\n'
        '  }\n'
        '\n'
        '  protect_config {\n'
        '    workload_config {\n'
        '      audit_mode = "BASIC"\n'
        '    }\n'
        '    workload_vulnerability_mode = "BASIC"\n'
        '  }\n'
        '\n'
        '  pod_security_policy_config {\n'
        '    enabled = false\n'
        '  }\n'
        '}\n'
    )

    def test_removed_blocks_are_stripped_including_nested(self):
        out, removed = strip_hcl_blocks(self.SAMPLE, _GKE_REMOVED_TF_BLOCKS)
        assert sorted(removed) == [
            "cluster_telemetry", "pod_security_policy_config", "protect_config"]
        for word in ("cluster_telemetry", "protect_config", "workload_config",
                     "pod_security_policy_config", "audit_mode"):
            assert word not in out
        # リソース本体と他の引数は残る
        assert 'resource "google_container_cluster" "c"' in out
        assert 'name = "c1"' in out
        # brace が対応している（除去でブロックが壊れていない）
        assert out.count("{") == out.count("}")

    def test_unlisted_blocks_are_kept(self):
        out, removed = strip_hcl_blocks(self.SAMPLE, ["nonexistent_block"])
        assert removed == [] and out == self.SAMPLE


class TestEnsureHclBlockArg:
    def test_missing_arg_is_inserted_with_indent(self):
        content = (
            'resource "google_compute_backend_service" "b" {\n'
            '  iap {\n'
            '    oauth2_client_id = "xxx"\n'
            '  }\n'
            '}\n'
        )
        out, n = ensure_hcl_block_arg(content, "iap", "enabled = true")
        assert n == 1
        assert '  iap {\n    enabled = true\n    oauth2_client_id = "xxx"\n' in out

    def test_existing_arg_is_untouched(self):
        content = '  iap {\n    enabled = false\n  }\n'
        out, n = ensure_hcl_block_arg(content, "iap", "enabled = true")
        assert n == 0 and "enabled = false" in out and "enabled = true" not in out

    def test_nested_block_target(self):
        content = (
            '  monitoring_config {\n'
            '    advanced_datapath_observability_config {\n'
            '      enable_metrics = false\n'
            '    }\n'
            '  }\n'
        )
        out, n = ensure_hcl_block_arg(
            content, "advanced_datapath_observability_config", "enable_relay = false")
        assert n == 1 and "enable_relay = false" in out

    def test_multiple_blocks_each_get_the_arg(self):
        content = '  iap {\n  }\n  iap {\n    enabled = true\n  }\n'
        out, n = ensure_hcl_block_arg(content, "iap", "enabled = true")
        assert n == 1


class TestSslCertificateSkip:
    def _setup(self, temp_dir):
        cfg = _full_config(temp_dir)
        cfg["global"]["dry_run"] = False
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        return o

    def test_self_managed_cert_is_skipped(self, temp_dir):
        o = self._setup(temp_dir)
        reason = o._skip_reason_for_file(
            'resource "google_compute_ssl_certificate" "c" {\n  name = "c"\n}\n')
        assert reason and "SSL" in reason

    def test_managed_cert_is_not_skipped(self, temp_dir):
        o = self._setup(temp_dir)
        assert o._skip_reason_for_file(
            'resource "google_compute_managed_ssl_certificate" "c" {\n}\n') is None


class TestFixProviderCompatGkeCidr:
    def _setup(self, temp_dir):
        cfg = _full_config(temp_dir)
        cfg["global"]["dry_run"] = False
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        import logging as _l
        o.org_logger = _l.getLogger("test-org")
        return o

    def test_cluster_ipv4_cidr_dropped_when_vpc_native(self, temp_dir):
        o = self._setup(temp_dir)
        content = (
            'resource "google_container_cluster" "c" {\n'
            '  cluster_ipv4_cidr = "10.4.0.0/17"\n'
            '  ip_allocation_policy {\n'
            '    cluster_ipv4_cidr_block      = "10.4.0.0/17"\n'
            '    services_ipv4_cidr_block     = "10.4.128.0/22"\n'
            '  }\n'
            '}\n'
        )
        out = o._fix_provider_compat(content, "x.tf")
        assert "cluster_ipv4_cidr =" not in out
        # block 内の cluster_ipv4_cidr_block は残す（こちらが正）
        assert 'cluster_ipv4_cidr_block      = "10.4.0.0/17"' in out

    def test_routes_based_cluster_keeps_cidr(self, temp_dir):
        o = self._setup(temp_dir)
        content = (
            'resource "google_container_cluster" "c" {\n'
            '  cluster_ipv4_cidr = "10.4.0.0/17"\n'
            '}\n'
        )
        out = o._fix_provider_compat(content, "x.tf")
        assert 'cluster_ipv4_cidr = "10.4.0.0/17"' in out


class TestFixProviderCompatIpAllocation:
    def _setup(self, temp_dir):
        cfg = _full_config(temp_dir)
        cfg["global"]["dry_run"] = False
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        import logging as _l
        o.org_logger = _l.getLogger("test-org")
        return o

    def test_cidr_blocks_dropped_when_range_name_present(self, temp_dir):
        o = self._setup(temp_dir)
        content = (
            'resource "google_container_cluster" "c" {\n'
            '  ip_allocation_policy {\n'
            '    cluster_ipv4_cidr_block      = "10.14.0.0/17"\n'
            '    cluster_secondary_range_name = "gke-c-pods-d5450142"\n'
            '    services_ipv4_cidr_block = "34.118.224.0/20"\n'
            '    stack_type               = "IPV4"\n'
            '  }\n'
            '}\n'
        )
        out = o._fix_provider_compat(content, "x.tf")
        assert "cluster_ipv4_cidr_block" not in out
        assert "services_ipv4_cidr_block" not in out
        # range 名参照（dst subnet に複製される実体）と他の引数は残す
        assert 'cluster_secondary_range_name = "gke-c-pods-d5450142"' in out
        assert 'stack_type               = "IPV4"' in out

    def test_cidr_only_cluster_is_untouched(self, temp_dir):
        # range 名を使わないクラスタ（CIDR 自動作成モード）はそのまま
        o = self._setup(temp_dir)
        content = (
            'resource "google_container_cluster" "c" {\n'
            '  ip_allocation_policy {\n'
            '    cluster_ipv4_cidr_block  = "10.14.0.0/17"\n'
            '    services_ipv4_cidr_block = "10.14.128.0/22"\n'
            '  }\n'
            '}\n'
        )
        out = o._fix_provider_compat(content, "x.tf")
        assert 'cluster_ipv4_cidr_block  = "10.14.0.0/17"' in out
        assert 'services_ipv4_cidr_block = "10.14.128.0/22"' in out


# ============================================================
# customize の手動対応・確認注記 → DIFF.md 掲載
# ============================================================
class TestCustomizeNotes:
    def _setup(self, temp_dir):
        cfg = _full_config(temp_dir)
        cfg["global"]["dry_run"] = False
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        return o

    def _write_raw(self, temp_dir):
        raw = os.path.join(temp_dir, "raw", "src-svc-1")
        os.makedirs(raw, exist_ok=True)
        with open(os.path.join(raw, "cert.tf"), "w", encoding="utf-8") as f:
            f.write(
                'resource "google_compute_ssl_certificate" "c" {\n'
                '  name    = "my-cert"\n'
                '  project = "src-svc-1"\n'
                '}\n'
            )
        with open(os.path.join(raw, "backend.tf"), "w", encoding="utf-8") as f:
            f.write(
                'resource "google_compute_backend_service" "b" {\n'
                '  iap {\n'
                '    oauth2_client_id = "xxx"\n'
                '  }\n'
                '  name    = "my-backend"\n'
                '  project = "src-svc-1"\n'
                '}\n'
            )
        return os.path.join(temp_dir, "raw")

    def test_notes_are_persisted_per_project(self, temp_dir):
        o = self._setup(temp_dir)
        raw = self._write_raw(temp_dir)
        active = os.path.join(temp_dir, "active")
        o.customize_hcl(raw, active)

        notes = load_customize_notes(active)
        kinds = sorted(n["kind"] for n in notes)
        assert kinds == ["iap_enabled", "ssl_certificate"]
        # project は dst 側に置換済みの値で記録される
        assert all(n["project"] == "dst-svc-1" for n in notes)
        by_kind = {n["kind"]: n for n in notes}
        assert by_kind["ssl_certificate"]["resource"] == "my-cert"
        assert by_kind["iap_enabled"]["resource"] == "my-backend"

    def test_notes_cleared_when_cause_disappears(self, temp_dir):
        o = self._setup(temp_dir)
        raw = self._write_raw(temp_dir)
        active = os.path.join(temp_dir, "active")
        o.customize_hcl(raw, active)
        assert load_customize_notes(active)
        # SSL 証明書と iap が raw から消えたら注記も消える（.tf と同じライフサイクル）
        os.remove(os.path.join(raw, "src-svc-1", "cert.tf"))
        os.remove(os.path.join(raw, "src-svc-1", "backend.tf"))
        with open(os.path.join(raw, "src-svc-1", "bucket.tf"), "w", encoding="utf-8") as f:
            f.write('resource "google_storage_bucket" "b" {\n  name = "src-bucket-x"\n}\n')
        o.customize_hcl(raw, active)
        assert load_customize_notes(active) == []

    def test_dry_run_does_not_write_notes(self, temp_dir):
        o = self._setup(temp_dir)
        o.dry_run = True
        raw = self._write_raw(temp_dir)
        active = os.path.join(temp_dir, "active")
        o.customize_hcl(raw, active)
        assert load_customize_notes(active) == []


class TestCustomizeNoteRow:
    def test_ssl_is_action_with_create_command(self):
        kind, what, why, how = customize_note_row(
            {"kind": "ssl_certificate", "resource": "my-cert", "project": "dst-p"})
        assert kind == "要対応"
        assert "my-cert" in what
        assert "gcloud compute ssl-certificates create my-cert" in how
        assert "--project=dst-p" in how

    def test_iap_is_confirm_with_disable_command(self):
        kind, what, why, how = customize_note_row(
            {"kind": "iap_enabled", "resource": "my-backend", "project": "dst-p"})
        assert kind == "確認"
        assert "--iap=disabled" in how

    def test_unknown_kind_is_not_swallowed(self):
        kind, what, _why, _how = customize_note_row(
            {"kind": "future_thing", "resource": "r", "project": "p"})
        assert kind == "確認" and "future_thing" in what


class TestDiffReportManualNotesSection:
    def _report(self):
        return {
            "src_project": "s", "dst_project": "d", "cai_total": 0, "tf_total": 0,
            "covered": 0, "missing": [], "unknown_types": [], "auto_handled": 0,
            "action_total": 0,
        }

    def test_section_rendered_with_action_first(self):
        md = format_diff_report([self._report()], manual_notes=[
            {"kind": "iap_enabled", "resource": "b", "project": "d"},
            {"kind": "ssl_certificate", "resource": "c", "project": "d"},
        ])
        assert "## customize による補正・スキップ（手動対応・確認）" in md
        assert "customize 補正・スキップ: **2** 件" in md
        # 要対応（SSL）が確認（IAP）より先
        assert md.index("SSL 証明書 `c`") < md.index("backend service `b`")

    def test_section_omitted_when_no_notes(self):
        md = format_diff_report([self._report()], manual_notes=[])
        assert "customize による補正・スキップ" not in md
        md2 = format_diff_report([self._report()])
        assert "customize による補正・スキップ" not in md2


# ============================================================
# subnetwork 参照の terraform 参照化 / Container Analysis occurrence の除外
# ============================================================
class TestTfBlocksOfType:
    def test_nested_blocks_are_included_in_body(self):
        content = (
            'resource "google_compute_subnetwork" "tokyo" {\n'
            '  name = "tokyo"\n'
            '  secondary_ip_range {\n'
            '    range_name = "pods"\n'
            '  }\n'
            '  region = "asia-northeast1"\n'
            '}\n'
        )
        blocks = tf_blocks_of_type(content, "google_compute_subnetwork")
        assert len(blocks) == 1
        label, body = blocks[0]
        assert label == "tokyo"
        # ネストブロックの後ろにある region まで body に含まれる
        assert 'region = "asia-northeast1"' in body
        assert 'range_name = "pods"' in body

    def test_other_types_and_empty(self):
        assert tf_blocks_of_type('resource "google_compute_network" "n" {}\n',
                                 "google_compute_subnetwork") == []
        assert tf_blocks_of_type("", "google_compute_subnetwork") == []


class TestRewriteSubnetRefs:
    def _setup(self, temp_dir):
        cfg = _full_config(temp_dir)
        cfg["global"]["dry_run"] = False
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        import logging as _l
        o.org_logger = _l.getLogger("test-org")
        return o

    def _root(self, temp_dir, subnet_label="tokyo", subnet_project="dst-host"):
        root = os.path.join(temp_dir, "active", "src-host")
        os.makedirs(root, exist_ok=True)
        with open(os.path.join(root, "subnet.tf"), "w", encoding="utf-8") as f:
            f.write(
                f'resource "google_compute_subnetwork" "{subnet_label}" {{\n'
                f'  name    = "tokyo"\n'
                f'  project = "{subnet_project}"\n'
                f'  region  = "asia-northeast1"\n'
                '}\n'
            )
        with open(os.path.join(root, "address.tf"), "w", encoding="utf-8") as f:
            f.write(
                'resource "google_compute_address" "a" {\n'
                '  name       = "fix-tokyo1"\n'
                '  project    = "dst-host"\n'
                '  subnetwork = "https://www.googleapis.com/compute/v1/projects/'
                'dst-host/regions/asia-northeast1/subnetworks/tokyo"\n'
                '}\n'
                '# terraform import google_compute_address.a '
                'projects/dst-host/regions/asia-northeast1/addresses/fix-tokyo1\n'
            )
        return root

    def test_same_root_subnet_url_becomes_reference(self, temp_dir):
        o = self._setup(temp_dir)
        root = self._root(temp_dir)
        o._rewrite_subnet_refs_in_active(os.path.join(temp_dir, "active"))
        out = open(os.path.join(root, "address.tf"), encoding="utf-8").read()
        assert "subnetwork = google_compute_subnetwork.tokyo.self_link" in out
        # import コメントの URL 形式でないパスは触らない
        assert ("# terraform import google_compute_address.a "
                "projects/dst-host/regions/asia-northeast1/addresses/fix-tokyo1") in out

    def test_renamed_label_is_followed(self, temp_dir):
        # dedupe で改名されたラベルでも、確定後の active を読むので追従する
        o = self._setup(temp_dir)
        root = self._root(temp_dir, subnet_label="tokyo_us_central1")
        o._rewrite_subnet_refs_in_active(os.path.join(temp_dir, "active"))
        out = open(os.path.join(root, "address.tf"), encoding="utf-8").read()
        assert "google_compute_subnetwork.tokyo_us_central1.self_link" in out

    def test_cross_project_subnet_url_is_left_alone(self, temp_dir):
        # 別ルート（Shared VPC host）の subnet は解決できないので URL のまま
        o = self._setup(temp_dir)
        root = self._root(temp_dir, subnet_project="other-host")
        o._rewrite_subnet_refs_in_active(os.path.join(temp_dir, "active"))
        out = open(os.path.join(root, "address.tf"), encoding="utf-8").read()
        assert "https://www.googleapis.com/compute/v1/projects/dst-host" in out
        assert "google_compute_subnetwork." not in out


class TestContainerAnalysisOccurrenceSkip:
    def _setup(self, temp_dir):
        cfg = _full_config(temp_dir)
        cfg["global"]["dry_run"] = False
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        return o

    def test_occurrence_is_skipped(self, temp_dir):
        o = self._setup(temp_dir)
        reason = o._skip_reason_for_file(
            'resource "google_container_analysis_occurrence" "x" {\n'
            '  note_name = "projects/p/notes/built-by-cloud-build"\n'
            '}\n')
        assert reason and "occurrence" in reason

    def test_note_and_pubsub_topic_are_kept(self, temp_dir):
        o = self._setup(temp_dir)
        assert o._skip_reason_for_file(
            'resource "google_container_analysis_note" "n" {}\n') is None
        assert o._skip_reason_for_file(
            'resource "google_pubsub_topic" "t" {\n'
            '  name = "container-analysis-occurrences-v1"\n}\n') is None


class TestCustomizeNoteDedupe:
    def test_identical_notes_collapse(self, temp_dir):
        cfg = _full_config(temp_dir)
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        o._customize_notes = []
        body = 'resource "google_container_analysis_occurrence" "x" {\n  project = "dst-p"\n}\n'
        for i in range(3):
            o._add_customize_note("container_analysis_occurrence", body, f"src/f{i}.tf")
        assert len(o._customize_notes) == 1

    def test_occurrence_note_row_is_confirmation(self):
        kind, target, why, how = customize_note_row(
            {"kind": "container_analysis_occurrence", "resource": "?", "project": "dst-p"})
        assert kind == "確認"
        assert "dst-p" in target and "note" in why and "再ビルド" in how


# ============================================================
# deletion_protection の補完 / Artifact Registry イメージ複製
# ============================================================
class TestEnsureTfResourceArg:
    def test_missing_arg_is_inserted(self):
        content = (
            'resource "google_cloud_run_v2_service" "svc" {\n'
            '  name = "svc"\n'
            '}\n'
        )
        out, added = ensure_tf_resource_arg(
            content, "google_cloud_run_v2_service", "deletion_protection = false")
        assert added == ["svc"]
        assert '  deletion_protection = false\n  name = "svc"' in out

    def test_existing_arg_is_untouched(self):
        content = (
            'resource "google_container_cluster" "c" {\n'
            '  deletion_protection = true\n'
            '}\n'
        )
        out, added = ensure_tf_resource_arg(
            content, "google_container_cluster", "deletion_protection = false")
        assert added == [] and "deletion_protection = true" in out

    def test_nested_block_arg_does_not_count_as_present(self):
        # ネストブロック内の同名引数を「あり」と誤判定しない
        content = (
            'resource "google_container_cluster" "c" {\n'
            '  node_config {\n'
            '    deletion_protection = true\n'
            '  }\n'
            '}\n'
        )
        _out, added = ensure_tf_resource_arg(
            content, "google_container_cluster", "deletion_protection = false")
        assert added == ["c"]

    def test_other_types_untouched(self):
        content = 'resource "google_storage_bucket" "b" {\n}\n'
        out, added = ensure_tf_resource_arg(
            content, "google_cloud_run_v2_service", "deletion_protection = false")
        assert added == [] and out == content


class TestParseArRepositories:
    def test_docker_only_and_sorted(self):
        text = json.dumps([
            {"name": "projects/p/locations/us-central1/repositories/b", "format": "DOCKER"},
            {"name": "projects/p/locations/asia-northeast1/repositories/a", "format": "DOCKER"},
            {"name": "projects/p/locations/asia-northeast1/repositories/py", "format": "PYTHON"},
        ])
        got = parse_ar_repositories(text)
        assert [(r["location"], r["repo"]) for r in got] == [
            ("asia-northeast1", "a"), ("us-central1", "b")]

    def test_broken_input_is_empty(self):
        assert parse_ar_repositories(None) == []
        assert parse_ar_repositories("not json") == []
        assert parse_ar_repositories(json.dumps([{"name": "bad", "format": "DOCKER"}])) == []


class TestBuildArImageCopyPlan:
    def _images(self, **over):
        base = {
            "package": "asia-northeast1-docker.pkg.dev/src-p/repo/app",
            "version": "sha256:" + "ab" * 32,
            "tags": ["latest", "v1"],
        }
        base.update(over)
        return json.dumps([base])

    def test_project_segment_is_replaced(self):
        plan = build_ar_image_copy_plan(self._images(), "src-p", "dst-p")
        assert len(plan) == 1
        e = plan[0]
        assert e["src_ref"] == "asia-northeast1-docker.pkg.dev/src-p/repo/app@sha256:" + "ab" * 32
        assert e["dst_pkg"] == "asia-northeast1-docker.pkg.dev/dst-p/repo/app"
        assert e["tags"] == ["latest", "v1"]

    def test_image_name_containing_project_id_is_not_mangled(self):
        # project 部だけを差し替える（イメージ名に src ID が入っていても壊さない）
        plan = build_ar_image_copy_plan(
            self._images(package="asia-northeast1-docker.pkg.dev/src-p/repo/src-p-api"),
            "src-p", "dst-p")
        assert plan[0]["dst_pkg"] == "asia-northeast1-docker.pkg.dev/dst-p/repo/src-p-api"

    def test_comma_string_tags_are_split(self):
        plan = build_ar_image_copy_plan(self._images(tags="a,b"), "src-p", "dst-p")
        assert plan[0]["tags"] == ["a", "b"]

    def test_untagged_image_is_kept(self):
        plan = build_ar_image_copy_plan(self._images(tags=[]), "src-p", "dst-p")
        assert plan[0]["tags"] == []

    def test_invalid_digest_or_foreign_project_is_dropped(self):
        assert build_ar_image_copy_plan(self._images(version="latest"), "src-p", "dst-p") == []
        assert build_ar_image_copy_plan(self._images(), "other-p", "dst-p") == []

    def test_duplicate_entries_collapse(self):
        one = json.loads(self._images())[0]
        plan = build_ar_image_copy_plan(json.dumps([one, one]), "src-p", "dst-p")
        assert len(plan) == 1


class TestFilterArPlanByScope:
    def _plan(self):
        return [
            {"digest": "sha256:" + "a" * 64, "tags": ["latest"]},
            {"digest": "sha256:" + "b" * 64, "tags": []},
            {"digest": "sha256:" + "c" * 64, "tags": []},
        ]

    def test_scope_all_keeps_everything(self):
        for scope in (None, "", "all", "ALL"):
            kept, dropped = filter_ar_plan_by_scope(self._plan(), scope)
            assert len(kept) == 3 and dropped == []

    def test_scope_tagged_drops_untagged(self):
        kept, dropped = filter_ar_plan_by_scope(self._plan(), "tagged")
        assert [e["tags"] for e in kept] == [["latest"]]
        assert len(dropped) == 2

    def test_tf_referenced_digest_survives_even_without_tag(self):
        # .tf が digest 固定で参照するものを落とすと apply が Image not found で死ぬ
        keep = {"sha256:" + "b" * 64}
        kept, dropped = filter_ar_plan_by_scope(self._plan(), "tagged", keep)
        assert {e["digest"] for e in kept} == {"sha256:" + "a" * 64, "sha256:" + "b" * 64}
        assert [e["digest"] for e in dropped] == ["sha256:" + "c" * 64]

    def test_unknown_scope_is_treated_as_all(self):
        # 綴り誤りは validate_steps_config が実行前に弾く。ここでは全量維持（安全側）
        kept, dropped = filter_ar_plan_by_scope(self._plan(), "referenced")
        assert len(kept) == 3 and dropped == []

    def test_input_plan_is_not_mutated(self):
        plan = self._plan()
        filter_ar_plan_by_scope(plan, "tagged")
        assert len(plan) == 3


class TestTfReferencedImageDigests:
    def test_collects_digest_pinned_images(self, temp_dir):
        d = os.path.join(temp_dir, "active")
        os.makedirs(d)
        with open(os.path.join(d, "run.tf"), "w") as f:
            f.write(
                'resource "google_cloud_run_v2_service" "s" {\n'
                '  template { containers {\n'
                f'    image = "asia-docker.pkg.dev/p/r/app@sha256:{"a" * 64}"\n'
                '  } }\n}\n'
            )
        with open(os.path.join(d, "job.tf"), "w") as f:
            f.write(f'image = "us-docker.pkg.dev/p/r/j@sha256:{"b" * 64}"\n')
        # .tf 以外は読まない
        with open(os.path.join(d, "notes.txt"), "w") as f:
            f.write(f'@sha256:{"c" * 64}\n')
        got = tf_referenced_image_digests(d)
        assert got == {"sha256:" + "a" * 64, "sha256:" + "b" * 64}

    def test_missing_dir_returns_empty(self, temp_dir):
        assert tf_referenced_image_digests(os.path.join(temp_dir, "nope")) == set()
        assert tf_referenced_image_digests(None) == set()


class TestArScopeValidation:
    def test_unknown_scope_is_rejected(self, temp_dir):
        cfg = _full_config(temp_dir, steps={
            "data_sync": {"enabled": True,
                          "artifact_registry": {"scope": "referenced"}}})
        errs = validate_steps_config(cfg)
        assert any("artifact_registry.scope" in e for e in errs)

    def test_valid_scopes_pass(self, temp_dir):
        for scope in ("all", "tagged"):
            cfg = _full_config(temp_dir, steps={
                "data_sync": {"enabled": True,
                              "artifact_registry": {"scope": scope}}})
            assert not [e for e in validate_steps_config(cfg)
                        if "artifact_registry" in e]

    def test_disabled_step_is_not_checked(self, temp_dir):
        cfg = _full_config(temp_dir, steps={
            "data_sync": {"enabled": False,
                          "artifact_registry": {"scope": "bogus"}}})
        assert not [e for e in validate_steps_config(cfg) if "artifact_registry" in e]


class TestArSyncGuards:
    def _orch(self, temp_dir, steps=None):
        cfg = _full_config(temp_dir, steps=steps or {})
        cfg["global"]["dry_run"] = False
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        o.dst_logger = logging.getLogger("test-dst")
        o.org_logger = logging.getLogger("test-org")
        return o

    def test_disabled_by_config(self, temp_dir):
        from unittest.mock import patch
        o = self._orch(temp_dir, steps={
            "data_sync": {"enabled": True, "artifact_registry": {"enabled": False}}})
        with patch.object(o, "_soft_run") as soft, \
             patch.object(o, "_collect_ar_copy_work") as collect:
            o.step_artifact_registry()
        assert soft.call_count == 0 and collect.call_count == 0

    def test_src_list_command_is_read_only_and_mock_known(self):
        cmd = ("gcloud artifacts repositories list --project=p --format=json --quiet")
        assert is_src_read_only(cmd) and is_known_mock_command(cmd)
        cmd2 = "gcloud artifacts docker images list host/p/r --include-tags --format=json --quiet"
        assert is_src_read_only(cmd2) and is_known_mock_command(cmd2)

    def test_write_commands_are_rejected_on_src(self):
        assert not is_src_read_only("docker push host/p/r/img:latest")
        assert not is_src_read_only("gcloud artifacts repositories create r --location=l")


# ============================================================
# k8s 所有者マーカー / GKE FW 判定 / import 失敗分類 ほか（レビュー修正）
# ============================================================
class TestK8sOwnerMarker:
    TP = (
        'resource "google_compute_target_pool" "a0cb2a7138c8b475e8f1de09dc442b6a" {\n'
        '  description      = "{\\"kubernetes.io/service-name\\":\\"default/frontend-external\\"}"\n'
        '  health_checks    = ["https://.../httpHealthChecks/k8s-aa49baa47b628c78-node"]\n'
        '  name             = "a0cb2a7138c8b475e8f1de09dc442b6a"\n'
        '}\n'
    )

    def test_marker_detection(self):
        assert has_k8s_owner_marker(self.TP)
        assert not has_k8s_owner_marker(
            'resource "google_compute_target_pool" "t" {\n'
            '  description = "my pool"\n  name = "user-pool"\n}\n')

    def test_lb_name_detection(self):
        assert is_k8s_lb_resource_name("a0cb2a7138c8b475e8f1de09dc442b6a")
        assert not is_k8s_lb_resource_name("user-pool")
        assert not is_k8s_lb_resource_name("a0cb")
        assert not is_k8s_lb_resource_name(None)


class TestK8sLbResourcesSkipped:
    def _setup(self, temp_dir):
        cfg = _full_config(temp_dir)
        cfg["global"]["dry_run"] = False
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        return o

    def test_hex_named_target_pool_with_marker_is_skipped(self, temp_dir):
        o = self._setup(temp_dir)
        reason = o._skip_reason_for_file(TestK8sOwnerMarker.TP)
        assert reason and "kubernetes.io" in reason

    def test_forwarding_rule_with_marker_is_skipped(self, temp_dir):
        o = self._setup(temp_dir)
        content = (
            'resource "google_compute_forwarding_rule" "a0cb2a7138c8b475e8f1de09dc442b6a" {\n'
            '  description = "{\\"kubernetes.io/service-name\\":\\"default/frontend-external\\"}"\n'
            '  name        = "a0cb2a7138c8b475e8f1de09dc442b6a"\n'
            '}\n'
        )
        assert o._skip_reason_for_file(content)

    def test_user_forwarding_rule_without_marker_is_kept(self, temp_dir):
        o = self._setup(temp_dir)
        content = (
            'resource "google_compute_forwarding_rule" "lb" {\n'
            '  name = "my-app-lb"\n'
            '}\n'
        )
        assert o._skip_reason_for_file(content) is None

    def test_user_target_pool_without_marker_is_kept(self, temp_dir):
        o = self._setup(temp_dir)
        content = (
            'resource "google_compute_target_pool" "t" {\n'
            '  name = "user-pool"\n'
            '}\n'
        )
        assert o._skip_reason_for_file(content) is None


class TestIsGkeManagedFwRule:
    def test_marker_wins(self):
        assert is_gke_managed_fw_rule({
            "name": "anything",
            "description": '{"kubernetes.io/service-name":"default/web"}'})

    def test_gke_core_structural_names(self):
        for n in ("gke-my-ec-cluster-fd56f6a4-vms", "gke-c1-1234abcd-all",
                  "gk3-ap-cluster-0a1b2c3d-master"):
            assert is_gke_managed_fw_rule({"name": n}), n

    def test_k8s_hex_structural_names(self):
        for n in ("k8s-fw-a1b2c3", "k8s-fw-a0cb2a7138c8b475e8f1de09dc442b6a-deny",
                  "k8s-aa49baa47b628c78-node-http-hc"):
            assert is_gke_managed_fw_rule({"name": n}), n

    def test_user_rules_with_gke_like_prefix_are_kept(self):
        # 接頭辞は GKE 風でも構造ハッシュ・マーカーが無ければ利用者ルール
        for n in ("k8s-nodeport-allow", "gke-admin-bastion", "k8s-deny-all"):
            assert not is_gke_managed_fw_rule({"name": n}), n


class TestImportErrorKind:
    def test_already_managed(self):
        assert import_error_kind("Error: Resource already managed by Terraform") == "already"

    def test_missing_variants(self):
        for t in ("Error: Cannot import non-existent remote object",
                  "googleapi: Error 404: Not found: projects/p/locations/l/clusters/c",
                  "Error 404: The resource 'x' was not found, notFound",
                  'note with ID "x" for project "p" does not exist'):
            assert import_error_kind(t) == "missing", t

    def test_real_failures_are_none(self):
        assert import_error_kind("Error 403: Permission denied") is None
        assert import_error_kind("") is None


class TestCoerceNonnegInt:
    def test_values(self):
        assert coerce_nonneg_int(30, 120) == 30
        assert coerce_nonneg_int("45", 120) == 45
        assert coerce_nonneg_int(0, 120) == 0
        assert coerce_nonneg_int("2m", 120) == 120
        assert coerce_nonneg_int(None, 120) == 120
        assert coerce_nonneg_int(-5, 120) == 120


class TestBaseApisProtected:
    def test_skip_apis_cannot_remove_base(self):
        plan = build_api_enable_plan(
            [], [], {}, skip_apis=list(_BASE_DST_APIS))
        for api in _BASE_DST_APIS:
            assert api in plan


class TestCaiToTfRegionVariants:
    def test_neg_and_template_variants(self):
        negs = _CAI_TO_TF_RESOURCE["compute.googleapis.com/NetworkEndpointGroup"]
        assert "google_compute_region_network_endpoint_group" in negs
        assert "google_compute_global_network_endpoint_group" in negs
        tpls = _CAI_TO_TF_RESOURCE["compute.googleapis.com/InstanceTemplate"]
        assert "google_compute_region_instance_template" in tpls

    def test_lb_types_registered(self):
        for atype in ("compute.googleapis.com/TargetPool",
                      "compute.googleapis.com/HttpHealthCheck",
                      "compute.googleapis.com/ForwardingRule",
                      "compute.googleapis.com/Autoscaler"):
            assert atype in _CAI_TO_TF_RESOURCE
            assert atype in _ASSET_COVERAGE


class TestClassifyK8sLbAsReference:
    def _item(self, atype, name):
        return {"asset_type": atype, "short_name": name, "name": name,
                "coverage_step": "terraform_apply", "reason": ""}

    def test_hex_named_target_pool_is_reference(self):
        got = classify_missing_asset(self._item(
            "compute.googleapis.com/TargetPool", "a0cb2a7138c8b475e8f1de09dc442b6a"))
        assert got["level"] == "reference" and got["priority"] == 3

    def test_user_named_target_pool_is_action(self):
        got = classify_missing_asset(self._item(
            "compute.googleapis.com/TargetPool", "user-pool"))
        assert got["level"] == "action"


class TestFixProviderCompatRegionBackendService:
    def _setup(self, temp_dir):
        cfg = _full_config(temp_dir)
        cfg["global"]["dry_run"] = False
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        import logging as _l
        o.org_logger = _l.getLogger("test-org")
        o._customize_notes = []
        return o

    def test_region_backend_service_iap_gets_enabled(self, temp_dir):
        o = self._setup(temp_dir)
        content = (
            'resource "google_compute_region_backend_service" "b" {\n'
            '  iap {\n'
            '    oauth2_client_id = "xxx"\n'
            '  }\n'
            '  name = "b"\n'
            '}\n'
        )
        out = o._fix_provider_compat(content, "x.tf")
        assert "enabled = true" in out


class TestMockLabelNoFalsePositive:
    def test_real_resource_named_mock_bucket_is_not_flagged(self, temp_dir):
        # 実在しうるバケット名 "mock_bucket"（name 属性）は mock 残骸ではない
        with open(os.path.join(temp_dir, "b.tf"), "w", encoding="utf-8") as f:
            f.write('resource "google_storage_bucket" "user_data" {\n'
                    '  name = "mock_bucket"\n}\n')
        assert not tf_dir_has_mock_artifacts(temp_dir)

    def test_declaration_label_is_still_flagged(self, temp_dir):
        with open(os.path.join(temp_dir, "b.tf"), "w", encoding="utf-8") as f:
            f.write('resource "google_storage_bucket" "mock_bucket" {\n'
                    '  name = "org-bucket-shared-data"\n}\n')
        assert tf_dir_has_mock_artifacts(temp_dir)


class TestNonUtf8TfFiles:
    def test_readers_survive_binary_bytes(self, temp_dir):
        with open(os.path.join(temp_dir, "x.tf"), "wb") as f:
            f.write(b'resource "google_container_cluster" "c" {\n'
                    b'  metadata = "\xff\xfe broken"\n}\n')
        assert tf_required_apis(temp_dir) == ["container.googleapis.com"]
        assert tf_dir_has_mock_artifacts(temp_dir) is False


class TestDedupeSameFileCollision:
    def test_sibling_label_equal_to_target_stays_unique(self):
        # 同一ファイルに x と x_asia が並ぶ場合でも全ラベルが一意に保たれる
        seen = {("resource", "google_x", "cloud_run")}
        content = (
            'resource "google_x" "cloud_run" {}\n'
            '# terraform import google_x.cloud_run id1\n'
            'resource "google_x" "cloud_run_asia" {}\n'
            '# terraform import google_x.cloud_run_asia id2\n'
        )
        out, renames = dedupe_tf_resource_labels(content, "asia", seen)
        labels = re.findall(r'resource "google_x" "([^"]+)"', out)
        assert len(labels) == 2 and len(set(labels)) == 2, labels
        # import コメントも宣言と同じラベルを指している
        for lbl in labels:
            assert f"google_x.{lbl} id" in out

    def test_data_blocks_are_deduped(self):
        seen = set()
        c1 = 'data "google_project" "project" {}\n'
        c2 = ('data "google_project" "project" {}\n'
              'resource "google_x" "r" {\n'
              '  num = data.google_project.project.number\n'
              '}\n')
        _o1, r1 = dedupe_tf_resource_labels(c1, "loc-a", seen)
        out2, r2 = dedupe_tf_resource_labels(c2, "loc-b", seen)
        assert r1 == [] and len(r2) == 1
        assert 'data "google_project" "project_loc_b"' in out2
        # ファイル内参照も追従する
        assert "data.google_project.project_loc_b.number" in out2

    def test_data_and_resource_namespaces_are_separate(self):
        seen = set()
        content = ('resource "google_project" "p" {}\n'
                   'data "google_project" "p" {}\n')
        _out, renames = dedupe_tf_resource_labels(content, "loc", seen)
        assert renames == []


# ============================================================
# AR 未使用 src の soft fail / エラー行抽出
# ============================================================
class TestFirstMeaningfulLine:
    GCLOUD_403 = (
        "ERROR: (gcloud.artifacts.repositories.list) [a@b.com] does not have permission "
        "to access projects instance [p]: Artifact Registry API has not been used in "
        "project p before or it is disabled.\n"
        "Google developers console API activation\n"
        "https://console.developers.google.com/apis/api/artifactregistry.googleapis.com\n"
        "- '@type': type.googleapis.com/google.rpc.ErrorInfo\n"
        "  domain: googleapis.com\n"
        "  metadata:\n"
        "    service: artifactregistry.googleapis.com\n"
    )

    def test_gcloud_uppercase_error_wins_over_errorinfo_detail(self):
        got = _first_meaningful_line(self.GCLOUD_403, "")
        assert got.startswith("ERROR: (gcloud.artifacts.repositories.list)")
        assert "@type" not in got

    def test_terraform_error_line(self):
        got = _first_meaningful_line("│ Error: googleapi: Error 403: denied", "")
        assert got == "Error: googleapi: Error 403: denied"

    def test_warning_lines_are_skipped(self):
        got = _first_meaningful_line("WARNING: impersonation in use\nplain message", "")
        assert got == "plain message"


class TestIsApiDisabledError:
    def test_service_disabled_variants(self):
        assert is_api_disabled_error(
            "Artifact Registry API has not been used in project p before or it is disabled")
        assert is_api_disabled_error("SERVICE_DISABLED")
        assert not is_api_disabled_error("PERMISSION_DENIED: caller lacks permission")
        assert not is_api_disabled_error("")


class TestArSyncSoftFail:
    def _orch(self, temp_dir):
        cfg = _full_config(temp_dir, steps={"data_sync": {"enabled": True}})
        cfg["global"]["dry_run"] = False
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        o.dst_logger = logging.getLogger("test-dst")
        o.org_logger = logging.getLogger("test-org")
        return o

    def test_api_disabled_src_does_not_fail_the_run(self, temp_dir):
        from unittest.mock import patch
        o = self._orch(temp_dir)

        def _soft(cmd, side, logger, impersonate_sa=None, timeout=300, skip_on_dry_run=True):
            return 1, "", TestFirstMeaningfulLine.GCLOUD_403

        with patch.object(o, "_soft_run", side_effect=_soft), \
             patch.object(o, "run_command") as rc:
            got = o._collect_ar_copy_work("src-p", "dst-p", None, None)

        # soft fail: failed に積まない / run_command（failed 計上経路）も使わない
        assert got == []
        assert o.stats.failed == 0
        assert rc.call_count == 0

    def test_repo_listing_failure_other_than_api_disabled_warns_only(self, temp_dir):
        from unittest.mock import patch
        o = self._orch(temp_dir)

        def _soft(cmd, side, logger, impersonate_sa=None, timeout=300, skip_on_dry_run=True):
            return 1, "", "ERROR: PERMISSION_DENIED"

        with patch.object(o, "_soft_run", side_effect=_soft):
            got = o._collect_ar_copy_work("src-p", "dst-p", None, None)
        assert got == [] and o.stats.failed == 0

    def test_image_listing_failure_skips_repo_only(self, temp_dir):
        from unittest.mock import patch
        o = self._orch(temp_dir)
        repos = json.dumps([{"name": "projects/src-p/locations/asia-northeast1"
                                     "/repositories/r", "format": "DOCKER"}])
        calls = []

        def _soft(cmd, side, logger, impersonate_sa=None, timeout=300, skip_on_dry_run=True):
            calls.append(cmd)
            if cmd.startswith("gcloud artifacts repositories list"):
                return 0, repos, ""
            if cmd.startswith("gcloud artifacts docker images list"):
                return 1, "", "ERROR: something broke"
            return 0, "", ""

        with patch.object(o, "_soft_run", side_effect=_soft), \
             patch.object(o, "_gcloud_exists", return_value=True):
            got = o._collect_ar_copy_work("src-p", "dst-p", None, None)

        assert got == [] and o.stats.failed == 0
        # イメージ複製（docker pull）までは進まない
        assert not any(c.startswith("docker pull") for c in calls)


# ============================================================
# Step 3.5: .tf 確定後の最終 API 有効化・検証
# ============================================================
class TestStepEnableApisFinal:
    def _orch(self, temp_dir, steps=None):
        cfg = _full_config(temp_dir, steps=steps or {})
        cfg["global"]["dry_run"] = False
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        o.dst_logger = logging.getLogger("test-dst")
        o.org_logger = logging.getLogger("test-org")
        return o

    def _write_tf(self, temp_dir, src_name, body):
        d = os.path.join(temp_dir, "terraform", "active", src_name)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "x.tf"), "w", encoding="utf-8") as f:
            f.write(body)
        return d

    def test_final_verifies_whole_want_set_even_when_nothing_missing(self, temp_dir):
        from unittest.mock import patch
        o = self._orch(temp_dir, steps={
            "bulk_export": {"output_dir": os.path.join(temp_dir, "terraform")}})
        for src in ("src-host", "src-svc-1"):
            self._write_tf(temp_dir, src,
                           'resource "google_container_cluster" "c" {\n  name = "c"\n}\n')

        # dst には全部有効済み（＝ missing 0 件）
        with patch.object(o, "_list_enabled_services",
                          return_value={"container.googleapis.com",
                                        "compute.googleapis.com",
                                        "storage.googleapis.com",
                                        "logging.googleapis.com",
                                        "bigquery.googleapis.com",
                                        "iam.googleapis.com",
                                        "iamcredentials.googleapis.com",
                                        "serviceusage.googleapis.com",
                                        "cloudresourcemanager.googleapis.com"}), \
             patch.object(o, "_enable_apis_on_dst", return_value=[]) as enabler, \
             patch.object(o, "_wait_for_apis_enabled") as waiter:
            o.step_enable_apis(final=True)

        # 追加は無いので enable は呼ばれないが、検証は必ず走る
        assert enabler.call_count == 0
        assert waiter.call_count == 2, "final は want 全体の有効化確認を必ず行う"
        verified = set(waiter.call_args_list[0][0][2])
        assert "container.googleapis.com" in verified

    def test_early_does_not_verify_when_nothing_missing(self, temp_dir):
        from unittest.mock import patch
        o = self._orch(temp_dir, steps={
            "bulk_export": {"output_dir": os.path.join(temp_dir, "terraform")}})
        with patch.object(o, "_list_enabled_services",
                          return_value={"compute.googleapis.com"} | set(_BASE_DST_APIS)), \
             patch.object(o, "_enable_apis_on_dst", return_value=[]), \
             patch.object(o, "_wait_for_apis_enabled") as waiter:
            o.step_enable_apis(final=False)
        assert waiter.call_count == 0

    def test_final_includes_tf_derived_apis(self, temp_dir):
        from unittest.mock import patch
        o = self._orch(temp_dir, steps={
            "bulk_export": {"output_dir": os.path.join(temp_dir, "terraform")}})
        for src in ("src-host", "src-svc-1"):
            self._write_tf(temp_dir, src,
                           'resource "google_sql_database_instance" "d" {\n  name = "d"\n}\n')
        asked = []

        with patch.object(o, "_list_enabled_services", return_value=set()), \
             patch.object(o, "_enable_apis_on_dst",
                          side_effect=lambda p, sa, apis: asked.extend(apis) or []), \
             patch.object(o, "_wait_for_apis_enabled"):
            o.step_enable_apis(final=True)

        assert "sqladmin.googleapis.com" in asked

    def test_final_ignores_mock_artifacts_in_real_run(self, temp_dir):
        from unittest.mock import patch
        o = self._orch(temp_dir, steps={
            "bulk_export": {"output_dir": os.path.join(temp_dir, "terraform")}})
        for src in ("src-host", "src-svc-1"):
            self._write_tf(
                temp_dir, src,
                f"# {_MOCK_TF_MARK}\n"
                'resource "google_sql_database_instance" "d" {\n  name = "d"\n}\n')
        asked = []

        with patch.object(o, "_list_enabled_services", return_value=set()), \
             patch.object(o, "_enable_apis_on_dst",
                          side_effect=lambda p, sa, apis: asked.extend(apis) or []), \
             patch.object(o, "_wait_for_apis_enabled"):
            o.step_enable_apis(final=True)

        # mock 生成物の .tf からは API を引かない（実行時のみのガード）
        assert "sqladmin.googleapis.com" not in asked

    def test_failures_are_summarized_and_do_not_fail_the_run(self, temp_dir):
        from unittest.mock import patch
        o = self._orch(temp_dir, steps={
            "bulk_export": {"output_dir": os.path.join(temp_dir, "terraform")}})
        with patch.object(o, "_list_enabled_services", return_value=set()), \
             patch.object(o, "_enable_apis_on_dst",
                          return_value=["source.googleapis.com"]), \
             patch.object(o, "_wait_for_apis_enabled"):
            o.step_enable_apis(final=True)
        assert o.stats.failed == 0


class TestArtifactRegistryRunsBeforeTerraform:
    """AR イメージ複製は terraform apply より前でなければならない。

    Cloud Run は revision 作成時に `image = "...@sha256:"` を解決するため、
    apply の後に複製しても間に合わない（Image ... not found で失敗する）。
    """

    def test_execute_order_places_ar_sync_before_terraform_apply(self, temp_dir):
        from unittest.mock import patch
        cfg = _full_config(temp_dir, steps={
            "cai_scan": {"enabled": False},
            "enable_apis": {"enabled": False},
            "gce_snapshot": {"enabled": False},
            # output_dir を temp に向けないと既定の ./terraform を掴み、
            # 実行中の make run / make plan の多重起動ロックで execute() が
            # 即終了してテストが落ちる。
            "bulk_export": {"enabled": False,
                            "output_dir": os.path.join(temp_dir, "terraform")},
            "terraform_apply": {"enabled": True},
            "network_firewall": {"enabled": False},
            "gce_restore": {"enabled": False},
            "iam_sync": {"enabled": False},
            "data_sync": {"enabled": True},
        })
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        order = []

        with patch.object(o, "check_prerequisites"), \
             patch.object(o, "check_service_accounts"), \
             patch.object(o, "check_dst_projects_exist"), \
             patch.object(o, "step_artifact_registry",
                          side_effect=lambda: order.append("ar")), \
             patch.object(o, "step_terraform_apply",
                          side_effect=lambda: order.append("tf")), \
             patch.object(o, "step_data_sync",
                          side_effect=lambda: order.append("data")), \
             patch.object(o, "_emit_cai_tf_diff"), \
             patch.object(o, "print_summary", create=True):
            try:
                o.execute()
            except SystemExit:
                pass

        assert "ar" in order and "tf" in order
        assert order.index("ar") < order.index("tf"), f"順序が不正: {order}"

    def test_data_sync_no_longer_copies_images(self, temp_dir):
        from unittest.mock import patch
        cfg = _full_config(temp_dir, steps={"data_sync": {"enabled": True}})
        cfg["global"]["dry_run"] = False
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        o.dst_logger = logging.getLogger("test-dst")
        o.org_logger = logging.getLogger("test-org")

        with patch.object(o, "_sync_gcs"), patch.object(o, "_sync_bq"), \
             patch.object(o, "_collect_ar_copy_work") as ar, \
             patch.object(o, "_copy_ar_image") as cp:
            o.step_data_sync()
        assert ar.call_count == 0 and cp.call_count == 0


class TestArAttestationSkip:
    """Cloud Build の attestation / SBOM は実イメージではないので複製しない。"""

    def test_cosign_tagged_versions_are_dropped_from_plan(self):
        att_tag = "sha256-" + "ab" * 32 + ".att"
        imgs = json.dumps([
            {"package": "asia-northeast1-docker.pkg.dev/src-p/repo/app",
             "version": "sha256:" + "11" * 32, "tags": [att_tag]},
            {"package": "asia-northeast1-docker.pkg.dev/src-p/repo/app",
             "version": "sha256:" + "22" * 32, "tags": ["latest"]},
            # 実タグと attestation タグ混在なら残す（実イメージ側の可能性）
            {"package": "asia-northeast1-docker.pkg.dev/src-p/repo/app",
             "version": "sha256:" + "33" * 32, "tags": [att_tag, "v1"]},
        ])
        plan = build_ar_image_copy_plan(imgs, "src-p", "dst-p")
        digests = {e["digest"] for e in plan}
        assert "sha256:" + "11" * 32 not in digests
        assert "sha256:" + "22" * 32 in digests
        assert "sha256:" + "33" * 32 in digests

    def test_crane_path_preserves_digest_and_skips_verification(self, temp_dir):
        """gcrane は digest を保つので describe による検証も後片付けも不要。"""
        from unittest.mock import patch
        cfg = _full_config(temp_dir, steps={"data_sync": {"enabled": True}})
        cfg["global"]["dry_run"] = False
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        o.dst_logger = logging.getLogger("test-dst")
        o.org_logger = logging.getLogger("test-org")
        calls = []

        def _soft(cmd, side, logger, impersonate_sa=None, timeout=300,
                  skip_on_dry_run=True):
            calls.append(cmd)
            return 0, "", ""

        item = {"src_ref": "h/src-p/r/app@sha256:" + "ee" * 32,
                "dst_pkg": "h/dst-p/r/app",
                "dst_ref": "h/dst-p/r/app@sha256:" + "ee" * 32,
                "digest": "sha256:" + "ee" * 32, "tags": ["v1", "latest"]}
        with patch.object(o, "_soft_run", side_effect=_soft), \
             patch.object(o, "_ar_copy_tool", return_value="gcrane"), \
             patch.object(o, "_gcloud_exists", return_value=False) as ex:
            o._copy_ar_image(item, None)

        assert calls == ["gcrane cp h/src-p/r/app@sha256:" + "ee" * 32 + " h/dst-p/r/app:v1",
                         "gcrane cp h/src-p/r/app@sha256:" + "ee" * 32 + " h/dst-p/r/app:latest"]
        assert not any(c.startswith("docker") for c in calls)
        ex.assert_not_called()   # digest は変わらないので検証しない
        assert o.stats.failed == 0

    def test_crane_path_synthesizes_tag_for_untagged_image(self, temp_dir):
        from unittest.mock import patch
        cfg = _full_config(temp_dir, steps={"data_sync": {"enabled": True}})
        cfg["global"]["dry_run"] = False
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        o.dst_logger = logging.getLogger("test-dst")
        o.org_logger = logging.getLogger("test-org")
        calls = []

        def _soft(cmd, side, logger, impersonate_sa=None, timeout=300,
                  skip_on_dry_run=True):
            calls.append(cmd)
            return 0, "", ""

        digest = "sha256:" + "ab" * 32
        item = {"src_ref": f"h/src-p/r/app@{digest}", "dst_pkg": "h/dst-p/r/app",
                "dst_ref": f"h/dst-p/r/app@{digest}", "digest": digest, "tags": []}
        with patch.object(o, "_soft_run", side_effect=_soft), \
             patch.object(o, "_ar_copy_tool", return_value="gcrane"):
            o._copy_ar_image(item, None)

        short = digest.split(":", 1)[1][:12]
        assert calls == [
            f"gcrane cp h/src-p/r/app@{digest} h/dst-p/r/app:migrated-{short}"]

    def test_crane_unsupported_media_type_is_info_skip(self, temp_dir):
        from unittest.mock import patch
        cfg = _full_config(temp_dir, steps={"data_sync": {"enabled": True}})
        cfg["global"]["dry_run"] = False
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        o.dst_logger = logging.getLogger("test-dst")
        o.org_logger = logging.getLogger("test-org")

        def _soft(cmd, side, logger, impersonate_sa=None, timeout=300,
                  skip_on_dry_run=True):
            return 1, "", "unsupported media type application/vnd.oci.empty.v1+json"

        item = {"src_ref": "h/src-p/r/app@sha256:" + "ee" * 32,
                "dst_pkg": "h/dst-p/r/app",
                "dst_ref": "h/dst-p/r/app@sha256:" + "ee" * 32,
                "digest": "sha256:" + "ee" * 32, "tags": []}
        before = o.stats.skipped
        with patch.object(o, "_soft_run", side_effect=_soft), \
             patch.object(o, "_ar_copy_tool", return_value="gcrane"):
            o._copy_ar_image(item, None)
        assert o.stats.skipped == before + 1
        assert o.stats.failed == 0


class TestArCopyFlatParallel:
    """AR コピーは (project × repo × image) の flat 単位で並列化する。"""

    def _orch(self, temp_dir, jobs=8):
        cfg = _full_config(temp_dir, steps={"data_sync": {"enabled": True}})
        cfg["global"]["dry_run"] = False
        cfg["global"]["parallel_jobs"] = jobs
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        o.dst_logger = logging.getLogger("test-dst")
        o.org_logger = logging.getLogger("test-org")
        return o

    def test_work_from_all_projects_is_copied_in_one_flat_batch(self, temp_dir):
        from unittest.mock import patch
        o = self._orch(temp_dir)
        # host / svc-1 の 2 プロジェクトからそれぞれ item が出る
        def fake_collect(src, dst, ssa, dsa):
            return [{"src_ref": f"h/{src}/r/a@sha256:" + "aa" * 32,
                     "dst_pkg": f"h/{dst}/r/a", "dst_ref": "x",
                     "digest": "sha256:" + "aa" * 32, "tags": [], "dst_sa": dsa}]
        copied = []
        batches = []
        orig_pfe = o._parallel_for_each

        def spy_pfe(items, worker, prefix):
            batches.append((prefix, len(items)))
            return orig_pfe(items, worker, prefix)

        with patch.object(o, "_collect_ar_copy_work", side_effect=fake_collect), \
             patch.object(o, "_copy_ar_image",
                          side_effect=lambda it, sa: copied.append(it["src_ref"])), \
             patch.object(o, "_soft_run", return_value=(0, "", "")), \
             patch.object(o, "_parallel_for_each", side_effect=spy_pfe), \
             patch("shutil.which", return_value="/usr/bin/docker"):
            o.step_artifact_registry()

        # 列挙はプロジェクト並列、コピーは全プロジェクト合算の 1 バッチ
        assert ("ar-list", 2) in batches
        assert ("ar-copy", 2) in batches
        assert len(copied) == 2

    def test_existing_digests_are_filtered_by_one_repo_listing(self, temp_dir):
        from unittest.mock import patch
        o = self._orch(temp_dir)
        d_have = "sha256:" + "aa" * 32
        d_need = "sha256:" + "bb" * 32
        src_imgs = json.dumps([
            {"package": "asia-northeast1-docker.pkg.dev/src-p/r/app",
             "version": d_have, "tags": ["v1"]},
            {"package": "asia-northeast1-docker.pkg.dev/src-p/r/app",
             "version": d_need, "tags": ["v2"]},
        ])
        repos = json.dumps([{"name": "projects/src-p/locations/asia-northeast1"
                                     "/repositories/r", "format": "DOCKER"}])
        describes = []

        def _soft(cmd, side, logger, impersonate_sa=None, timeout=300, skip_on_dry_run=True):
            if cmd.startswith("gcloud artifacts repositories list"):
                return 0, repos, ""
            if "docker images list" in cmd and "/src-p/" in cmd:
                return 0, src_imgs, ""
            if "docker images list" in cmd and "/dst-p/" in cmd:
                return 0, d_have + "\n", ""   # dst には aa だけ存在
            describes.append(cmd)
            return 0, "", ""

        before = o.stats.skipped
        with patch.object(o, "_soft_run", side_effect=_soft), \
             patch.object(o, "_gcloud_exists", return_value=True):
            work = o._collect_ar_copy_work("src-p", "dst-p", None, None)

        assert [e["digest"] for e in work] == [d_need]
        assert o.stats.skipped == before + 1


# ============================================================
# 文字列参照 → terraform 参照 変換（SA / compute URL / 通知チャネル）
# ============================================================
class TestRewriteResourceRefs:
    def _setup(self, temp_dir):
        cfg = _full_config(temp_dir)
        cfg["global"]["dry_run"] = False
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        import logging as _l
        o.org_logger = _l.getLogger("test-org")
        o._customize_notes = []
        return o

    def _root(self, temp_dir):
        root = os.path.join(temp_dir, "active", "src-host")
        os.makedirs(root, exist_ok=True)
        return root

    def _write(self, root, fn, body):
        with open(os.path.join(root, fn), "w", encoding="utf-8") as f:
            f.write(body)

    def test_sa_email_becomes_reference_with_30char_project(self, temp_dir):
        # actAs 403 の本丸。project ID が上限 30 文字ちょうどでも一致すること
        o = self._setup(temp_dir)
        root = self._root(temp_dir)
        proj30 = "shingo-ar-standalone2026081400"
        assert len(proj30) == 30
        self._write(root, "sa.tf",
                    'resource "google_service_account" "eigo_teacher" {\n'
                    '  account_id = "eigo-teacher"\n'
                    f'  project    = "{proj30}"\n'
                    '}\n')
        self._write(root, "run.tf",
                    'resource "google_cloud_run_v2_service" "svc" {\n'
                    '  template {\n'
                    f'    service_account = "eigo-teacher@{proj30}.iam.gserviceaccount.com"\n'
                    '  }\n'
                    '}\n')
        o._rewrite_resource_refs_in_active(os.path.join(temp_dir, "active"))
        out = open(os.path.join(root, "run.tf"), encoding="utf-8").read()
        assert "service_account = google_service_account.eigo_teacher.email" in out

    def test_default_compute_sa_is_left_alone(self, temp_dir):
        o = self._setup(temp_dir)
        root = self._root(temp_dir)
        self._write(root, "run.tf",
                    'resource "google_cloud_run_v2_service" "svc" {\n'
                    '  service_account = "123456789-compute@developer.gserviceaccount.com"\n'
                    '}\n')
        o._rewrite_resource_refs_in_active(os.path.join(temp_dir, "active"))
        out = open(os.path.join(root, "run.tf"), encoding="utf-8").read()
        assert '"123456789-compute@developer.gserviceaccount.com"' in out

    def test_security_policy_url_becomes_self_link(self, temp_dir):
        o = self._setup(temp_dir)
        root = self._root(temp_dir)
        self._write(root, "policy.tf",
                    'resource "google_compute_security_policy" "pol" {\n'
                    '  name    = "my-policy"\n'
                    '  project = "dst-host"\n'
                    '}\n')
        self._write(root, "bes.tf",
                    'resource "google_compute_backend_service" "b" {\n'
                    '  name = "b"\n'
                    '  security_policy = "https://www.googleapis.com/compute/beta/'
                    'projects/dst-host/global/securityPolicies/my-policy"\n'
                    '}\n')
        o._rewrite_resource_refs_in_active(os.path.join(temp_dir, "active"))
        out = open(os.path.join(root, "bes.tf"), encoding="utf-8").read()
        assert "security_policy = google_compute_security_policy.pol.self_link" in out

    def test_manually_created_cert_url_is_left_as_string(self, temp_dir):
        # 証明書が dst に手動作成済みなら proxy は残り、URL 文字列のまま適用される
        from unittest.mock import patch
        o = self._setup(temp_dir)
        root = self._root(temp_dir)
        self._write(root, "proxy.tf",
                    'resource "google_compute_target_https_proxy" "p" {\n'
                    '  name = "p"\n  project = "dst-host"\n'
                    '  ssl_certificates = ["https://www.googleapis.com/compute/v1/'
                    'projects/dst-host/global/sslCertificates/manual-cert"]\n'
                    '}\n')
        with patch.object(o, "_gcloud_exists", return_value=True):
            o._rewrite_resource_refs_in_active(os.path.join(temp_dir, "active"))
        out = open(os.path.join(root, "proxy.tf"), encoding="utf-8").read()
        assert "sslCertificates/manual-cert" in out

    def test_missing_cert_holds_proxy_and_forwarding_rule(self, temp_dir):
        # 未作成の証明書に依存する proxy と、その proxy を参照する FR を保留する
        from unittest.mock import patch
        o = self._setup(temp_dir)
        root = self._root(temp_dir)
        self._write(root, "proxy.tf",
                    'resource "google_compute_target_https_proxy" "p" {\n'
                    '  name = "notify-api-proxy"\n  project = "dst-host"\n'
                    '  ssl_certificates = ["https://www.googleapis.com/compute/v1/'
                    'projects/dst-host/global/sslCertificates/notify-api"]\n'
                    '}\n')
        self._write(root, "fr.tf",
                    'resource "google_compute_global_forwarding_rule" "fr" {\n'
                    '  name   = "notify-api-fr"\n  project = "dst-host"\n'
                    '  target = "https://www.googleapis.com/compute/v1/'
                    'projects/dst-host/global/targetHttpsProxies/notify-api-proxy"\n'
                    '}\n')
        self._write(root, "keep.tf",
                    'resource "google_compute_url_map" "m" {\n'
                    '  name = "m"\n  project = "dst-host"\n'
                    '}\n')
        with patch.object(o, "_gcloud_exists", return_value=False):
            o._rewrite_resource_refs_in_active(os.path.join(temp_dir, "active"))
        left = sorted(os.listdir(root))
        assert "proxy.tf" not in left and "fr.tf" not in left
        assert "keep.tf" in left
        kinds = [n["kind"] for n in o._customize_notes]
        assert kinds.count("lb_blocked_on_cert") == 2

    def test_in_root_cert_definition_keeps_proxy(self, temp_dir):
        # Google-managed 等、同ルートで作られる証明書なら保留しない
        from unittest.mock import patch
        o = self._setup(temp_dir)
        root = self._root(temp_dir)
        self._write(root, "cert.tf",
                    'resource "google_compute_ssl_certificate" "c" {\n'
                    '  name = "in-root-cert"\n  project = "dst-host"\n'
                    '}\n')
        self._write(root, "proxy.tf",
                    'resource "google_compute_target_https_proxy" "p" {\n'
                    '  name = "p"\n  project = "dst-host"\n'
                    '  ssl_certificates = ["https://www.googleapis.com/compute/v1/'
                    'projects/dst-host/global/sslCertificates/in-root-cert"]\n'
                    '}\n')
        with patch.object(o, "_gcloud_exists", return_value=False) as ge:
            o._rewrite_resource_refs_in_active(os.path.join(temp_dir, "active"))
        assert os.path.exists(os.path.join(root, "proxy.tf"))
        assert ge.call_count == 0

    def test_notification_channel_resolved_via_import_comment(self, temp_dir):
        o = self._setup(temp_dir)
        root = self._root(temp_dir)
        self._write(root, "ch.tf",
                    'resource "google_monitoring_notification_channel" "email" {\n'
                    '  display_name = "email"\n  project = "dst-host"\n'
                    '}\n'
                    '# terraform import google_monitoring_notification_channel.email '
                    'dst-host projects/dst-host/notificationChannels/9027756872843776763\n')
        self._write(root, "alert.tf",
                    'resource "google_monitoring_alert_policy" "a" {\n'
                    '  notification_channels = ["projects/dst-host/notificationChannels/9027756872843776763"]\n'
                    '}\n')
        o._rewrite_resource_refs_in_active(os.path.join(temp_dir, "active"))
        out = open(os.path.join(root, "alert.tf"), encoding="utf-8").read()
        assert ("notification_channels = [google_monitoring_notification_channel"
                ".email.name]") in out

    def test_unresolved_channel_line_is_stripped_with_note(self, temp_dir):
        o = self._setup(temp_dir)
        root = self._root(temp_dir)
        self._write(root, "alert.tf",
                    'resource "google_monitoring_alert_policy" "a" {\n'
                    '  project = "dst-host"\n'
                    '  notification_channels = ["projects/dst-host/notificationChannels/111"]\n'
                    '}\n')
        o._rewrite_resource_refs_in_active(os.path.join(temp_dir, "active"))
        out = open(os.path.join(root, "alert.tf"), encoding="utf-8").read()
        assert "notification_channels" not in out
        assert any(n["kind"] == "alert_notification_channels"
                   for n in o._customize_notes)


class TestForeignProjectResourceDropped:
    def test_unmapped_project_file_is_skipped(self, temp_dir):
        # bulk-export の越境出力（mapping 外プロジェクト）は apply させない
        cfg = _full_config(temp_dir)
        cfg["global"]["dry_run"] = False
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        raw = os.path.join(temp_dir, "raw", "src-host")
        active = os.path.join(temp_dir, "active")
        os.makedirs(raw)
        with open(os.path.join(raw, "foreign.tf"), "w", encoding="utf-8") as f:
            f.write('resource "google_monitoring_notification_channel" "email" {\n'
                    '  project = "unrelated-foreign-proj"\n'
                    '}\n')
        with open(os.path.join(raw, "own.tf"), "w", encoding="utf-8") as f:
            f.write('resource "google_pubsub_topic" "t" {\n'
                    '  name    = "t1"\n'
                    '  project = "src-host"\n'
                    '}\n')
        o.customize_hcl(os.path.join(temp_dir, "raw"), active)
        files = os.listdir(os.path.join(active, "src-host"))
        assert not any("foreign" in f for f in files)
        assert any("own" in f for f in files)


# ============================================================
# DIFF.md の GKE 手動アドバイス（Backup for GKE 前提）
# ============================================================
class TestGkeBackupRestoreNote:
    def test_note_row_is_action_with_both_sides(self):
        kind, target, why, how = customize_note_row({
            "kind": "gke_backup_restore", "resource": "my-ec-cluster",
            "project": "dst-standalone", "src_dir": "my-argolis"})
        assert kind == "要対応"
        assert "my-ec-cluster" in target and "dst-standalone" in target
        # src 側 backup と dst 側 restore の両方を案内する
        assert "backup-plans create" in how and "--project=my-argolis" in how
        assert "restore" in how and "dst-standalone" in how
        assert "gkebackup.googleapis.com" in how
        # クラスタ外リソース（SSL 証明書等）は対象外である旨も明記
        assert "対象外" in how

    def test_customize_emits_note_per_cluster(self, temp_dir):
        cfg = _full_config(temp_dir)
        cfg["global"]["dry_run"] = False
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        raw = os.path.join(temp_dir, "raw", "src-host")
        os.makedirs(raw)
        with open(os.path.join(raw, "cluster.tf"), "w", encoding="utf-8") as f:
            f.write('resource "google_container_cluster" "c" {\n'
                    '  name     = "my-cluster"\n'
                    '  project  = "src-host"\n'
                    '  location = "asia-northeast1"\n'
                    '}\n')
        with open(os.path.join(raw, "bucket.tf"), "w", encoding="utf-8") as f:
            f.write('resource "google_storage_bucket" "b" {\n'
                    '  name    = "some-bucket"\n'
                    '  project = "src-host"\n'
                    '}\n')
        o.customize_hcl(os.path.join(temp_dir, "raw"), os.path.join(temp_dir, "active"))
        notes = [n for n in o._customize_notes if n["kind"] == "gke_backup_restore"]
        assert len(notes) == 1
        assert notes[0]["resource"] == "my-cluster"
        assert notes[0]["project"] == "dst-host"
        assert notes[0]["src_dir"] == "src-host"

    def test_gke_derived_reference_mentions_backup_for_gke(self):
        got = classify_missing_asset({
            "asset_type": "compute.googleapis.com/TargetPool",
            "short_name": "a0cb2a7138c8b475e8f1de09dc442b6a",
            "full_name": "//x", "coverage_step": "terraform_apply", "reason": ""})
        assert got["level"] == "reference"
        assert "Backup for GKE" in got["why"] or "Backup for GKE" in got["how"]


class TestFixProviderCompatNodeVersion:
    def _setup(self, temp_dir):
        cfg = _full_config(temp_dir)
        cfg["global"]["dry_run"] = False
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        import logging as _l
        o.org_logger = _l.getLogger("test-org")
        o._customize_notes = []
        return o

    def test_node_version_without_min_master_is_removed(self, temp_dir):
        o = self._setup(temp_dir)
        content = ('resource "google_container_cluster" "c" {\n'
                   '  name         = "c"\n'
                   '  node_version = "1.35.6-gke.1258000"\n'
                   '}\n')
        out = o._fix_provider_compat(content, "x.tf")
        assert "node_version" not in out

    def test_mismatched_pair_drops_node_version_keeps_master(self, temp_dir):
        o = self._setup(temp_dir)
        content = ('resource "google_container_cluster" "c" {\n'
                   '  min_master_version = "1.36.0-gke.100"\n'
                   '  node_version       = "1.35.6-gke.1258000"\n'
                   '}\n')
        out = o._fix_provider_compat(content, "x.tf")
        assert "node_version" not in out
        assert 'min_master_version = "1.36.0-gke.100"' in out

    def test_equivalent_pair_is_untouched(self, temp_dir):
        o = self._setup(temp_dir)
        content = ('resource "google_container_cluster" "c" {\n'
                   '  min_master_version = "1.35.6-gke.1258000"\n'
                   '  node_version       = "1.35.6-gke.1258000"\n'
                   '}\n')
        out = o._fix_provider_compat(content, "x.tf")
        assert 'node_version       = "1.35.6-gke.1258000"' in out


class TestSkipOnRunOverride:
    """--no-skip-on-run / --skip-on-run（make run SKIP_ON_RUN=0/1）の実行時上書き。"""

    def _orch(self, temp_dir, override):
        cfg = _full_config(temp_dir, steps={
            "bulk_export": {"enabled": True,
                            "output_dir": os.path.join(temp_dir, "terraform"),
                            "skip_on_run": True}})
        cfg["global"]["dry_run"] = False
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path, skip_on_run_override=override)
        o.load_config()
        o.dst_logger = logging.getLogger("test-dst")
        o.org_logger = logging.getLogger("test-org")
        return o

    def _make_active(self, temp_dir, o):
        # skip 再利用パスに乗る最低条件: active/<src>/*.tf + 現 dst のマーカー
        d = os.path.join(temp_dir, "terraform", "active", "src-host")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "x.tf"), "w", encoding="utf-8") as f:
            f.write('resource "google_pubsub_topic" "t" {\n  name = "t"\n}\n')
        with open(os.path.join(d, ".dst_project"), "w", encoding="utf-8") as f:
            f.write("dst-host")
        d2 = os.path.join(temp_dir, "terraform", "active", "src-svc-1")
        os.makedirs(d2, exist_ok=True)
        with open(os.path.join(d2, "y.tf"), "w", encoding="utf-8") as f:
            f.write('resource "google_pubsub_topic" "t" {\n  name = "t"\n}\n')
        with open(os.path.join(d2, ".dst_project"), "w", encoding="utf-8") as f:
            f.write("dst-svc-1")

    def test_override_false_forces_reexport(self, temp_dir):
        from unittest.mock import patch
        o = self._orch(temp_dir, override=False)
        self._make_active(temp_dir, o)
        with patch.object(o, "run_command", return_value="") as rc, \
             patch.object(o, "_build_project_number_map"), \
             patch.object(o, "customize_hcl") as cz:
            o.step_bulk_export()
        # config は skip_on_run: true だが、上書きにより bulk-export が実行される
        assert any("bulk-export" in str(c) for c in rc.call_args_list)
        assert cz.call_count == 1

    def test_config_true_without_override_skips(self, temp_dir):
        from unittest.mock import patch
        o = self._orch(temp_dir, override=None)
        self._make_active(temp_dir, o)
        with patch.object(o, "run_command", return_value="") as rc, \
             patch.object(o, "_build_project_number_map"), \
             patch.object(o, "customize_hcl") as cz:
            o.step_bulk_export()
        assert not any("bulk-export" in str(c) for c in rc.call_args_list)
        assert cz.call_count == 0


# ============================================================
# Cloud Run 公開設定（allUsers → run.invoker）の複製
# ============================================================
class TestRunInvokerPureFunctions:
    def test_parse_services_list(self):
        text = json.dumps([
            {"metadata": {"name": "www-1",
                          "labels": {"cloud.googleapis.com/location": "us-central1"}}},
            {"metadata": {"name": "api",
                          "labels": {"cloud.googleapis.com/location": "asia-northeast1"}}},
            {"metadata": {"name": "no-region", "labels": {}}},
        ])
        assert parse_run_services_list(text) == [
            ("api", "asia-northeast1"), ("www-1", "us-central1")]
        assert parse_run_services_list("broken") == []

    def test_public_invoker_members(self):
        pol = json.dumps({"bindings": [
            {"role": "roles/run.invoker",
             "members": ["allUsers", "serviceAccount:x@p.iam.gserviceaccount.com"]},
            {"role": "roles/run.admin", "members": ["allAuthenticatedUsers"]},
        ]})
        # 公開 2 種のみ。SA 個別付与や invoker 以外のロールは対象外
        assert run_service_public_invoker_members(pol) == ["allUsers"]
        assert run_service_public_invoker_members(json.dumps({
            "bindings": [{"role": "roles/run.invoker",
                          "members": ["allAuthenticatedUsers"]}]})) == \
            ["allAuthenticatedUsers"]

    def test_conditional_binding_is_ignored(self):
        pol = json.dumps({"bindings": [
            {"role": "roles/run.invoker", "members": ["allUsers"],
             "condition": {"expression": "request.time < x"}},
        ]})
        assert run_service_public_invoker_members(pol) == []


class TestSyncRunServiceInvokers:
    def _orch(self, temp_dir):
        cfg = _full_config(temp_dir)
        cfg["global"]["dry_run"] = False
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        o.dst_logger = logging.getLogger("test-dst")
        o.org_logger = logging.getLogger("test-org")
        return o

    def _soft_factory(self, dst_service_exists, issued):
        svc_list = json.dumps([{"metadata": {
            "name": "any-method-api",
            "labels": {"cloud.googleapis.com/location": "asia-northeast1"}}}])
        policy = json.dumps({"bindings": [
            {"role": "roles/run.invoker", "members": ["allUsers"]}]})

        def _soft(cmd, side, logger, impersonate_sa=None, timeout=300,
                  skip_on_dry_run=True):
            issued.append(cmd)
            if cmd.startswith("gcloud run services list"):
                return 0, svc_list, ""
            if cmd.startswith("gcloud run services get-iam-policy"):
                return 0, policy, ""
            if cmd.startswith("gcloud run services describe"):
                return (0, "any-method-api", "") if dst_service_exists \
                    else (1, "", "not found")
            return 0, "", ""
        return _soft

    def test_public_binding_is_replicated_when_dst_exists(self, temp_dir):
        from unittest.mock import patch
        o = self._orch(temp_dir)
        issued = []
        with patch.object(o, "_soft_run",
                          side_effect=self._soft_factory(True, issued)):
            o._sync_run_service_invokers()
        adds = [c for c in issued
                if c.startswith("gcloud run services add-iam-policy-binding")]
        assert adds and "--member=allUsers" in adds[0]
        assert "--role=roles/run.invoker" in adds[0]
        assert o.stats.failed == 0

    def test_missing_dst_service_is_skipped_with_warning(self, temp_dir):
        from unittest.mock import patch
        o = self._orch(temp_dir)
        issued = []
        with patch.object(o, "_soft_run",
                          side_effect=self._soft_factory(False, issued)):
            o._sync_run_service_invokers()
        assert not any(c.startswith("gcloud run services add-iam-policy-binding")
                       for c in issued)
        assert o.stats.failed == 0

    def test_guards(self):
        assert is_src_read_only(
            "gcloud run services get-iam-policy x --region=r --project=p --format=json --quiet")
        assert is_src_read_only("gcloud run services list --project=p --format=json --quiet")
        assert not is_src_read_only(
            "gcloud run services add-iam-policy-binding x --region=r --project=p "
            "--member=allUsers --role=roles/run.invoker")
        for c in ("gcloud run services list", "gcloud run services get-iam-policy x",
                  "gcloud run services describe x",
                  "gcloud run services add-iam-policy-binding x"):
            assert is_known_mock_command(c), c

    def test_run_service_asset_types_registered(self):
        assert _ASSET_COVERAGE["run.googleapis.com/Service"] == "terraform_apply"
        assert "run.googleapis.com/Revision" in _ASSET_COVERAGE
        assert "google_cloud_run_v2_service" in \
            _CAI_TO_TF_RESOURCE["run.googleapis.com/Service"]


# ============================================================
# 移行範囲の選択（steps.bulk_export.resource_types）
# ============================================================
class TestResourceTypeFilterPure:
    def test_exclude_patterns(self):
        assert not tf_type_kept("google_cloud_run_v2_service", [], ["google_cloud_run_*"])
        assert tf_type_kept("google_compute_instance", [], ["google_cloud_run_*"])

    def test_include_whitelist(self):
        inc = ["google_compute_*", "google_service_account"]
        assert tf_type_kept("google_compute_network", inc, [])
        assert tf_type_kept("google_service_account", inc, [])
        assert not tf_type_kept("google_cloud_run_v2_service", inc, [])

    def test_exclude_wins_over_include(self):
        assert not tf_type_kept("google_compute_snapshot",
                                ["google_compute_*"], ["google_compute_snapshot"])

    def test_file_reason(self):
        # 全型除外 → 理由。1 つでも残れば None（安全側 = コピー）
        assert resource_type_filter_reason(
            ["google_cloud_run_v2_service"], [], ["google_cloud_run_*"])
        assert resource_type_filter_reason(
            ["google_cloud_run_v2_service", "google_compute_network"],
            [], ["google_cloud_run_*"]) is None
        # resource ブロック無し / フィルタ未指定は対象外
        assert resource_type_filter_reason([], [], ["google_*"]) is None
        assert resource_type_filter_reason(["google_compute_network"], [], []) is None


class TestResourceTypeFilterInCustomize:
    def _run(self, temp_dir, resource_types):
        cfg = _full_config(temp_dir, steps={
            "bulk_export": {"enabled": True, "resource_types": resource_types}})
        cfg["global"]["dry_run"] = False
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        raw = os.path.join(temp_dir, "raw", "src-host")
        os.makedirs(raw, exist_ok=True)
        files = {
            "run.tf": 'resource "google_cloud_run_v2_service" "svc" {\n'
                      '  name = "svc"\n  project = "src-host"\n}\n',
            "net.tf": 'resource "google_compute_network" "n" {\n'
                      '  name = "vpc0"\n  project = "src-host"\n}\n',
            "cluster.tf": 'resource "google_container_cluster" "c" {\n'
                          '  name = "c1"\n  project = "src-host"\n'
                          '  location = "asia-northeast1"\n}\n',
        }
        for fn, body in files.items():
            with open(os.path.join(raw, fn), "w", encoding="utf-8") as f:
                f.write(body)
        o.customize_hcl(os.path.join(temp_dir, "raw"), os.path.join(temp_dir, "active"))
        return o, sorted(os.listdir(os.path.join(temp_dir, "active", "src-host")))

    def test_exclude_drops_only_matching(self, temp_dir):
        o, files = self._run(temp_dir, {"exclude": ["google_cloud_run_*",
                                                    "google_container_*"]})
        assert "net.tf" in files
        assert "run.tf" not in files and "cluster.tf" not in files
        # 除外したクラスタには GKE 移行手順 note を出さない
        assert not any(n["kind"] == "gke_backup_restore" for n in o._customize_notes)

    def test_include_whitelist_keeps_only_listed(self, temp_dir):
        _o, files = self._run(temp_dir, {"include": ["google_compute_*"]})
        assert "net.tf" in files
        assert "run.tf" not in files and "cluster.tf" not in files

    def test_no_filter_keeps_all(self, temp_dir):
        o, files = self._run(temp_dir, {})
        assert {"net.tf", "run.tf", "cluster.tf"} <= set(files)
        assert any(n["kind"] == "gke_backup_restore" for n in o._customize_notes)


class TestResourceTypeFilterInDiff:
    def _item(self, atype, short):
        return {"asset_type": atype, "short_name": short, "full_name": "//x",
                "coverage_step": "terraform_apply", "reason": "", "state": "",
                "ip_address": ""}

    def test_excluded_type_is_reference_p3(self):
        got = classify_missing_asset(
            self._item("run.googleapis.com/Service", "www-1"),
            rt_exclude=["google_cloud_run_*"])
        assert got["level"] == "reference" and got["priority"] == 3
        assert "resource_types" in got["why"]

    def test_not_excluded_type_stays_action(self):
        got = classify_missing_asset(
            self._item("run.googleapis.com/Service", "www-1"),
            rt_exclude=["google_monitoring_*"])
        assert got["level"] == "action"

    def test_include_mode_marks_out_of_list_as_reference(self):
        got = classify_missing_asset(
            self._item("run.googleapis.com/Service", "www-1"),
            rt_include=["google_compute_*"])
        assert got["level"] == "reference" and got["priority"] == 3


class TestResourceTypeFilterValidation:
    def _cfg(self, temp_dir, resource_types):
        cfg = _full_config(temp_dir, steps={
            "bulk_export": {"enabled": True, "resource_types": resource_types}})
        return cfg

    def test_valid_patterns_pass(self, temp_dir):
        errs = validate_steps_config(self._cfg(temp_dir, {
            "include": ["google_compute_*"], "exclude": ["google_cloud_run_*"]}))
        assert not [e for e in errs if "resource_types" in e]

    def test_non_google_pattern_is_rejected(self, temp_dir):
        # typo（google_ 無し）は include が何にも一致せず全除外になる静かな事故
        errs = validate_steps_config(self._cfg(temp_dir, {"include": ["compute_*"]}))
        assert any("resource_types.include" in e for e in errs)

    def test_non_list_is_rejected(self, temp_dir):
        errs = validate_steps_config(self._cfg(temp_dir, {"exclude": "google_x"}))
        assert any("リスト" in e for e in errs)


class TestGkeNodePoolWiring:
    """別リソース node_pool 運用のクラスタに必要な補完と依存関係。"""

    def _setup(self, temp_dir):
        cfg = _full_config(temp_dir)
        cfg["global"]["dry_run"] = False
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        import logging as _l
        o.org_logger = _l.getLogger("test-org")
        o._customize_notes = []
        return o

    def test_initial_node_count_and_remove_default_are_added(self, temp_dir):
        o = self._setup(temp_dir)
        content = ('resource "google_container_cluster" "c" {\n'
                   '  name     = "c1"\n'
                   '  location = "asia-northeast1"\n'
                   '}\n')
        out = o._fix_provider_compat(content, "x.tf")
        assert "initial_node_count = 1" in out
        assert "remove_default_node_pool = true" in out

    def test_autopilot_cluster_is_untouched(self, temp_dir):
        # remove_default_node_pool は enable_autopilot と ConflictsWith
        o = self._setup(temp_dir)
        content = ('resource "google_container_cluster" "c" {\n'
                   '  name             = "c1"\n'
                   '  enable_autopilot = true\n'
                   '}\n')
        out = o._fix_provider_compat(content, "x.tf")
        assert "remove_default_node_pool" not in out
        assert "initial_node_count" not in out

    def test_inline_node_pool_cluster_is_untouched(self, temp_dir):
        o = self._setup(temp_dir)
        content = ('resource "google_container_cluster" "c" {\n'
                   '  name = "c1"\n'
                   '  node_pool {\n'
                   '    name = "np"\n'
                   '  }\n'
                   '}\n')
        out = o._fix_provider_compat(content, "x.tf")
        assert "remove_default_node_pool" not in out

    def test_exported_initial_node_count_is_respected(self, temp_dir):
        o = self._setup(temp_dir)
        content = ('resource "google_container_cluster" "c" {\n'
                   '  name               = "c1"\n'
                   '  initial_node_count = 5\n'
                   '}\n')
        out = o._fix_provider_compat(content, "x.tf")
        assert "initial_node_count = 5" in out
        assert "initial_node_count = 1" not in out

    def test_node_pool_version_is_removed(self, temp_dir):
        o = self._setup(temp_dir)
        content = ('resource "google_container_node_pool" "np" {\n'
                   '  cluster = "c1"\n'
                   '  version = "1.35.6-gke.1258000"\n'
                   '}\n')
        out = o._fix_provider_compat(content, "x.tf")
        assert "version" not in out
        assert 'cluster = "c1"' in out

    def test_node_pool_cluster_becomes_reference(self, temp_dir):
        o = self._setup(temp_dir)
        root = os.path.join(temp_dir, "active", "src-host")
        os.makedirs(root)
        with open(os.path.join(root, "cluster.tf"), "w", encoding="utf-8") as f:
            f.write('resource "google_container_cluster" "my_ec_cluster" {\n'
                    '  name     = "my-ec-cluster"\n'
                    '  project  = "dst-host"\n'
                    '}\n')
        with open(os.path.join(root, "np.tf"), "w", encoding="utf-8") as f:
            f.write('resource "google_container_node_pool" "default_pool" {\n'
                    '  cluster            = "my-ec-cluster"\n'
                    '  initial_node_count = 3\n'
                    '  project            = "dst-host"\n'
                    '}\n')
        o._rewrite_resource_refs_in_active(os.path.join(temp_dir, "active"))
        out = open(os.path.join(root, "np.tf"), encoding="utf-8").read()
        assert "cluster            = google_container_cluster.my_ec_cluster.name" in out

    def test_unknown_cluster_name_is_left_as_string(self, temp_dir):
        o = self._setup(temp_dir)
        root = os.path.join(temp_dir, "active", "src-host")
        os.makedirs(root)
        with open(os.path.join(root, "np.tf"), "w", encoding="utf-8") as f:
            f.write('resource "google_container_node_pool" "p" {\n'
                    '  cluster = "not-in-this-root"\n'
                    '}\n')
        o._rewrite_resource_refs_in_active(os.path.join(temp_dir, "active"))
        out = open(os.path.join(root, "np.tf"), encoding="utf-8").read()
        assert 'cluster = "not-in-this-root"' in out


# ============================================================
# Backup for GKE 前提の補正（エージェント有効化 / 短縮パス参照）
# ============================================================
class TestGkeBackupAgentEnforced:
    def _setup(self, temp_dir):
        cfg = _full_config(temp_dir)
        cfg["global"]["dry_run"] = False
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        import logging as _l
        o.org_logger = _l.getLogger("test-org")
        o._customize_notes = []
        return o

    def test_disabled_agent_is_flipped_to_true(self, temp_dir):
        o = self._setup(temp_dir)
        content = ('resource "google_container_cluster" "c" {\n'
                   '  addons_config {\n'
                   '    gke_backup_agent_config {\n'
                   '      enabled = false\n'
                   '    }\n'
                   '  }\n'
                   '  name = "c1"\n'
                   '}\n')
        out = o._fix_provider_compat(content, "x.tf")
        assert "enabled = true" in out
        assert "enabled = false" not in out.split("gke_backup_agent_config")[1][:80]

    def test_missing_addons_config_gets_agent_block(self, temp_dir):
        o = self._setup(temp_dir)
        content = 'resource "google_container_cluster" "c" {\n  name = "c2"\n}\n'
        out = o._fix_provider_compat(content, "x.tf")
        assert "gke_backup_agent_config" in out and "enabled = true" in out

    def test_already_enabled_is_untouched(self, temp_dir):
        o = self._setup(temp_dir)
        content = ('resource "google_container_cluster" "c" {\n'
                   '  addons_config {\n'
                   '    gke_backup_agent_config {\n'
                   '      enabled = true\n'
                   '    }\n'
                   '  }\n'
                   '}\n')
        out = o._fix_provider_compat(content, "x.tf")
        assert out.count("gke_backup_agent_config") == 1

    def test_non_cluster_resource_is_untouched(self, temp_dir):
        o = self._setup(temp_dir)
        content = 'resource "google_compute_network" "n" {\n  name = "vpc"\n}\n'
        out = o._fix_provider_compat(content, "x.tf")
        assert "gke_backup_agent_config" not in out


class TestShortPathNetworkRefs:
    def _setup(self, temp_dir):
        cfg = _full_config(temp_dir)
        cfg["global"]["dry_run"] = False
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        import logging as _l
        o.org_logger = _l.getLogger("test-org")
        o._customize_notes = []
        return o

    def _root(self, temp_dir):
        root = os.path.join(temp_dir, "active", "src-host")
        os.makedirs(root, exist_ok=True)
        with open(os.path.join(root, "net.tf"), "w", encoding="utf-8") as f:
            f.write('resource "google_compute_network" "vpc_0" {\n'
                    '  name    = "vpc-0"\n  project = "dst-host"\n}\n')
        with open(os.path.join(root, "subnet.tf"), "w", encoding="utf-8") as f:
            f.write('resource "google_compute_subnetwork" "subnet_tokyo" {\n'
                    '  name    = "subnet-tokyo"\n  project = "dst-host"\n'
                    '  region  = "asia-northeast1"\n}\n')
        return root

    def test_short_paths_become_references(self, temp_dir):
        o = self._setup(temp_dir)
        root = self._root(temp_dir)
        with open(os.path.join(root, "cluster.tf"), "w", encoding="utf-8") as f:
            f.write('resource "google_container_cluster" "c" {\n'
                    '  name       = "c1"\n  project = "dst-host"\n'
                    '  network    = "projects/dst-host/global/networks/vpc-0"\n'
                    '  subnetwork = "projects/dst-host/regions/asia-northeast1'
                    '/subnetworks/subnet-tokyo"\n'
                    '}\n')
        o._rewrite_resource_refs_in_active(os.path.join(temp_dir, "active"))
        out = open(os.path.join(root, "cluster.tf"), encoding="utf-8").read()
        assert "network    = google_compute_network.vpc_0.self_link" in out
        assert ("subnetwork = google_compute_subnetwork.subnet_tokyo.self_link"
                in out)

    def test_cross_project_short_path_is_left_alone(self, temp_dir):
        # Shared VPC host（別ルート）の参照は解決できないので文字列のまま
        o = self._setup(temp_dir)
        root = self._root(temp_dir)
        with open(os.path.join(root, "cluster.tf"), "w", encoding="utf-8") as f:
            f.write('resource "google_container_cluster" "c" {\n'
                    '  network = "projects/other-host/global/networks/shared-vpc"\n'
                    '}\n')
        o._rewrite_resource_refs_in_active(os.path.join(temp_dir, "active"))
        out = open(os.path.join(root, "cluster.tf"), encoding="utf-8").read()
        assert '"projects/other-host/global/networks/shared-vpc"' in out


class TestGkeBackupNoteCrossProject:
    def test_note_covers_channels_and_iam(self):
        _kind, _target, why, how = customize_note_row({
            "kind": "gke_backup_restore", "resource": "my-ec-cluster",
            "project": "dst-standalone", "src_dir": "my-argolis"})
        # 同一プロジェクト制約と回避手段（channel）を明示する
        assert "同一プロジェクト" in why
        assert "backup-channels create" in how
        assert "restore-channels create" in how
        assert "gkebackup.crossProjectServiceAgent" in how
        assert "gkebackup.serviceAgent" in how
        assert "--update-addons=BackupRestore=ENABLED" in how


class TestNodePoolConfigInherited:
    """ノード VM はスナップショット復元しないが、ノードプールの構成は忠実に継承する。"""

    def _setup(self, temp_dir):
        cfg = _full_config(temp_dir)
        cfg["global"]["dry_run"] = False
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        import logging as _l
        o.org_logger = _l.getLogger("test-org")
        o._customize_notes = []
        return o

    SRC = (
        'resource "google_container_node_pool" "default_pool" {\n'
        '  cluster            = "c1"\n'
        '  initial_node_count = 3\n'
        '  location           = "asia-northeast1"\n'
        '  max_pods_per_node  = 110\n'
        '  name               = "default-pool"\n'
        '\n'
        '  network_config {\n'
        '    pod_ipv4_cidr_block = "10.4.0.0/17"\n'
        '    pod_range           = "gke-c1-pods-fd56f6a4"\n'
        '  }\n'
        '\n'
        '  node_config {\n'
        '    disk_size_gb = 100\n'
        '    disk_type    = "pd-standard"\n'
        '    image_type   = "COS_CONTAINERD"\n'
        '    machine_type = "e2-standard-2"\n'
        '  }\n'
        '\n'
        '  node_count     = 3\n'
        '  node_locations = ["asia-northeast1-a", "asia-northeast1-b"]\n'
        '\n'
        '  upgrade_settings {\n'
        '    max_surge = 1\n'
        '  }\n'
        '\n'
        '  version = "1.35.6-gke.1258000"\n'
        '}\n'
    )

    def test_count_and_hardware_config_are_preserved(self, temp_dir):
        o = self._setup(temp_dir)
        out = o._fix_provider_compat(self.SRC, "np.tf")
        # 台数とゾーン（initial_node_count は node_count と排他のため除去され、
        # 台数は node_count が引き継ぐ）
        assert "node_count     = 3" in out
        assert "initial_node_count" not in out
        assert 'node_locations = ["asia-northeast1-a", "asia-northeast1-b"]' in out
        # マシン構成
        assert 'machine_type = "e2-standard-2"' in out
        assert "disk_size_gb = 100" in out
        assert 'disk_type    = "pd-standard"' in out
        assert 'image_type   = "COS_CONTAINERD"' in out
        assert "max_pods_per_node  = 110" in out
        assert "max_surge = 1" in out

    def test_pod_cidr_removed_but_range_name_kept(self, temp_dir):
        o = self._setup(temp_dir)
        out = o._fix_provider_compat(self.SRC, "np.tf")
        assert "pod_ipv4_cidr_block" not in out
        assert 'pod_range           = "gke-c1-pods-fd56f6a4"' in out

    def test_create_pod_range_true_keeps_cidr(self, temp_dir):
        # 新規レンジを作る指定なら CIDR は有効なので残す
        o = self._setup(temp_dir)
        content = ('resource "google_container_node_pool" "p" {\n'
                   '  network_config {\n'
                   '    create_pod_range    = true\n'
                   '    pod_ipv4_cidr_block = "10.8.0.0/17"\n'
                   '    pod_range           = "new-range"\n'
                   '  }\n'
                   '}\n')
        out = o._fix_provider_compat(content, "np.tf")
        assert "pod_ipv4_cidr_block" in out


class TestNodePoolInitialCountConflict:
    def _setup(self, temp_dir):
        cfg = _full_config(temp_dir)
        cfg["global"]["dry_run"] = False
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        import logging as _l
        o.org_logger = _l.getLogger("test-org")
        o._customize_notes = []
        return o

    def test_both_set_drops_initial_keeps_node_count(self, temp_dir):
        o = self._setup(temp_dir)
        content = ('resource "google_container_node_pool" "p" {\n'
                   '  cluster            = "c1"\n'
                   '  initial_node_count = 3\n'
                   '  node_count     = 3\n'
                   '}\n')
        out = o._fix_provider_compat(content, "np.tf")
        assert "initial_node_count" not in out
        assert "node_count     = 3" in out

    def test_initial_only_is_kept(self, temp_dir):
        o = self._setup(temp_dir)
        content = ('resource "google_container_node_pool" "p" {\n'
                   '  initial_node_count = 2\n'
                   '}\n')
        out = o._fix_provider_compat(content, "np.tf")
        assert "initial_node_count = 2" in out

    def test_cluster_initial_node_count_is_not_touched(self, temp_dir):
        # クラスタ本体の initial_node_count（remove_default_node_pool 用）は残す
        o = self._setup(temp_dir)
        content = ('resource "google_container_cluster" "c" {\n'
                   '  name = "c1"\n'
                   '}\n')
        out = o._fix_provider_compat(content, "x.tf")
        assert "initial_node_count = 1" in out


class TestGloballyUniqueResourceSkips:
    """複製不能なグローバル一意リソース（public DNS ゾーン / ドット入りバケット）。"""

    def _setup(self, temp_dir):
        cfg = _full_config(temp_dir)
        cfg["global"]["dry_run"] = False
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        return o

    def test_public_dns_zone_is_skipped(self, temp_dir):
        o = self._setup(temp_dir)
        reason = o._skip_reason_for_file(
            'resource "google_dns_managed_zone" "z" {\n'
            '  dns_name   = "kawanos.demo.altostrat.com."\n'
            '  name       = "kawanos-demo"\n'
            '  visibility = "public"\n'
            '}\n')
        assert reason and "public DNS" in reason

    def test_zone_without_visibility_defaults_to_public_and_skipped(self, temp_dir):
        o = self._setup(temp_dir)
        assert o._skip_reason_for_file(
            'resource "google_dns_managed_zone" "z" {\n'
            '  dns_name = "example.com."\n  name = "z"\n}\n')

    def test_private_dns_zone_is_kept(self, temp_dir):
        o = self._setup(temp_dir)
        assert o._skip_reason_for_file(
            'resource "google_dns_managed_zone" "z" {\n'
            '  dns_name   = "internal.example."\n'
            '  visibility = "private"\n'
            '}\n') is None

    def test_dotted_bucket_is_skipped(self, temp_dir):
        o = self._setup(temp_dir)
        reason = o._skip_reason_for_file(
            'resource "google_storage_bucket" "b" {\n'
            '  name = "artifacts.my-argolis.appspot.com"\n'
            '}\n')
        assert reason and "ドット入り" in reason

    def test_normal_bucket_is_kept(self, temp_dir):
        o = self._setup(temp_dir)
        assert o._skip_reason_for_file(
            'resource "google_storage_bucket" "b" {\n'
            '  name = "normal-bucket-name"\n'
            '  uniform_bucket_level_access = true\n'
            '}\n') is None

    def test_note_rows(self):
        k1, t1, _w1, h1 = customize_note_row(
            {"kind": "dns_managed_zone", "resource": "kawanos-demo", "project": "dst-p"})
        assert k1 == "要対応" and "NS 委任" in h1
        k2, _t2, _w2, h2 = customize_note_row(
            {"kind": "dotted_bucket", "resource": "artifacts.x.appspot.com",
             "project": "dst-p"})
        assert k2 == "要対応" and "overrides" in h2


class TestInternalAddressHandling:
    """内部予約は元 IP 保持 / IN_USE は Step 5 に委ねて複製しない。"""

    def _setup(self, temp_dir, cai_body=None):
        cfg = _full_config(temp_dir, steps={
            "cai_scan": {"enabled": True,
                         "output_dir": os.path.join(temp_dir, "cai")},
            "gce_restore": {"enabled": True},
        })
        cfg["global"]["dry_run"] = False
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        os.makedirs(os.path.join(temp_dir, "cai"), exist_ok=True)
        if cai_body is not None:
            with open(os.path.join(temp_dir, "cai", "cai_resources_src-host.txt"),
                      "w", encoding="utf-8") as f:
                f.write(cai_body)
        o = MigrationOrchestrator(path)
        o.load_config()
        return o

    CAI = (
        "---\n"
        "assetType: compute.googleapis.com/Address\n"
        "name: //compute.googleapis.com/projects/src-host/regions/r/addresses/vm1-ip\n"
        "state: IN_USE\n"
        "additionalAttributes:\n"
        "  address: 10.100.1.11\n"
        "---\n"
        "assetType: compute.googleapis.com/Address\n"
        "name: //compute.googleapis.com/projects/src-host/regions/r/addresses/hold-ip\n"
        "state: RESERVED\n"
        "additionalAttributes:\n"
        "  address: 10.100.1.203\n"
    )

    def test_cai_in_use_internal_addresses(self, temp_dir):
        o = self._setup(temp_dir, self.CAI)
        got = cai_in_use_internal_addresses(
            os.path.join(temp_dir, "cai", "cai_resources_src-host.txt"))
        assert got == {"vm1-ip"}

    def _addr_tf(self, name, ip):
        return ('resource "google_compute_address" "a" {\n'
                f'  address      = "{ip}"\n'
                '  address_type = "INTERNAL"\n'
                f'  name         = "{name}"\n'
                '  project      = "src-host"\n'
                '  region       = "asia-northeast1"\n'
                '}\n')

    def test_in_use_address_is_skipped_reserved_kept_with_ip(self, temp_dir):
        o = self._setup(temp_dir, self.CAI)
        raw = os.path.join(temp_dir, "raw", "src-host")
        os.makedirs(raw)
        with open(os.path.join(raw, "vm1-ip.tf"), "w", encoding="utf-8") as f:
            f.write(self._addr_tf("vm1-ip", "10.100.1.11"))
        with open(os.path.join(raw, "hold-ip.tf"), "w", encoding="utf-8") as f:
            f.write(self._addr_tf("hold-ip", "10.100.1.203"))
        o.customize_hcl(os.path.join(temp_dir, "raw"), os.path.join(temp_dir, "active"))
        files = os.listdir(os.path.join(temp_dir, "active", "src-host"))
        assert not any("vm1-ip" in f for f in files), "IN_USE は複製しない"
        kept = [f for f in files if "hold-ip" in f]
        assert kept, "RESERVED は複製する"
        body = open(os.path.join(temp_dir, "active", "src-host", kept[0]),
                    encoding="utf-8").read()
        # 取り置きの意味を保つため元 IP を残す（自動採番にしない）
        assert 'address      = "10.100.1.203"' in body

    def test_external_address_ip_is_still_stripped(self, temp_dir):
        o = self._setup(temp_dir, self.CAI)
        content = ('resource "google_compute_address" "e" {\n'
                   '  address      = "34.84.204.112"\n'
                   '  address_type = "EXTERNAL"\n'
                   '  name         = "ext-ip"\n'
                   '}\n')
        out = o._strip_reserved_ip(content)
        assert "34.84.204.112" not in out

    def test_internal_global_address_psa_keeps_ip(self, temp_dir):
        o = self._setup(temp_dir, self.CAI)
        content = ('resource "google_compute_global_address" "psa" {\n'
                   '  address       = "10.50.0.0"\n'
                   '  address_type  = "INTERNAL"\n'
                   '  prefix_length = 16\n'
                   '}\n')
        out = o._strip_reserved_ip(content)
        assert '10.50.0.0' in out


# ============================================================
# DIFF.md 要対応の削減（誤検知・自動生成・二重計上の分類）
# ============================================================
class TestDiffNoiseReduction:
    def _item(self, atype, short, full=""):
        return {"asset_type": atype, "short_name": short,
                "full_name": full or f"//x/{short}", "location": "global",
                "coverage_step": "terraform_apply", "reason": "",
                "state": "", "ip_address": ""}

    def test_system_entry_groups_are_p3(self):
        got = classify_missing_asset(self._item(
            "dataplex.googleapis.com/EntryGroup", "@bigquery"))
        assert got["level"] == "reference" and got["priority"] == 3

    def test_user_entry_group_stays_action(self):
        got = classify_missing_asset(self._item(
            "dataplex.googleapis.com/EntryGroup", "my-catalog"))
        assert got["level"] == "action"

    def test_gke_psc_service_directory_is_p3(self):
        got = classify_missing_asset(self._item(
            "servicedirectory.googleapis.com/Service",
            "gk3-my-ec-cluster-fd56f6a4-b824f17b-pe"))
        assert got["level"] == "reference" and got["priority"] == 3
        got2 = classify_missing_asset(self._item(
            "servicedirectory.googleapis.com/Namespace", "goog-psc-default"))
        assert got2["level"] == "reference"
        got3 = classify_missing_asset(self._item(
            "servicedirectory.googleapis.com/Endpoint", "default",
            full="//servicedirectory/.../gk3-abc-pe/endpoints/default"))
        assert got3["level"] == "reference"

    def test_secret_version_folds_into_secret(self):
        got = classify_missing_asset(self._item(
            "secretmanager.googleapis.com/SecretVersion", "1"))
        assert got["level"] == "reference" and got["priority"] == 2
        got2 = classify_missing_asset(self._item(
            "secretmanager.googleapis.com/Secret", "DBPASS"))
        assert got2["level"] == "action"

    def test_numeric_zone_is_duplicate_representation(self):
        got = classify_missing_asset(self._item(
            "dns.googleapis.com/ManagedZone", "7616733230961605059"))
        assert got["level"] == "reference"
        got2 = classify_missing_asset(self._item(
            "dns.googleapis.com/ManagedZone", "kawanos-demo"))
        assert got2["level"] == "action"

    def test_notification_channel_is_reference(self):
        got = classify_missing_asset(self._item(
            "monitoring.googleapis.com/NotificationChannel", "9027756872843776763"))
        assert got["level"] == "reference"

    def test_gen2_function_folds_into_run_service(self):
        got = classify_missing_asset(
            self._item("cloudfunctions.googleapis.com/Function", "www-1"),
            run_service_names={"www-1", "test-1"})
        assert got["level"] == "reference" and got["priority"] == 1
        got2 = classify_missing_asset(
            self._item("cloudfunctions.googleapis.com/Function", "function-1"),
            run_service_names={"www-1"})
        assert got2["level"] == "action"

    def test_gke_internal_range_is_p3(self):
        got = classify_missing_asset(self._item(
            "networkconnectivity.googleapis.com/InternalRange",
            "gke-my-ec-cluster-pods-fd56f6a4"))
        assert got["level"] == "reference" and got["priority"] == 3


class TestParseTfResourcesIdFallbacks:
    def test_repository_id_is_used_when_no_name(self, temp_dir):
        with open(os.path.join(temp_dir, "ar.tf"), "w", encoding="utf-8") as f:
            f.write('resource "google_artifact_registry_repository" '
                    '"cloud_run_source_deploy" {\n'
                    '  repository_id = "cloud-run-source-deploy"\n'
                    '  location      = "asia-northeast1"\n'
                    '}\n')
        got = parse_tf_resources(temp_dir)
        assert "cloud-run-source-deploy" in \
            got["google_artifact_registry_repository"]

    def test_label_fallback_registers_hyphen_alias(self, temp_dir):
        with open(os.path.join(temp_dir, "x.tf"), "w", encoding="utf-8") as f:
            f.write('resource "google_dataform_repository" "my_repo" {\n'
                    '  display_name = "x"\n'
                    '}\n')
        got = parse_tf_resources(temp_dir)
        names = got["google_dataform_repository"]
        assert "my_repo" in names and "my-repo" in names


class TestCheckDstProjectsExist:
    """dst 未作成のまま 30 分走らせない fail-fast。"""

    def _orch(self, temp_dir):
        cfg = _full_config(temp_dir)
        cfg["global"]["dry_run"] = False
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        o.dst_logger = logging.getLogger("test-dst")
        o.org_logger = logging.getLogger("test-org")
        return o

    def test_missing_project_exits_before_any_write(self, temp_dir):
        from unittest.mock import patch, MagicMock
        o = self._orch(temp_dir)
        res = MagicMock(returncode=1, stdout="", stderr="ERROR: not found")
        with patch("subprocess.run", return_value=res), \
             pytest.raises(SystemExit):
            o.check_dst_projects_exist()

    def test_active_projects_pass(self, temp_dir):
        from unittest.mock import patch, MagicMock
        o = self._orch(temp_dir)
        res = MagicMock(returncode=0, stdout="ACTIVE\n", stderr="")
        with patch("subprocess.run", return_value=res):
            o.check_dst_projects_exist()   # 例外なし

    def test_delete_requested_state_fails(self, temp_dir):
        from unittest.mock import patch, MagicMock
        o = self._orch(temp_dir)
        res = MagicMock(returncode=0, stdout="DELETE_REQUESTED\n", stderr="")
        with patch("subprocess.run", return_value=res), \
             pytest.raises(SystemExit):
            o.check_dst_projects_exist()

    def test_mock_mode_skips(self, temp_dir):
        from unittest.mock import patch
        cfg = _full_config(temp_dir)
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path, mock_override=True)
        o.load_config()
        o.org_logger = logging.getLogger("test-org")
        with patch("subprocess.run") as sr:
            o.check_dst_projects_exist()
        assert sr.call_count == 0


class TestRunLock:
    """make run / plan の多重起動ガード（state 相互破壊の防止）。"""

    def _orch(self, temp_dir):
        cfg = _full_config(temp_dir, steps={
            "bulk_export": {"output_dir": os.path.join(temp_dir, "terraform")}})
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        return o

    def test_second_instance_is_rejected(self, temp_dir):
        o1 = self._orch(temp_dir)
        o1._acquire_run_lock()
        o2 = self._orch(temp_dir)
        with pytest.raises(SystemExit):
            o2._acquire_run_lock()

    def test_lock_released_on_close(self, temp_dir):
        o1 = self._orch(temp_dir)
        o1._acquire_run_lock()
        o1._run_lock_file.close()   # プロセス終了相当
        o2 = self._orch(temp_dir)
        o2._acquire_run_lock()      # 取得できる（例外なし）

    def test_mock_uses_separate_lock_dir(self, temp_dir):
        o1 = self._orch(temp_dir)
        o1._acquire_run_lock()
        cfg = _full_config(temp_dir, steps={
            "bulk_export": {"output_dir": os.path.join(temp_dir, "terraform")}})
        path = os.path.join(temp_dir, "config2.yaml")
        _write_yaml(path, cfg)
        om = MigrationOrchestrator(path, mock_override=True)
        om.load_config()
        om._acquire_run_lock()      # terraform/mock 配下なので競合しない


# ============================================================
# bulk-export の timeout 対策（Kind 絞り込み / storage-path / 再試行）
# ============================================================
class TestBulkExportScaleOptions:
    def _orch(self, temp_dir, bulk_extra):
        bulk = {"enabled": True, "output_dir": os.path.join(temp_dir, "terraform")}
        bulk.update(bulk_extra)
        cfg = _full_config(temp_dir, steps={"bulk_export": bulk})
        cfg["global"]["dry_run"] = False
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        o.dst_logger = logging.getLogger("test-dst")
        o.org_logger = logging.getLogger("test-org")
        return o

    def _run(self, o):
        from unittest.mock import patch
        issued = []

        def _rc(cmd, **kw):
            issued.append((cmd, kw))
            return ""

        with patch.object(o, "run_command", side_effect=_rc), \
             patch.object(o, "_build_project_number_map"), \
             patch.object(o, "customize_hcl"):
            o.step_bulk_export()
        return [(c, k) for c, k in issued if "bulk-export" in c]

    def test_export_resource_types_passed_to_gcloud(self, temp_dir):
        o = self._orch(temp_dir, {"export_resource_types":
                                  ["ComputeInstance", "ComputeNetwork"]})
        got = self._run(o)
        assert got and "--resource-types=ComputeInstance,ComputeNetwork" in got[0][0]
        # 排他なので storage-path は付かない
        assert "--storage-path" not in got[0][0]

    def test_storage_path_passed_when_no_kind_filter(self, temp_dir):
        o = self._orch(temp_dir, {"storage_path": "gs://b/prefix"})
        got = self._run(o)
        assert got and "--storage-path=gs://b/prefix" in got[0][0]

    def test_kind_filter_wins_over_storage_path(self, temp_dir):
        o = self._orch(temp_dir, {"export_resource_types": ["ComputeInstance"],
                                  "storage_path": "gs://b/prefix"})
        got = self._run(o)
        assert "--resource-types=ComputeInstance" in got[0][0]
        assert "--storage-path" not in got[0][0]

    def test_retry_defaults_are_longer_and_fewer(self, temp_dir):
        o = self._orch(temp_dir, {})
        got = self._run(o)
        _cmd, kw = got[0]
        assert kw["retries"] == 2
        assert kw["retry_wait_seconds"] == 180

    def test_retry_settings_are_configurable(self, temp_dir):
        o = self._orch(temp_dir, {"retries": 1, "retry_wait_seconds": 600})
        _cmd, kw = self._run(o)[0]
        assert kw["retries"] == 1 and kw["retry_wait_seconds"] == 600

    def test_no_options_keeps_plain_command(self, temp_dir):
        o = self._orch(temp_dir, {})
        cmd = self._run(o)[0][0]
        assert "--resource-types" not in cmd and "--storage-path" not in cmd


class TestBulkExportScaleValidation:
    def _errs(self, temp_dir, bulk_extra):
        bulk = {"enabled": True}
        bulk.update(bulk_extra)
        return validate_steps_config(_full_config(temp_dir,
                                                  steps={"bulk_export": bulk}))

    def test_terraform_type_in_kind_list_is_rejected(self, temp_dir):
        errs = self._errs(temp_dir, {"export_resource_types": ["google_compute_instance"]})
        assert any("KRM Kind" in e for e in errs)

    def test_lowercase_kind_is_rejected(self, temp_dir):
        errs = self._errs(temp_dir, {"export_resource_types": ["computeInstance"]})
        assert any("KRM Kind" in e for e in errs)

    def test_valid_kinds_pass(self, temp_dir):
        errs = self._errs(temp_dir, {"export_resource_types":
                                     ["ComputeInstance", "ContainerCluster"]})
        assert not [e for e in errs if "export_resource_types" in e]

    def test_bad_storage_path_is_rejected(self, temp_dir):
        errs = self._errs(temp_dir, {"storage_path": "my-bucket/prefix"})
        assert any("gs://" in e for e in errs)

    def test_valid_storage_path_passes(self, temp_dir):
        errs = self._errs(temp_dir, {"storage_path": "gs://my-bucket/prefix"})
        assert not [e for e in errs if "storage_path" in e]


class TestRunCommandRetryWait:
    def test_custom_wait_is_used(self, temp_dir):
        from unittest.mock import patch, MagicMock
        cfg = _full_config(temp_dir)
        cfg["global"]["dry_run"] = False
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        o.org_logger = logging.getLogger("test-org")
        o.dst_logger = logging.getLogger("test-dst")
        slept = []
        fail = MagicMock(returncode=1, stdout="", stderr="boom")
        with patch("subprocess.run", return_value=fail), \
             patch("time.sleep", side_effect=lambda s: slept.append(s)):
            o.run_command("gcloud compute instances list --project=p", side="src",
                          logger=o.org_logger, allow_fail=True,
                          retries=2, retry_wait_seconds=120)
        assert slept == [120, 120]

    def test_default_backoff_unchanged(self, temp_dir):
        from unittest.mock import patch, MagicMock
        cfg = _full_config(temp_dir)
        cfg["global"]["dry_run"] = False
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        o.org_logger = logging.getLogger("test-org")
        o.dst_logger = logging.getLogger("test-dst")
        slept = []
        fail = MagicMock(returncode=1, stdout="", stderr="boom")
        with patch("subprocess.run", return_value=fail), \
             patch("time.sleep", side_effect=lambda s: slept.append(s)):
            o.run_command("gcloud compute instances list --project=p", side="src",
                          logger=o.org_logger, allow_fail=True, retries=2)
        assert slept == [5, 10]


class TestAutoKindExclusion:
    """export_resource_types: auto — Kind 自動判定で k8s を対象外にする。"""

    def test_parse_krm_kinds_filters_unsupported(self):
        text = json.dumps([
            {"GVK": {"Kind": "ComputeInstance"}, "SupportsBulkExport": True},
            {"GVK": {"Kind": "ComputeNetwork"}, "SupportsBulkExport": True},
            {"GVK": {"Kind": "IAMPolicy"}, "SupportsBulkExport": False},
            {"GVK": {"Kind": ""}, "SupportsBulkExport": True},
        ])
        assert parse_krm_kinds(text) == ["ComputeInstance", "ComputeNetwork"]

    def test_parse_krm_kinds_broken_input(self):
        assert parse_krm_kinds(None) == [] and parse_krm_kinds("nope") == []

    def _orch(self, temp_dir, bulk_extra):
        bulk = {"enabled": True, "output_dir": os.path.join(temp_dir, "terraform")}
        bulk.update(bulk_extra)
        cfg = _full_config(temp_dir, steps={"bulk_export": bulk})
        cfg["global"]["dry_run"] = False
        path = os.path.join(temp_dir, "config.yaml")
        _write_yaml(path, cfg)
        o = MigrationOrchestrator(path)
        o.load_config()
        o.dst_logger = logging.getLogger("test-dst")
        o.org_logger = logging.getLogger("test-org")
        return o

    def test_auto_queries_kinds_and_passes_them(self, temp_dir):
        from unittest.mock import patch
        o = self._orch(temp_dir, {"export_resource_types": "auto"})
        kinds_json = json.dumps([
            {"GVK": {"Kind": "ComputeInstance"}, "SupportsBulkExport": True},
            {"GVK": {"Kind": "ContainerCluster"}, "SupportsBulkExport": True},
        ])
        issued = []

        with patch.object(o, "_soft_run", return_value=(0, kinds_json, "")), \
             patch.object(o, "run_command",
                          side_effect=lambda cmd, **kw: issued.append(cmd) or ""), \
             patch.object(o, "_build_project_number_map"), \
             patch.object(o, "customize_hcl"):
            o.step_bulk_export()

        exports = [c for c in issued if "bulk-export" in c]
        assert exports
        assert "--resource-types=ComputeInstance,ContainerCluster" in exports[0]

    def test_auto_falls_back_when_listing_fails(self, temp_dir):
        from unittest.mock import patch
        o = self._orch(temp_dir, {"export_resource_types": "auto"})
        issued = []
        with patch.object(o, "_soft_run", return_value=(1, "", "ERROR: denied")), \
             patch.object(o, "run_command",
                          side_effect=lambda cmd, **kw: issued.append(cmd) or ""), \
             patch.object(o, "_build_project_number_map"), \
             patch.object(o, "customize_hcl"):
            o.step_bulk_export()
        exports = [c for c in issued if "bulk-export" in c]
        # 取得できなければ絞り込みなしで続行（移行範囲は狭めない安全側）
        assert exports and "--resource-types" not in exports[0]

    def test_auto_is_accepted_by_validation(self, temp_dir):
        errs = validate_steps_config(_full_config(temp_dir, steps={
            "bulk_export": {"enabled": True, "export_resource_types": "auto"}}))
        assert not [e for e in errs if "export_resource_types" in e]

    def test_bad_string_still_rejected(self, temp_dir):
        errs = validate_steps_config(_full_config(temp_dir, steps={
            "bulk_export": {"enabled": True, "export_resource_types": "everything"}}))
        assert any("export_resource_types" in e for e in errs)

    def test_list_command_is_read_only_and_mock_known(self):
        cmd = ("gcloud beta resource-config list-resource-types "
               "--project=p --format=json --quiet")
        assert is_src_read_only(cmd) and is_known_mock_command(cmd)
