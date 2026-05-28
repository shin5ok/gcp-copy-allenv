# SPECIFICATION: 環境コピー元（Original）構成定義

## 概要
本プロジェクトは、既存 of Google Cloud環境（オリジナル）の構成情報を定義し、それを元に新しい環境へコピー（作成）するための基盤を構築する。
本仕様書では、コピー元となる環境 of 定義ファイル `ORG.md` の仕様について規定する。

## `ORG.md` の役割
- コピー元のプロジェクト、ネットワーク（共有VPCを想定）、およびVMインスタンスの情報を記述する。
- 後続 of 自動化スクリプトや `gcloud` コマンド群が、このファイルをインプットとして解釈できるように構造化して記述する。

## 構成要素

### 1. プロジェクト構造とネットワーク
共有VPC（Shared VPC）構成を前提とする。
- **Host Project (ホストプロジェクト)**: `shingo-ar-sharedhost0926`
  - 共有VPCネットワーク (`shared-vpc`) を管理。
  - 各サービスプロジェクト用にサブネットを作成し、権限を付与する。
  - プライベートVMがインターネット通信（外部アップデート等）を行えるよう、Cloud Router および Cloud NAT ゲートウェイを配置・管理する。
  - **踏み台サーバー等を使わず、安全に gcloud compute ssh (IAP トンネリング) 経由でプライベートVMにアクセスできるよう、IAP専用のファイアウォール許可ルール (tcp:22) を配置・管理する。**
- **Service Projects (サービスプロジェクト)**: VMインスタンスなどのコンピューティングリソースを配置するプロジェクト。
  - ホストプロジェクトから共有されたサブネットを利用してVMを配置する。
  - VMには外部IPアドレスを付与せず、安全に Cloud NAT 経由でのみインターネットに発信通信を行えるようにする。

### 2. プロジェクト一覧と詳細
ユーザーの指定に基づき、以下のプロジェクトおよびネットワーク構成を定義する。

#### Host Project
- プロジェクトID: `shingo-ar-sharedhost0926`
- 共有VPCネットワーク名: `shared-vpc`
- **インターネット接続ゲートウェイ**:
  - Cloud Router名: `shared-router` (リージョン: `asia-northeast1`)
  - Cloud NAT名: `shared-nat` (すべてのサブネットの全IP範囲を対象、外部IP自動割り当て)
- **セキュアアクセス (IAP SSH)**:
  - ファイアウォールルール名: `allow-shared-iap-ssh` (INGRESS, ALLOW, ソースIP: `35.235.240.0/20`, ポート: `tcp:22`)
- サブネット定義:
  - Service 1用サブネット: `subnet-svc1` (IP範囲: `10.100.1.0/24`)
  - Service 3用サブネット: `subnet-svc3` (IP範囲: `10.100.3.0/24`)

#### Service Project 1
- プロジェクトID: `shingo-ar-sharedservice0926-1`
- サブネット: `subnet-svc1` (Host Projectから共有)
- インスタンス数: 5台 (すべて固定内部IPを割り当て)
  - マシンタイプ: `e2-standard-4` x 3台 (IP: `10.100.1.11` ~ `10.100.1.13`)
  - マシンタイプ: `n2-standard-4` x 2台 (IP: `10.100.1.14` ~ `10.100.1.15`)
  - OS: Debian (最新の安定版を想定)

#### Service Project 3
- プロジェクトID: `shingo-ar-sharedservice0926-3`
- サブネット: `subnet-svc3` (Host Projectから共有)
- インスタンス数: 6台 (すべて固定内部IPを割り当て)
  - マシンタイプ: `e2-medium` x 3台 (IP: `10.100.3.11` ~ `10.100.3.13`)
  - マシンタイプ: `e2-micro` x 2台 (IP: `10.100.3.14` ~ `10.100.3.15`)
  - マシンタイプ: `c2-standard-4` x 1台 (IP: `10.100.3.16`)
  - OS: Ubuntu (最新のLTSを想定)


## `ORG.md` のフォーマット仕様
Markdown形式で記述する。
パースの容易性を考慮し、表（Table）形式またはリスト形式で構造化する。
将来的に自動化ツールで読み込むことを考慮し、キーと値が明確になるように記述する。

### インスタンス名の命名規則（仮）
具体的なインスタンス名が指定されていないため、以下のデフォルトルールを適用する。
- 形式: `[プロジェクトの略称]-[マシンタイプ]-[連番]`
- 例:
  - Service 1: `svc1-e2-std4-01`, `svc1-e2-std4-02`, ...
  - Service 3: `svc3-e2-med-01`, ...

---

## 自動構築ツール仕様

`ORG.md` に定義された構成に基づき、GCP上に実際のリソースを自動構築するツールを実装する。

### 1. インターフェース (Makefile)
ユーザーは `make` コマンドを介してツールを実行する。

- **`make plan`**:
  - `ORG.md` を読み込み、実行される予定の `gcloud` コマンド（ドライラン結果）を表示する。
  - 実際のリソース作成は行わない。
- **`make deploy`**:
  - 実際のリソース作成処理を実行する。
  - 実行前にユーザーに確認プロンプトを表示する。
- **`make destroy`**:
  - `ORG.md` に定義されたリソースをすべて削除（クリーンアップ）する。
  - 実行前に、意図しない削除を防ぐための強力な確認プロンプト（赤字警告、あるいは「destroy」と文字列を入力させる等）を表示する。

### 2. 実装要件

#### 2.1. 言語・実行環境
- **Python 3.12以上** を使用。
- パッケージ管理および実行には **`uv`** を使用.
- コードスタイルは **PEP8** に準拠。
- 単体テストは `tests/` ディレクトリ配下に `pytest` で記述。

#### 2.2. 主要ロジック (Pythonスクリプト)
スクリプト名: `scripts/build_env.py` (仮)

1. **パース機能**:
   - `org/ORG.md` を解析し、以下の情報を抽出する。
     - ホストプロジェクトID、サービスプロジェクトID
     - 共有VPCのネットワーク名、サブネット名、IP範囲
     - 各VMのインスタンス名、マシンタイプ、OSイメージ、サブネット、内部固定IPアドレス
2. **`gcloud` コマンド生成**:
   - 抽出した情報に基づき、リソース作成に必要な `gcloud` コマンドを生成する。
   - 生成するコマンドの例:
     - 共有VPCの有効化
     - Cloud Routerの作成: `gcloud compute routers create shared-router --network=shared-vpc --region=asia-northeast1 --project=shingo-ar-sharedhost0926`
     - Cloud NATの作成: `gcloud compute routers nats create shared-nat --router=shared-router --region=asia-northeast1 --auto-allocate-nat-external-ips --nat-all-subnet-ip-ranges --project=shingo-ar-sharedhost0926`
     - **IAP SSHファイアウォールルールの作成**: `gcloud compute firewall-rules create allow-shared-iap-ssh --network=shared-vpc --allow=tcp:22 --source-ranges=35.235.240.0/20 --direction=INGRESS --project=shingo-ar-sharedhost0926`
     - サブネットの作成
     - 固定内部IPアドレスの予約: `gcloud compute addresses create ... --addresses ... --subnet ... --region ... --project ...`
     - VMインスタンスの作成: `gcloud compute instances create ... --machine-type ... --image ... --subnet ... --private-network-ip ... --zone ... --project ...`
3. **実行制御 (ドライランと適用)**:
   - `--dry-run` フラグが有効な場合、生成した `gcloud` コマンドを標準出力に表示するのみとする。
   - フラグが無効な場合、コマンドをサブプロセスとして実行する。実行前に「本当に実行しますか？ [y/N]」の確認を行う。
4. **削除機能 (Destroy)**:
   - `--destroy` フラグが指定された場合、作成時とは逆の順序でリソースの削除を行う。
   - 削除時も事前に存在チェックを行い、存在する場合のみ削除コマンドを実行する（べき等性）。
   - 削除コマンドの例:
     - VMインスタンスの削除: `gcloud compute instances delete ... --zone ... --project ... --quiet`
     - 固定内部IPアドレスの解放: `gcloud compute addresses delete ... --region ... --project ... --quiet`
     - サブネットの削除: `gcloud compute networks subnets delete ... --region ... --project ... --quiet`
     - Cloud NATの削除: `gcloud compute routers nats delete shared-nat --router=shared-router --region=asia-northeast1 --project=shingo-ar-sharedhost0926 --quiet`
     - Cloud Routerの削除: `gcloud compute routers delete shared-router --region=asia-northeast1 --project=shingo-ar-sharedhost0926 --quiet`
     - **IAP SSHファイアウォールルールの削除**: `gcloud compute firewall-rules delete allow-shared-iap-ssh --project=shingo-ar-sharedhost0926 --quiet`
     - プロジェクト関連付け解除: `gcloud compute shared-vpc associated-projects remove ... --host-project ... --quiet`
     - 共有VPC無効化: `gcloud compute shared-vpc disable ... --quiet`
     - VPCの削除: `gcloud compute networks delete ... --project ... --quiet`
   - `--yes` フラグがない場合は、非常に慎重な確認（例: 「本当にすべて削除しますか？ 削除する場合は [YES] と入力してください」）を求める。
5. **並列実行 (Parallel Execution)**:
   - リソース間の依存関係を考慮し、処理を複数の **ステージ (Stage)** に分割して実行する。
   - 同一ステージ内の依存関係がないタスク（例: 異なるVMの作成や削除）は、スレッドプール (`concurrent.futures.ThreadPoolExecutor`) 等を用いて **並列に非同期実行** する。
   - これにより、特にVM台数が多い環境において、構築および削除時間を大幅に短縮する。
   - **構築時のステージ設計**:
     - Stage 1 (同期): VPC作成 -> Shared VPC有効化 -> サービスプロジェクト関連付け -> Cloud Router作成 -> Cloud NAT作成 -> **IAP SSHファイアウォール作成**
     - Stage 2 (同期): サブネット作成
     - Stage 3 (並列): 各VM用の固定IP予約 & VMインスタンス作成 (※VMごとに「IP予約 -> VM作成」を一連のタスクとして並列実行)
   - **削除時のステージ設計**:
     - Stage 1 (並列): VMインスタンスの削除 (全台並列)
     - Stage 2 (並列): 固定IPの解放 (全IP並列)
     - Stage 3 (並列): サブネットの削除 (全サブネット並列)
     - Stage 4 (同期): **IAP SSHファイアウォール削除** -> Cloud NAT削除 -> Cloud Router削除 -> プロジェクト関連付け解除 -> Shared VPC無効化 -> VPC削除
6. **ステート管理 (`state.json`)**:
   - `make deploy` にて実際に作成（成功）したリソース情報を、動的に `state.json` に記録・保存する。
   - 記録情報: `resource_type`, `resource_name`, `project`, `check_cmd`, `delete_cmd` (削除用コマンド)。
   - 途中でデプロイが失敗した場合でも、そこまでに成功したリソースのみが `state.json` に記録される。
   - `make destroy` 実行時は、`ORG.md` の静的定義ではなく、この `state.json` を読み込み、**実際に作成された実績のあるリソースのみを逆順で削除** する。
   - これにより、定義外の他リソース（手動作成されたVMなど）を誤って削除対象に含むことを完全に防止する。
   - 削除に成功したリソースは順次 `state.json` から除外され、すべてのクリーンアップが完了すると `state.json` は自動的に削除される。

### 3. Safety measures (べき等性の確保とログ記録)

- **べき等性 (Idempotency) の確保**:
  - リソース作成・削除を実行する前に、対象リソースが既に存在するか（または削除済みか）を `gcloud ... describe` コマンドや `state.json` の記録を元にチェックする。
  - `make deploy` 時に作成済みリソースはスキップし、`make destroy` 時に削除済みリソースはスキップする。
  - ステートファイルを用いることで、より厳密なべき等性とレジューム（失敗箇所からの再開）を実現する。
- **エラーハンドリング**:
  - 各リソース作成コマンドの実行結果を厳格にチェックし、エラーが発生した場合は即座に処理を中断する。
  - スキップされた処理、成功した処理、失敗した処理を画面上に明確に区別して出力する（カラー出力等を推奨）。
- **詳細なログ出力と記録**:
  - 実行結果（コマンド、実行日時、成否、出力）をログファイル `build.log` に追加書き込み (Append) 形式で記録する。
  - 失敗時には、エラーとなった具体的な `gcloud` コマンドと標準エラー出力をログファイルに記録し、後から原因分析と個別再試行が容易になるようにする。
- **並列実行時のロググループ化 (Log Buffering)**:
  - 並列実行時にログが混ざり合って（インターリーブして）出力されるのを防ぐため、**タスク（リソース）ごとにログをメモリ上にバッファリング** する。
  - タスクの実行中（チェック、作成、成功、失敗）のログはバッファに溜め、そのタスクが完了（または失敗）した時点で、ひためまりのログブロックとして標準出力および `build.log` に一括してフラッシュ（出力）する。
  - これにより、並列実行の高速性を維持しつつ、シーケンシャル（時系列）で追いやすい極めて視認性の高いログ出力を実現する。
