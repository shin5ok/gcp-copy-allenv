# SPECIFICATION: 環境コピー元（Original）構成定義および複製（Sync）仕様

## 概要
本プロジェクトは、既存 of Google Cloud環境（オリジナル）の構成情報を定義・分析し、それを元に新しい環境（コピー先）へ完全な同期コピー（作成・復元）を行うための基盤を構築する。
本仕様書では、コピー元の定義・自動スキャン仕様、およびコピー先（Destination）への複製仕様について規定する。

## `ORG.md` / `DST.md` の役割
- **`org/ORG.md`**: コピー元の静的な構成定義（リファレンス）。
- **`dst/DST.md`**: コピー元の実機をスキャンして得られた構成情報を元に自動生成される、コピー先のあるべき構成定義。
- これらのファイルをインプットとし、自動化スクリプトが各環境の構築・削除・同期制御を行う。

## 構成要素 (インフラ設計)
共有VPC（Shared VPC）構成を前提とする。

### 1. プロジェクト構造とネットワーク
- **Host Project (ホストプロジェクト)**: `<SRC_HOST_PROJECT_ID>` (オリジナル)
  - 共有VPCネットワーク (`shared-vpc`) を管理。
  - 各サービスプロジェクト用にサブネットを作成し、権限を付与する。
  - プライベートVMがインターネット通信（外部アップデート等）を行えるよう、Cloud Router および Cloud NAT ゲートウェイを配置・管理する。
  - **コピー元のエクスポート定義に基づき、以下の4つの「本物」のファイアウォールルールをホストプロジェクトに完全に再現配置する。**
    1. `allow-shared-iap-ssh`: ポート `tcp:22` INGRESS, ソース `35.235.240.0/20`
    2. `all-for-incredibuild`: プロトコル `all` INGRESS, ソース `10.0.0.0/8`
    3. `ssh`: ポート `tcp:22` INGRESS, ソース `10.0.0.0/8`
    4. `rdp`: ポート `tcp:3389` INGRESS, ソース `0.0.0.0/0`
- **Service Projects (サービスプロジェクト)**: VMインスタンスなどのコンピューティングリソースを配置するプロジェクト。
  - ホストプロジェクトから共有されたサブネットを利用してVMを配置する。
  - VMには外部IPアドレスを付与せず、安全に Cloud NAT 経由でのみインターネットに発信通信を行えるようにする。

### 2. コピー先（Destination）プロジェクトのマッピング（想定）
同期コピー時、オリジナルリソースは以下の新しいコピー先プロジェクト群に複製される。
- コピー先ホストプロジェクトID: (例: `<DST_HOST_PROJECT_ID>`)
- コピー先サービスプロジェクト1ID: (例: `<DST_SERVICE_PROJECT_ID_1>`)
- コピー先サービスプロジェクト3ID: (例: `<DST_SERVICE_PROJECT_ID_3>`)

---

## 自動構築・同期複製ツール仕様

本ツールは、環境の構築（Deploy）、削除（Destroy）、バックアップ（Snapshot）に加え、オリジナル環境の動的スキャン（Scan）、コピー先のAPI自動有効化（Prepare）、およびスナップショットからのクローン復元（Sync）をサポートする。

### 1. インターフェース (Makefile)
ユーザーは `make` コマンドを介してツールを実行する。

- **`make projects-plan` / `make projects`**: `dst/config.yaml` に基づき、コピー先（Destination）プロジェクト群を新規作成し、請求先アカウントの紐付けと必要なAPIの有効化を行います（`-plan` は dry-run）。
- **`make bootstrap-plan` / `make bootstrap`**: dst SA 作成・dst SA への src 読取権限付与・Shared VPC 化を順に実行します（`-plan` は dry-run）。`make plan` の SA 事前チェックを通すための前提整備。
- **`make plan`**: ドライランモードで実行計画（予定されるgcloud/terraformコマンドと日本語補足）を表示します。直後に `logs/<timestamp>/DIFF.md`（CAI ↔ bulk-export terraform 差分）も出力し、リポジトリ直下の `DIFF.md` を最新版への相対 symlink に張り替えます。
- **`make mock`**: GCP 未接続で `sync_env.py` のフロー全体をシミュレートします。未対応コマンドは fail-closed で停止。
- **`make run`**: コピー先プロジェクト群に対し、移行処理（スキャンからクローン同期まで）を本番実行します。
- **`make delete-projects-plan PATTERN=...` / `make delete-projects PATTERN=...`**: `dst/config.yaml` に登録された dst プロジェクトを `PATTERN` で絞り込み、6 桁ランダムコード入力で確認のうえ削除します（lien も自動解除、`-plan` は表示のみ）。
- **`make test`**: ツール全体の単体テストを実行します。

> Makefile には他に `make org` / `make org-plan` / `make vmware-*` などのターゲットがあります。詳細は `README.md` を参照してください。

### 2. 実行前提条件 (Prerequisites)

ツール本体（`make plan` / `make run`）は起動時に `validate_config()` + `validate_steps_config()` + 前提チェック（CLI / SA）を **fail-fast** で実行する。不足があれば dst へ一切書き込まずに全件列挙して `exit 1`。

詳細チェックリストは [`README.md`](./README.md)「0. 前提条件チェックリスト」/ [`PROCEDURE.md`](./PROCEDURE.md)「0. 事前準備（手動）」を正とする。本仕様書では検証対象と検証主体の対応のみ規定する。

| カテゴリ | 必要な入力 / 状態 | 検証主体 |
|---------|------------------|---------|
| ローカル CLI | `gcloud` / `bq` / `terraform`、`bulk_export` 有効時は `gcloud components install config-connector` | 前提チェック（Mock はスキップ） |
| 認証 | `gcloud auth login` 済み + 実行ユーザーに `roles/iam.serviceAccountTokenCreator` | SA 事前チェック |
| src 借用 SA（推奨） | `roles/viewer` / `roles/cloudasset.viewer`（`scripts/bootstrap_src_sa.sh --apply` で各 src プロジェクトへ投入） | `gcloud auth print-access-token` + `testIamPermissions` で代表 read 権限を確認 |
| src 借用 SA 未指定時 | ローカル認証 (gcloud アクティブアカウント / ADC) にフォールバック。src 書込権を持つ場合は警告 + `[y/N]` 続行確認（非対話は `COPY_ALL_ENV_AUTO_APPROVE=1` で明示許可） | `_SRC_DANGEROUS_PERMS` を `testIamPermissions` で検査 |
| GCE スナップショット | `gce_snapshot` 有効時、移行対象の全 VM に `steps.gce_snapshot.max_age_days`（既定 30 日）以内のスナップショットが必要 | Step 2 `gce_snapshot` で検証。無ければエラー停止し手動作成 (`gcloud compute disks snapshot ...`) を促す |
| dst プロジェクト | `make projects` で新規作成（または既存を流用） | SA 事前チェック (`projects test-iam-permissions`) |
| dst 借用 SA | `roles/editor` / `roles/storage.admin` / `roles/bigquery.admin`（`scripts/bootstrap_dst_sa.sh` で投入） | SA 事前チェックで write 権限を確認 |
| 組織 / フォルダ / 請求先 ID | `bootstrap.org_id`（`organizations/<id>`）/ `bootstrap.folder_id`（任意）/ `bootstrap.billing_account`（`billingAccounts/<id>`） | `make projects` 実行時に利用 |
| VPC SC 設定 | `vpc_sc.enabled=true` 時は `access_policy` / `perimeter` / `billing_project` 全て必須・明示指定 | `validate_steps_config()` で fail-fast |
| GCS リネーム | `bulk_export` / `data_sync` 有効時、`rename_rules.gcs.method` ∈ `{suffix, prefix, custom}`、`suffix`/`prefix` は `value` 非空 | `validate_steps_config()` で fail-fast |

---

### 3. 実装要件

#### 3.1. 言語・実行環境
- Python 3.13 以上（`pyproject.toml` の `requires-python` と整合）、`uv`、PEP8準拠、`pytest` によるテスト。

#### 3.2. 主要ロジック (Pythonスクリプト)

##### A. プロジェクト作成 (scripts/create_projects.py)
コピー先プロジェクトを自動作成し、ブートストラップ（初期化）を行う。
1. `dst/config.yaml` の `bootstrap` および `project_mapping` を読み込む。
2. コピー先ホストプロジェクトおよびサービスプロジェクトの `dst` IDを抽出。
3. 各プロジェクトについて、未存在の場合は `gcloud projects create` で作成（組織IDまたはフォルダID配下）。
4. `gcloud beta billing projects link` で請求先アカウントを紐付ける。
5. `gcloud services enable` で必要なAPI（`compute.googleapis.com` など）を有効化する。

##### B. 環境複製コアオーケストレータ (scripts/sync_env.py)
`scripts/sync_env.py` が一連のステップ（Step 1〜7、うち Step 4.5 と Step 5.5 はサブフェーズ）を統合して実行するコアオーケストレータである。

1. **モック実行モード (Mock Mode)**:
   - コマンドライン引数 (`--mock`) または `global.mock: true` が指定された場合、実際のGCP APIおよびTerraformの呼び出しをシミュレートする。
   - `run_command` は、実際のコマンドを実行する代わりに、コマンドに応じたダミーデータ（VMリストJSON、スナップショットリストJSON、ダミーHCLファイルなど）を生成して返す。
   - **未対応コマンドは fail-closed**（本物実行に進ませない）。新しい状態遷移コマンド（suspend/resume 等）を追加する際は `_WRITE_VERBS`（src 拒否リスト）と `_MOCK_KNOWN_PATTERNS`（mock 許容リスト）の両方への登録が必要。
   - これにより、実際のGCP環境や有効なサービスアカウントがない状態でも、`make run` 全体のフローをエラーなしでテスト実行可能にする。

2. **[Step 1] CAI Scan (現状確認)**:
   - コピー元プロジェクトレベルでCAIを検索し、エクスポート対象のリソースリストを確定・保存する。
   - モックモード時は、ダミーのリソースリストファイルを生成する。
3. **[Step 2] GCE Snapshot Verification (バックアップ検証)**:
   - 最新のGCEスナップショット（デフォルトで30日以内）が存在するか検証し、なければエラーとする。
   - モックモード時は、ダミーのVMリストと最新のスナップショットリストをシミュレートし、常に検証成功とする。
4. **[Step 3] Bulk Export & HCL Customization (コード化とカスタマイズ)**:
   - `gcloud beta resource-config bulk-export --resource-format=terraform` を使用してTerraform HCLを生成（`config-connector` 依存。事前チェックで存在検証）。
   - モックモード時は、ダミーの `.tf` ファイル群を自動生成する。
   - 生成されたHCL内のプロジェクトIDの置換、GCSバケット名のリネーム、同一プロジェクト内 network 参照の `self_link` 化、およびVMの `boot_disk.source` 行の削除を自動で行う。FW (`google_compute_firewall` 等) は Step 4.5 で `gcloud` 経由で複製するため、ここで除外する。
   - `bulk_export.skip_on_run: true` のときは `terraform/active/<src>/.dst_project` マーカーが現 dst と一致すれば export/customize を完全スキップ、不一致でも `terraform/raw/` が残っていれば customize のみ再実行（bulk-export 自体は省略）。
5. **[Step 4] Terraform Apply (IaC再現)**:
   - カスタマイズされたTerraformコードを適用し、コピー先に **VPC / subnet / Cloud Router / Cloud NAT** などのインフラを再構築する（FW rule / Firewall Policy は Step 4.5）。
   - 冪等性: dst プロジェクト変更時は `terraform.tfstate` を破棄して import からやり直し（`.dst_project` マーカー判定）。`google_storage_bucket` はリネーム後の実名で import して adopt。
   - モックモード時は、適用成功をシミュレートする。
6. **[Step 4.5] Network Firewall (FW ルール / ポリシー複製)**:
   - classic firewall ルールと Network Firewall Policy（rules / associations）を `gcloud` で冪等複製する独立フェーズ。実行順は Step 4 の直後・Step 5 の前。
   - ステップ冒頭で `_replicate_host_networks()` を呼び、dst host の Shared VPC ネットワークと subnet を src host と同型に複製（`--network=<NAME>` 参照不可で全 FW 操作が失敗する regression の防止）。
   - `network-firewall-policies` サブコマンドごとに scope flag が異なる（`list`=`--regions=`、`describe`/`create`=`--global` または `--region=`、`rules`/`associations`=`--global-firewall-policy` または `--firewall-policy-region=`）。`fw_rule_scope_flag()` で変換。
   - secure tag (`tagValues/<数値ID>`) は ORG スコープの permanent ID。別 ORG にはそのまま存在しないため `config.steps.network_firewall.secure_tag_map` で `src → dst tagValues` 変換。未登録参照は FW を緩めないよう skip + WARNING。
7. **[Step 5] GCE VM Restore (スナップショットからのクローンVM復元)**:
   - コピー先に作成されたダミーVMのディスクを一旦デタッチ・削除し、コピー元のスナップショットから復元した本物ディスクをアタッチして起動する。src の電源状態に関わらず、復元直後は **必ず RUNNING で残す**。
   - `(project, vm)` のフラット work unit で並列化（`parallel_jobs=8` 推奨）。同一 VM 内の `stop → detach → delete → create disk → attach → start → secondary disks` チェーンは API 競合回避のため直列。
   - モックモード時は、一連の `gcloud` コマンド（stop, detach, delete, create, attach, start）の成功をシミュレートする。
8. **[Step 5.5] Power State Reconciliation (電源状態反映)**:
   - 全 VM の復元完了後、`_finalize_vm_power_states` で src と同じ電源状態（`TERMINATED` / `SUSPENDED`）に揃える独立フェーズ。
   - `config.steps.gce_restore.power_state_wait_seconds`（既定 120 秒）だけ sleep してから開始（GCE suspend は guest OS が ACPI S3 シグナルに 3 分以内に応答する必要があり、boot 直後は失敗しやすいため）。
   - `TERMINATED` は `gcloud compute instances stop --quiet`（forceful fallback あり）。`SUSPENDED` は `_try_dst_suspend` が `subprocess` を直接呼び、失敗しても `stats.failed` に計上せず WARNING + 手動復旧コマンド案内（run 全体の exit code に影響させない）。
   - `make plan` / `make mock` ではスキップ。
9. **[Step 6] Data Sync (データ同期)**:
   - GCSバケットの同期（`gcloud storage rsync`）およびBigQueryデータセット・テーブルの同期を実行する。
   - BigQuery データセットは **src の location を継承** して dst に作成（クロスリージョン失敗を回避）。
   - モックモード時は、バケット一覧やデータセット一覧の取得および同期コマンドの成功をシミュレートする。
10. **[Step 7] VPC Service Controls (ペリメタ追加)**:
    - 全データ移行の最後に、dst プロジェクト（番号）を既存の VPC SC ペリメタへ `--add-resources` で追記する（org / access policy 自体は作成・変更しない・冪等）。
    - `access-context-manager` は org/policy スコープで `--project` を持たないため、quota project を `steps.vpc_sc.billing_project`（**必須・明示指定**）で与える。未指定だとローカル `gcloud config` の無関係なプロジェクトが quota に使われ `SERVICE_DISABLED` で失敗するため、安全側に倒して未設定ならスキップする（自動推測しない）。`load_config` の `validate_steps_config()` で `vpc_sc.enabled=true` かつ `access_policy` / `perimeter` / `billing_project` のいずれかが空ならば実行前に fail-fast。
    - モックモード時は、ペリメタ describe / update / API 有効化コマンドの成功をシミュレートする。

##### C. CAI ↔ Terraform 差分レポート (DIFF.md)
- Step 3 の bulk-export 完了後（`make plan` / `make run` の双方）、CAI 検出リソースと bulk-export 由来 `.tf` を `analyze_cai_tf_diff()` で突合し、欠落リソースに dst 再現用 `gcloud` 作成系コマンドを併記したレポートを生成する。
- 「**要手動**」（`bulk_export` が出すはずで欠落したもの）と「**自動処理・対象外**」（専用ステップ `gce_restore` / `network_firewall` / `data_sync` が複製、または `_ASSET_COVERAGE` で `None` 指定の意図的対象外）を区別し、後者は件数のみ集計して本文に列挙しない。
- 実体は `logs/<timestamp>/DIFF.md`、リポジトリ直下の `DIFF.md` は最新実行への**相対 symlink** に張り替える（過去実行のレポートと並べて比較可能）。`.gitignore` 配下。

---

## VMware VMDK → GCE インポートワークフロー

VMware 環境の VM を GCE に移行するための独立したサブシステム。`vmware/` ディレクトリに格納。

### 1. フロー概要

```
setup → import → start
```

| ステップ | コマンド | 処理内容 |
|---------|---------|---------|
| setup | `make vmware-setup-apply` | 必要 API 有効化 / Migrate to VMs TargetProject 登録 / GCS bucket 権限付与 / 静的 IP 予約 |
| import | `make vmware-import-apply` | GCS 上の VMDK を `gcloud migration vms image-imports create` でカスタムイメージ化（非同期） |
| start | `make vmware-start-apply` | カスタムイメージから `gcloud compute instances create` で GCE インスタンスを作成・起動 |

全ステップ一括実行: `make vmware-all-apply`

### 2. 設定ファイル (`vmware/config.yaml`)

| セクション | 役割 |
|-----------|------|
| `global` | 対象プロジェクト / region / zone / dry_run フラグ |
| `vms[]` | VM ごとのソース VMDK (GCS URI)・イメージ名プレフィックス・インスタンス設定・ネットワーク設定 |

複数 VM を `vms[]` 配列で定義可能。同一 GCS URI を複数 VM で指定しても、イメージ名に VM 名が含まれるため（`<prefix>-<vm_name>-<disk_name>`）、VM ごとに独立したカスタムイメージが生成される。

### 3. イメージ命名規則

```
<image_name_prefix>-<vm_name>-<disk_name>
例: vmdk-imported-20260608-centos8t-boot
```

### 4. dry_run / --apply

- `config.yaml` の `global.dry_run: true` が既定。コマンドを表示するのみ、実行しない。
- `--apply` フラグ（または `*-apply` Make ターゲット）で実際に実行。

### 5. import 完了確認

`import` ステップは非同期。完了後に以下のコマンドで状態を確認：

```bash
gcloud migration vms image-imports list --project=<project_id> --location=<region>
```

複数 (project, region) がある場合はそれぞれ出力される。

---

### 4. 安全対策と堅牢性 (べき等性の確保とログ記録)
- **べき等性の維持**: すべてのステップでべき等チェックを徹底し、すでに完了している処理はスキップする。
- **ログの分離**: コピー元の読み取り操作は `logs/<timestamp>/org.log`、コピー先への書き込み・変更操作は `logs/<timestamp>/dst.log` に記録する。CAI ↔ TF 差分レポート `DIFF.md` も同じ `logs/<timestamp>/` 配下に出力し、リポジトリ直下の `DIFF.md` は最新版への相対 symlink。
- **ORG 保護のコード強制**: `side="src"` の外部コマンドは `is_src_read_only` ガードで書き込み動詞（`create / delete / update / stop / start / attach / detach / mk / cp / rsync / apply` 等）を **実行前に拒否**。impersonate の有無に関わらず常時有効。
- **サービスアカウント権限借用 + ADC フォールバック**: 既定はサービスアカウントの権限借用（`CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT` 経由）。`config.yaml` の `*_impersonate_service_account` が空の場合はローカル認証（gcloud のアクティブアカウント / ADC）にフォールバックする。
  - ADC フォールバック時、`check_service_accounts` が `_SRC_DANGEROUS_PERMS`（Editor/Owner/各種 Admin の代表値）の有無を `testIamPermissions` で確認し、src プロジェクトへの書込権を持つ場合は対象プロジェクトと付与権限を一覧で警告して `[y/N]` で続行確認する。
  - 非対話セッションは既定で abort。明示続行は `COPY_ALL_ENV_AUTO_APPROVE=1` を要求。
- **実行前設定検証 (fail-fast)**: `load_config` が `validate_config()`（ORG 保護: src/dst マッピングの欠落・src=dst・dst が src ID と衝突 等）と `validate_steps_config()`（有効ステップの設定不備: `vpc_sc.access_policy/perimeter/billing_project`、`rename_rules.gcs.method/value`、`gce_snapshot.max_age_days` 等）の両方を実行し、不備を全件列挙して `exit 1`（dst へ一切書き込まずに停止）。`make plan` / `make run` / `make mock` 共通。

