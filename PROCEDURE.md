# 推奨運用フロー: 技術設定まとめ

`README.md` の「推奨運用フロー」に沿って `make` ターゲットを順に実行したとき、各ステップで何が起きるかをハイレベルな目的と技術粒度の両面でまとめた手順書。

> **全体像**: src ORG の GCP プロジェクト群を、別 ORG の dst プロジェクト群に「設定 + データ + VM 状態」ごと複製する。src 側は最後まで read-only、書き込みはすべて dst 側で完結する。

---

## 0. 事前準備（手動）

**目的**: 「どこから何を、どこへ複製するか」を宣言し、必要な認証を準備する。

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

**目的**: コピー先となる「空の dst プロジェクト群」を組織内に確保する。後続の bootstrap/plan/run はここで作った箱に対して行う。

- `bootstrap.org_id` / `folder_id` / `billing_account` を読み込み、dst プロジェクト（host + svc）を新規作成
- 請求先アカウントを紐付け
- 必要 API を有効化（compute / storage / bigquery / cloudasset / iam など）
- `projects-plan` は dry-run、`projects` で実書き込み

---

## 2. `make bootstrap-plan` → `make bootstrap`

**目的**: 「誰が、どのプロジェクトに、何を読み書きできるか」を整える。ここで権限と Shared VPC のトポロジが確定する。

3 スクリプトを順次実行。`bootstrap-plan` で内容確認 → `bootstrap` で適用（`projects*` と同じく裸 = 実適用 / `-plan` = dry-run）。

### 2a. `bootstrap-dst-sa` (`scripts/bootstrap_dst_sa.sh`)
**役割**: dst 側の書き込み用 SA を作る。以降の書き込みはすべてこの SA を impersonate して行う。
- 各 dst プロジェクトに dst SA を作成
- 付与: `roles/editor` / `roles/storage.admin` / `roles/bigquery.admin`
- 実行アカウントに `roles/iam.serviceAccountTokenCreator`（impersonate 用）

### 2b. `bootstrap-cross-project` (`scripts/bootstrap_cross_project.sh`)
**役割**: dst SA が src を「覗ける」ようにする。src への書き込み権限は与えない。
- 各 src プロジェクトに read-only カスタムロール `migrationSrcReader` を作成（compute/GCS/BQ/CAI の read 権限のみ）
- 対応する dst SA に `migrationSrcReader` + `roles/bigquery.dataViewer` を付与
- **src(ORG) への read-only IAM 書き込み**を伴うため、`sync_env.py` の ORG 保護からは意図的に分離

### 2c. `bootstrap-shared-vpc` (`scripts/bootstrap_shared_vpc.sh`)
**役割**: dst 側のネットワーク構造（Shared VPC）を src と同型にする。
- dst host を Shared VPC ホスト化
- dst svc プロジェクトを host にアタッチ
- 各 svc の `cloudservices` / `compute` SA と svc 借用 SA に `roles/compute.networkUser` を付与

---

## 3. `make plan`（ドライラン + 差分出力）

**目的**: 本番実行 (`make run`) の前に「何を作る/変更するか」を全部見える化する。書き込みは一切しない。`DIFF.md` を眺めて意図通りか確認するためのステップ。

### 事前チェック（fail-fast）
**役割**: 権限・CLI 不足で途中失敗するのを防ぐ。全件列挙して即停止。
- 有効ステップに必要な CLI を検査: `gcloud` / `terraform` / `bq` / `config-connector`
- 借用 SA 検証:
  - `gcloud auth print-access-token` で SA 実在 + tokenCreator 権限を確認
  - `gcloud projects test-iam-permissions` で代表権限（src=read / dst=write）を確認
  - 不足は全件列挙して即停止（dst SA の不備もここで検出）

### 計画ステップ（src は read-only、impersonate 経由）
1. **cai_scan**: Cloud Asset Inventory で src の有効リソース一覧を取得（「何が存在するか」のスナップショット）
2. **gce_snapshot**: 各 VM に期限内（既定 30 日）の有効スナップショットがあるか検証（復元元の鮮度チェック）
3. **bulk_export**: `gcloud beta resource-config bulk-export --resource-format=terraform` で HCL 出力（並列）
   - src の現状を Terraform コードとして書き出し、dst 向けに書き換える工程
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
- 「CAI で見つかったのに tf に出てこない」リソースをここで気付ける

---

## 4. `make mock`（任意・ローカル試走）

**目的**: GCP に触らずにスクリプトのフロー自体を確認する。リファクタや CI、新規メンバーの動作理解用。

- GCP 未接続で `sync_env.py` のフロー全体をシミュレート
- 外部コマンドは実行せずダミーデータ
- **未対応コマンドは fail-closed**（本物実行に進ませない）
- 前提チェック（CLI / SA）はスキップ

---

## 5. `make run`（本番実行）

**目的**: `make plan` で確認した内容を dst にだけ実書き込みする。src には一切触れない。VM は「OS 状態・データごと」復元される。

`make plan` と同じ事前チェック後、dst にのみ書き込み:

1. **Terraform apply**: dst host に VPC / subnet / NAT / FW / FW Policy を再現
   - 冪等性: dst プロジェクト変更時は `terraform.tfstate` を破棄して import からやり直し（`active/<src>/.dst_project` マーカー判定）
   - `google_storage_bucket` はリネーム後の実名で import して adopt
   - VM/disk は Step 4 では作らず Step 5 で管理（責務分離）
2. **gce_restore**: 期限内スナップショットから dst にディスクを復元 → boot disk を差し替えて VM 起動（OS 状態・データごと復元）
   - 並列化: `_replicate_host_networks()` の後、(project, vm) のフラット work unit に展開し VM 単位で並列復元（`parallel_jobs=8` 推奨）。VM 内の操作チェーン (stop→detach→delete→create→attach→start) は依存があるため直列。
   - snapshot 未検出時の挙動: 並列モードで `sys.exit(1)` すると他 VM の進行を巻き添えで止めるため、`stats.failed` に記録して return する（最終的に `main()` で exit 1）。
3. **data_sync**:
   - GCS: リネーム後バケットへ `gcloud storage rsync` で同期
   - BigQuery: src の location を継承してデータセット作成 → コピー
4. **Network Firewall (Step 4.5)**: host の FW rules / policies を `gcloud` で冪等複製（Terraform で表現しきれない部分の補完）
   - `network-firewall-policies` のサブコマンドごとに scope flag が異なる: `list`=`--regions=`（複数形）/ `describe`・`create`=`--global`・`--region=`（ポリシー本体）/ `rules ...`・`associations create`=`--global-firewall-policy`・`--firewall-policy-region=`。誤ると `unrecognized arguments`。`fw_rule_scope_flag()` で変換する。
   - `fw_policy_rule_flags()` は REST API の FirewallPolicyRule 全フィールドに対応する。INGRESS ルールは `srcIpRanges / srcThreatIntelligences / srcAddressGroups / srcFqdns / srcSecureTags / srcRegionCodes / srcNetworkScope` のいずれかが必須（gcloud 仕様）。欠落すると `Must specify src_... for ingress direction` / `Could not fetch resource:` で失敗する。
   - **Secure tag**（`tagValues/<数値ID>`）は ORG スコープの permanent ID で別 ORG には存在しない。そのまま渡すと `rules create` が `Could not fetch resource:` で失敗する。`config steps.network_firewall.secure_tag_map` に src→dst の tagValues を登録すると変換して複製。未登録タグを参照するルールは FW を意図せず緩めないようエラーにせずスキップし WARNING を出す。
5. `bulk_export.skip_on_run: true` の場合は export/customize をスキップし `terraform/active/` を再利用（再実行高速化）

---

## 6. 事後

**目的**: 何が起きたかを後から追えるようにする。失敗時の原因切り分けもここを起点に行う。

- `logs/<timestamp>/org.log` / `dst.log` をレビュー
  - ステップ単位 `━━━━` 区切り、`✓/+/−/✗` 記号、スレッドタグ `[main]` / `[cai-scan_0]`
  - `verbose_logging: true` で生コマンド + STDOUT を DEBUG レベルで記録

---

## 7. `make delete-projects-plan PATTERN=...` → `make delete-projects PATTERN=...`（任意・クリーンアップ）

**目的**: 試行錯誤で作った dst プロジェクト群を一括で片付ける。src は対象外（コードレベルで母集団から除外）。

### 削除対象の決定
- 母集団は `dst/config.yaml` の `project_mapping.host_project.dst` と `service_projects[].dst` のみ。**config に無い無関係なプロジェクトは仕様上候補に上がらない**（誤爆防止のコア）。
- `PATTERN` (3 文字以上必須) で母集団を project_id 部分一致でさらに絞り込む。
- 各候補は `gcloud projects describe` で存在 / 状態を確認: `lifecycleState != ACTIVE` のものや describe 不可（存在しない / 権限不足）は自動でスキップし、理由付きで列挙。

### 多重安全策
1. **`PATTERN` 必須・3 文字未満は拒否**（make ターゲット側でも未指定なら即エラー）
2. **削除前に一覧テーブル**を出力: `# / kind(host|svc) / project_id (dst) / name / state / lien 数 / src project` を桁揃え
3. **6 桁ランダムコード**を端末に表示。**標準入力で一致するまで削除は実行されない**（一致しなければ exit 1 で中止）
4. lien (`compute.googleapis.com/projects-delete-prevented` 等) が付いていれば `gcloud alpha resource-manager liens delete` で先に解除してから `gcloud projects delete --quiet`
5. 既定は `--dry-run`（make ターゲット側で `--no-dry-run` を付与）。`delete-projects-plan` は dry-run 固定で表示のみ
6. 並列度は `global.parallel_jobs`（既定 8）。worker 内で `sys.exit` せず、`threading.Lock` で success/fail カウンタを保護（他 worker を巻き添えで止めない）

### ログ
- `logs/<timestamp>_delete-projects/dst.log` に独立出力（`create-projects` と同じ書式）
- サマリ: `削除済 / lien 解除 / 失敗` 件数 + ログパス。1 件でも失敗で exit 1
