# SPEC: GCPプロジェクトまるごとコピー (Original to Destination)

## 1. 目的
既存のGCPマルチプロジェクト環境（Original）を、構成およびデータを保持したまま、新しいGCPプロジェクト群（Destination）に「まるごとコピー」して再現する。

## 2. 対象環境（Original）
`DST.md` に記載された以下の構成を対象とする。

### 2.1. プロジェクト構造
- **Host Project**: `shingo-ar-sharedhost0926`
- **Service Project 1**: `shingo-ar-sharedservice0926-1`
- **Service Project 2**: `shingo-ar-sharedservice0926-3`

### 2.2. 主な移行対象リソース
- **ネットワーク**: 共有VPC (`shared-vpc`), サブネット, Cloud Router, Cloud NAT
- **コンピューティング**: GCE VMインスタンス（Debian 12 x 5, Ubuntu 22.04 x 6）、内部固定IP設定
- **その他**: （CAIスキャンにより追加検出されるリソース、IAM、有効化されたAPI等）

## 3. 移行先環境（Destination）
移行先のプロジェクトIDは、実行時に指定されるものとする（仮称として以下を使用）。
- **Host Project (Dst)**: `dst-sharedhost`
- **Service Project 1 (Dst)**: `dst-sharedservice-1`
- **Service Project 2 (Dst)**: `dst-sharedservice-3`

## 4. 移行アプローチ・要件
1. **コード化 (IaC)**: `gcloud` を用いて既存リソースをTerraformコードとしてエクスポートし、マッピング情報を修正して適用する。
2. **データ整合性**: GCEのディスクデータはスナップショット/イメージ経由で確実に移行する。
3. **順序の整合性**: 
   1. 基礎インフラ（VPC、サブネット、共有VPC設定）の構築
   2. VMインスタンスおよびディスクの復元
   3. データオブジェクト（GCS等がある場合）の同期
4. **Original環境の安全確保 (不変性の保証)**:
   - Original環境へのアクセスはすべて読み取り専用（Viewer等）の権限で行い、移行作業によってOriginal環境のリソース変更・破壊が決して発生しないようにする。
   - スナップショット作成など、Original側でのリソース作成が必要なステップについては、事前に手動で作成されたものを読み取る運用を基本とし、ツールによる書き込み権限の要求を排除する。
5. **クレデンシャルの安全管理 (サービスアカウントキーの排除)**:
   - セキュリティリスクの高いサービスアカウントキー（JSONファイル）は原則使用せず、**サービスアカウントの権限借用 (Impersonation)** を使用する。
   - 実行ユーザーに一時的なアクセストークンを発行させ、各プロジェクトの操作用サービスアカウント（Original用は読み取り専用、Destination用は編集者以上）の権限を借用して実行する。
6. **リソースのリネーム (グローバルユニーク制約への対応)**:
   - GCSバケットなど、グローバルで一意である必要があるリソース名は、コピー先での名前重複による衝突を避けるため、適切なルール（接尾辞の付与など）に基づいてリネームして再現する。
7. **スナップショットの有効性判定**:
   - 移行に使用するGCEスナップショットは、データの正確性を担保するため、一定期間内（デフォルトで1ヶ月/30日以内）に作成されたものであることを必須条件とする。条件を満たすスナップショットがない場合はエラーとする。



## 5. 成果物
- 移行に使用したTerraformコード一式
- プロジェクトマッピング定義ファイル
- 移行手順書（最終的な実行結果に基づく）
- 移行検証レポート（疎通確認、構成確認）

## 6. 設定ファイル仕様 (config.yaml)
プロジェクトコピーの動作を制御するため、`dst/config.yaml` を使用する。

### 6.1. スキーマ定義

#### global (共通設定)
- `log_dir` (string, 必須): ログ出力ディレクトリ。`org.log` と `dst.log` がこのディレクトリ内に生成される。
- `parallel_jobs` (integer, 任意): 並列実行数。デフォルトは `4`。
- `dry_run` (boolean, 任意): ドライランモード。デフォルトは `true`。安全のため、明示的に `false` にしない限り実際のリソース操作は行わない。

#### project_mapping (プロジェクト対応定義)
- `host_project` (object, 必須): 共有VPCホストプロジェクトの対応。
  - `src` (string, 必須): コピー元ホストプロジェクトID。
  - `dst` (string, 必須): コピー先ホストプロジェクトID。
  - `src_impersonate_service_account` (string, 必須): コピー元プロジェクト操作用の権限借用対象サービスアカウント（SA）のメールアドレス（読み取り専用権限を推奨）。
  - `dst_impersonate_service_account` (string, 必須): コピー先プロジェクト操作用の権限借用対象サービスアカウント（SA）のメールアドレス（編集者以上の権限が必要）。
- `service_projects` (array of objects, 必須): サービスプロジェクトの対応リスト。
  - `src` (string, 必須): コピー元サービスプロジェクトID。
  - `dst` (string, 必須): コピー先サービスプロジェクトID。
  - `src_impersonate_service_account` (string, 必須): コピー元プロジェクト操作用の権限借用対象サービスアカウント（SA）のメールアドレス（読み取り専用権限を推奨）。
  - `dst_impersonate_service_account` (string, 必須): コピー先プロジェクト操作用の権限借用対象サービスアカウント（SA）のメールアドレス（編集者以上の権限が必要）。

#### rename_rules (リネームルール定義)
グローバルユニークである必要があるリソースのリネーム規則。
- `gcs` (object, 必須): GCSバケットのリネームルール。
  - `method` (string, 必須): リネーム方法。`suffix` (接尾辞), `prefix` (接頭辞), `custom` のいずれか。
  - `value` (string, 任意): `method` が `suffix`/`prefix` の場合に付与する文字列。
  - `overrides` (map, 任意): 特定のバケットに対する個別マッピング定義（元のバケット名: 移行先のバケット名）。

#### steps (ステップ制御)
各移行ステップの有効/無効および個別設定。
- `cai_scan` (object): CAIスキャン設定。
  - `enabled` (boolean, 必須): ステップの有効化フラグ。
  - `output_dir` (string, 必須): スキャン結果の出力先。
- `bulk_export` (object): Terraformエクスポート設定。
  - `enabled` (boolean, 必須): ステップの有効化フラグ。
  - `output_dir` (string, 必須): エクスポートしたTerraformコードの格納先。
- `gce_snapshot` (object): GCEスナップショット設定。
  - `enabled` (boolean, 必須): ステップの有効化フラグ。
  - `prefix` (string, 必須): 作成するスナップショットの接頭辞。
  - `max_age_days` (integer, 任意): 許容するスナップショットの最大作成経過日数。デフォルトは `30`（約1ヶ月）。
- `terraform_apply` (object): Terraform適用設定。
  - `enabled` (boolean, 必須): ステップの有効化フラグ。
- `shared_vpc` (object): 共有VPC設定。
  - `enabled` (boolean, 必須): ステップの有効化フラグ。
- `gce_copy` (object): GCE復元設定。
  - `enabled` (boolean, 必須): ステップの有効化フラグ。
- `data_migration` (object): データ移行設定。
  - `enabled` (boolean, 必須): ステップの有効化フラグ。
