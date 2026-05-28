# ウォークスルー: クローン同期複製 (`make sync-to-dst`) の検証および障害解決実績

本ドキュメントは、スナップショットを用いたコピー先環境への完全同期クローン複製において、発生した課題のトラブルシューティング、その原因、解決策、および実機における最終検証（Ping/Curl疎通、マシンタイプ適合）の再現手順・結果をまとめたものである。

---

## 📝 検証の前提条件

- **コピー元（移行元）**:
  - ホストプロジェクト: `<SRC_HOST_PROJECT_ID>`
  - サービスプロジェクト1 (Debian): `<SRC_SERVICE_PROJECT_ID_1>`
  - サービスプロジェクト3 (Ubuntu): `<SRC_SERVICE_PROJECT_ID_3>`
- **コピー先（移行先）**:
  - ホストプロジェクト: `<DST_HOST_PROJECT_ID>`
  - サービスプロジェクト1: `<DST_HOST_PROJECT_ID>`
  - サービスプロジェクト3: **意図的に移行対象外とし、完全除外（セーフガードの検証）**

---

## 🛠️ トラブルシューティングと解決実績

### 1. Shared VPCホスト関連付けエラー
- **事象**: Associated Projectの追加ステップで `is not a shared VPC host project` エラーが発生して停止。
- **原因**: `gcloud compute shared-vpc get-host-project` は、未有効化のプロジェクトであっても正常終了コード（Exit Code 0）を返すため、べき等チェックが「すでに有効化済み」と誤判定し、ホスト有効化ステップ（`enable`）を誤ってスキップしていた。
- **解決策**: べき等チェックコマンドを `gcloud compute shared-vpc associated-projects list` に刷新。未ホスト時は厳格にエラー（Exit Code 1）を返すようにし、有効化ステップが100%確実に実行されるように修正。

### 2. GCE間の接続不良 (Ping/Curl遮断)
- **事象**: 起動したクローンVM間で `ping` や `curl` が通らない。
- **原因**: カスタムサブネットネットワークの初期状態では、VPC内部の通信もデフォルトですべて遮断（INGRESS拒否）される。IAP SSH用のポート22以外のルールが存在しなかった。
- **解決策**: VPC内部通信をすべて許可するファイアウォールルール **`allow-shared-internal`** (ソース: `10.100.0.0/16`, プロトコル: `tcp,udp,icmp`) を自動構築ステージに統合。

### 3. クローンVMのマシンタイプ不一致 (n1-standard-2 へのフォールバック)
- **事象**: コピー先に復元されたVMのマシンタイプが、すべてデフォルトの `n1-standard-2` になってしまっていた。
- **原因**: `gcloud compute instances create` コマンド実行時に `--machine-type` 引数の指定が漏れていたため、GCPのデフォルトスペックが強制適用されていた。
- **解決策**: スキャン定義から読み取った正しいマシンタイプを `--machine-type={vm.machine_type}` としてコマンドに明示的に組み込むよう修正。

---

## 🚀 最終検証の再現手順と結果

### Step 1: コピー先プロジェクトのAPI事前準備 (`make prepare-dst`)
```bash
export DST_HOST_PROJECT_ID="<DST_HOST_PROJECT_ID>"
export DST_SVC1_PROJECT_ID="<DST_HOST_PROJECT_ID>"

make prepare-dst ARGS="--project-map <SRC_HOST_PROJECT_ID>=$DST_HOST_PROJECT_ID,<SRC_SERVICE_PROJECT_ID_1>=$DST_SVC1_PROJECT_ID -y"
```
- **結果**:
  - 2つのプロジェクトに対してAPI有効化状況が正確にチェックされ、`[SKIP] already enabled` で安全に正常終了。

### Step 2: スナップショットからの完全同期クローンデプロイ (`make sync-to-dst`)
```bash
make sync-to-dst ARGS="--project-map <SRC_HOST_PROJECT_ID>=$DST_HOST_PROJECT_ID,<SRC_SERVICE_PROJECT_ID_1>=$DST_SVC1_PROJECT_ID -y"
```
- **結果**:
  - **インフラの完全自動構築**: `shared-vpc`（VPC）、サブネット、NAT、IAP SSH FW、および内部通信用FW `allow-shared-internal` が無事に作成。
  - **セーフガードの動作**: `project-map` に指定しなかったサービス3（Ubuntu）関連のリソース（サブネット、VM 6台）は構築リストから**完璧に自動除外・スキップ**。
  - **ディスク・VMクローン**: スナップショットからディスクが並列復元され、正しいスペック（`e2-standard-4` / `n2-standard-4`）を指定してVMが一撃並列デプロイされ、`Environment Replication completed successfully` で大成功。

### Step 3: GCP実機上のマシンタイプ適合検証
```bash
gcloud compute instances describe org-svc1-deb-e2-std4-01 --zone=asia-northeast1-a --project=<DST_HOST_PROJECT_ID> --format='value(machineType)'
gcloud compute instances describe org-svc1-deb-n2-std4-01 --zone=asia-northeast1-a --project=<DST_HOST_PROJECT_ID> --format='value(machineType)'
```
- **結果**:
  - `org-svc1-deb-e2-std4-01` ➔ **`e2-standard-4`** (完全適合確認)
  - `org-svc1-deb-n2-std4-01` ➔ **`n2-standard-4`** (完全適合確認)

### Step 4: クローンVM同士の内部Ping疎通検証
`org-svc1-deb-e2-std4-01` から `10.100.1.12` (deb-e2-std4-02) に対して `ping` を送信。
```bash
ping -c 3 10.100.1.12
```
- **結果**:
  - **パケットロス 0%**。平均レイテンシ **`0.61 ms`** で完全に開通。

### Step 5: クローンVM同士のNginx Web/JSON疎通検証
同じVM間において、相手のWebサーバーポート80に対して `curl` を送信。
```bash
curl -s http://10.100.1.12
curl -s http://10.100.1.12/json
```
- **結果**:
  - 起動スクリプトなしの復元起動にもかかわらず、Nginxが即時自動動作し、相手マシンの正しいホスト名・IPが正確に返ってきた。
  - **/ (Text)**: `Hostname: org-svc1-deb-e2-std4-02`, `IP: 10.100.1.12`
  - **/json (JSON)**: `{"hostname": "org-svc1-deb-e2-std4-02", "ip": "10.100.1.12"}`

## 🏆 結論
クローン同期システム (`make sync-to-dst`) は、GCP API有効化、複雑な共有VPCネットワーク構築、ファイアウォール、安全なプロジェクト除外フィルタ（セーフガード）、スナップショットからのクローンディスク復元、マシンタイプの完全適合、およびデータ・設定を100%引き継いだVMプロビジョニングにいたるまで、実機上で**完全に正常動作すること**が最終実証された。
