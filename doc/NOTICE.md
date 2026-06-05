# NOTICE: GCE 移行で個別調整が必要な項目

本ツールは `bulk-export` で取得した Terraform HCL（Step4）と、スナップショット
からの VM 復元（Step5）を組み合わせて GCP プロジェクトを移行します。
この方式の都合上、**GCE まわりは Terraform だけでは引き継がれない属性が複数あります**。
本ドキュメントは「terraform で扱われない項目」と「Step5 で現状引き継いでいない属性」
を明示し、運用時のチェックリストとして使うためのものです。

---

## 1. Terraform (Step4) で意図的に除外しているリソース

`scripts/sync_env.py` の `_skip_reason_for_file` および `_strip_reserved_ip` で
明示的に skip / 改変しています。

| リソース / 条件 | 扱い | 理由 |
|---|---|---|
| `google_compute_instance` | **除外** | VM 本体は Step5 でスナップショット復元 |
| `google_compute_disk` | **除外** | 同上（disk は VM とセットで Step5 が管理） |
| `google_compute_snapshot` | **除外** | Step5 が src スナップショットを直接参照して復元 |
| `google_compute_image` | **除外** | snapshot 由来で dst に存在せず作成不可 |
| `google_project` | **除外** | dst プロジェクトは既存（再作成不可） |
| `google_logging_project_sink` の `_Default` / `_Required` | **除外** | 既定シンクは更新/作成不可 |
| `google_service_account` で account_id が GCP 命名規則違反のもの | **除外** | プロジェクト番号始まりの Google 管理 SA は作成不能 |
| `google_compute_address` で `purpose = "NAT_AUTO"` | **除外** | NAT_AUTO は手動作成不可 |
| `google_compute_address` でクロスプロジェクト subnet 参照 | **除外** | Cross-project references not allowed |
| `google_compute_address` / `google_compute_global_address` の `address = "<ip>"` 行 | **自動採番に書き換え** | src の予約 IP は dst で未割当のため `IP address is not allocated` で失敗する |

---

## 2. Step5 (GCE 復元) で**現状引き継いでいない VM 属性**

`gcloud compute instances create` に現在渡しているのは以下のみ：

- `--machine-type`
- `--network-interface=network=...,subnet=...,private-network-ip=<ip>,no-address`
- `--disk=name=...,boot=yes,auto-delete=yes`

**以下の属性は dst で既定値に戻ります**。要件に合わせて手動で再設定するか、
このツールに追加実装してください。

| 属性 | 影響 | 復旧手段 |
|---|---|---|
| `metadata`（startup-script / ssh-keys / カスタム key-value） | 起動時の動作・SSH 鍵が失われる | `gcloud compute instances add-metadata` または create 時に `--metadata` / `--metadata-from-file` |
| `tags`（network tags） | firewall のターゲットタグ前提のルールが効かなくなる | `gcloud compute instances add-tags` または `--tags` |
| `labels` | コスト配分・監視クエリが切れる | `gcloud compute instances add-labels` または `--labels` |
| `service_account`（VM が借用する SA） | アプリの ADC が compute 既定 SA になる | `gcloud compute instances set-service-account` または `--service-account` + `--scopes` |
| `scheduling.preemptible` / `scheduling.provisioning_model = SPOT` | Spot/Preemptible が standard に戻る（コスト/起動挙動変化） | `--preemptible` / `--provisioning-model=SPOT` |
| `scheduling.on_host_maintenance` / `automatic_restart` | メンテ時の挙動が既定化 | `--maintenance-policy` / `--restart-on-failure` |
| `min_cpu_platform` | CPU 機能差による挙動変化の可能性 | `--min-cpu-platform` |
| `shielded_instance_config`（vTPM / 整合性監視 / セキュアブート） | セキュリティ要件が落ちる | `--shielded-secure-boot` / `--shielded-vtpm` / `--shielded-integrity-monitoring` |
| `confidential_instance_config` | Confidential VM が通常 VM に戻る | `--confidential-compute` + 対応マシンタイプ |
| `guest_accelerators`（GPU / TPU） | GPU 付き VM が CPU のみで起動して失敗 | `--accelerator=type=...,count=...` |
| `can_ip_forward` | ルーター/NAT 役の VM が機能しない | `--can-ip-forward` |
| **セカンダリディスク**（boot 以外の attached disk） | データディスクが付かない | 各ディスクを Step5 と同じ手順で snapshot 復元 → `gcloud compute instances attach-disk` |
| `resource_policies`（snapshot schedule 等） | バックアップ運用が外れる | `gcloud compute instances add-resource-policies` または `--resource-policies` |
| `deletion_protection` | 既定 `false` に戻る | `--deletion-protection` |
| ディスクの CMEK (`disk_encryption_key`) | 暗号鍵が Google 管理鍵に戻る | KMS 鍵指定して disk を再作成 |

---

## 3. `bulk-export` がそもそも出さない / 不完全な項目（GCE 周辺）

`bulk-export` は Config Connector 由来のため、出力に含まれないリソース・属性があります。

- **VM の startup-script 本文** … `startup-script-url` で GCS を参照しているケースは
  Step6 (GCS rsync) で別途データ移行が必要。
- **セカンダリディスク** … VM とは別 export されることがあるが、本ツールでは
  snapshot 復元対象外。
- **ゾーン / リージョン予約** (`gcloud compute reservations`)
- **Cloud NAT / Cloud Router の構成詳細** … network/subnet は出るが NAT が漏れることが多い。
- **VPN / Interconnect**
- **マシンイメージ** (`gcloud compute machine-images`)
- **Compute Engine の Operating Policy / VM Manager（OS Config）設定**

---

## 4. 内部 IP の引き継ぎ（参考: 既に実装済）

- src VM の `networkInterfaces[0].networkIP`（例 `10.100.3.11`）を
  dst host project の subnet に `gcloud compute addresses create --addresses=<ip>` で
  静的予約してから VM に `--network-interface=...,private-network-ip=<ip>` で割り当て。
- アドレス名は `mig-<vm_name>-<ip-dashed>`（例 `mig-vm-deb-01-10-100-3-11`）。
- 冪等：同名 address が既存で IP 一致なら再利用、IP 不一致なら警告のみ。
- **既存 dst VM（差し替えパス）では NIC を触らない**ため IP が変わりません。
  src と異なる場合は警告ログを出し、`gcloud compute instances delete` 後の
  再実行で新規作成パスに分岐させて引き継ぎます。

---

## 5. 運用チェックリスト（移行後に dst で確認すること）

- [ ] VM の **内部 IP** が src と一致
- [ ] VM の **metadata**（startup-script / ssh-keys / カスタム key）
- [ ] VM の **network tags** と firewall ルールの整合
- [ ] VM の **labels**
- [ ] VM に attach された **service account** とスコープ
- [ ] **scheduling**（Spot/Preemptible / on_host_maintenance / automatic_restart）
- [ ] **shielded VM** / **confidential VM** 設定
- [ ] **GPU / accelerator** の有無
- [ ] **セカンダリディスク**と中身
- [ ] **resource_policies**（snapshot schedule）
- [ ] **deletion_protection**
- [ ] **CMEK** が必要なディスクの暗号化鍵
- [ ] Cloud NAT / Router / VPN / Interconnect の構成（必要であれば手動再構築）
