# 推奨運用フロー: 技術設定まとめ

`README.md` の「推奨運用フロー」に沿って `make` ターゲットを順に実行したとき、各ステップで具体的に何が設定されるかを技術粒度でまとめた手順書。

---

## 0. 事前準備（手動）

- `dst/config.yaml` を `dst/config.yaml.template` から複製
  - `project_mapping`: src/dst プロジェクト ID、`host_project` + `service_projects`、`src_impersonate_service_account` / `dst_impersonate_service_account` を定義
  - `rename_rules.gcs.value`: 固定文字列 or `"auto"`（日付ベース suffix `-dst-MMDDHHMM` を `terraform/.gcs_rename_value` に永続化）
  - `steps`: 1〜6 の有効/無効、`gce_snapshot` の期限（既定 30 日）、`bulk_export.skip_on_run` 等
  - `bootstrap`: 組織 ID / フォルダ ID / 請求先アカウント
- 実行ユーザーは `gcloud auth login` 済み、`roles/iam.serviceAccountTokenCreator` を保有
- **src 側 SA**: `scripts/bootstrap_src_sa.sh --apply` で各 src プロジェクトに read-only SA を作成
  - 付与: `roles/viewer` / `roles/cloudasset.viewer` / 実行ユーザーへ `roles/iam.serviceAccountTokenCreator`

---

## 1. `make projects-plan` → `make projects`

- `bootstrap.org_id` / `folder_id` / `billing_account` を読み込み、dst プロジェクト（host + svc）を新規作成
- 請求先アカウントを紐付け
- 必要 API を有効化（compute / storage / bigquery / cloudasset / iam など）
- `projects-plan` は dry-run、`projects` で実書き込み

---

## 2. `make bootstrap` → `make bootstrap-apply`

3 スクリプトを順次実行。dry-run で内容確認 → `--apply` で適用。

### 2a. `bootstrap-dst-sa-apply` (`scripts/bootstrap_dst_sa.sh`)
- 各 dst プロジェクトに dst SA を作成
- 付与: `roles/editor` / `roles/storage.admin` / `roles/bigquery.admin`
- 実行アカウントに `roles/iam.serviceAccountTokenCreator`（impersonate 用）

### 2b. `bootstrap-cross-project-apply` (`scripts/bootstrap_cross_project.sh`)
- 各 src プロジェクトに read-only カスタムロール `migrationSrcReader` を作成（compute/GCS/BQ/CAI の read 権限のみ）
- 対応する dst SA に `migrationSrcReader` + `roles/bigquery.dataViewer` を付与
- **src(ORG) への read-only IAM 書き込み**を伴うため、`sync_env.py` の ORG 保護からは意図的に分離

### 2c. `bootstrap-shared-vpc-apply` (`scripts/bootstrap_shared_vpc.sh`)
- dst host を Shared VPC ホスト化
- dst svc プロジェクトを host にアタッチ
- 各 svc の `cloudservices` / `compute` SA と svc 借用 SA に `roles/compute.networkUser` を付与

---

## 3. `make plan`（ドライラン + 差分出力）

### 事前チェック（fail-fast）
- 有効ステップに必要な CLI を検査: `gcloud` / `terraform` / `bq` / `config-connector`
- 借用 SA 検証:
  - `gcloud auth print-access-token` で SA 実在 + tokenCreator 権限を確認
  - `gcloud projects test-iam-permissions` で代表権限（src=read / dst=write）を確認
  - 不足は全件列挙して即停止（dst SA の不備もここで検出）

### 計画ステップ（src は read-only、impersonate 経由）
1. **cai_scan**: Cloud Asset Inventory で src の有効リソース一覧を取得
2. **gce_snapshot**: 各 VM に期限内（既定 30 日）の有効スナップショットがあるか検証
3. **bulk_export**: `gcloud beta resource-config bulk-export --resource-format=terraform` で HCL 出力（並列）
   - プロジェクト ID 置換（src → dst）
   - GCS バケットを `rename_rules` でリネーム
   - 同一プロジェクト内 network 参照を `google_compute_network.<label>.self_link` に書き換え
   - `boot_disk.source` 行を削除（Step 5 で管理するため）
   - 成果物: `terraform/active/<src>/`
4. **terraform_apply**: `terraform plan -out=tfplan` を生成（apply はしない）
5. **gce_restore**: スナップショットから復元するディスク差し替え計画
6. **data_sync**: GCS（リネーム後名）/ BigQuery（src の location 継承）の同期計画

### 差分レポート
- 直後に **`DIFF.md`** を出力（CAI スキャン結果と bulk-export terraform の差分）

---

## 4. `make mock`（任意・ローカル試走）

- GCP 未接続で `sync_env.py` のフロー全体をシミュレート
- 外部コマンドは実行せずダミーデータ
- **未対応コマンドは fail-closed**（本物実行に進ませない）
- 前提チェック（CLI / SA）はスキップ

---

## 5. `make run`（本番実行）

`make plan` と同じ事前チェック後、dst にのみ書き込み:

1. **Terraform apply**: dst host に VPC / subnet / NAT / FW / FW Policy を再現
   - 冪等性: dst プロジェクト変更時は `terraform.tfstate` を破棄して import からやり直し（`active/<src>/.dst_project` マーカー判定）
   - `google_storage_bucket` はリネーム後の実名で import して adopt
   - VM/disk は Step 4 では作らず Step 5 で管理
2. **gce_restore**: 期限内スナップショットから dst にディスクを復元 → boot disk を差し替えて VM 起動（OS 状態・データごと復元）
3. **data_sync**:
   - GCS: リネーム後バケットへ `gcloud storage rsync` で同期
   - BigQuery: src の location を継承してデータセット作成 → コピー
4. **Network Firewall (Step 4.5)**: host の FW rules / policies を `gcloud` で冪等複製
5. `bulk_export.skip_on_run: true` の場合は export/customize をスキップし `terraform/active/` を再利用

---

## 6. 事後

- `logs/<timestamp>/org.log` / `dst.log` をレビュー
  - ステップ単位 `━━━━` 区切り、`✓/+/−/✗` 記号、スレッドタグ `[main]` / `[cai-scan_0]`
  - `verbose_logging: true` で生コマンド + STDOUT を DEBUG レベルで記録
