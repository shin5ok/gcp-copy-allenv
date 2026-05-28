# 技術解説: `make sync-to-dst` (スナップショットからの同期クローンデプロイ) の仕組み

`make sync-to-dst` コマンドは、自動生成された設計図 `dst/DST.md` と、前フェーズで取得したVMの「ディスクスナップショット」をインプットとし、指定した別の新しいプロジェクト群に、**OS設定、インストール済みパッケージ、データなどを完全維持した状態で一撃で環境を複製（クローン）**するシステムです。

---

## 🛠️ コアとなる高度な技術メカニズム

### 1. 安全なマルチテナンシー置換 (`--project-map`)
オリジナル環境のリソースやVPCを壊したり誤って上書きしたりしないよう、コピー先へのプロジェクト差し替え（マッピング置換）を完全に自動化しています。
- **引数指定**:
  `--project-map コピー元ホスト=コピー先ホスト,コピー元サービス1=コピー先サービス1...`
- **仕組み**:
  `dst/DST.md` からパースされた `EnvConfig` のプロジェクトID（ホスト、サービス1、サービス3等）を、デプロイ開始前にメモリ上で動的にすべて新しいコピー先プロジェクトIDに差し替えます。これにより、コピー元のインフラに影響を与えることなく、新環境へ完全に複製が切り替わります。

### 2. ステージ分割と非同期マルチスレッド並列処理
構築時間の大幅な短縮のため、リソースの依存関係を整理し、以下の4つのステージで並列処理を行います。

#### Stage 1: VPC & Host Setup (SEQUENTIAL - 同期)
コピー先ホストプロジェクトにおいて、インフラの土台となる共有VPC、NAT、ルーター、FWルールを順に構築します。
- **内部発行コマンド例**:
  ```bash
  gcloud compute networks create shared-vpc --subnet-mode=custom --project=shingo-ar-dsthost0926
  gcloud compute shared-vpc enable shingo-ar-dsthost0926
  gcloud compute shared-vpc associated-projects add shingo-ar-dstservice0926-1 --host-project=shingo-ar-dsthost0926
  gcloud compute routers create shared-router --network=shared-vpc --region=asia-northeast1 --project=shingo-ar-dsthost0926
  gcloud compute routers nats create shared-nat --router=shared-router --region=asia-northeast1 --auto-allocate-nat-external-ips --nat-all-subnet-ip-ranges --project=shingo-ar-dsthost0926
  gcloud compute firewall-rules create allow-shared-iap-ssh --network=shared-vpc --allow=tcp:22 --source-ranges=35.235.240.0/20 --direction=INGRESS --project=shingo-ar-dsthost0926
  ```

#### Stage 2: Subnet Creation (PARALLEL - 並列)
VPCが完成したら、コピー先サービスプロジェクト用にサブネットを並列で作成し、ホストから共有します。
- **内部発行コマンド例 (2スレッド並列)**:
  ```bash
  gcloud compute networks subnets create subnet-svc1 --network=shared-vpc --range=10.100.1.0/24 --region=asia-northeast1 --project=shingo-ar-dsthost0926
  ```
  > 💡 **インフラノイズのフィルタリング**
  > 実機スキャンで検出された定義外の既存サブネット（`tokyo` 等）は、このステージから自動的に除外され、構築されません。

#### Stage 3: Disk Cloning from Snapshot (PARALLEL - 並列) - 【重要】
本システムの最大の特徴です。コピー元プロジェクトに存在するスナップショット（グローバルリソース）をフルパスで指定し、**コピー先のサービスプロジェクトの適切なゾーンにディスクとして直接クローン復元**します。
- **内部発行コマンド例 (11スレッド並列)**:
  ```bash
  gcloud compute disks create org-svc1-deb-e2-std4-01-disk --source-snapshot=projects/shingo-ar-sharedservice0926-1/global/snapshots/org-svc1-deb-e2-std4-01 --zone=asia-northeast1-a --project=shingo-ar-dstservice0926-1 --quiet
  ```
- **仕組み**:
  `projects/[コピー元プロジェクト]/global/snapshots/[スナップショット名]` というフルパスを指定することで、プロジェクトの境界を越えて、コピー先のプロジェクト側に直接ディスクとしてデータごと瞬時に復元させます。

#### Stage 4: VM Cloned Provisioning (PARALLEL - 並列) - 【重要】
先ほど復元された「クローンディスク」をブートディスクに指定し、外部IPなしでクローンVMインスタンスを並列デプロイします。
- **内部発行コマンド例 (11スレッド並列)**:
  ```bash
  gcloud compute instances create org-svc1-deb-e2-std4-01 --disk=name=org-svc1-deb-e2-std4-01-disk,boot=yes,auto-delete=yes --subnet=projects/shingo-ar-dsthost0926/regions/asia-northeast1/subnetworks/subnet-svc1 --private-network-ip=10.100.1.11 --zone=asia-northeast1-a --project=shingo-ar-dstservice0926-1 --no-address --quiet
  ```
- **仕組み**:
  - `--disk=name=[復元ディスク名],boot=yes,auto-delete=yes` オプションによって、新規にOSイメージから起動するのではなく、**データが完全に引き継がれたクローンディスクから直接ブート起動**させます。
  - このため、起動用メタデータスクリプト（Nginxのインストールなど）を通すことなく、**起動した瞬間にすでにNginxが自動動作する完全クローンVM**として再現されます。

---

## 🛡️ 堅牢性と運用の安全性

### 1. べき等性の確保とPre-check
各スレッドは処理の実行前に、コピー先プロジェクトにおいてリソースが「すでに存在するか」を厳密にPre-checkします。
- ディスクのチェック:
  `gcloud compute disks describe {ディスク名} --zone={ゾーン} --project={dst_project}`
- VMのチェック:
  `gcloud compute instances describe {VM名} --zone={ゾーン} --project={dst_project}`
すでに存在するリソースは安全にスキップ（`[SKIP]`）され、失敗した箇所からのクリーンな再試行が保証されます。

### 2. クローン先ステート管理と一撃撤去 (`state-sync.json`)
クローン作成に成功したリソース群は、動的に `state-sync.json` という別ステートファイルにリアルタイム保存されます。
これにより、コピー先環境のテストが終わった後は、以下のコマンドでクローンインフラだけを安全かつ完全に一撃クリーンアップできます。
```bash
make destroy ARGS="--config dst/DST.md --state-file state-sync.json"
```
これによって、複数の独立した環境の並行構築・管理を非常に安全に行うことが可能になります。
