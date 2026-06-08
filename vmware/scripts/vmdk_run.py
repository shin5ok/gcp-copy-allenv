#!/usr/bin/env python3
"""VMDK → GCE 化を vmware/config.yaml に従って実行するユーティリティ。

サブコマンド:
  setup   : ターゲット project の事前準備
              - 必要な API の有効化 (compute, storage, vmmigration, iam)
              - Migrate to VMs の TargetProject 登録
              - vmmigration SA への source bucket 権限付与
              - 各 VM の内部 IP / 外部 static IP 予約
  import  : vms[].source.disks[] を `gcloud migration vms image-imports` でカスタムイメージ化 (非同期)
  start   : 各 VM のカスタムイメージから GCE インスタンスを作成・起動

挙動:
  - 既定では config.global.dry_run の値に従う (template 既定は true)。
  - --apply で常に実行モード、--dry-run で常に表示のみ。
  - 副作用のあるコマンドは run() を通し、表示 → (apply時のみ) 実行 する。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


# --------------------------------------------------------------------------- #
# Config loading
# --------------------------------------------------------------------------- #
@dataclass
class Ctx:
    cfg: dict[str, Any]
    cfg_path: Path
    apply: bool

    @property
    def project(self) -> str:
        return self.cfg["global"]["project_id"]

    @property
    def region(self) -> str:
        return self.cfg["global"]["region"]

    @property
    def zone(self) -> str:
        return self.cfg["global"]["zone"]


def load_ctx(path: Path, apply_flag: bool | None) -> Ctx:
    if not path.exists():
        sys.exit(f"エラー: config が見つかりません: {path}")
    with path.open(encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    cfg_dry_run = bool(cfg.get("global", {}).get("dry_run", True))
    if apply_flag is True:
        apply = True
    elif apply_flag is False:
        apply = False
    else:
        apply = not cfg_dry_run
    if cfg.get("global", {}).get("project_id", "").startswith("REPLACE_ME"):
        sys.exit("エラー: config.global.project_id が REPLACE_ME のままです。")
    return Ctx(cfg=cfg, cfg_path=path, apply=apply)


def _vms(ctx: Ctx) -> list[dict[str, Any]]:
    vms = ctx.cfg.get("vms") or []
    if not vms:
        sys.exit("エラー: config に vms[] が定義されていません。")
    return vms


# --------------------------------------------------------------------------- #
# Logging / command runner
# --------------------------------------------------------------------------- #
def setup_logging(ctx: Ctx) -> None:
    g = ctx.cfg["global"]
    log_dir = Path(g.get("log_dir", "./logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / g.get("log_file", "vmdk_import.log")
    level = logging.DEBUG if g.get("verbose_logging") else logging.INFO
    fmt = "%(asctime)s [%(levelname)s] %(message)s"
    logging.basicConfig(
        level=level,
        format=fmt,
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )


def run(ctx: Ctx, argv: list[str], *, check: bool = True, allow_fail: bool = False) -> int:
    """コマンドを表示し、apply=True のときだけ実行する。"""
    pretty = " ".join(shlex.quote(a) for a in argv)
    mode = "RUN" if ctx.apply else "DRY"
    logging.info("[%s] %s", mode, pretty)
    if not ctx.apply:
        return 0
    rc = subprocess.call(argv)
    if rc != 0:
        if allow_fail:
            logging.warning("コマンド失敗 (許容): rc=%s : %s", rc, pretty)
            return rc
        if check:
            sys.exit(f"コマンドが失敗しました (rc={rc}): {pretty}")
    return rc


def run_capture(ctx: Ctx, argv: list[str]) -> tuple[int, str, str]:
    """存在確認用。dry-run なら実行せず「未存在」相当を返す。

    gcloud 不在環境 (ローカル試走) でも落ちないように FileNotFoundError も握る。
    """
    if not ctx.apply:
        logging.debug("[DRY-check] %s", " ".join(shlex.quote(a) for a in argv))
        return 1, "", ""
    try:
        p = subprocess.run(argv, capture_output=True, text=True)
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError as e:
        logging.warning("コマンドが見つかりません: %s", e)
        return 127, "", str(e)


# --------------------------------------------------------------------------- #
# helpers: per-VM
# --------------------------------------------------------------------------- #
def image_name(vm_cfg: dict[str, Any], disk_name: str) -> str:
    prefix = vm_cfg["image_import"]["image_name_prefix"]
    vm_name = vm_cfg.get("name", "vm")
    return f"{prefix}-{vm_name}-{disk_name}"


def boot_disk_entry(vm_cfg: dict[str, Any]) -> dict[str, Any]:
    for d in vm_cfg["source"]["disks"]:
        if d.get("boot"):
            return d
    sys.exit(f"エラー: vms[{vm_cfg.get('name')}].source.disks に boot: true のディスクがありません。")


def data_disk_entries(vm_cfg: dict[str, Any]) -> list[dict[str, Any]]:
    return [d for d in vm_cfg["source"]["disks"] if not d.get("boot")]


def _migration_target_ids(ctx: Ctx, vm_cfg: dict[str, Any]) -> tuple[str, str]:
    """(host_project, target_project_id) を返す。未指定なら両方 ctx.project。"""
    ii = vm_cfg.get("image_import", {}) or {}
    host = ii.get("target_project_host") or ctx.project
    target = ii.get("target_project_name") or ctx.project
    return host, target


def _source_bucket_names_for_vm(vm_cfg: dict[str, Any]) -> list[str]:
    seen: list[str] = []
    for d in vm_cfg.get("source", {}).get("disks", []) or []:
        uri = str(d.get("gcs_uri", ""))
        if not uri.startswith("gs://"):
            continue
        bucket = uri[len("gs://"):].split("/", 1)[0]
        if bucket and bucket not in seen:
            seen.append(bucket)
    return seen


def image_disk_size_gb(ctx: Ctx, img_name: str) -> int | None:
    """カスタムイメージの diskSizeGb を取得する。取得できない場合は None。

    read-only の describe なので dry-run でも実行する。
    """
    argv = [
        "gcloud", "compute", "images", "describe", img_name,
        "--project", ctx.project,
        "--format", "value(diskSizeGb)",
    ]
    try:
        p = subprocess.run(argv, capture_output=True, text=True)
    except FileNotFoundError as e:
        logging.warning("gcloud 未インストール: %s", e)
        return None
    if p.returncode != 0:
        logging.warning(
            "image describe 失敗 (%s): rc=%s err=%s",
            img_name, p.returncode, p.stderr.strip(),
        )
        return None
    s = p.stdout.strip()
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        logging.warning("image diskSizeGb のパースに失敗: %r", s)
        return None


# --------------------------------------------------------------------------- #
# setup helpers
# --------------------------------------------------------------------------- #
REQUIRED_APIS = [
    "compute.googleapis.com",
    "storage.googleapis.com",
    "vmmigration.googleapis.com",
    "iam.googleapis.com",
]


def _gcloud_access_token() -> str:
    p = subprocess.run(
        ["gcloud", "auth", "print-access-token"],
        capture_output=True, text=True, check=True,
    )
    return p.stdout.strip()


def ensure_target_project(ctx: Ctx, host_project: str, target_id: str) -> None:
    """Migrate to VMs の TargetProject リソースを登録 (REST API)。

    gcloud には `target-projects create` が無いので vmmigration REST を直接叩く。
    """
    expected = f"projects/{host_project}/locations/global/targetProjects/{target_id}"
    rc, out, _ = run_capture(ctx, [
        "gcloud", "migration", "vms", "target-projects", "list",
        "--project", host_project,
        "--format", "value(name)",
    ])
    if rc == 0 and any(line.strip() == expected for line in out.splitlines()):
        logging.info("target project 既登録: %s", expected)
        return

    url = (
        f"https://vmmigration.googleapis.com/v1/"
        f"projects/{host_project}/locations/global/targetProjects"
        f"?targetProjectId={target_id}"
    )
    body = json.dumps({"project": target_id}).encode()
    mode = "RUN" if ctx.apply else "DRY"
    logging.info("[%s] POST %s body=%s", mode, url, body.decode())
    if not ctx.apply:
        return

    token = _gcloud_access_token()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            op = json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        sys.exit(f"target project 登録失敗: HTTP {e.code}: {e.read().decode()}")

    op_name = op.get("name")
    if not op_name:
        logging.info("target project 登録完了 (即時)")
        return
    poll_url = f"https://vmmigration.googleapis.com/v1/{op_name}"
    for _ in range(60):
        time.sleep(1)
        req2 = urllib.request.Request(poll_url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req2) as r2:
            op = json.loads(r2.read().decode())
        if op.get("done"):
            if "error" in op:
                sys.exit(f"target project 登録 operation 失敗: {op['error']}")
            logging.info("target project 登録完了: %s", expected)
            return
    logging.warning("target project 登録 operation がタイムアウト。確認: %s", poll_url)


def _project_number(ctx: Ctx, project_id: str) -> str | None:
    rc, out, _ = run_capture(ctx, [
        "gcloud", "projects", "describe", project_id,
        "--format", "value(projectNumber)",
    ])
    if rc != 0:
        return None
    return out.strip() or None


def _vmmigration_sa_email(project_number: str) -> str:
    return f"service-{project_number}@gcp-sa-vmmigration.iam.gserviceaccount.com"


def grant_source_bucket_access(ctx: Ctx, sa_email: str, all_vms: list[dict[str, Any]]) -> None:
    """全 VM の source VMDK が置かれた GCS バケットに vmmigration SA の objectViewer を付与。"""
    all_buckets: list[str] = []
    for vm_cfg in all_vms:
        for b in _source_bucket_names_for_vm(vm_cfg):
            if b not in all_buckets:
                all_buckets.append(b)
    if not all_buckets:
        logging.info("全 VM の source.disks に gcs_uri が無いので bucket 権限付与はスキップ。")
        return
    for bucket in all_buckets:
        run(ctx, [
            "gcloud", "storage", "buckets", "add-iam-policy-binding",
            f"gs://{bucket}",
            "--member", f"serviceAccount:{sa_email}",
            "--role", "roles/storage.objectViewer",
        ])


def _reserve_internal_ip(ctx: Ctx, vm_name: str, net: dict[str, Any]) -> None:
    ip = net.get("internal_ip", {}) or {}
    if ip.get("mode") == "address_name":
        addr = ip.get("address_name")
        if not addr or addr.startswith("REPLACE_ME"):
            sys.exit(f"エラー: vms[{vm_name}].network.internal_ip.address_name が未設定です。")
        host_project = net.get("host_project") or ctx.project
        subnet = net.get("subnetwork")
        if not subnet or subnet.startswith("REPLACE_ME"):
            sys.exit(f"エラー: vms[{vm_name}].network.subnetwork が未設定です。")
        rc, _, _ = run_capture(ctx, [
            "gcloud", "compute", "addresses", "describe", addr,
            "--region", ctx.region, "--project", ctx.project,
        ])
        if rc == 0:
            logging.info("[%s] 内部 IP 予約は既存: %s", vm_name, addr)
            return
        subnet_ref = (
            subnet
            if subnet.startswith("projects/") or subnet.startswith("https://")
            else f"projects/{host_project}/regions/{ctx.region}/subnetworks/{subnet}"
        )
        cmd = [
            "gcloud", "compute", "addresses", "create", addr,
            "--region", ctx.region,
            "--project", ctx.project,
            "--subnet", subnet_ref,
            "--purpose", "GCE_ENDPOINT",
        ]
        if ip.get("ip"):
            cmd += ["--addresses", str(ip["ip"])]
        run(ctx, cmd)
    elif ip.get("mode") == "ip":
        logging.info("[%s] 内部 IP は固定値モード。予約はスキップ。", vm_name)


def _reserve_external_ip(ctx: Ctx, vm_name: str, net: dict[str, Any]) -> None:
    ext = net.get("external_ip", {}) or {}
    if not (ext.get("enabled") and ext.get("mode") == "static"):
        return
    addr = ext.get("address_name")
    if not addr:
        return
    rc, _, _ = run_capture(ctx, [
        "gcloud", "compute", "addresses", "describe", addr,
        "--region", ctx.region, "--project", ctx.project,
    ])
    if rc == 0:
        logging.info("[%s] external static IP 既存: %s", vm_name, addr)
        return
    tier = ext.get("network_tier", "PREMIUM")
    run(ctx, [
        "gcloud", "compute", "addresses", "create", addr,
        "--region", ctx.region,
        "--project", ctx.project,
        "--network-tier", tier,
    ])


# --------------------------------------------------------------------------- #
# setup
# --------------------------------------------------------------------------- #
def cmd_setup(ctx: Ctx) -> None:
    vms = _vms(ctx)
    logging.info("===== setup: project=%s, %d VM =====", ctx.project, len(vms))

    # 1) API 有効化 (共通・1回)
    for api in REQUIRED_APIS:
        run(ctx, ["gcloud", "services", "enable", api, "--project", ctx.project])

    # 2) TargetProject 登録 (unique な (host, target) ペアごと)
    seen_targets: set[tuple[str, str]] = set()
    for vm_cfg in vms:
        pair = _migration_target_ids(ctx, vm_cfg)
        if pair not in seen_targets:
            ensure_target_project(ctx, pair[0], pair[1])
            seen_targets.add(pair)

    # 3) vmmigration SA 作成 + 全 VM の source bucket 権限付与 (共通・1回)
    run(ctx, [
        "gcloud", "beta", "services", "identity", "create",
        "--service", "vmmigration.googleapis.com",
        "--project", ctx.project,
    ])
    pnum = _project_number(ctx, ctx.project)
    if not pnum:
        if ctx.apply:
            sys.exit(f"エラー: project number を取得できません: {ctx.project}")
        logging.info("[DRY] project number 取得スキップ。")
        sa_email = "service-<PROJECT_NUMBER>@gcp-sa-vmmigration.iam.gserviceaccount.com"
    else:
        sa_email = _vmmigration_sa_email(pnum)
    grant_source_bucket_access(ctx, sa_email, vms)

    # 4) 各 VM の IP 予約
    for vm_cfg in vms:
        vm_name = vm_cfg.get("name", "?")
        net = vm_cfg.get("network", {})
        _reserve_internal_ip(ctx, vm_name, net)
        _reserve_external_ip(ctx, vm_name, net)

    logging.info("setup 完了。(%d VM)", len(vms))


# --------------------------------------------------------------------------- #
# import
# --------------------------------------------------------------------------- #
def cmd_import(ctx: Ctx) -> None:
    vms = _vms(ctx)
    logging.info("===== import: VMDK -> custom image (Migrate to VMs), %d VM =====", len(vms))

    used_locations: set[tuple[str, str]] = set()

    for vm_cfg in vms:
        vm_name = vm_cfg.get("name", "?")
        logging.info("----- [%s] import 開始 -----", vm_name)
        used_locations.add((ctx.project, ctx.region))
        ii = vm_cfg.get("image_import", {}) or {}
        license_type = ii.get("license_type")
        host_project, target_id = _migration_target_ids(ctx, vm_cfg)
        target_project_path = (
            f"projects/{host_project}/locations/global/targetProjects/{target_id}"
        )

        for disk in vm_cfg["source"]["disks"]:
            img = image_name(vm_cfg, disk["name"])
            import_name = img

            rc, _, _ = run_capture(ctx, [
                "gcloud", "compute", "images", "describe", img, "--project", ctx.project,
            ])
            if rc == 0:
                logging.info("[%s] image 既存: %s (skip)", vm_name, img)
                continue

            rc, _, _ = run_capture(ctx, [
                "gcloud", "migration", "vms", "image-imports", "describe", import_name,
                "--location", ctx.region,
                "--project", ctx.project,
            ])
            if rc == 0:
                logging.info(
                    "[%s] image-import 進行中または失敗: %s (describe で確認してください)",
                    vm_name, import_name,
                )
                continue

            cmd = [
                "gcloud", "migration", "vms", "image-imports", "create", import_name,
                "--project", ctx.project,
                "--location", ctx.region,
                "--source-file", disk["gcs_uri"],
                "--image-name", img,
            ]
            cmd += ["--target-project", target_project_path]
            if disk.get("boot"):
                if license_type:
                    cmd += ["--license-type", license_type]
            else:
                cmd += ["--skip-os-adaptation"]
            run(ctx, cmd)

    logging.info("import 投入完了 (非同期)。完了確認:")
    for proj, loc in sorted(used_locations):
        logging.info(
            "  gcloud migration vms image-imports list --project=%s --location=%s",
            proj, loc,
        )


# --------------------------------------------------------------------------- #
# start (instance create)
# --------------------------------------------------------------------------- #
def _build_instance_cmd(
    ctx: Ctx, vm_name: str, inst: dict[str, Any], net: dict[str, Any], vm_cfg: dict[str, Any],
) -> list[str]:
    name = inst["name"]
    boot_disk = boot_disk_entry(vm_cfg)
    boot_img = image_name(vm_cfg, boot_disk["name"])
    bd = inst.get("boot_disk", {}) or {}

    cmd: list[str] = [
        "gcloud", "compute", "instances", "create", name,
        "--project", ctx.project,
        "--zone", ctx.zone,
        "--machine-type", inst["machine_type"],
        "--image", boot_img,
        "--image-project", ctx.project,
        "--boot-disk-type", bd.get("type", "pd-balanced"),
    ]

    img_size = image_disk_size_gb(ctx, boot_img)
    cfg_size = bd.get("size_gb")
    if img_size is not None:
        if cfg_size and int(cfg_size) >= img_size:
            final_size = int(cfg_size)
        else:
            if cfg_size:
                logging.info(
                    "[%s] boot_disk.size_gb=%s はイメージ %s の実サイズ %sGB より小さいため %sGB に引き上げ",
                    vm_name, cfg_size, boot_img, img_size, img_size,
                )
            final_size = img_size
        cmd += ["--boot-disk-size", f"{final_size}GB"]
    elif cfg_size:
        cmd += ["--boot-disk-size", f"{cfg_size}GB"]
    if bd.get("auto_delete") is False:
        cmd += ["--no-boot-disk-auto-delete"]

    host_project = net.get("host_project") or ctx.project
    subnet = net.get("subnetwork")
    if not subnet:
        sys.exit(f"エラー: vms[{vm_name}].network.subnetwork が必須です。")
    subnet_ref = (
        subnet
        if subnet.startswith("projects/") or subnet.startswith("https://")
        else f"projects/{host_project}/regions/{ctx.region}/subnetworks/{subnet}"
    )
    cmd += ["--subnet", subnet_ref]

    ip = net.get("internal_ip", {}) or {}
    if ip.get("mode") == "address_name":
        cmd += ["--private-network-ip", ip["address_name"]]
    elif ip.get("mode") == "ip":
        cmd += ["--private-network-ip", str(ip["ip"])]

    ext = net.get("external_ip", {}) or {}
    if not ext.get("enabled"):
        cmd += ["--no-address"]
    else:
        tier = ext.get("network_tier", "PREMIUM")
        cmd += ["--network-tier", tier]
        if ext.get("mode") == "static" and ext.get("address_name"):
            cmd += ["--address", ext["address_name"]]

    tags = inst.get("tags") or []
    if tags:
        cmd += ["--tags", ",".join(tags)]

    labels = inst.get("labels") or {}
    if labels:
        cmd += ["--labels", ",".join(f"{k}={v}" for k, v in labels.items())]

    if inst.get("service_account"):
        cmd += ["--service-account", inst["service_account"]]
    if inst.get("scopes"):
        cmd += ["--scopes", ",".join(inst["scopes"])]

    md = inst.get("metadata") or {}
    if md:
        md_items = []
        md_files = []
        for k, v in md.items():
            if k == "startup-script":
                sp = Path(ctx.cfg["global"].get("log_dir", "./logs")) / f"{name}.startup.sh"
                sp.write_text(v, encoding="utf-8")
                md_files.append(f"startup-script={sp}")
            else:
                md_items.append(f"{k}={v}")
        if md_items:
            cmd += ["--metadata", ",".join(md_items)]
        if md_files:
            cmd += ["--metadata-from-file", ",".join(md_files)]

    return cmd


def _attach_data_disks(ctx: Ctx, vm_name: str, inst: dict[str, Any], vm_cfg: dict[str, Any]) -> None:
    name = inst["name"]
    for d in data_disk_entries(vm_cfg):
        attach_entry = None
        for ad in inst.get("additional_disks") or []:
            if ad.get("source_name") == d["name"]:
                attach_entry = ad
                break
        if not attach_entry:
            logging.warning("[%s] source.disks の %s に対応する additional_disks がありません。", vm_name, d["name"])
            continue

        disk_name = f"{name}-{d['name']}"
        rc, _, _ = run_capture(ctx, [
            "gcloud", "compute", "disks", "describe", disk_name,
            "--zone", ctx.zone, "--project", ctx.project,
        ])
        if rc != 0:
            create = [
                "gcloud", "compute", "disks", "create", disk_name,
                "--project", ctx.project,
                "--zone", ctx.zone,
                "--type", attach_entry.get("type", "pd-balanced"),
                "--image", image_name(vm_cfg, d["name"]),
                "--image-project", ctx.project,
            ]
            if attach_entry.get("size_gb"):
                create += ["--size", f"{attach_entry['size_gb']}GB"]
            run(ctx, create)
        else:
            logging.info("[%s] data disk 既存: %s (create skip)", vm_name, disk_name)

        run(ctx, [
            "gcloud", "compute", "instances", "attach-disk", name,
            "--project", ctx.project,
            "--zone", ctx.zone,
            "--disk", disk_name,
            "--device-name", attach_entry.get("device_name", d["name"]),
            "--mode", attach_entry.get("mode", "READ_WRITE"),
        ], allow_fail=True)

        if attach_entry.get("auto_delete") is True:
            run(ctx, [
                "gcloud", "compute", "instances", "set-disk-auto-delete", name,
                "--project", ctx.project,
                "--zone", ctx.zone,
                "--disk", disk_name,
                "--auto-delete",
            ], allow_fail=True)


def cmd_start(ctx: Ctx) -> None:
    vms = _vms(ctx)
    logging.info("===== start: create GCE instances, %d VM =====", len(vms))

    for vm_cfg in vms:
        vm_name = vm_cfg.get("name", "?")
        logging.info("----- [%s] start 開始 -----", vm_name)
        inst = vm_cfg["instance"]
        net = vm_cfg.get("network", {})
        name = inst["name"]
        if name.startswith("REPLACE_ME"):
            sys.exit(f"エラー: vms[{vm_name}].instance.name が REPLACE_ME のままです。")

        rc, _, _ = run_capture(ctx, [
            "gcloud", "compute", "instances", "describe", name,
            "--zone", ctx.zone, "--project", ctx.project,
        ])
        if rc == 0:
            logging.info("[%s] instance 既存: %s (create skip)。起動状態のみ確認。", vm_name, name)
        else:
            run(ctx, _build_instance_cmd(ctx, vm_name, inst, net, vm_cfg))

        _attach_data_disks(ctx, vm_name, inst, vm_cfg)

        rc, out, _ = run_capture(ctx, [
            "gcloud", "compute", "instances", "describe", name,
            "--zone", ctx.zone, "--project", ctx.project,
            "--format", "value(status)",
        ])
        status = (out or "").strip()
        if rc == 0 and status and status != "RUNNING":
            run(ctx, [
                "gcloud", "compute", "instances", "start", name,
                "--zone", ctx.zone, "--project", ctx.project,
            ])
        else:
            logging.info("[%s] instance status = %s (start 不要)", vm_name, status or "unknown")

    logging.info("start 完了。(%d VM)", len(vms))


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("subcommand", choices=["setup", "import", "start"])
    p.add_argument("--config", default=os.environ.get("VMWARE_CONFIG", "config.yaml"))
    g = p.add_mutually_exclusive_group()
    g.add_argument("--apply", dest="apply", action="store_true", default=None,
                   help="config の dry_run を無視して実行する")
    g.add_argument("--dry-run", dest="apply", action="store_false",
                   help="config の dry_run を無視して表示のみ")
    return p


def main() -> None:
    args = build_parser().parse_args()
    ctx = load_ctx(Path(args.config), args.apply)
    setup_logging(ctx)
    logging.info("config=%s apply=%s project=%s", ctx.cfg_path, ctx.apply, ctx.project)
    {
        "setup": cmd_setup,
        "import": cmd_import,
        "start": cmd_start,
    }[args.subcommand](ctx)


if __name__ == "__main__":
    main()
