# SPEC: GCPプロジェクトまるごとコピー (Terraform / IaC 方式)

## 1. 目的
既存のGCPマルチプロジェクト環境（Original）を、構成およびデータを保持したまま、新規のGCPプロジェクト群（Destination）に Terraform (IaC) を用いて「まるごとコピー」し、べき等かつ安全に再現する。

## 2. 移行フローとアーキテクチャ
本ツールは `dst/config.yaml` に定義された設定に基づき、以下の手順で移行を自動化します。

```
[Original 環境]
      │
      ├── (1) CAI Scan & Snapshot Check (30日以内の有効なバックアップを確認)
      │
      ├── (2) Bulk Export (gcloud beta resource-config bulk-export によるHCL生成)
      │      └── 成果物: dst/terraform/raw/
      │
      ├── (3) HCL Customize (プロジェクトID置換、GCSバケットのリネームルール適用)
      │      └── 成果物: dst/terraform/active/
      │
      ├── (4) Terraform Apply (コピー先インフラの構築)
      │      └── 成果物: コピー先GCPリソース (VPC, Subnet, FW等)
      │
      ├── (5) GCE VM 復元 (スナップショットから復元したディスクをVMに紐付け起動)
      │
      └── (6) データ同期 (GCS/BQなどのデータ移行・rsync)
```

---

## 3. 各移行ステップの詳細仕様

### 3.1. [Step 1] 現状確認 & バックアップ検証 (`cai_scan` & `gce_snapshot`)
1. **CAIスキャン**: コピー元プロジェクトで稼働しているリソースを探索し、移行対象リストを作成します。
2. **スナップショット検証**: 
   - 各VMのブートディスクに対し、`config.yaml` で指定された期間内（デフォルト: 30日以内）に作成された有効なスナップショットが存在するか確認します。
   - 条件を満たすスナップショットがない場合はエラーとし、実行を中断して手動作成を促します（Original環境への書き込み権限を持たないため、ツール側での自動作成は行いません）。

### 3.2. [Step 2] Terraformコードの自動エクスポート (`bulk_export`)
- `gcloud beta resource-config bulk-export` を実行し、コピー元プロジェクトのリソース定義を Terraform HCL コードとしてエクスポートします。
- エクスポートされた生コードは `dst/terraform/raw/` に出力されます。

### 3.3. [Step 3] HCL コードの自動カスタマイズ (`customize_tf`)
`dst/terraform/raw/` に出力された HCL コードを自動パース・置換し、`dst/terraform/active/` に出力します。
- **プロジェクトID置換**: 全てのリソース定義において、`project = "src-id"` を `project = "dst-id"` に置換します。
- **GCSバケット名リネーム**: `rename_rules.gcs` に従い、グローバルユニーク制約のあるバケット名をリネームします（例: `org-bucket` -> `org-bucket-dst-0602`）。
- **共有VPC接続の整合性確保**: ホストプロジェクトとサービスプロジェクトの接続関係（関連付け）をコピー先IDで正しく再定義します。
- **VMディスクソースの書き換え**: 
  - VMインスタンス定義において、ディスクの初期化パラメータを、スナップショットから作成したカスタムイメージ（またはディスク）を参照するように書き換えます。

### 3.4. [Step 4] インフラ再現 (`terraform_apply`)
- `dst/terraform/active/` ディレクトリにて、以下のコマンドを順次実行します。
  ```bash
  terraform init
  terraform apply -auto-approve
  ```
- これにより、ネットワーク、サブネット、ファイアウォールルール、および空のVM（またはOS初期状態のVM）が構築されます。

### 3.5. [Step 5] GCE VM データ復元 (`gce_restore`)
- スナップショットから作成したディスクを、Terraformで作成されたVMインスタンスのブートディスクとしてアタッチし、VMを起動します。
- これにより、Original環境のOS状態、データ、インストール済みアプリケーション（Nginx等）が完全に保持された状態でVMがクローン復元されます。

### 3.6. [Step 6] データ同期 (`data_sync`)
- `gsutil -m rsync` 等を使用し、OriginalのGCSバケット内のデータを、リネームして再構築されたDestinationのGCSバケットに高速同期します。
- BigQueryのデータセットおよびテーブルについても同様にコピー処理を行います。

---

## 4. 認証と安全要件
- **サービスアカウントキー (JSON) の排除**:
  キーファイルの漏洩を防ぐため、JSON キーは使用しません。認証方法は 2 つあり、`config.yaml` の
  `*_impersonate_service_account` で切り替えます。
  - **ローカル認証**（同フィールドを空にする）: ログイン中の実行ユーザー（`gcloud auth login` +
    `gcloud auth application-default login` / ADC）の権限で動作する。
  - **サービスアカウントの権限借用 (Impersonation)**（同フィールドに SA を指定）: 指定 SA を借用して動作する。
- **不変性の保証 (Originalの保護)**:
  - コピー元 (Original) は read-only。`side="src"` のコマンドは `is_src_read_only` ガードで書き込み動詞を
    実行前に拒否する（impersonate の有無に関わらず常時有効）。
  - **src（オリジナル）を一切書き換えたくない場合のおすすめは、SA を使わないローカル認証**。
    実行ユーザーに src の **閲覧者 (Viewer)** 相当を用意し（既存の読取権限を流用してもよい）、src 側に
    SA を作らず IAM も変更しない。Impersonation で src を借用する場合は src 側に SA 作成 + ロール付与が
    必要になり、オリジナルへの IAM 書き込みが発生する点に注意。
  - コピー先 (Destination) は書き込みが必要。ローカル認証なら実行ユーザーに **編集者 (Editor)** 相当を、
    Impersonation なら Editor 相当の専用 SA を指定する。

---

## 5. 設定ファイル仕様 (`dst/config.yaml`)

### 5.1. スキーマ定義

#### `global` (共通設定)
- `log_dir` (string, 必須): ログ出力先。
- `org_log_file` (string, 任意): コピー元作業ログファイル名。デフォルト `"org.log"`。
- `dst_log_file` (string, 任意): コピー先作業ログファイル名. デフォルト `"dst.log"`。
- `parallel_jobs` (integer, 任意): 並列実行数。デフォルト `4`。
- `dry_run` (boolean, 任意): ドライランモード。デフォルト `true`。
- `verbose_logging` (boolean, 任意): 詳細ログ（生コマンドおよび実行補足説明）の出力有無。デフォルト `true`。

#### `bootstrap` (事前準備設定)
- `org_id` (string, 必須): 組織ID。
- `folder_id` (string, 任意): フォルダID。
- `billing_account` (string, 必須): 請求先アカウントID。

#### `project_mapping` (プロジェクト対応定義)
- `host_project` (object, 必須): 共有VPCホストプロジェクト。
  - `src` / `dst` (string, 必須): コピー元 / コピー先 プロジェクトID。
  - `src_impersonate_service_account` (string, 任意): コピー元操作用の借用 SA。空ならローカル認証。指定する場合は読み取り専用 SA。
  - `dst_impersonate_service_account` (string, 任意): コピー先操作用の借用 SA。空ならローカル認証。指定する場合は編集者以上の SA。
- `service_projects` (array of objects, 必須): サービスプロジェクトのリスト。
  - `src` / `dst` (string, 必須): 同上。
  - `src_impersonate_service_account` / `dst_impersonate_service_account` (string, 任意): 同上（空ならローカル認証）。

#### `rename_rules` (リネームルール定義)
- `gcs` (object, 必須): GCSバケットのリネーム規則。
  - `method` (string, 必須): `suffix` (接尾辞), `prefix` (接頭辞), `custom` のいずれか。
  - `value` (string, 任意): `method` が `suffix`/`prefix` の場合に付与する文字列。
  - `overrides` (map, 任意): 個別の明示的マッピング。

#### `steps` (ステップ制御)
- `cai_scan` / `bulk_export` / `terraform_apply` / `gce_restore` / `data_sync` (object):
  - `enabled` (boolean, 必須): 有効フラグ。
  - `output_dir` (string, 任意): ステップ個別出力先。
- `gce_snapshot` (object):
  - `enabled` (boolean, 必須): 有効フラグ。
  - `max_age_days` (integer, 任意): 許容経過日数。デフォルト `30`。

---

## 6. テストおよび検証計画

### 6.1. 単体テスト方針 (HCL置換エンジン)
- `tests/test_tf_customizer.py` を作成し、エクスポートされた生の `.tf` コードが、マッピングルールに従って正しく置換されるかを検証します。
- **テストケース**:
  - プロジェクトIDの置換（`project = "src"` -> `project = "dst"`）
  - GCSバケット名の接尾辞追加（`bucket = "my-bucket"` -> `bucket = "my-bucket-dst-0602"`）
  - GCSバケットの個別マッピングオーバーライドの適用
  - 複数ファイルに跨る依存関係の解決

---

## 7. ログ出力・可視性仕様 (Logging & Visibility)
本ツールは、実行中の各操作の透明性を高め、デバッグやドライラン時の検証を容易にするため、詳細な実行履歴（コマンドおよび補足説明）をログに記録します。

### 7.1. ログ記録の基本ルール
1. **外部コマンドの可視化**:
   - `gcloud` や `terraform`, `gsutil` などの外部システムコマンドを呼び出す際、実際にシェルに渡される**生のコマンド文字列および引数**をログに記録します。
2. **人間が理解しやすい補足説明の付与**:
   - コマンド実行の成否だけでなく、「今何の目的でそのコマンドを実行しようとしているか」の補足解説メッセージを必ず各ログ行またはコマンド出力の前に記録します。
   - 例: `[GCSデータ同期] コピー元バケット "org-data" から コピー先バケット "org-data-dst-0602" へデータを同期しています...`
3. **ドライランモードへの適用**:
   - `dry_run: true` 時においても、実際にコマンドを実行するのと全く同じログフォーマットで「実行予定のコマンド」と「補足説明」をログおよび標準出力に出力します。

### 7.2. 並列実行時のログ混濁防止
- `parallel_jobs` を 2 以上に設定して並列実行を行う場合、複数のスレッドのログが混ざり合って解読不能になるのを防ぐため、すべてのログ出力に以下の識別タグを自動的に付与します。
  - 識別タグのフォーマット: `[ステップ名] [スレッドID / 対象オブジェクト名]`
  - 例: `[2026-06-02 04:29:11] [INFO] [cai_scan] [Thread-1 / service-project-1] [CAIスキャン開始] プロジェクト "<SRC_SERVICE_PROJECT_ID_1>" のリソースを探索しています...`
