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
- **Host Project (ホストプロジェクト)**: `shingo-ar-sharedhost0926` (オリジナル)
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
- コピー先ホストプロジェクトID: (例: `shingo-ar-dsthost0926`)
- コピー先サービスプロジェクト1ID: (例: `shingo-ar-dstservice0926-1`)
- コピー先サービスプロジェクト3ID: (例: `shingo-ar-dstservice0926-3`)

---

## 自動構築・同期複製ツール仕様

本ツールは、環境の構築（Deploy）、削除（Destroy）、バックアップ（Snapshot）に加え、オリジナル環境の動的スキャン（Scan）、コピー先のAPI自動有効化（Prepare）、およびスナップショットからのクローン復元（Sync）をサポートする。

### 1. インターフェース (Makefile)
ユーザーは `make` コマンドを介してツールを実行する。

- **`make projects`**: `dst/config.yaml` に基づき、コピー先（Destination）プロジェクト群を新規作成し、請求先アカウントの紐付けと必要なAPIの有効化を行います。
- **`make plan`**: ドライランモードで実行計画（予定されるgcloud/terraformコマンドと日本語補足）を表示します。
- **`make run`**: コピー先プロジェクト群に対し、移行処理（スキャンからクローン同期まで）を本番実行します。
- **`make test`**: ツール全体の単体テストを実行します。

### 2. 実装要件

#### 2.1. 言語・実行環境
- Python 3.12以上、`uv`、PEP8準拠、`pytest` によるテスト。

#### 2.2. 主要ロジック (Pythonスクリプト)

##### A. プロジェクト作成 (scripts/create_projects.py)
コピー先プロジェクトを自動作成し、ブートストラップ（初期化）を行う。
1. `dst/config.yaml` の `bootstrap` および `project_mapping` を読み込む。
2. コピー先ホストプロジェクトおよびサービスプロジェクトの `dst` IDを抽出。
3. 各プロジェクトについて、未存在の場合は `gcloud projects create` で作成（組織IDまたはフォルダID配下）。
4. `gcloud beta billing projects link` で請求先アカウントを紐付ける。
5. `gcloud services enable` で必要なAPI（`compute.googleapis.com` など）を有効化する。

##### B. 環境複製コアオーケストレータ (scripts/sync_env.py)
`scripts/sync_env.py` が一連のステップ（1〜7）を統合して実行するコアオーケストレータである。

1. **モック実行モード (Mock Mode)**:
   - 設定ファイル (`global.mock: true`) またはコマンドライン引数 (`--mock`) が指定された場合、実際のGCP APIおよびTerraformの呼び出しをシミュレートする。
   - `run_command` は、実際のコマンドを実行する代わりに、コマンドに応じたダミーデータ（VMリストJSON、スナップショットリストJSON、ダミーHCLファイルなど）を生成して返す。
   - これにより、実際のGCP環境や有効なサービスアカウントがない状態でも、`make run` 全体のフローをエラーなしでテスト実行可能にする。

2. **[Step 1] CAI Scan (現状確認)**:
   - コピー元プロジェクトレベルでCAIを検索し、エクスポート対象のリソースリストを確定・保存する。
   - モックモード時は、ダミーのリソースリストファイルを生成する。
3. **[Step 2] GCE Snapshot Verification (バックアップ検証)**:
   - 最新のGCEスナップショット（デフォルトで30日以内）が存在するか検証し、なければエラーとする。
   - モックモード時は、ダミーのVMリストと最新のスナップショットリストをシミュレートし、常に検証成功とする。
4. **[Step 3] Bulk Export & HCL Customization (コード化とカスタマイズ)**:
   - `gcloud` の `bulk-export` を使用してTerraform HCLを生成。
   - モックモード時は、ダミーの `.tf` ファイル群を自動生成する。
   - 生成されたHCL内のプロジェクトIDの置換、GCSバケット名のリネーム、およびVMの `boot_disk.source` 行の削除を自動で行う。
5. **[Step 4] Terraform Apply (IaC再現)**:
   - カスタマイズされたTerraformコードを適用し、コピー先にインフラを再構築する。
   - モックモード時は、適用成功をシミュレートする。
6. **[Step 5] GCE VM Restore (スナップショットからのクローンVM復元)**:
   - コピー先に作成されたダミーVMのディスクを一旦デタッチ・削除し、コピー元のスナップショットから復元した本物ディスクをアタッチして起動する。
   - モックモード時は、一連の `gcloud` コマンド（stop, detach, delete, create, attach, start）の成功をシミュレートする。
7. **[Step 6] Data Sync (データ同期)**:
   - GCSバケットの同期（`gcloud storage rsync`）およびBigQueryデータセット・テーブルの同期を実行する。
   - モックモード時は、バケット一覧やデータセット一覧の取得および同期コマンドの成功をシミュレートする。
8. **[Step 7] VPC Service Controls (ペリメタ追加)**:
   - 全データ移行の最後に、dst プロジェクト（番号）を既存の VPC SC ペリメタへ `--add-resources` で追記する（org / access policy 自体は作成・変更しない・冪等）。
   - `access-context-manager` は org/policy スコープで `--project` を持たないため、quota project を `steps.vpc_sc.billing_project`（**必須・明示指定**）で与える。未指定だとローカル `gcloud config` の無関係なプロジェクトが quota に使われ `SERVICE_DISABLED` で失敗するため、安全側に倒して未設定ならスキップする（自動推測しない）。
   - モックモード時は、ペリメタ describe / update / API 有効化コマンドの成功をシミュレートする。

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

### 3. 安全対策と堅牢性 (べき等性の確保とログ記録)
- **べき等性の維持**: すべてのステップでべき等チェックを徹底し、すでに完了している処理はスキップする。
- **ログの分離**: コピー元の読み取り操作は `org.log`、コピー先への書き込み・変更操作は `dst.log` に記録する。
- **サービスアカウント権限借用**: セキュリティのため静的キーは使用せず、`CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT` 環境変数による動的権限借用を使用する。

