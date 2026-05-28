# 技術解説: `make scan-org` (実機環境自動スキャン/Discovery) の仕組み

`make scan-org` コマンドは、現在稼働しているオリジナルのGCPインフラを動的にスキャンし、その構成要素（共有VPC、サブネット、関連プロジェクト、VMスペック、OSイメージ、固定IPアドレス等）を自動発見（Discovery）して、コピー先設計図である `dst/DST.md` を自動生成するシステムです。

---

## ⚙️ 処理フローと内部発行コマンド詳細

スキャナー (`scripts/scan_env.py`) は、ホストプロジェクトを起点として以下の順番で動的探索を実行します。

### Stage 1: 共有VPCホストの有効性検証
指定されたホストプロジェクトが、GCP上で「共有VPCホスト」として正しく有効化されているかを検証します。非ホストプロジェクトを指定した場合は即時エラー終了します。
- **内部発行コマンド**:
  ```bash
  gcloud compute shared-vpc associated-projects list --project=<SRC_HOST_PROJECT_ID>
  ```
- **仕組み**:
  Shared VPCが有効化されていない場合、このコマンドは Exit Code 1 (エラー) を返すため、それを利用してホスト状態を厳格に検証します。

### Stage 2: 関連するサービスプロジェクトの動的ロード
共有VPCネットワークに紐付けられている（共有先となっている）すべてのサービスプロジェクトIDを自動発見します。
- **内部発行コマンド**:
  ```bash
  gcloud compute shared-vpc associated-projects list --project=<SRC_HOST_PROJECT_ID> --format="value(id)"
  ```
- **仕組み**:
  取得したサービスプロジェクトIDの一覧（例: `<SRC_SERVICE_PROJECT_ID_1>`, `<SRC_SERVICE_PROJECT_ID_3>`）を変数に格納し、後続のVM探索スコープとして動的に使用します。

### Stage 3: サブネット定義の動的ロード
共有VPC内のサブネット一覧を探索し、それぞれのIPCIDR範囲とデプロイされているリージョンを取得します。
- **内部発行コマンド**:
  ```bash
  gcloud compute networks subnets list --network=shared-vpc --project=<SRC_HOST_PROJECT_ID> --format="json(name, ipCidrRange, region)"
  ```
- **仕組み**:
  返却されたJSONをパースし、リージョン名（例: `asia-northeast1`）を動的に特定します。また、サブネット名（例: `subnet-svc1`）に基づいて、共有先プロジェクトのマッピング関係を動的に解決します。

### Stage 4: サービスプロジェクト内の VM インスタンス自動発見
各サービスプロジェクト内にデプロイされているVMインスタンスの情報を一括探索します。
- **内部発行コマンド**:
  ```bash
  gcloud compute instances list --project=<SRC_SERVICE_PROJECT_ID_1> --format="json(name, zone, machineType, networkInterfaces)"
  ```
- **仕組み**:
  1. **定義外リソースのフィルタリング**:
     VM名が `org-` から始まらない、手動作成された無関係なVMインスタンス（例: `instance-1`）は、スキャン計画から自動的に除外（スキップ）します。
  2. **インフラ構成の抽出**:
     各VMのゾーン（例: `asia-northeast1-a`）、マシンタイプ（例: `e2-standard-4`）、内部固定IP（例: `10.100.1.11`）、および所属サブネット（例: `subnet-svc1`）をJSONデータから正確に抽出します。

### Stage 5: 高度なOSイメージ種別の自動識別
GCP上では、作成済みのVMインスタンスのブート元OSイメージ情報は直接インスタンス一覧からは取得できません。本ツールは、**ブートディスクのライセンスURL**を動的にパースすることで、DebianかUbuntuかを100%正確に識別します。
- **内部発行コマンド**:
  ```bash
  gcloud compute instances describe org-svc1-deb-e2-std4-01 --zone=asia-northeast1-a --project=<SRC_SERVICE_PROJECT_ID_1> --format="json(disks)"
  ```
- **解析・判定ロジック**:
  返却された `disks` のネストJSONから、`boot: true` (ブートディスク) となっているオブジェクトの `licenses` 配列をロードします。
  ```json
  "licenses": [
    "https://www.googleapis.com/.../projects/debian-cloud/global/licenses/debian-12-bookworm"
  ]
  ```
  - ライセンスURL内に `/debian-cloud/` を検出 ➔ **`debian-12`** と判定。
  - ライセンスURL内に `/ubuntu-os-cloud/` を検出 ➔ **`ubuntu-2204-lts`** と判定。
  これにより、手動でスペックシートを作ることなく、実機のOS構成を完全自動判別します。

### Stage 6: `dst/DST.md` への自動レンダリング
スキャンが完了すると、収集したすべての構造化データを整理し、オリジナル定義と完全に互換性のある美しいMarkdownテーブル構造として `dst/DST.md` に書き出します。これが、次フェーズの完全同期クローンにおける「あるべき設計図（Single Source of Truth）」となります。
