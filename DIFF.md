# CAI ↔ Terraform bulk-export 差分レポート

Cloud Asset Inventory（CAI）が観測した src 側リソースのうち、
`gcloud beta resource-config bulk-export` の出力に**含まれなかった**ものを
プロジェクトごとに列挙し、dst 側に再現するための gcloud コマンドを併記します。

- 「意図的に対象外」: `_ASSET_COVERAGE` で None 指定。実害なしとして除外可。
- 「別ステップが担当」: Step 4.5 / Step 5 / Step 6 等で複製。bulk-export 単体での欠落は想定通り。
- 「未登録」「bulk-export が出力しなかった」: 対応の検討が必要。

## プロジェクト: `<SRC_HOST_PROJECT_ID>` → `<DST_HOST_PROJECT_ID>`

- CAI 検出リソース: **61** 件 / TF 出力リソース: **3** 件 / 一致: **0** 件 / 欠落候補: **61** 件

### `cloudbilling.googleapis.com/ProjectBillingInfo` （1 件）

#### `billingInfo` (location=`global`)

- full name: `//cloudbilling.googleapis.com/projects/<SRC_HOST_PROJECT_ID>/billingInfo`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `なし`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud asset describe '//cloudbilling.googleapis.com/projects/<SRC_HOST_PROJECT_ID>/billingInfo' --project=<SRC_HOST_PROJECT_ID>
  # cloudbilling.googleapis.com/ProjectBillingInfo は自動補完対象外。手動でドキュメント参照のうえ dst で再作成してください。
  ```

### `cloudresourcemanager.googleapis.com/Lien` （1 件）

#### `p1035210593832-l8e02e6f7-52d6-43a0-a660-612e9b91095c` (location=`global`)

- full name: `//cloudresourcemanager.googleapis.com/liens/p1035210593832-l8e02e6f7-52d6-43a0-a660-612e9b91095c`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `なし`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud asset describe '//cloudresourcemanager.googleapis.com/liens/p1035210593832-l8e02e6f7-52d6-43a0-a660-612e9b91095c' 
  # cloudresourcemanager.googleapis.com/Lien は自動補完対象外。手動でドキュメント参照のうえ dst で再作成してください。
  ```

### `cloudresourcemanager.googleapis.com/Project` （1 件）

#### `<SRC_HOST_PROJECT_ID>` (location=`global`)

- full name: `//cloudresourcemanager.googleapis.com/projects/<SRC_HOST_PROJECT_ID>`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `なし`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud asset describe '//cloudresourcemanager.googleapis.com/projects/<SRC_HOST_PROJECT_ID>' 
  # cloudresourcemanager.googleapis.com/Project は自動補完対象外。手動でドキュメント参照のうえ dst で再作成してください。
  ```

### `compute.googleapis.com/Address` （2 件）

#### `nat-auto-ip-10281266-7-1780362359330384` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/<SRC_HOST_PROJECT_ID>/regions/asia-northeast1/addresses/nat-auto-ip-10281266-7-1780362359330384`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses describe nat-auto-ip-10281266-7-1780362359330384 --region=asia-northeast1 --project=<SRC_HOST_PROJECT_ID>
  gcloud compute addresses create nat-auto-ip-10281266-7-1780362359330384 --project=<DST_HOST_PROJECT_ID> --region=asia-northeast1
  ```

#### `coordinator` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_HOST_PROJECT_ID>/global/addresses/coordinator`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses describe coordinator --global --project=<SRC_HOST_PROJECT_ID>
  gcloud compute addresses create coordinator --project=<DST_HOST_PROJECT_ID> --global
  ```

### `compute.googleapis.com/Firewall` （5 件）

#### `testrule30000` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_HOST_PROJECT_ID>/global/firewalls/testrule30000`
- 担当ステップ: `network_firewall`
- 期待 TF 型: `google_compute_firewall`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_firewall)
- 推奨コマンド:
  ```bash
  gcloud compute firewall-rules describe testrule30000 --project=<SRC_HOST_PROJECT_ID>
  gcloud compute firewall-rules create testrule30000 --project=<DST_HOST_PROJECT_ID> --network=<NETWORK> --direction=<INGRESS|EGRESS> --action=<ALLOW|DENY> --rules=<PROTO:PORT,...>
  ```

#### `rdp` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_HOST_PROJECT_ID>/global/firewalls/rdp`
- 担当ステップ: `network_firewall`
- 期待 TF 型: `google_compute_firewall`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_firewall)
- 推奨コマンド:
  ```bash
  gcloud compute firewall-rules describe rdp --project=<SRC_HOST_PROJECT_ID>
  gcloud compute firewall-rules create rdp --project=<DST_HOST_PROJECT_ID> --network=<NETWORK> --direction=<INGRESS|EGRESS> --action=<ALLOW|DENY> --rules=<PROTO:PORT,...>
  ```

#### `ssh` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_HOST_PROJECT_ID>/global/firewalls/ssh`
- 担当ステップ: `network_firewall`
- 期待 TF 型: `google_compute_firewall`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_firewall)
- 推奨コマンド:
  ```bash
  gcloud compute firewall-rules describe ssh --project=<SRC_HOST_PROJECT_ID>
  gcloud compute firewall-rules create ssh --project=<DST_HOST_PROJECT_ID> --network=<NETWORK> --direction=<INGRESS|EGRESS> --action=<ALLOW|DENY> --rules=<PROTO:PORT,...>
  ```

#### `all-for-incredibuild` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_HOST_PROJECT_ID>/global/firewalls/all-for-incredibuild`
- 担当ステップ: `network_firewall`
- 期待 TF 型: `google_compute_firewall`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_firewall)
- 推奨コマンド:
  ```bash
  gcloud compute firewall-rules describe all-for-incredibuild --project=<SRC_HOST_PROJECT_ID>
  gcloud compute firewall-rules create all-for-incredibuild --project=<DST_HOST_PROJECT_ID> --network=<NETWORK> --direction=<INGRESS|EGRESS> --action=<ALLOW|DENY> --rules=<PROTO:PORT,...>
  ```

#### `allow-shared-iap-ssh` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_HOST_PROJECT_ID>/global/firewalls/allow-shared-iap-ssh`
- 担当ステップ: `network_firewall`
- 期待 TF 型: `google_compute_firewall`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_firewall)
- 推奨コマンド:
  ```bash
  gcloud compute firewall-rules describe allow-shared-iap-ssh --project=<SRC_HOST_PROJECT_ID>
  gcloud compute firewall-rules create allow-shared-iap-ssh --project=<DST_HOST_PROJECT_ID> --network=<NETWORK> --direction=<INGRESS|EGRESS> --action=<ALLOW|DENY> --rules=<PROTO:PORT,...>
  ```

### `compute.googleapis.com/FirewallPolicy` （2 件）

#### `test8000` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_HOST_PROJECT_ID>/global/firewallPolicies/test8000`
- 担当ステップ: `network_firewall`
- 期待 TF 型: `google_compute_network_firewall_policy/google_compute_firewall_policy`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_network_firewall_policy/google_compute_firewall_policy)
- 推奨コマンド:
  ```bash
  gcloud compute network-firewall-policies describe test8000 --global --project=<SRC_HOST_PROJECT_ID>
  gcloud compute network-firewall-policies create test8000 --global --project=<DST_HOST_PROJECT_ID> --description=<DESC>
  ```

#### `ssh-from-all` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/<SRC_HOST_PROJECT_ID>/regions/asia-northeast1/firewallPolicies/ssh-from-all`
- 担当ステップ: `network_firewall`
- 期待 TF 型: `google_compute_network_firewall_policy/google_compute_firewall_policy`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_network_firewall_policy/google_compute_firewall_policy)
- 推奨コマンド:
  ```bash
  gcloud compute network-firewall-policies describe ssh-from-all --global --project=<SRC_HOST_PROJECT_ID>
  gcloud compute network-firewall-policies create ssh-from-all --global --project=<DST_HOST_PROJECT_ID> --description=<DESC>
  ```

### `compute.googleapis.com/InstanceSettings` （3 件）

#### `InstanceSettings` (location=`asia-northeast1-c`)

- full name: `//compute.googleapis.com/projects/<SRC_HOST_PROJECT_ID>/zones/asia-northeast1-c/instanceSettings/InstanceSettings`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `なし`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud asset describe '//compute.googleapis.com/projects/<SRC_HOST_PROJECT_ID>/zones/asia-northeast1-c/instanceSettings/InstanceSettings' --project=<SRC_HOST_PROJECT_ID>
  # compute.googleapis.com/InstanceSettings は自動補完対象外。手動でドキュメント参照のうえ dst で再作成してください。
  ```

#### `InstanceSettings` (location=`asia-northeast1-a`)

- full name: `//compute.googleapis.com/projects/<SRC_HOST_PROJECT_ID>/zones/asia-northeast1-a/instanceSettings/InstanceSettings`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `なし`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud asset describe '//compute.googleapis.com/projects/<SRC_HOST_PROJECT_ID>/zones/asia-northeast1-a/instanceSettings/InstanceSettings' --project=<SRC_HOST_PROJECT_ID>
  # compute.googleapis.com/InstanceSettings は自動補完対象外。手動でドキュメント参照のうえ dst で再作成してください。
  ```

#### `InstanceSettings` (location=`asia-northeast1-b`)

- full name: `//compute.googleapis.com/projects/<SRC_HOST_PROJECT_ID>/zones/asia-northeast1-b/instanceSettings/InstanceSettings`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `なし`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud asset describe '//compute.googleapis.com/projects/<SRC_HOST_PROJECT_ID>/zones/asia-northeast1-b/instanceSettings/InstanceSettings' --project=<SRC_HOST_PROJECT_ID>
  # compute.googleapis.com/InstanceSettings は自動補完対象外。手動でドキュメント参照のうえ dst で再作成してください。
  ```

### `compute.googleapis.com/Network` （1 件）

#### `shared-vpc` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_HOST_PROJECT_ID>/global/networks/shared-vpc`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_network`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_network)
- 推奨コマンド:
  ```bash
  gcloud compute networks describe shared-vpc --project=<SRC_HOST_PROJECT_ID>
  gcloud compute networks create shared-vpc --project=<DST_HOST_PROJECT_ID> --subnet-mode=custom
  ```

### `compute.googleapis.com/Project` （1 件）

#### `<SRC_HOST_PROJECT_ID>` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_HOST_PROJECT_ID>`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `なし`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud asset describe '//compute.googleapis.com/projects/<SRC_HOST_PROJECT_ID>' 
  # compute.googleapis.com/Project は自動補完対象外。手動でドキュメント参照のうえ dst で再作成してください。
  ```

### `compute.googleapis.com/Route` （5 件）

#### `default-route-r-4461f276b01d2f9b` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_HOST_PROJECT_ID>/global/routes/default-route-r-4461f276b01d2f9b`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-r-4461f276b01d2f9b --project=<SRC_HOST_PROJECT_ID>
  gcloud compute routes create default-route-r-4461f276b01d2f9b --project=<DST_HOST_PROJECT_ID> --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-r-5b0ce4d4d24c5d20` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_HOST_PROJECT_ID>/global/routes/default-route-r-5b0ce4d4d24c5d20`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-r-5b0ce4d4d24c5d20 --project=<SRC_HOST_PROJECT_ID>
  gcloud compute routes create default-route-r-5b0ce4d4d24c5d20 --project=<DST_HOST_PROJECT_ID> --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-e7b27198104c4cc0` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_HOST_PROJECT_ID>/global/routes/default-route-e7b27198104c4cc0`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-e7b27198104c4cc0 --project=<SRC_HOST_PROJECT_ID>
  gcloud compute routes create default-route-e7b27198104c4cc0 --project=<DST_HOST_PROJECT_ID> --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-2d5c5b7662d1a301` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_HOST_PROJECT_ID>/global/routes/default-route-2d5c5b7662d1a301`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-2d5c5b7662d1a301 --project=<SRC_HOST_PROJECT_ID>
  gcloud compute routes create default-route-2d5c5b7662d1a301 --project=<DST_HOST_PROJECT_ID> --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-4a82a4f6a6983b3d` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_HOST_PROJECT_ID>/global/routes/default-route-4a82a4f6a6983b3d`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-4a82a4f6a6983b3d --project=<SRC_HOST_PROJECT_ID>
  gcloud compute routes create default-route-4a82a4f6a6983b3d --project=<DST_HOST_PROJECT_ID> --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

### `compute.googleapis.com/Router` （1 件）

#### `shared-router` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/<SRC_HOST_PROJECT_ID>/regions/asia-northeast1/routers/shared-router`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_router`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routers describe shared-router --region=asia-northeast1 --project=<SRC_HOST_PROJECT_ID>
  gcloud compute routers create shared-router --project=<DST_HOST_PROJECT_ID> --region=asia-northeast1 --network=<NETWORK> --asn=<ASN>
  ```

### `compute.googleapis.com/Subnetwork` （4 件）

#### `subnet-svc3` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/<SRC_HOST_PROJECT_ID>/regions/asia-northeast1/subnetworks/subnet-svc3`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe subnet-svc3 --region=asia-northeast1 --project=<SRC_HOST_PROJECT_ID>
  gcloud compute networks subnets create subnet-svc3 --project=<DST_HOST_PROJECT_ID> --region=asia-northeast1 --network=<NETWORK> --range=<CIDR>
  ```

#### `subnet-svc1` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/<SRC_HOST_PROJECT_ID>/regions/asia-northeast1/subnetworks/subnet-svc1`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe subnet-svc1 --region=asia-northeast1 --project=<SRC_HOST_PROJECT_ID>
  gcloud compute networks subnets create subnet-svc1 --project=<DST_HOST_PROJECT_ID> --region=asia-northeast1 --network=<NETWORK> --range=<CIDR>
  ```

#### `tokyo-2` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/<SRC_HOST_PROJECT_ID>/regions/asia-northeast1/subnetworks/tokyo-2`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe tokyo-2 --region=asia-northeast1 --project=<SRC_HOST_PROJECT_ID>
  gcloud compute networks subnets create tokyo-2 --project=<DST_HOST_PROJECT_ID> --region=asia-northeast1 --network=<NETWORK> --range=<CIDR>
  ```

#### `tokyo` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/<SRC_HOST_PROJECT_ID>/regions/asia-northeast1/subnetworks/tokyo`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe tokyo --region=asia-northeast1 --project=<SRC_HOST_PROJECT_ID>
  gcloud compute networks subnets create tokyo --project=<DST_HOST_PROJECT_ID> --region=asia-northeast1 --network=<NETWORK> --range=<CIDR>
  ```

### `iam.googleapis.com/Role` （1 件）

#### `migrationSrcReader` (location=`global`)

- full name: `//iam.googleapis.com/projects/<SRC_HOST_PROJECT_ID>/roles/migrationSrcReader`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_project_iam_custom_role`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_project_iam_custom_role)
- 推奨コマンド:
  ```bash
  gcloud iam roles describe migrationSrcReader --project=<SRC_HOST_PROJECT_ID>
  gcloud iam roles create migrationSrcReader --project=<DST_HOST_PROJECT_ID> --title=<TITLE> --permissions=<PERM1,PERM2,...> --stage=GA
  ```

### `logging.googleapis.com/LogBucket` （2 件）

#### `_Default` (location=`global`)

- full name: `//logging.googleapis.com/projects/1035210593832/locations/global/buckets/_Default`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_logging_project_bucket_config`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_logging_project_bucket_config)
- 推奨コマンド:
  ```bash
  gcloud logging buckets describe _Default --location=global --project=1035210593832
  gcloud logging buckets create _Default --location=global --project=<DST_HOST_PROJECT_ID> --retention-days=<N>
  ```

#### `_Required` (location=`global`)

- full name: `//logging.googleapis.com/projects/1035210593832/locations/global/buckets/_Required`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_logging_project_bucket_config`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_logging_project_bucket_config)
- 推奨コマンド:
  ```bash
  gcloud logging buckets describe _Required --location=global --project=1035210593832
  gcloud logging buckets create _Required --location=global --project=<DST_HOST_PROJECT_ID> --retention-days=<N>
  ```

### `logging.googleapis.com/LogSink` （2 件）

#### `_Required` (location=`global`)

- full name: `//logging.googleapis.com/projects/1035210593832/sinks/_Required`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_logging_project_sink`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_logging_project_sink)
- 推奨コマンド:
  ```bash
  gcloud logging sinks describe _Required --project=1035210593832
  gcloud logging sinks create _Required <DESTINATION> --project=<DST_HOST_PROJECT_ID> --log-filter='<FILTER>'
  ```

#### `_Default` (location=`global`)

- full name: `//logging.googleapis.com/projects/1035210593832/sinks/_Default`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_logging_project_sink`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_logging_project_sink)
- 推奨コマンド:
  ```bash
  gcloud logging sinks describe _Default --project=1035210593832
  gcloud logging sinks create _Default <DESTINATION> --project=<DST_HOST_PROJECT_ID> --log-filter='<FILTER>'
  ```

### `serviceusage.googleapis.com/Service` （27 件）

#### `cloudtrace.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1035210593832/services/cloudtrace.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1035210593832 --filter='config.name:cloudtrace.googleapis.com'
  gcloud services enable cloudtrace.googleapis.com --project=<DST_HOST_PROJECT_ID>
  ```

#### `artifactregistry.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1035210593832/services/artifactregistry.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1035210593832 --filter='config.name:artifactregistry.googleapis.com'
  gcloud services enable artifactregistry.googleapis.com --project=<DST_HOST_PROJECT_ID>
  ```

#### `compute.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1035210593832/services/compute.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1035210593832 --filter='config.name:compute.googleapis.com'
  gcloud services enable compute.googleapis.com --project=<DST_HOST_PROJECT_ID>
  ```

#### `cloudapis.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1035210593832/services/cloudapis.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1035210593832 --filter='config.name:cloudapis.googleapis.com'
  gcloud services enable cloudapis.googleapis.com --project=<DST_HOST_PROJECT_ID>
  ```

#### `storage.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1035210593832/services/storage.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1035210593832 --filter='config.name:storage.googleapis.com'
  gcloud services enable storage.googleapis.com --project=<DST_HOST_PROJECT_ID>
  ```

#### `oslogin.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1035210593832/services/oslogin.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1035210593832 --filter='config.name:oslogin.googleapis.com'
  gcloud services enable oslogin.googleapis.com --project=<DST_HOST_PROJECT_ID>
  ```

#### `iamcredentials.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1035210593832/services/iamcredentials.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1035210593832 --filter='config.name:iamcredentials.googleapis.com'
  gcloud services enable iamcredentials.googleapis.com --project=<DST_HOST_PROJECT_ID>
  ```

#### `logging.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1035210593832/services/logging.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1035210593832 --filter='config.name:logging.googleapis.com'
  gcloud services enable logging.googleapis.com --project=<DST_HOST_PROJECT_ID>
  ```

#### `telemetry.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1035210593832/services/telemetry.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1035210593832 --filter='config.name:telemetry.googleapis.com'
  gcloud services enable telemetry.googleapis.com --project=<DST_HOST_PROJECT_ID>
  ```

#### `cloudasset.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1035210593832/services/cloudasset.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1035210593832 --filter='config.name:cloudasset.googleapis.com'
  gcloud services enable cloudasset.googleapis.com --project=<DST_HOST_PROJECT_ID>
  ```

#### `storage-component.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1035210593832/services/storage-component.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1035210593832 --filter='config.name:storage-component.googleapis.com'
  gcloud services enable storage-component.googleapis.com --project=<DST_HOST_PROJECT_ID>
  ```

#### `iam.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1035210593832/services/iam.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1035210593832 --filter='config.name:iam.googleapis.com'
  gcloud services enable iam.googleapis.com --project=<DST_HOST_PROJECT_ID>
  ```

#### `servicemanagement.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1035210593832/services/servicemanagement.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1035210593832 --filter='config.name:servicemanagement.googleapis.com'
  gcloud services enable servicemanagement.googleapis.com --project=<DST_HOST_PROJECT_ID>
  ```

#### `serviceusage.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1035210593832/services/serviceusage.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1035210593832 --filter='config.name:serviceusage.googleapis.com'
  gcloud services enable serviceusage.googleapis.com --project=<DST_HOST_PROJECT_ID>
  ```

#### `sql-component.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1035210593832/services/sql-component.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1035210593832 --filter='config.name:sql-component.googleapis.com'
  gcloud services enable sql-component.googleapis.com --project=<DST_HOST_PROJECT_ID>
  ```

#### `bigquery.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1035210593832/services/bigquery.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1035210593832 --filter='config.name:bigquery.googleapis.com'
  gcloud services enable bigquery.googleapis.com --project=<DST_HOST_PROJECT_ID>
  ```

#### `cloudbuild.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1035210593832/services/cloudbuild.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1035210593832 --filter='config.name:cloudbuild.googleapis.com'
  gcloud services enable cloudbuild.googleapis.com --project=<DST_HOST_PROJECT_ID>
  ```

#### `bigquerymigration.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1035210593832/services/bigquerymigration.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1035210593832 --filter='config.name:bigquerymigration.googleapis.com'
  gcloud services enable bigquerymigration.googleapis.com --project=<DST_HOST_PROJECT_ID>
  ```

#### `monitoring.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1035210593832/services/monitoring.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1035210593832 --filter='config.name:monitoring.googleapis.com'
  gcloud services enable monitoring.googleapis.com --project=<DST_HOST_PROJECT_ID>
  ```

#### `containerregistry.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1035210593832/services/containerregistry.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1035210593832 --filter='config.name:containerregistry.googleapis.com'
  gcloud services enable containerregistry.googleapis.com --project=<DST_HOST_PROJECT_ID>
  ```

#### `servicecontrol.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1035210593832/services/servicecontrol.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1035210593832 --filter='config.name:servicecontrol.googleapis.com'
  gcloud services enable servicecontrol.googleapis.com --project=<DST_HOST_PROJECT_ID>
  ```

#### `datastore.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1035210593832/services/datastore.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1035210593832 --filter='config.name:datastore.googleapis.com'
  gcloud services enable datastore.googleapis.com --project=<DST_HOST_PROJECT_ID>
  ```

#### `cloudresourcemanager.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1035210593832/services/cloudresourcemanager.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1035210593832 --filter='config.name:cloudresourcemanager.googleapis.com'
  gcloud services enable cloudresourcemanager.googleapis.com --project=<DST_HOST_PROJECT_ID>
  ```

#### `bigquerystorage.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1035210593832/services/bigquerystorage.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1035210593832 --filter='config.name:bigquerystorage.googleapis.com'
  gcloud services enable bigquerystorage.googleapis.com --project=<DST_HOST_PROJECT_ID>
  ```

#### `pubsub.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1035210593832/services/pubsub.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1035210593832 --filter='config.name:pubsub.googleapis.com'
  gcloud services enable pubsub.googleapis.com --project=<DST_HOST_PROJECT_ID>
  ```

#### `storage-api.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1035210593832/services/storage-api.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1035210593832 --filter='config.name:storage-api.googleapis.com'
  gcloud services enable storage-api.googleapis.com --project=<DST_HOST_PROJECT_ID>
  ```

#### `vmmigration.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1035210593832/services/vmmigration.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1035210593832 --filter='config.name:vmmigration.googleapis.com'
  gcloud services enable vmmigration.googleapis.com --project=<DST_HOST_PROJECT_ID>
  ```

### `storage.googleapis.com/Bucket` （2 件）

#### `<SRC_HOST_PROJECT_ID>` (location=`us-central1`)

- full name: `//storage.googleapis.com/<SRC_HOST_PROJECT_ID>`
- 担当ステップ: `data_sync`
- 期待 TF 型: `google_storage_bucket`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_storage_bucket)
- 推奨コマンド:
  ```bash
  gcloud storage buckets describe gs://<SRC_HOST_PROJECT_ID>
  gcloud storage buckets create gs://<DST_BUCKET_NAME> --project=<DST_HOST_PROJECT_ID> --location=us-central1  # 名前は rename_rules.gcs を適用すること
  ```

#### `gcs-test-<SRC_HOST_PROJECT_ID>` (location=`asia`)

- full name: `//storage.googleapis.com/gcs-test-<SRC_HOST_PROJECT_ID>`
- 担当ステップ: `data_sync`
- 期待 TF 型: `google_storage_bucket`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_storage_bucket)
- 推奨コマンド:
  ```bash
  gcloud storage buckets describe gs://gcs-test-<SRC_HOST_PROJECT_ID>
  gcloud storage buckets create gs://<DST_BUCKET_NAME> --project=<DST_HOST_PROJECT_ID> --location=asia  # 名前は rename_rules.gcs を適用すること
  ```

## プロジェクト: `<SRC_SERVICE_PROJECT_ID_1>` → `<DST_SERVICE_PROJECT_ID_1>`

- CAI 検出リソース: **91** 件 / TF 出力リソース: **3** 件 / 一致: **1** 件 / 欠落候補: **90** 件

### `cloudbilling.googleapis.com/ProjectBillingInfo` （1 件）

#### `billingInfo` (location=`global`)

- full name: `//cloudbilling.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/billingInfo`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `なし`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud asset describe '//cloudbilling.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/billingInfo' --project=<SRC_SERVICE_PROJECT_ID_1>
  # cloudbilling.googleapis.com/ProjectBillingInfo は自動補完対象外。手動でドキュメント参照のうえ dst で再作成してください。
  ```

### `cloudresourcemanager.googleapis.com/Project` （1 件）

#### `<SRC_SERVICE_PROJECT_ID_1>` (location=`global`)

- full name: `//cloudresourcemanager.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `なし`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud asset describe '//cloudresourcemanager.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>' 
  # cloudresourcemanager.googleapis.com/Project は自動補完対象外。手動でドキュメント参照のうえ dst で再作成してください。
  ```

### `compute.googleapis.com/Address` （5 件）

#### `org-svc1-deb-n2-std2-02-ip` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/regions/asia-northeast1/addresses/org-svc1-deb-n2-std2-02-ip`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses describe org-svc1-deb-n2-std2-02-ip --region=asia-northeast1 --project=<SRC_SERVICE_PROJECT_ID_1>
  gcloud compute addresses create org-svc1-deb-n2-std2-02-ip --project=<DST_SERVICE_PROJECT_ID_1> --region=asia-northeast1
  ```

#### `org-svc1-deb-n2-std2-01-ip` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/regions/asia-northeast1/addresses/org-svc1-deb-n2-std2-01-ip`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses describe org-svc1-deb-n2-std2-01-ip --region=asia-northeast1 --project=<SRC_SERVICE_PROJECT_ID_1>
  gcloud compute addresses create org-svc1-deb-n2-std2-01-ip --project=<DST_SERVICE_PROJECT_ID_1> --region=asia-northeast1
  ```

#### `org-svc1-deb-e2-mic-01-ip` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/regions/asia-northeast1/addresses/org-svc1-deb-e2-mic-01-ip`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses describe org-svc1-deb-e2-mic-01-ip --region=asia-northeast1 --project=<SRC_SERVICE_PROJECT_ID_1>
  gcloud compute addresses create org-svc1-deb-e2-mic-01-ip --project=<DST_SERVICE_PROJECT_ID_1> --region=asia-northeast1
  ```

#### `org-svc1-deb-e2-mic-02-ip` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/regions/asia-northeast1/addresses/org-svc1-deb-e2-mic-02-ip`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses describe org-svc1-deb-e2-mic-02-ip --region=asia-northeast1 --project=<SRC_SERVICE_PROJECT_ID_1>
  gcloud compute addresses create org-svc1-deb-e2-mic-02-ip --project=<DST_SERVICE_PROJECT_ID_1> --region=asia-northeast1
  ```

#### `org-svc1-deb-e2-mic-03-ip` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/regions/asia-northeast1/addresses/org-svc1-deb-e2-mic-03-ip`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses describe org-svc1-deb-e2-mic-03-ip --region=asia-northeast1 --project=<SRC_SERVICE_PROJECT_ID_1>
  gcloud compute addresses create org-svc1-deb-e2-mic-03-ip --project=<DST_SERVICE_PROJECT_ID_1> --region=asia-northeast1
  ```

### `compute.googleapis.com/Disk` （8 件）

#### `centos8-from-vmv` (location=`asia-northeast1-a`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/zones/asia-northeast1-a/disks/centos8-from-vmv`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_disk/google_compute_region_disk`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_disk/google_compute_region_disk)
- 推奨コマンド:
  ```bash
  gcloud compute disks describe centos8-from-vmv --zone=asia-northeast1-a --project=<SRC_SERVICE_PROJECT_ID_1>
  gcloud compute disks create centos8-from-vmv --project=<DST_SERVICE_PROJECT_ID_1> --zone=asia-northeast1-a --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore)
  ```

#### `windows` (location=`asia-northeast1-c`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/zones/asia-northeast1-c/disks/windows`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_disk/google_compute_region_disk`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_disk/google_compute_region_disk)
- 推奨コマンド:
  ```bash
  gcloud compute disks describe windows --zone=asia-northeast1-c --project=<SRC_SERVICE_PROJECT_ID_1>
  gcloud compute disks create windows --project=<DST_SERVICE_PROJECT_ID_1> --zone=asia-northeast1-c --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore)
  ```

#### `org-svc1-deb-e2-mic-03` (location=`asia-northeast1-a`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/zones/asia-northeast1-a/disks/org-svc1-deb-e2-mic-03`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_disk/google_compute_region_disk`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_disk/google_compute_region_disk)
- 推奨コマンド:
  ```bash
  gcloud compute disks describe org-svc1-deb-e2-mic-03 --zone=asia-northeast1-a --project=<SRC_SERVICE_PROJECT_ID_1>
  gcloud compute disks create org-svc1-deb-e2-mic-03 --project=<DST_SERVICE_PROJECT_ID_1> --zone=asia-northeast1-a --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore)
  ```

#### `org-svc1-deb-n2-std2-01` (location=`asia-northeast1-a`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/zones/asia-northeast1-a/disks/org-svc1-deb-n2-std2-01`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_disk/google_compute_region_disk`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_disk/google_compute_region_disk)
- 推奨コマンド:
  ```bash
  gcloud compute disks describe org-svc1-deb-n2-std2-01 --zone=asia-northeast1-a --project=<SRC_SERVICE_PROJECT_ID_1>
  gcloud compute disks create org-svc1-deb-n2-std2-01 --project=<DST_SERVICE_PROJECT_ID_1> --zone=asia-northeast1-a --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore)
  ```

#### `org-svc1-deb-e2-mic-02` (location=`asia-northeast1-a`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/zones/asia-northeast1-a/disks/org-svc1-deb-e2-mic-02`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_disk/google_compute_region_disk`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_disk/google_compute_region_disk)
- 推奨コマンド:
  ```bash
  gcloud compute disks describe org-svc1-deb-e2-mic-02 --zone=asia-northeast1-a --project=<SRC_SERVICE_PROJECT_ID_1>
  gcloud compute disks create org-svc1-deb-e2-mic-02 --project=<DST_SERVICE_PROJECT_ID_1> --zone=asia-northeast1-a --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore)
  ```

#### `org-svc1-deb-e2-mic-01` (location=`asia-northeast1-a`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/zones/asia-northeast1-a/disks/org-svc1-deb-e2-mic-01`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_disk/google_compute_region_disk`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_disk/google_compute_region_disk)
- 推奨コマンド:
  ```bash
  gcloud compute disks describe org-svc1-deb-e2-mic-01 --zone=asia-northeast1-a --project=<SRC_SERVICE_PROJECT_ID_1>
  gcloud compute disks create org-svc1-deb-e2-mic-01 --project=<DST_SERVICE_PROJECT_ID_1> --zone=asia-northeast1-a --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore)
  ```

#### `org-svc1-deb-n2-std2-02` (location=`asia-northeast1-a`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/zones/asia-northeast1-a/disks/org-svc1-deb-n2-std2-02`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_disk/google_compute_region_disk`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_disk/google_compute_region_disk)
- 推奨コマンド:
  ```bash
  gcloud compute disks describe org-svc1-deb-n2-std2-02 --zone=asia-northeast1-a --project=<SRC_SERVICE_PROJECT_ID_1>
  gcloud compute disks create org-svc1-deb-n2-std2-02 --project=<DST_SERVICE_PROJECT_ID_1> --zone=asia-northeast1-a --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore)
  ```

#### `instance-1` (location=`asia-northeast1-b`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/zones/asia-northeast1-b/disks/instance-1`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_disk/google_compute_region_disk`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_disk/google_compute_region_disk)
- 推奨コマンド:
  ```bash
  gcloud compute disks describe instance-1 --zone=asia-northeast1-b --project=<SRC_SERVICE_PROJECT_ID_1>
  gcloud compute disks create instance-1 --project=<DST_SERVICE_PROJECT_ID_1> --zone=asia-northeast1-b --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore)
  ```

### `compute.googleapis.com/Image` （8 件）

#### `vmdk-imported-20260608-centos8t-boot` (location=`asia`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/global/images/vmdk-imported-20260608-centos8t-boot`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_image`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute images describe vmdk-imported-20260608-centos8t-boot --project=<SRC_SERVICE_PROJECT_ID_1>
  # image は使用しない方針（snapshot 由来）。必要なら gcloud compute images create vmdk-imported-20260608-centos8t-boot --project=<DST_SERVICE_PROJECT_ID_1> --source-snapshot=<SNAPSHOT>
  ```

#### `vmdk-imported-20260608-centos8v-boot` (location=`asia`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/global/images/vmdk-imported-20260608-centos8v-boot`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_image`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute images describe vmdk-imported-20260608-centos8v-boot --project=<SRC_SERVICE_PROJECT_ID_1>
  # image は使用しない方針（snapshot 由来）。必要なら gcloud compute images create vmdk-imported-20260608-centos8v-boot --project=<DST_SERVICE_PROJECT_ID_1> --source-snapshot=<SNAPSHOT>
  ```

#### `vmdk-imported-20260608-boot` (location=`asia`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/global/images/vmdk-imported-20260608-boot`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_image`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute images describe vmdk-imported-20260608-boot --project=<SRC_SERVICE_PROJECT_ID_1>
  # image は使用しない方針（snapshot 由来）。必要なら gcloud compute images create vmdk-imported-20260608-boot --project=<DST_SERVICE_PROJECT_ID_1> --source-snapshot=<SNAPSHOT>
  ```

#### `img-org-svc1-deb-n2-std4-02` (location=`asia`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/global/images/img-org-svc1-deb-n2-std4-02`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_image`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute images describe img-org-svc1-deb-n2-std4-02 --project=<SRC_SERVICE_PROJECT_ID_1>
  # image は使用しない方針（snapshot 由来）。必要なら gcloud compute images create img-org-svc1-deb-n2-std4-02 --project=<DST_SERVICE_PROJECT_ID_1> --source-snapshot=<SNAPSHOT>
  ```

#### `img-org-svc1-deb-n2-std4-01` (location=`asia`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/global/images/img-org-svc1-deb-n2-std4-01`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_image`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute images describe img-org-svc1-deb-n2-std4-01 --project=<SRC_SERVICE_PROJECT_ID_1>
  # image は使用しない方針（snapshot 由来）。必要なら gcloud compute images create img-org-svc1-deb-n2-std4-01 --project=<DST_SERVICE_PROJECT_ID_1> --source-snapshot=<SNAPSHOT>
  ```

#### `img-org-svc1-deb-e2-std4-03` (location=`asia`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/global/images/img-org-svc1-deb-e2-std4-03`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_image`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute images describe img-org-svc1-deb-e2-std4-03 --project=<SRC_SERVICE_PROJECT_ID_1>
  # image は使用しない方針（snapshot 由来）。必要なら gcloud compute images create img-org-svc1-deb-e2-std4-03 --project=<DST_SERVICE_PROJECT_ID_1> --source-snapshot=<SNAPSHOT>
  ```

#### `img-org-svc1-deb-e2-std4-02` (location=`asia`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/global/images/img-org-svc1-deb-e2-std4-02`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_image`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute images describe img-org-svc1-deb-e2-std4-02 --project=<SRC_SERVICE_PROJECT_ID_1>
  # image は使用しない方針（snapshot 由来）。必要なら gcloud compute images create img-org-svc1-deb-e2-std4-02 --project=<DST_SERVICE_PROJECT_ID_1> --source-snapshot=<SNAPSHOT>
  ```

#### `img-org-svc1-deb-e2-std4-01` (location=`asia`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/global/images/img-org-svc1-deb-e2-std4-01`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_image`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute images describe img-org-svc1-deb-e2-std4-01 --project=<SRC_SERVICE_PROJECT_ID_1>
  # image は使用しない方針（snapshot 由来）。必要なら gcloud compute images create img-org-svc1-deb-e2-std4-01 --project=<DST_SERVICE_PROJECT_ID_1> --source-snapshot=<SNAPSHOT>
  ```

### `compute.googleapis.com/Instance` （7 件）

#### `centos8-from-vmv` (location=`asia-northeast1-a`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/zones/asia-northeast1-a/instances/centos8-from-vmv`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_instance`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_instance)
- 推奨コマンド:
  ```bash
  gcloud compute instances describe centos8-from-vmv --zone=asia-northeast1-a --project=<SRC_SERVICE_PROJECT_ID_1>
  gcloud compute instances create centos8-from-vmv --project=<DST_SERVICE_PROJECT_ID_1> --zone=asia-northeast1-a --machine-type=<MACHINE_TYPE> --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore) が担当
  ```

#### `windows` (location=`asia-northeast1-c`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/zones/asia-northeast1-c/instances/windows`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_instance`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_instance)
- 推奨コマンド:
  ```bash
  gcloud compute instances describe windows --zone=asia-northeast1-c --project=<SRC_SERVICE_PROJECT_ID_1>
  gcloud compute instances create windows --project=<DST_SERVICE_PROJECT_ID_1> --zone=asia-northeast1-c --machine-type=<MACHINE_TYPE> --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore) が担当
  ```

#### `org-svc1-deb-e2-mic-02` (location=`asia-northeast1-a`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/zones/asia-northeast1-a/instances/org-svc1-deb-e2-mic-02`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_instance`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_instance)
- 推奨コマンド:
  ```bash
  gcloud compute instances describe org-svc1-deb-e2-mic-02 --zone=asia-northeast1-a --project=<SRC_SERVICE_PROJECT_ID_1>
  gcloud compute instances create org-svc1-deb-e2-mic-02 --project=<DST_SERVICE_PROJECT_ID_1> --zone=asia-northeast1-a --machine-type=<MACHINE_TYPE> --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore) が担当
  ```

#### `org-svc1-deb-n2-std2-01` (location=`asia-northeast1-a`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/zones/asia-northeast1-a/instances/org-svc1-deb-n2-std2-01`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_instance`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_instance)
- 推奨コマンド:
  ```bash
  gcloud compute instances describe org-svc1-deb-n2-std2-01 --zone=asia-northeast1-a --project=<SRC_SERVICE_PROJECT_ID_1>
  gcloud compute instances create org-svc1-deb-n2-std2-01 --project=<DST_SERVICE_PROJECT_ID_1> --zone=asia-northeast1-a --machine-type=<MACHINE_TYPE> --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore) が担当
  ```

#### `org-svc1-deb-e2-mic-03` (location=`asia-northeast1-a`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/zones/asia-northeast1-a/instances/org-svc1-deb-e2-mic-03`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_instance`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_instance)
- 推奨コマンド:
  ```bash
  gcloud compute instances describe org-svc1-deb-e2-mic-03 --zone=asia-northeast1-a --project=<SRC_SERVICE_PROJECT_ID_1>
  gcloud compute instances create org-svc1-deb-e2-mic-03 --project=<DST_SERVICE_PROJECT_ID_1> --zone=asia-northeast1-a --machine-type=<MACHINE_TYPE> --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore) が担当
  ```

#### `org-svc1-deb-n2-std2-02` (location=`asia-northeast1-a`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/zones/asia-northeast1-a/instances/org-svc1-deb-n2-std2-02`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_instance`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_instance)
- 推奨コマンド:
  ```bash
  gcloud compute instances describe org-svc1-deb-n2-std2-02 --zone=asia-northeast1-a --project=<SRC_SERVICE_PROJECT_ID_1>
  gcloud compute instances create org-svc1-deb-n2-std2-02 --project=<DST_SERVICE_PROJECT_ID_1> --zone=asia-northeast1-a --machine-type=<MACHINE_TYPE> --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore) が担当
  ```

#### `instance-1` (location=`asia-northeast1-b`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/zones/asia-northeast1-b/instances/instance-1`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_instance`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_instance)
- 推奨コマンド:
  ```bash
  gcloud compute instances describe instance-1 --zone=asia-northeast1-b --project=<SRC_SERVICE_PROJECT_ID_1>
  gcloud compute instances create instance-1 --project=<DST_SERVICE_PROJECT_ID_1> --zone=asia-northeast1-b --machine-type=<MACHINE_TYPE> --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore) が担当
  ```

### `compute.googleapis.com/InstanceSettings` （3 件）

#### `InstanceSettings` (location=`asia-northeast1-a`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/zones/asia-northeast1-a/instanceSettings/InstanceSettings`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `なし`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud asset describe '//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/zones/asia-northeast1-a/instanceSettings/InstanceSettings' --project=<SRC_SERVICE_PROJECT_ID_1>
  # compute.googleapis.com/InstanceSettings は自動補完対象外。手動でドキュメント参照のうえ dst で再作成してください。
  ```

#### `InstanceSettings` (location=`asia-northeast1-c`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/zones/asia-northeast1-c/instanceSettings/InstanceSettings`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `なし`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud asset describe '//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/zones/asia-northeast1-c/instanceSettings/InstanceSettings' --project=<SRC_SERVICE_PROJECT_ID_1>
  # compute.googleapis.com/InstanceSettings は自動補完対象外。手動でドキュメント参照のうえ dst で再作成してください。
  ```

#### `InstanceSettings` (location=`asia-northeast1-b`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/zones/asia-northeast1-b/instanceSettings/InstanceSettings`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `なし`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud asset describe '//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/zones/asia-northeast1-b/instanceSettings/InstanceSettings' --project=<SRC_SERVICE_PROJECT_ID_1>
  # compute.googleapis.com/InstanceSettings は自動補完対象外。手動でドキュメント参照のうえ dst で再作成してください。
  ```

### `compute.googleapis.com/Project` （1 件）

#### `<SRC_SERVICE_PROJECT_ID_1>` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `なし`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud asset describe '//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>' 
  # compute.googleapis.com/Project は自動補完対象外。手動でドキュメント参照のうえ dst で再作成してください。
  ```

### `compute.googleapis.com/ResourcePolicy` （1 件）

#### `default-schedule-1` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/regions/asia-northeast1/resourcePolicies/default-schedule-1`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_resource_policy`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute resource-policies describe default-schedule-1 --region=asia-northeast1 --project=<SRC_SERVICE_PROJECT_ID_1>
  gcloud compute resource-policies create snapshot-schedule default-schedule-1 --project=<DST_SERVICE_PROJECT_ID_1> --region=asia-northeast1 --max-retention-days=<N> --daily-schedule --start-time=<HH:MM>
  ```

### `compute.googleapis.com/Snapshot` （22 件）

#### `centos8-snapshot` (location=`asia`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/global/snapshots/centos8-snapshot`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe centos8-snapshot --project=<SRC_SERVICE_PROJECT_ID_1>
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `windows-asia-northeast1-c-20260607184701-4itlpj1e` (location=`asia`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/global/snapshots/windows-asia-northeast1-c-20260607184701-4itlpj1e`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe windows-asia-northeast1-c-20260607184701-4itlpj1e --project=<SRC_SERVICE_PROJECT_ID_1>
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `windows-asia-northeast1-c-20260606184701-588u0esr` (location=`asia`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/global/snapshots/windows-asia-northeast1-c-20260606184701-588u0esr`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe windows-asia-northeast1-c-20260606184701-588u0esr --project=<SRC_SERVICE_PROJECT_ID_1>
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `windows-asia-northeast1-c-20260605184701-hbjv5u1f` (location=`asia`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/global/snapshots/windows-asia-northeast1-c-20260605184701-hbjv5u1f`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe windows-asia-northeast1-c-20260605184701-hbjv5u1f --project=<SRC_SERVICE_PROJECT_ID_1>
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `snapshot-3` (location=`asia`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/global/snapshots/snapshot-3`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe snapshot-3 --project=<SRC_SERVICE_PROJECT_ID_1>
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `org-svc1-deb-n2-std2-02` (location=`asia-east1`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/global/snapshots/org-svc1-deb-n2-std2-02`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe org-svc1-deb-n2-std2-02 --project=<SRC_SERVICE_PROJECT_ID_1>
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `snapshotorg-svc1-deb-n2-std2-02` (location=`asia`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/global/snapshots/snapshotorg-svc1-deb-n2-std2-02`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe snapshotorg-svc1-deb-n2-std2-02 --project=<SRC_SERVICE_PROJECT_ID_1>
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `snapshot-org-svc1-deb-n2-std2-01` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/global/snapshots/snapshot-org-svc1-deb-n2-std2-01`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe snapshot-org-svc1-deb-n2-std2-01 --project=<SRC_SERVICE_PROJECT_ID_1>
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `snapshot-for-org-svc1-deb-e2-mic-03` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/global/snapshots/snapshot-for-org-svc1-deb-e2-mic-03`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe snapshot-for-org-svc1-deb-e2-mic-03 --project=<SRC_SERVICE_PROJECT_ID_1>
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `snapshot-org-svc1-deb-e2-mic-02` (location=`asia`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/global/snapshots/snapshot-org-svc1-deb-e2-mic-02`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe snapshot-org-svc1-deb-e2-mic-02 --project=<SRC_SERVICE_PROJECT_ID_1>
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `snapshot-2` (location=`asia`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/global/snapshots/snapshot-2`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe snapshot-2 --project=<SRC_SERVICE_PROJECT_ID_1>
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `snapshot-1` (location=`asia`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/global/snapshots/snapshot-1`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe snapshot-1 --project=<SRC_SERVICE_PROJECT_ID_1>
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `instance-20260528-0-asia-northeast1-c-20260601184701-le4cnjms` (location=`asia`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/global/snapshots/instance-20260528-0-asia-northeast1-c-20260601184701-le4cnjms`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe instance-20260528-0-asia-northeast1-c-20260601184701-le4cnjms --project=<SRC_SERVICE_PROJECT_ID_1>
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `instance-20260528-0-asia-northeast1-c-20260531184701-dx2f6fdv` (location=`asia`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/global/snapshots/instance-20260528-0-asia-northeast1-c-20260531184701-dx2f6fdv`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe instance-20260528-0-asia-northeast1-c-20260531184701-dx2f6fdv --project=<SRC_SERVICE_PROJECT_ID_1>
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `instance-20260528-0-asia-northeast1-c-20260530184701-8uxmmxmw` (location=`asia`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/global/snapshots/instance-20260528-0-asia-northeast1-c-20260530184701-8uxmmxmw`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe instance-20260528-0-asia-northeast1-c-20260530184701-8uxmmxmw --project=<SRC_SERVICE_PROJECT_ID_1>
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `instance-20260528-0-asia-northeast1-c-20260529184701-8m3p4np0` (location=`asia`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/global/snapshots/instance-20260528-0-asia-northeast1-c-20260529184701-8m3p4np0`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe instance-20260528-0-asia-northeast1-c-20260529184701-8m3p4np0 --project=<SRC_SERVICE_PROJECT_ID_1>
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `instance-20260528-0-asia-northeast1-c-20260528184701-72pgudlw` (location=`asia`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/global/snapshots/instance-20260528-0-asia-northeast1-c-20260528184701-72pgudlw`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe instance-20260528-0-asia-northeast1-c-20260528184701-72pgudlw --project=<SRC_SERVICE_PROJECT_ID_1>
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `org-svc1-deb-e2-std4-01` (location=`asia`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/global/snapshots/org-svc1-deb-e2-std4-01`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe org-svc1-deb-e2-std4-01 --project=<SRC_SERVICE_PROJECT_ID_1>
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `org-svc1-deb-n2-std4-01` (location=`asia`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/global/snapshots/org-svc1-deb-n2-std4-01`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe org-svc1-deb-n2-std4-01 --project=<SRC_SERVICE_PROJECT_ID_1>
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `org-svc1-deb-n2-std4-02` (location=`asia`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/global/snapshots/org-svc1-deb-n2-std4-02`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe org-svc1-deb-n2-std4-02 --project=<SRC_SERVICE_PROJECT_ID_1>
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `org-svc1-deb-e2-std4-03` (location=`asia`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/global/snapshots/org-svc1-deb-e2-std4-03`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe org-svc1-deb-e2-std4-03 --project=<SRC_SERVICE_PROJECT_ID_1>
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `org-svc1-deb-e2-std4-02` (location=`asia`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/global/snapshots/org-svc1-deb-e2-std4-02`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe org-svc1-deb-e2-std4-02 --project=<SRC_SERVICE_PROJECT_ID_1>
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

### `iam.googleapis.com/Role` （1 件）

#### `migrationSrcReader` (location=`global`)

- full name: `//iam.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/roles/migrationSrcReader`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_project_iam_custom_role`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_project_iam_custom_role)
- 推奨コマンド:
  ```bash
  gcloud iam roles describe migrationSrcReader --project=<SRC_SERVICE_PROJECT_ID_1>
  gcloud iam roles create migrationSrcReader --project=<DST_SERVICE_PROJECT_ID_1> --title=<TITLE> --permissions=<PERM1,PERM2,...> --stage=GA
  ```

### `iam.googleapis.com/ServiceAccount` （1 件）

#### `1007606807581-compute@developer.gserviceaccount.com` (location=`global`)

- full name: `//iam.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/serviceAccounts/1007606807581-compute@developer.gserviceaccount.com`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_service_account`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_service_account)
- 推奨コマンド:
  ```bash
  gcloud iam service-accounts describe 1007606807581-compute@developer.gserviceaccount.com --project=<SRC_SERVICE_PROJECT_ID_1>
  gcloud iam service-accounts create 1007606807581-compute --project=<DST_SERVICE_PROJECT_ID_1> --display-name=<DISPLAY_NAME>
  ```

### `logging.googleapis.com/LogBucket` （2 件）

#### `_Default` (location=`global`)

- full name: `//logging.googleapis.com/projects/1007606807581/locations/global/buckets/_Default`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_logging_project_bucket_config`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_logging_project_bucket_config)
- 推奨コマンド:
  ```bash
  gcloud logging buckets describe _Default --location=global --project=1007606807581
  gcloud logging buckets create _Default --location=global --project=<DST_SERVICE_PROJECT_ID_1> --retention-days=<N>
  ```

#### `_Required` (location=`global`)

- full name: `//logging.googleapis.com/projects/1007606807581/locations/global/buckets/_Required`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_logging_project_bucket_config`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_logging_project_bucket_config)
- 推奨コマンド:
  ```bash
  gcloud logging buckets describe _Required --location=global --project=1007606807581
  gcloud logging buckets create _Required --location=global --project=<DST_SERVICE_PROJECT_ID_1> --retention-days=<N>
  ```

### `logging.googleapis.com/LogSink` （2 件）

#### `_Required` (location=`global`)

- full name: `//logging.googleapis.com/projects/1007606807581/sinks/_Required`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_logging_project_sink`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_logging_project_sink)
- 推奨コマンド:
  ```bash
  gcloud logging sinks describe _Required --project=1007606807581
  gcloud logging sinks create _Required <DESTINATION> --project=<DST_SERVICE_PROJECT_ID_1> --log-filter='<FILTER>'
  ```

#### `_Default` (location=`global`)

- full name: `//logging.googleapis.com/projects/1007606807581/sinks/_Default`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_logging_project_sink`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_logging_project_sink)
- 推奨コマンド:
  ```bash
  gcloud logging sinks describe _Default --project=1007606807581
  gcloud logging sinks create _Default <DESTINATION> --project=<DST_SERVICE_PROJECT_ID_1> --log-filter='<FILTER>'
  ```

### `osconfig.googleapis.com/OSPolicyAssignment` （1 件）

#### `goog-ops-agent-v2-template-1-7-0-asia-northeast1-c` (location=`asia-northeast1-c`)

- full name: `//osconfig.googleapis.com/projects/1007606807581/locations/asia-northeast1-c/osPolicyAssignments/goog-ops-agent-v2-template-1-7-0-asia-northeast1-c`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `なし`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud asset describe '//osconfig.googleapis.com/projects/1007606807581/locations/asia-northeast1-c/osPolicyAssignments/goog-ops-agent-v2-template-1-7-0-asia-northeast1-c' --project=1007606807581
  # osconfig.googleapis.com/OSPolicyAssignment は自動補完対象外。手動でドキュメント参照のうえ dst で再作成してください。
  ```

### `osconfig.googleapis.com/OSPolicyAssignmentReport` （1 件）

#### `report` (location=`asia-northeast1-c`)

- full name: `//osconfig.googleapis.com/projects/1007606807581/locations/asia-northeast1-c/instances/5142894100681324485/osPolicyAssignments/goog-ops-agent-v2-template-1-7-0-asia-northeast1-c/report`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `なし`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud asset describe '//osconfig.googleapis.com/projects/1007606807581/locations/asia-northeast1-c/instances/5142894100681324485/osPolicyAssignments/goog-ops-agent-v2-template-1-7-0-asia-northeast1-c/report' --project=1007606807581
  # osconfig.googleapis.com/OSPolicyAssignmentReport は自動補完対象外。手動でドキュメント参照のうえ dst で再作成してください。
  ```

### `serviceusage.googleapis.com/Service` （21 件）

#### `bigquerystorage.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1007606807581/services/bigquerystorage.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1007606807581 --filter='config.name:bigquerystorage.googleapis.com'
  gcloud services enable bigquerystorage.googleapis.com --project=<DST_SERVICE_PROJECT_ID_1>
  ```

#### `sql-component.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1007606807581/services/sql-component.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1007606807581 --filter='config.name:sql-component.googleapis.com'
  gcloud services enable sql-component.googleapis.com --project=<DST_SERVICE_PROJECT_ID_1>
  ```

#### `storage-component.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1007606807581/services/storage-component.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1007606807581 --filter='config.name:storage-component.googleapis.com'
  gcloud services enable storage-component.googleapis.com --project=<DST_SERVICE_PROJECT_ID_1>
  ```

#### `logging.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1007606807581/services/logging.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1007606807581 --filter='config.name:logging.googleapis.com'
  gcloud services enable logging.googleapis.com --project=<DST_SERVICE_PROJECT_ID_1>
  ```

#### `cloudasset.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1007606807581/services/cloudasset.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1007606807581 --filter='config.name:cloudasset.googleapis.com'
  gcloud services enable cloudasset.googleapis.com --project=<DST_SERVICE_PROJECT_ID_1>
  ```

#### `serviceusage.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1007606807581/services/serviceusage.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1007606807581 --filter='config.name:serviceusage.googleapis.com'
  gcloud services enable serviceusage.googleapis.com --project=<DST_SERVICE_PROJECT_ID_1>
  ```

#### `compute.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1007606807581/services/compute.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1007606807581 --filter='config.name:compute.googleapis.com'
  gcloud services enable compute.googleapis.com --project=<DST_SERVICE_PROJECT_ID_1>
  ```

#### `servicemanagement.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1007606807581/services/servicemanagement.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1007606807581 --filter='config.name:servicemanagement.googleapis.com'
  gcloud services enable servicemanagement.googleapis.com --project=<DST_SERVICE_PROJECT_ID_1>
  ```

#### `vmmigration.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1007606807581/services/vmmigration.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1007606807581 --filter='config.name:vmmigration.googleapis.com'
  gcloud services enable vmmigration.googleapis.com --project=<DST_SERVICE_PROJECT_ID_1>
  ```

#### `osconfig.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1007606807581/services/osconfig.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1007606807581 --filter='config.name:osconfig.googleapis.com'
  gcloud services enable osconfig.googleapis.com --project=<DST_SERVICE_PROJECT_ID_1>
  ```

#### `monitoring.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1007606807581/services/monitoring.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1007606807581 --filter='config.name:monitoring.googleapis.com'
  gcloud services enable monitoring.googleapis.com --project=<DST_SERVICE_PROJECT_ID_1>
  ```

#### `iam.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1007606807581/services/iam.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1007606807581 --filter='config.name:iam.googleapis.com'
  gcloud services enable iam.googleapis.com --project=<DST_SERVICE_PROJECT_ID_1>
  ```

#### `cloudapis.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1007606807581/services/cloudapis.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1007606807581 --filter='config.name:cloudapis.googleapis.com'
  gcloud services enable cloudapis.googleapis.com --project=<DST_SERVICE_PROJECT_ID_1>
  ```

#### `datastore.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1007606807581/services/datastore.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1007606807581 --filter='config.name:datastore.googleapis.com'
  gcloud services enable datastore.googleapis.com --project=<DST_SERVICE_PROJECT_ID_1>
  ```

#### `oslogin.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1007606807581/services/oslogin.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1007606807581 --filter='config.name:oslogin.googleapis.com'
  gcloud services enable oslogin.googleapis.com --project=<DST_SERVICE_PROJECT_ID_1>
  ```

#### `bigquerymigration.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1007606807581/services/bigquerymigration.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1007606807581 --filter='config.name:bigquerymigration.googleapis.com'
  gcloud services enable bigquerymigration.googleapis.com --project=<DST_SERVICE_PROJECT_ID_1>
  ```

#### `storage-api.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1007606807581/services/storage-api.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1007606807581 --filter='config.name:storage-api.googleapis.com'
  gcloud services enable storage-api.googleapis.com --project=<DST_SERVICE_PROJECT_ID_1>
  ```

#### `cloudtrace.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1007606807581/services/cloudtrace.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1007606807581 --filter='config.name:cloudtrace.googleapis.com'
  gcloud services enable cloudtrace.googleapis.com --project=<DST_SERVICE_PROJECT_ID_1>
  ```

#### `iamcredentials.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1007606807581/services/iamcredentials.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1007606807581 --filter='config.name:iamcredentials.googleapis.com'
  gcloud services enable iamcredentials.googleapis.com --project=<DST_SERVICE_PROJECT_ID_1>
  ```

#### `storage.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1007606807581/services/storage.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1007606807581 --filter='config.name:storage.googleapis.com'
  gcloud services enable storage.googleapis.com --project=<DST_SERVICE_PROJECT_ID_1>
  ```

#### `bigquery.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1007606807581/services/bigquery.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1007606807581 --filter='config.name:bigquery.googleapis.com'
  gcloud services enable bigquery.googleapis.com --project=<DST_SERVICE_PROJECT_ID_1>
  ```

### `vmmigration.googleapis.com/ImageImport` （3 件）

#### `vmdk-imported-20260608-centos8v-boot` (location=`asia-northeast1`)

- full name: `//vmmigration.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/locations/asia-northeast1/imageImports/vmdk-imported-20260608-centos8v-boot`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `なし`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud asset describe '//vmmigration.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/locations/asia-northeast1/imageImports/vmdk-imported-20260608-centos8v-boot' --project=<SRC_SERVICE_PROJECT_ID_1>
  # vmmigration.googleapis.com/ImageImport は自動補完対象外。手動でドキュメント参照のうえ dst で再作成してください。
  ```

#### `vmdk-imported-20260608-centos8t-boot` (location=`asia-northeast1`)

- full name: `//vmmigration.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/locations/asia-northeast1/imageImports/vmdk-imported-20260608-centos8t-boot`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `なし`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud asset describe '//vmmigration.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/locations/asia-northeast1/imageImports/vmdk-imported-20260608-centos8t-boot' --project=<SRC_SERVICE_PROJECT_ID_1>
  # vmmigration.googleapis.com/ImageImport は自動補完対象外。手動でドキュメント参照のうえ dst で再作成してください。
  ```

#### `vmdk-imported-20260608-boot` (location=`asia-northeast1`)

- full name: `//vmmigration.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/locations/asia-northeast1/imageImports/vmdk-imported-20260608-boot`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `なし`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud asset describe '//vmmigration.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/locations/asia-northeast1/imageImports/vmdk-imported-20260608-boot' --project=<SRC_SERVICE_PROJECT_ID_1>
  # vmmigration.googleapis.com/ImageImport は自動補完対象外。手動でドキュメント参照のうえ dst で再作成してください。
  ```

### `vmmigration.googleapis.com/TargetProject` （1 件）

#### `<SRC_SERVICE_PROJECT_ID_1>` (location=`global`)

- full name: `//vmmigration.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/locations/global/targetProjects/<SRC_SERVICE_PROJECT_ID_1>`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `なし`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud asset describe '//vmmigration.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_1>/locations/global/targetProjects/<SRC_SERVICE_PROJECT_ID_1>' --project=<SRC_SERVICE_PROJECT_ID_1>
  # vmmigration.googleapis.com/TargetProject は自動補完対象外。手動でドキュメント参照のうえ dst で再作成してください。
  ```

## プロジェクト: `<SRC_SERVICE_PROJECT_ID_3>` → `<DST_SERVICE_PROJECT_ID_3>`

- CAI 検出リソース: **176** 件 / TF 出力リソース: **3** 件 / 一致: **0** 件 / 欠落候補: **176** 件

### `bigquery.googleapis.com/Dataset` （2 件）

#### `dataset_bar` (location=`US`)

- full name: `//bigquery.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/datasets/dataset_bar`
- 担当ステップ: `data_sync`
- 期待 TF 型: `google_bigquery_dataset`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_bigquery_dataset)
- 推奨コマンド:
  ```bash
  bq --project_id=<SRC_SERVICE_PROJECT_ID_3> show --format=prettyjson dataset_bar
  bq --project_id=<DST_SERVICE_PROJECT_ID_3> mk --location=US --dataset <DST_SERVICE_PROJECT_ID_3>:dataset_bar
  ```

#### `dataset_foo` (location=`US`)

- full name: `//bigquery.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/datasets/dataset_foo`
- 担当ステップ: `data_sync`
- 期待 TF 型: `google_bigquery_dataset`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_bigquery_dataset)
- 推奨コマンド:
  ```bash
  bq --project_id=<SRC_SERVICE_PROJECT_ID_3> show --format=prettyjson dataset_foo
  bq --project_id=<DST_SERVICE_PROJECT_ID_3> mk --location=US --dataset <DST_SERVICE_PROJECT_ID_3>:dataset_foo
  ```

### `bigquery.googleapis.com/Table` （2 件）

#### `item_purchase_logs_all_json` (location=`US`)

- full name: `//bigquery.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/datasets/dataset_bar/tables/item_purchase_logs_all_json`
- 担当ステップ: `data_sync`
- 期待 TF 型: `google_bigquery_table`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_bigquery_table)
- 推奨コマンド:
  ```bash
  bq --project_id=<SRC_SERVICE_PROJECT_ID_3> show --format=prettyjson <SRC_SERVICE_PROJECT_ID_3>:dataset_bar.item_purchase_logs_all_json
  bq --project_id=<DST_SERVICE_PROJECT_ID_3> cp <SRC_SERVICE_PROJECT_ID_3>:dataset_bar.item_purchase_logs_all_json <DST_SERVICE_PROJECT_ID_3>:dataset_bar.item_purchase_logs_all_json  # 通常は Step 6 (data_sync) が担当
  ```

#### `game_players_json` (location=`US`)

- full name: `//bigquery.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/datasets/dataset_foo/tables/game_players_json`
- 担当ステップ: `data_sync`
- 期待 TF 型: `google_bigquery_table`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_bigquery_table)
- 推奨コマンド:
  ```bash
  bq --project_id=<SRC_SERVICE_PROJECT_ID_3> show --format=prettyjson <SRC_SERVICE_PROJECT_ID_3>:dataset_foo.game_players_json
  bq --project_id=<DST_SERVICE_PROJECT_ID_3> cp <SRC_SERVICE_PROJECT_ID_3>:dataset_foo.game_players_json <DST_SERVICE_PROJECT_ID_3>:dataset_foo.game_players_json  # 通常は Step 6 (data_sync) が担当
  ```

### `cloudbilling.googleapis.com/ProjectBillingInfo` （1 件）

#### `billingInfo` (location=`global`)

- full name: `//cloudbilling.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/billingInfo`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `なし`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud asset describe '//cloudbilling.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/billingInfo' --project=<SRC_SERVICE_PROJECT_ID_3>
  # cloudbilling.googleapis.com/ProjectBillingInfo は自動補完対象外。手動でドキュメント参照のうえ dst で再作成してください。
  ```

### `cloudresourcemanager.googleapis.com/Project` （1 件）

#### `<SRC_SERVICE_PROJECT_ID_3>` (location=`global`)

- full name: `//cloudresourcemanager.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `なし`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud asset describe '//cloudresourcemanager.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>' 
  # cloudresourcemanager.googleapis.com/Project は自動補完対象外。手動でドキュメント参照のうえ dst で再作成してください。
  ```

### `compute.googleapis.com/Address` （7 件）

#### `org-svc3-ub-c2-std4-01-ip` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/regions/asia-northeast1/addresses/org-svc3-ub-c2-std4-01-ip`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses describe org-svc3-ub-c2-std4-01-ip --region=asia-northeast1 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute addresses create org-svc3-ub-c2-std4-01-ip --project=<DST_SERVICE_PROJECT_ID_3> --region=asia-northeast1
  ```

#### `org-svc3-ub-e2-med-02-ip` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/regions/asia-northeast1/addresses/org-svc3-ub-e2-med-02-ip`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses describe org-svc3-ub-e2-med-02-ip --region=asia-northeast1 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute addresses create org-svc3-ub-e2-med-02-ip --project=<DST_SERVICE_PROJECT_ID_3> --region=asia-northeast1
  ```

#### `org-svc3-ub-e2-med-01-ip` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/regions/asia-northeast1/addresses/org-svc3-ub-e2-med-01-ip`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses describe org-svc3-ub-e2-med-01-ip --region=asia-northeast1 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute addresses create org-svc3-ub-e2-med-01-ip --project=<DST_SERVICE_PROJECT_ID_3> --region=asia-northeast1
  ```

#### `org-svc3-ub-e2-mic-01-ip` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/regions/asia-northeast1/addresses/org-svc3-ub-e2-mic-01-ip`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses describe org-svc3-ub-e2-mic-01-ip --region=asia-northeast1 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute addresses create org-svc3-ub-e2-mic-01-ip --project=<DST_SERVICE_PROJECT_ID_3> --region=asia-northeast1
  ```

#### `org-svc3-ub-e2-mic-02-ip` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/regions/asia-northeast1/addresses/org-svc3-ub-e2-mic-02-ip`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses describe org-svc3-ub-e2-mic-02-ip --region=asia-northeast1 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute addresses create org-svc3-ub-e2-mic-02-ip --project=<DST_SERVICE_PROJECT_ID_3> --region=asia-northeast1
  ```

#### `org-svc3-ub-e2-med-03-ip` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/regions/asia-northeast1/addresses/org-svc3-ub-e2-med-03-ip`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses describe org-svc3-ub-e2-med-03-ip --region=asia-northeast1 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute addresses create org-svc3-ub-e2-med-03-ip --project=<DST_SERVICE_PROJECT_ID_3> --region=asia-northeast1
  ```

#### `test` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/regions/asia-northeast1/addresses/test`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses describe test --region=asia-northeast1 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute addresses create test --project=<DST_SERVICE_PROJECT_ID_3> --region=asia-northeast1
  ```

### `compute.googleapis.com/Disk` （6 件）

#### `org-svc3-ub-c2-std4-01` (location=`asia-northeast1-a`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/zones/asia-northeast1-a/disks/org-svc3-ub-c2-std4-01`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_disk/google_compute_region_disk`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_disk/google_compute_region_disk)
- 推奨コマンド:
  ```bash
  gcloud compute disks describe org-svc3-ub-c2-std4-01 --zone=asia-northeast1-a --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute disks create org-svc3-ub-c2-std4-01 --project=<DST_SERVICE_PROJECT_ID_3> --zone=asia-northeast1-a --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore)
  ```

#### `org-svc3-ub-e2-med-02` (location=`asia-northeast1-a`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/zones/asia-northeast1-a/disks/org-svc3-ub-e2-med-02`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_disk/google_compute_region_disk`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_disk/google_compute_region_disk)
- 推奨コマンド:
  ```bash
  gcloud compute disks describe org-svc3-ub-e2-med-02 --zone=asia-northeast1-a --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute disks create org-svc3-ub-e2-med-02 --project=<DST_SERVICE_PROJECT_ID_3> --zone=asia-northeast1-a --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore)
  ```

#### `org-svc3-ub-e2-mic-02` (location=`asia-northeast1-a`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/zones/asia-northeast1-a/disks/org-svc3-ub-e2-mic-02`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_disk/google_compute_region_disk`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_disk/google_compute_region_disk)
- 推奨コマンド:
  ```bash
  gcloud compute disks describe org-svc3-ub-e2-mic-02 --zone=asia-northeast1-a --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute disks create org-svc3-ub-e2-mic-02 --project=<DST_SERVICE_PROJECT_ID_3> --zone=asia-northeast1-a --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore)
  ```

#### `org-svc3-ub-e2-mic-01` (location=`asia-northeast1-a`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/zones/asia-northeast1-a/disks/org-svc3-ub-e2-mic-01`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_disk/google_compute_region_disk`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_disk/google_compute_region_disk)
- 推奨コマンド:
  ```bash
  gcloud compute disks describe org-svc3-ub-e2-mic-01 --zone=asia-northeast1-a --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute disks create org-svc3-ub-e2-mic-01 --project=<DST_SERVICE_PROJECT_ID_3> --zone=asia-northeast1-a --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore)
  ```

#### `org-svc3-ub-e2-med-03` (location=`asia-northeast1-a`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/zones/asia-northeast1-a/disks/org-svc3-ub-e2-med-03`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_disk/google_compute_region_disk`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_disk/google_compute_region_disk)
- 推奨コマンド:
  ```bash
  gcloud compute disks describe org-svc3-ub-e2-med-03 --zone=asia-northeast1-a --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute disks create org-svc3-ub-e2-med-03 --project=<DST_SERVICE_PROJECT_ID_3> --zone=asia-northeast1-a --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore)
  ```

#### `org-svc3-ub-e2-med-01` (location=`asia-northeast1-a`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/zones/asia-northeast1-a/disks/org-svc3-ub-e2-med-01`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_disk/google_compute_region_disk`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_disk/google_compute_region_disk)
- 推奨コマンド:
  ```bash
  gcloud compute disks describe org-svc3-ub-e2-med-01 --zone=asia-northeast1-a --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute disks create org-svc3-ub-e2-med-01 --project=<DST_SERVICE_PROJECT_ID_3> --zone=asia-northeast1-a --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore)
  ```

### `compute.googleapis.com/Firewall` （7 件）

#### `test` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/firewalls/test`
- 担当ステップ: `network_firewall`
- 期待 TF 型: `google_compute_firewall`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_firewall)
- 推奨コマンド:
  ```bash
  gcloud compute firewall-rules describe test --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute firewall-rules create test --project=<DST_SERVICE_PROJECT_ID_3> --network=<NETWORK> --direction=<INGRESS|EGRESS> --action=<ALLOW|DENY> --rules=<PROTO:PORT,...>
  ```

#### `deny` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/firewalls/deny`
- 担当ステップ: `network_firewall`
- 期待 TF 型: `google_compute_firewall`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_firewall)
- 推奨コマンド:
  ```bash
  gcloud compute firewall-rules describe deny --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute firewall-rules create deny --project=<DST_SERVICE_PROJECT_ID_3> --network=<NETWORK> --direction=<INGRESS|EGRESS> --action=<ALLOW|DENY> --rules=<PROTO:PORT,...>
  ```

#### `ib-network-allow-internal` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/firewalls/ib-network-allow-internal`
- 担当ステップ: `network_firewall`
- 期待 TF 型: `google_compute_firewall`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_firewall)
- 推奨コマンド:
  ```bash
  gcloud compute firewall-rules describe ib-network-allow-internal --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute firewall-rules create ib-network-allow-internal --project=<DST_SERVICE_PROJECT_ID_3> --network=<NETWORK> --direction=<INGRESS|EGRESS> --action=<ALLOW|DENY> --rules=<PROTO:PORT,...>
  ```

#### `default-allow-ssh` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/firewalls/default-allow-ssh`
- 担当ステップ: `network_firewall`
- 期待 TF 型: `google_compute_firewall`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_firewall)
- 推奨コマンド:
  ```bash
  gcloud compute firewall-rules describe default-allow-ssh --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute firewall-rules create default-allow-ssh --project=<DST_SERVICE_PROJECT_ID_3> --network=<NETWORK> --direction=<INGRESS|EGRESS> --action=<ALLOW|DENY> --rules=<PROTO:PORT,...>
  ```

#### `default-allow-internal` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/firewalls/default-allow-internal`
- 担当ステップ: `network_firewall`
- 期待 TF 型: `google_compute_firewall`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_firewall)
- 推奨コマンド:
  ```bash
  gcloud compute firewall-rules describe default-allow-internal --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute firewall-rules create default-allow-internal --project=<DST_SERVICE_PROJECT_ID_3> --network=<NETWORK> --direction=<INGRESS|EGRESS> --action=<ALLOW|DENY> --rules=<PROTO:PORT,...>
  ```

#### `default-allow-rdp` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/firewalls/default-allow-rdp`
- 担当ステップ: `network_firewall`
- 期待 TF 型: `google_compute_firewall`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_firewall)
- 推奨コマンド:
  ```bash
  gcloud compute firewall-rules describe default-allow-rdp --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute firewall-rules create default-allow-rdp --project=<DST_SERVICE_PROJECT_ID_3> --network=<NETWORK> --direction=<INGRESS|EGRESS> --action=<ALLOW|DENY> --rules=<PROTO:PORT,...>
  ```

#### `default-allow-icmp` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/firewalls/default-allow-icmp`
- 担当ステップ: `network_firewall`
- 期待 TF 型: `google_compute_firewall`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_firewall)
- 推奨コマンド:
  ```bash
  gcloud compute firewall-rules describe default-allow-icmp --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute firewall-rules create default-allow-icmp --project=<DST_SERVICE_PROJECT_ID_3> --network=<NETWORK> --direction=<INGRESS|EGRESS> --action=<ALLOW|DENY> --rules=<PROTO:PORT,...>
  ```

### `compute.googleapis.com/Image` （4 件）

#### `img-org-svc3-ub-e2-med-03` (location=`asia`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/images/img-org-svc3-ub-e2-med-03`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_image`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute images describe img-org-svc3-ub-e2-med-03 --project=<SRC_SERVICE_PROJECT_ID_3>
  # image は使用しない方針（snapshot 由来）。必要なら gcloud compute images create img-org-svc3-ub-e2-med-03 --project=<DST_SERVICE_PROJECT_ID_3> --source-snapshot=<SNAPSHOT>
  ```

#### `img-org-svc3-ub-e2-med-02` (location=`asia`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/images/img-org-svc3-ub-e2-med-02`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_image`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute images describe img-org-svc3-ub-e2-med-02 --project=<SRC_SERVICE_PROJECT_ID_3>
  # image は使用しない方針（snapshot 由来）。必要なら gcloud compute images create img-org-svc3-ub-e2-med-02 --project=<DST_SERVICE_PROJECT_ID_3> --source-snapshot=<SNAPSHOT>
  ```

#### `img-org-svc3-ub-e2-med-01` (location=`asia`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/images/img-org-svc3-ub-e2-med-01`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_image`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute images describe img-org-svc3-ub-e2-med-01 --project=<SRC_SERVICE_PROJECT_ID_3>
  # image は使用しない方針（snapshot 由来）。必要なら gcloud compute images create img-org-svc3-ub-e2-med-01 --project=<DST_SERVICE_PROJECT_ID_3> --source-snapshot=<SNAPSHOT>
  ```

#### `img-org-svc3-ub-c2-std4-01` (location=`asia`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/images/img-org-svc3-ub-c2-std4-01`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_image`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute images describe img-org-svc3-ub-c2-std4-01 --project=<SRC_SERVICE_PROJECT_ID_3>
  # image は使用しない方針（snapshot 由来）。必要なら gcloud compute images create img-org-svc3-ub-c2-std4-01 --project=<DST_SERVICE_PROJECT_ID_3> --source-snapshot=<SNAPSHOT>
  ```

### `compute.googleapis.com/Instance` （6 件）

#### `org-svc3-ub-c2-std4-01` (location=`asia-northeast1-a`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/zones/asia-northeast1-a/instances/org-svc3-ub-c2-std4-01`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_instance`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_instance)
- 推奨コマンド:
  ```bash
  gcloud compute instances describe org-svc3-ub-c2-std4-01 --zone=asia-northeast1-a --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute instances create org-svc3-ub-c2-std4-01 --project=<DST_SERVICE_PROJECT_ID_3> --zone=asia-northeast1-a --machine-type=<MACHINE_TYPE> --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore) が担当
  ```

#### `org-svc3-ub-e2-med-01` (location=`asia-northeast1-a`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/zones/asia-northeast1-a/instances/org-svc3-ub-e2-med-01`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_instance`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_instance)
- 推奨コマンド:
  ```bash
  gcloud compute instances describe org-svc3-ub-e2-med-01 --zone=asia-northeast1-a --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute instances create org-svc3-ub-e2-med-01 --project=<DST_SERVICE_PROJECT_ID_3> --zone=asia-northeast1-a --machine-type=<MACHINE_TYPE> --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore) が担当
  ```

#### `org-svc3-ub-e2-med-03` (location=`asia-northeast1-a`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/zones/asia-northeast1-a/instances/org-svc3-ub-e2-med-03`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_instance`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_instance)
- 推奨コマンド:
  ```bash
  gcloud compute instances describe org-svc3-ub-e2-med-03 --zone=asia-northeast1-a --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute instances create org-svc3-ub-e2-med-03 --project=<DST_SERVICE_PROJECT_ID_3> --zone=asia-northeast1-a --machine-type=<MACHINE_TYPE> --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore) が担当
  ```

#### `org-svc3-ub-e2-mic-01` (location=`asia-northeast1-a`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/zones/asia-northeast1-a/instances/org-svc3-ub-e2-mic-01`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_instance`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_instance)
- 推奨コマンド:
  ```bash
  gcloud compute instances describe org-svc3-ub-e2-mic-01 --zone=asia-northeast1-a --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute instances create org-svc3-ub-e2-mic-01 --project=<DST_SERVICE_PROJECT_ID_3> --zone=asia-northeast1-a --machine-type=<MACHINE_TYPE> --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore) が担当
  ```

#### `org-svc3-ub-e2-med-02` (location=`asia-northeast1-a`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/zones/asia-northeast1-a/instances/org-svc3-ub-e2-med-02`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_instance`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_instance)
- 推奨コマンド:
  ```bash
  gcloud compute instances describe org-svc3-ub-e2-med-02 --zone=asia-northeast1-a --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute instances create org-svc3-ub-e2-med-02 --project=<DST_SERVICE_PROJECT_ID_3> --zone=asia-northeast1-a --machine-type=<MACHINE_TYPE> --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore) が担当
  ```

#### `org-svc3-ub-e2-mic-02` (location=`asia-northeast1-a`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/zones/asia-northeast1-a/instances/org-svc3-ub-e2-mic-02`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_instance`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_instance)
- 推奨コマンド:
  ```bash
  gcloud compute instances describe org-svc3-ub-e2-mic-02 --zone=asia-northeast1-a --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute instances create org-svc3-ub-e2-mic-02 --project=<DST_SERVICE_PROJECT_ID_3> --zone=asia-northeast1-a --machine-type=<MACHINE_TYPE> --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore) が担当
  ```

### `compute.googleapis.com/InstanceSettings` （3 件）

#### `InstanceSettings` (location=`asia-northeast1-a`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/zones/asia-northeast1-a/instanceSettings/InstanceSettings`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `なし`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud asset describe '//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/zones/asia-northeast1-a/instanceSettings/InstanceSettings' --project=<SRC_SERVICE_PROJECT_ID_3>
  # compute.googleapis.com/InstanceSettings は自動補完対象外。手動でドキュメント参照のうえ dst で再作成してください。
  ```

#### `InstanceSettings` (location=`asia-northeast1-c`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/zones/asia-northeast1-c/instanceSettings/InstanceSettings`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `なし`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud asset describe '//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/zones/asia-northeast1-c/instanceSettings/InstanceSettings' --project=<SRC_SERVICE_PROJECT_ID_3>
  # compute.googleapis.com/InstanceSettings は自動補完対象外。手動でドキュメント参照のうえ dst で再作成してください。
  ```

#### `InstanceSettings` (location=`asia-northeast1-b`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/zones/asia-northeast1-b/instanceSettings/InstanceSettings`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `なし`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud asset describe '//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/zones/asia-northeast1-b/instanceSettings/InstanceSettings' --project=<SRC_SERVICE_PROJECT_ID_3>
  # compute.googleapis.com/InstanceSettings は自動補完対象外。手動でドキュメント参照のうえ dst で再作成してください。
  ```

### `compute.googleapis.com/Network` （2 件）

#### `ib-network` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/networks/ib-network`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_network`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_network)
- 推奨コマンド:
  ```bash
  gcloud compute networks describe ib-network --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute networks create ib-network --project=<DST_SERVICE_PROJECT_ID_3> --subnet-mode=custom
  ```

#### `default` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/networks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_network`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_network)
- 推奨コマンド:
  ```bash
  gcloud compute networks describe default --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute networks create default --project=<DST_SERVICE_PROJECT_ID_3> --subnet-mode=custom
  ```

### `compute.googleapis.com/Project` （1 件）

#### `<SRC_SERVICE_PROJECT_ID_3>` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `なし`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud asset describe '//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>' 
  # compute.googleapis.com/Project は自動補完対象外。手動でドキュメント参照のうえ dst で再作成してください。
  ```

### `compute.googleapis.com/Route` （48 件）

#### `default-route-r-98d048215189550b` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/routes/default-route-r-98d048215189550b`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-r-98d048215189550b --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute routes create default-route-r-98d048215189550b --project=<DST_SERVICE_PROJECT_ID_3> --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-r-11f907f3279696b5` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/routes/default-route-r-11f907f3279696b5`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-r-11f907f3279696b5 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute routes create default-route-r-11f907f3279696b5 --project=<DST_SERVICE_PROJECT_ID_3> --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-d11f2034c4aeb51e` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/routes/default-route-d11f2034c4aeb51e`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-d11f2034c4aeb51e --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute routes create default-route-d11f2034c4aeb51e --project=<DST_SERVICE_PROJECT_ID_3> --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-3fe82b14c98b7cdf` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/routes/default-route-3fe82b14c98b7cdf`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-3fe82b14c98b7cdf --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute routes create default-route-3fe82b14c98b7cdf --project=<DST_SERVICE_PROJECT_ID_3> --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-de5c154989722050` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/routes/default-route-de5c154989722050`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-de5c154989722050 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute routes create default-route-de5c154989722050 --project=<DST_SERVICE_PROJECT_ID_3> --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-522dfd5a9228c0e4` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/routes/default-route-522dfd5a9228c0e4`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-522dfd5a9228c0e4 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute routes create default-route-522dfd5a9228c0e4 --project=<DST_SERVICE_PROJECT_ID_3> --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-893caa5ad4a6657c` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/routes/default-route-893caa5ad4a6657c`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-893caa5ad4a6657c --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute routes create default-route-893caa5ad4a6657c --project=<DST_SERVICE_PROJECT_ID_3> --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-57660cdbff324af4` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/routes/default-route-57660cdbff324af4`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-57660cdbff324af4 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute routes create default-route-57660cdbff324af4 --project=<DST_SERVICE_PROJECT_ID_3> --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-09c2c7b1ab514ff6` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/routes/default-route-09c2c7b1ab514ff6`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-09c2c7b1ab514ff6 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute routes create default-route-09c2c7b1ab514ff6 --project=<DST_SERVICE_PROJECT_ID_3> --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-c8973eb1c13ac479` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/routes/default-route-c8973eb1c13ac479`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-c8973eb1c13ac479 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute routes create default-route-c8973eb1c13ac479 --project=<DST_SERVICE_PROJECT_ID_3> --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-42c467ae5fed1ac0` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/routes/default-route-42c467ae5fed1ac0`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-42c467ae5fed1ac0 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute routes create default-route-42c467ae5fed1ac0 --project=<DST_SERVICE_PROJECT_ID_3> --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-e755856d9b20ba36` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/routes/default-route-e755856d9b20ba36`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-e755856d9b20ba36 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute routes create default-route-e755856d9b20ba36 --project=<DST_SERVICE_PROJECT_ID_3> --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-0a66d0cc9c75cc8b` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/routes/default-route-0a66d0cc9c75cc8b`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-0a66d0cc9c75cc8b --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute routes create default-route-0a66d0cc9c75cc8b --project=<DST_SERVICE_PROJECT_ID_3> --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-2c3846332a2bc3e0` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/routes/default-route-2c3846332a2bc3e0`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-2c3846332a2bc3e0 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute routes create default-route-2c3846332a2bc3e0 --project=<DST_SERVICE_PROJECT_ID_3> --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-2369d72760b8807f` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/routes/default-route-2369d72760b8807f`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-2369d72760b8807f --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute routes create default-route-2369d72760b8807f --project=<DST_SERVICE_PROJECT_ID_3> --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-b7740d025b045e64` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/routes/default-route-b7740d025b045e64`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-b7740d025b045e64 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute routes create default-route-b7740d025b045e64 --project=<DST_SERVICE_PROJECT_ID_3> --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-4402e07ee1f2aeec` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/routes/default-route-4402e07ee1f2aeec`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-4402e07ee1f2aeec --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute routes create default-route-4402e07ee1f2aeec --project=<DST_SERVICE_PROJECT_ID_3> --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-51f46281a5f33c88` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/routes/default-route-51f46281a5f33c88`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-51f46281a5f33c88 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute routes create default-route-51f46281a5f33c88 --project=<DST_SERVICE_PROJECT_ID_3> --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-ba1b19c510ed59d0` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/routes/default-route-ba1b19c510ed59d0`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-ba1b19c510ed59d0 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute routes create default-route-ba1b19c510ed59d0 --project=<DST_SERVICE_PROJECT_ID_3> --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-60c15ba7ae600fc8` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/routes/default-route-60c15ba7ae600fc8`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-60c15ba7ae600fc8 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute routes create default-route-60c15ba7ae600fc8 --project=<DST_SERVICE_PROJECT_ID_3> --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-9dde7ae8184c3852` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/routes/default-route-9dde7ae8184c3852`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-9dde7ae8184c3852 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute routes create default-route-9dde7ae8184c3852 --project=<DST_SERVICE_PROJECT_ID_3> --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-55547ff6ba2ae8e8` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/routes/default-route-55547ff6ba2ae8e8`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-55547ff6ba2ae8e8 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute routes create default-route-55547ff6ba2ae8e8 --project=<DST_SERVICE_PROJECT_ID_3> --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-485b9b21cd18f53c` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/routes/default-route-485b9b21cd18f53c`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-485b9b21cd18f53c --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute routes create default-route-485b9b21cd18f53c --project=<DST_SERVICE_PROJECT_ID_3> --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-67a144c3c4144632` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/routes/default-route-67a144c3c4144632`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-67a144c3c4144632 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute routes create default-route-67a144c3c4144632 --project=<DST_SERVICE_PROJECT_ID_3> --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-10327149af16388b` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/routes/default-route-10327149af16388b`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-10327149af16388b --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute routes create default-route-10327149af16388b --project=<DST_SERVICE_PROJECT_ID_3> --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-bd63b42c414571ce` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/routes/default-route-bd63b42c414571ce`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-bd63b42c414571ce --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute routes create default-route-bd63b42c414571ce --project=<DST_SERVICE_PROJECT_ID_3> --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-9e9d115beaec855b` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/routes/default-route-9e9d115beaec855b`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-9e9d115beaec855b --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute routes create default-route-9e9d115beaec855b --project=<DST_SERVICE_PROJECT_ID_3> --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-05ea1a0ec1214c63` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/routes/default-route-05ea1a0ec1214c63`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-05ea1a0ec1214c63 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute routes create default-route-05ea1a0ec1214c63 --project=<DST_SERVICE_PROJECT_ID_3> --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-5b4b0c3510dd4c63` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/routes/default-route-5b4b0c3510dd4c63`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-5b4b0c3510dd4c63 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute routes create default-route-5b4b0c3510dd4c63 --project=<DST_SERVICE_PROJECT_ID_3> --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-3cb551462fd6d6d5` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/routes/default-route-3cb551462fd6d6d5`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-3cb551462fd6d6d5 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute routes create default-route-3cb551462fd6d6d5 --project=<DST_SERVICE_PROJECT_ID_3> --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-0c0a16c7a37a0d3f` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/routes/default-route-0c0a16c7a37a0d3f`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-0c0a16c7a37a0d3f --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute routes create default-route-0c0a16c7a37a0d3f --project=<DST_SERVICE_PROJECT_ID_3> --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-17612d48b7875af0` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/routes/default-route-17612d48b7875af0`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-17612d48b7875af0 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute routes create default-route-17612d48b7875af0 --project=<DST_SERVICE_PROJECT_ID_3> --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-11e87903139ccd22` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/routes/default-route-11e87903139ccd22`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-11e87903139ccd22 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute routes create default-route-11e87903139ccd22 --project=<DST_SERVICE_PROJECT_ID_3> --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-45f2ff727e2416b8` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/routes/default-route-45f2ff727e2416b8`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-45f2ff727e2416b8 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute routes create default-route-45f2ff727e2416b8 --project=<DST_SERVICE_PROJECT_ID_3> --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-58fd01a24169e46d` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/routes/default-route-58fd01a24169e46d`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-58fd01a24169e46d --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute routes create default-route-58fd01a24169e46d --project=<DST_SERVICE_PROJECT_ID_3> --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-8367b740ba1fb361` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/routes/default-route-8367b740ba1fb361`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-8367b740ba1fb361 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute routes create default-route-8367b740ba1fb361 --project=<DST_SERVICE_PROJECT_ID_3> --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-b3369bd0128f75e6` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/routes/default-route-b3369bd0128f75e6`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-b3369bd0128f75e6 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute routes create default-route-b3369bd0128f75e6 --project=<DST_SERVICE_PROJECT_ID_3> --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-111246bc0783214c` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/routes/default-route-111246bc0783214c`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-111246bc0783214c --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute routes create default-route-111246bc0783214c --project=<DST_SERVICE_PROJECT_ID_3> --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-2de92a3dadc51467` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/routes/default-route-2de92a3dadc51467`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-2de92a3dadc51467 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute routes create default-route-2de92a3dadc51467 --project=<DST_SERVICE_PROJECT_ID_3> --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-b74117b3eb2f1ec9` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/routes/default-route-b74117b3eb2f1ec9`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-b74117b3eb2f1ec9 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute routes create default-route-b74117b3eb2f1ec9 --project=<DST_SERVICE_PROJECT_ID_3> --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-3c185c4503f8f32f` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/routes/default-route-3c185c4503f8f32f`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-3c185c4503f8f32f --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute routes create default-route-3c185c4503f8f32f --project=<DST_SERVICE_PROJECT_ID_3> --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-e73e5fcce9e01700` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/routes/default-route-e73e5fcce9e01700`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-e73e5fcce9e01700 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute routes create default-route-e73e5fcce9e01700 --project=<DST_SERVICE_PROJECT_ID_3> --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-988a7668582a422b` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/routes/default-route-988a7668582a422b`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-988a7668582a422b --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute routes create default-route-988a7668582a422b --project=<DST_SERVICE_PROJECT_ID_3> --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-fb0320b87f0aa00d` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/routes/default-route-fb0320b87f0aa00d`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-fb0320b87f0aa00d --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute routes create default-route-fb0320b87f0aa00d --project=<DST_SERVICE_PROJECT_ID_3> --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-7ca7d814326a7c78` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/routes/default-route-7ca7d814326a7c78`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-7ca7d814326a7c78 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute routes create default-route-7ca7d814326a7c78 --project=<DST_SERVICE_PROJECT_ID_3> --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-d3cb1dfc35875d6f` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/routes/default-route-d3cb1dfc35875d6f`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-d3cb1dfc35875d6f --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute routes create default-route-d3cb1dfc35875d6f --project=<DST_SERVICE_PROJECT_ID_3> --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-90e4a484caccf593` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/routes/default-route-90e4a484caccf593`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-90e4a484caccf593 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute routes create default-route-90e4a484caccf593 --project=<DST_SERVICE_PROJECT_ID_3> --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-2c546851f7c5d132` (location=`global`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/routes/default-route-2c546851f7c5d132`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-2c546851f7c5d132 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute routes create default-route-2c546851f7c5d132 --project=<DST_SERVICE_PROJECT_ID_3> --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

### `compute.googleapis.com/Snapshot` （6 件）

#### `org-svc3-ub-c2-std4-01` (location=`asia`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/snapshots/org-svc3-ub-c2-std4-01`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe org-svc3-ub-c2-std4-01 --project=<SRC_SERVICE_PROJECT_ID_3>
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `org-svc3-ub-e2-mic-02` (location=`asia`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/snapshots/org-svc3-ub-e2-mic-02`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe org-svc3-ub-e2-mic-02 --project=<SRC_SERVICE_PROJECT_ID_3>
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `org-svc3-ub-e2-med-01` (location=`asia`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/snapshots/org-svc3-ub-e2-med-01`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe org-svc3-ub-e2-med-01 --project=<SRC_SERVICE_PROJECT_ID_3>
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `org-svc3-ub-e2-med-03` (location=`asia`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/snapshots/org-svc3-ub-e2-med-03`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe org-svc3-ub-e2-med-03 --project=<SRC_SERVICE_PROJECT_ID_3>
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `org-svc3-ub-e2-mic-01` (location=`asia`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/snapshots/org-svc3-ub-e2-mic-01`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe org-svc3-ub-e2-mic-01 --project=<SRC_SERVICE_PROJECT_ID_3>
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `org-svc3-ub-e2-med-02` (location=`asia`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/global/snapshots/org-svc3-ub-e2-med-02`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe org-svc3-ub-e2-med-02 --project=<SRC_SERVICE_PROJECT_ID_3>
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

### `compute.googleapis.com/Subnetwork` （46 件）

#### `default` (location=`asia-southeast3`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/regions/asia-southeast3/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=asia-southeast3 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute networks subnets create default --project=<DST_SERVICE_PROJECT_ID_3> --region=asia-southeast3 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`europe-north2`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/regions/europe-north2/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=europe-north2 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute networks subnets create default --project=<DST_SERVICE_PROJECT_ID_3> --region=europe-north2 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`northamerica-south1`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/regions/northamerica-south1/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=northamerica-south1 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute networks subnets create default --project=<DST_SERVICE_PROJECT_ID_3> --region=northamerica-south1 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`us-west8`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/regions/us-west8/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=us-west8 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute networks subnets create default --project=<DST_SERVICE_PROJECT_ID_3> --region=us-west8 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`africa-south1`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/regions/africa-south1/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=africa-south1 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute networks subnets create default --project=<DST_SERVICE_PROJECT_ID_3> --region=africa-south1 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`me-central2`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/regions/me-central2/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=me-central2 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute networks subnets create default --project=<DST_SERVICE_PROJECT_ID_3> --region=me-central2 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`europe-west10`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/regions/europe-west10/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=europe-west10 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute networks subnets create default --project=<DST_SERVICE_PROJECT_ID_3> --region=europe-west10 --network=<NETWORK> --range=<CIDR>
  ```

#### `tokyo` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/regions/asia-northeast1/subnetworks/tokyo`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe tokyo --region=asia-northeast1 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute networks subnets create tokyo --project=<DST_SERVICE_PROJECT_ID_3> --region=asia-northeast1 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`me-central1`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/regions/me-central1/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=me-central1 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute networks subnets create default --project=<DST_SERVICE_PROJECT_ID_3> --region=me-central1 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`europe-west12`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/regions/europe-west12/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=europe-west12 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute networks subnets create default --project=<DST_SERVICE_PROJECT_ID_3> --region=europe-west12 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`us-east7`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/regions/us-east7/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=us-east7 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute networks subnets create default --project=<DST_SERVICE_PROJECT_ID_3> --region=us-east7 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`europe-north1`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/regions/europe-north1/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=europe-north1 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute networks subnets create default --project=<DST_SERVICE_PROJECT_ID_3> --region=europe-north1 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`southamerica-east1`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/regions/southamerica-east1/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=southamerica-east1 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute networks subnets create default --project=<DST_SERVICE_PROJECT_ID_3> --region=southamerica-east1 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`europe-west2`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/regions/europe-west2/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=europe-west2 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute networks subnets create default --project=<DST_SERVICE_PROJECT_ID_3> --region=europe-west2 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`europe-west4`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/regions/europe-west4/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=europe-west4 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute networks subnets create default --project=<DST_SERVICE_PROJECT_ID_3> --region=europe-west4 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`asia-northeast2`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/regions/asia-northeast2/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=asia-northeast2 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute networks subnets create default --project=<DST_SERVICE_PROJECT_ID_3> --region=asia-northeast2 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`asia-south1`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/regions/asia-south1/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=asia-south1 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute networks subnets create default --project=<DST_SERVICE_PROJECT_ID_3> --region=asia-south1 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`europe-central2`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/regions/europe-central2/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=europe-central2 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute networks subnets create default --project=<DST_SERVICE_PROJECT_ID_3> --region=europe-central2 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`us-west1`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/regions/us-west1/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=us-west1 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute networks subnets create default --project=<DST_SERVICE_PROJECT_ID_3> --region=us-west1 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`asia-east1`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/regions/asia-east1/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=asia-east1 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute networks subnets create default --project=<DST_SERVICE_PROJECT_ID_3> --region=asia-east1 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`us-south1`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/regions/us-south1/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=us-south1 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute networks subnets create default --project=<DST_SERVICE_PROJECT_ID_3> --region=us-south1 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`us-east4`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/regions/us-east4/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=us-east4 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute networks subnets create default --project=<DST_SERVICE_PROJECT_ID_3> --region=us-east4 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`northamerica-northeast1`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/regions/northamerica-northeast1/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=northamerica-northeast1 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute networks subnets create default --project=<DST_SERVICE_PROJECT_ID_3> --region=northamerica-northeast1 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`us-west4`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/regions/us-west4/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=us-west4 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute networks subnets create default --project=<DST_SERVICE_PROJECT_ID_3> --region=us-west4 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`europe-west6`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/regions/europe-west6/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=europe-west6 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute networks subnets create default --project=<DST_SERVICE_PROJECT_ID_3> --region=europe-west6 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`europe-southwest1`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/regions/europe-southwest1/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=europe-southwest1 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute networks subnets create default --project=<DST_SERVICE_PROJECT_ID_3> --region=europe-southwest1 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`asia-southeast1`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/regions/asia-southeast1/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=asia-southeast1 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute networks subnets create default --project=<DST_SERVICE_PROJECT_ID_3> --region=asia-southeast1 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`europe-west9`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/regions/europe-west9/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=europe-west9 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute networks subnets create default --project=<DST_SERVICE_PROJECT_ID_3> --region=europe-west9 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`me-west1`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/regions/me-west1/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=me-west1 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute networks subnets create default --project=<DST_SERVICE_PROJECT_ID_3> --region=me-west1 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`us-west3`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/regions/us-west3/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=us-west3 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute networks subnets create default --project=<DST_SERVICE_PROJECT_ID_3> --region=us-west3 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`us-east5`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/regions/us-east5/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=us-east5 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute networks subnets create default --project=<DST_SERVICE_PROJECT_ID_3> --region=us-east5 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`australia-southeast1`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/regions/australia-southeast1/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=australia-southeast1 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute networks subnets create default --project=<DST_SERVICE_PROJECT_ID_3> --region=australia-southeast1 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`asia-south2`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/regions/asia-south2/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=asia-south2 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute networks subnets create default --project=<DST_SERVICE_PROJECT_ID_3> --region=asia-south2 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`us-west2`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/regions/us-west2/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=us-west2 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute networks subnets create default --project=<DST_SERVICE_PROJECT_ID_3> --region=us-west2 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`europe-west8`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/regions/europe-west8/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=europe-west8 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute networks subnets create default --project=<DST_SERVICE_PROJECT_ID_3> --region=europe-west8 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`asia-southeast2`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/regions/asia-southeast2/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=asia-southeast2 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute networks subnets create default --project=<DST_SERVICE_PROJECT_ID_3> --region=asia-southeast2 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`australia-southeast2`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/regions/australia-southeast2/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=australia-southeast2 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute networks subnets create default --project=<DST_SERVICE_PROJECT_ID_3> --region=australia-southeast2 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`asia-east2`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/regions/asia-east2/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=asia-east2 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute networks subnets create default --project=<DST_SERVICE_PROJECT_ID_3> --region=asia-east2 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`northamerica-northeast2`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/regions/northamerica-northeast2/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=northamerica-northeast2 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute networks subnets create default --project=<DST_SERVICE_PROJECT_ID_3> --region=northamerica-northeast2 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`us-east1`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/regions/us-east1/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=us-east1 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute networks subnets create default --project=<DST_SERVICE_PROJECT_ID_3> --region=us-east1 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`europe-west1`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/regions/europe-west1/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=europe-west1 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute networks subnets create default --project=<DST_SERVICE_PROJECT_ID_3> --region=europe-west1 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/regions/asia-northeast1/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=asia-northeast1 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute networks subnets create default --project=<DST_SERVICE_PROJECT_ID_3> --region=asia-northeast1 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`europe-west3`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/regions/europe-west3/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=europe-west3 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute networks subnets create default --project=<DST_SERVICE_PROJECT_ID_3> --region=europe-west3 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`southamerica-west1`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/regions/southamerica-west1/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=southamerica-west1 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute networks subnets create default --project=<DST_SERVICE_PROJECT_ID_3> --region=southamerica-west1 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`asia-northeast3`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/regions/asia-northeast3/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=asia-northeast3 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute networks subnets create default --project=<DST_SERVICE_PROJECT_ID_3> --region=asia-northeast3 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`us-central1`)

- full name: `//compute.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/regions/us-central1/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=us-central1 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud compute networks subnets create default --project=<DST_SERVICE_PROJECT_ID_3> --region=us-central1 --network=<NETWORK> --range=<CIDR>
  ```

### `iam.googleapis.com/Role` （5 件）

#### `incre3` (location=`global`)

- full name: `//iam.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/roles/incre3`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_project_iam_custom_role`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_project_iam_custom_role)
- 推奨コマンド:
  ```bash
  gcloud iam roles describe incre3 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud iam roles create incre3 --project=<DST_SERVICE_PROJECT_ID_3> --title=<TITLE> --permissions=<PERM1,PERM2,...> --stage=GA
  ```

#### `Incre` (location=`global`)

- full name: `//iam.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/roles/Incre`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_project_iam_custom_role`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_project_iam_custom_role)
- 推奨コマンド:
  ```bash
  gcloud iam roles describe Incre --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud iam roles create Incre --project=<DST_SERVICE_PROJECT_ID_3> --title=<TITLE> --permissions=<PERM1,PERM2,...> --stage=GA
  ```

#### `migrationSrcReader` (location=`global`)

- full name: `//iam.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/roles/migrationSrcReader`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_project_iam_custom_role`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_project_iam_custom_role)
- 推奨コマンド:
  ```bash
  gcloud iam roles describe migrationSrcReader --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud iam roles create migrationSrcReader --project=<DST_SERVICE_PROJECT_ID_3> --title=<TITLE> --permissions=<PERM1,PERM2,...> --stage=GA
  ```

#### `incre2` (location=`global`)

- full name: `//iam.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/roles/incre2`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_project_iam_custom_role`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_project_iam_custom_role)
- 推奨コマンド:
  ```bash
  gcloud iam roles describe incre2 --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud iam roles create incre2 --project=<DST_SERVICE_PROJECT_ID_3> --title=<TITLE> --permissions=<PERM1,PERM2,...> --stage=GA
  ```

#### `incre` (location=`global`)

- full name: `//iam.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/roles/incre`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_project_iam_custom_role`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_project_iam_custom_role)
- 推奨コマンド:
  ```bash
  gcloud iam roles describe incre --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud iam roles create incre --project=<DST_SERVICE_PROJECT_ID_3> --title=<TITLE> --permissions=<PERM1,PERM2,...> --stage=GA
  ```

### `iam.googleapis.com/ServiceAccount` （2 件）

#### `incredibuild@<SRC_SERVICE_PROJECT_ID_3>.iam.gserviceaccount.com` (location=`global`)

- full name: `//iam.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/serviceAccounts/incredibuild@<SRC_SERVICE_PROJECT_ID_3>.iam.gserviceaccount.com`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_service_account`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_service_account)
- 推奨コマンド:
  ```bash
  gcloud iam service-accounts describe incredibuild@<SRC_SERVICE_PROJECT_ID_3>.iam.gserviceaccount.com --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud iam service-accounts create incredibuild --project=<DST_SERVICE_PROJECT_ID_3> --display-name=<DISPLAY_NAME>
  ```

#### `1033858800454-compute@developer.gserviceaccount.com` (location=`global`)

- full name: `//iam.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/serviceAccounts/1033858800454-compute@developer.gserviceaccount.com`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_service_account`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_service_account)
- 推奨コマンド:
  ```bash
  gcloud iam service-accounts describe 1033858800454-compute@developer.gserviceaccount.com --project=<SRC_SERVICE_PROJECT_ID_3>
  gcloud iam service-accounts create 1033858800454-compute --project=<DST_SERVICE_PROJECT_ID_3> --display-name=<DISPLAY_NAME>
  ```

### `iam.googleapis.com/ServiceAccountKey` （2 件）

#### `d04a4ff33affc3a5124a8aef69152ab31ca7a091` (location=`global`)

- full name: `//iam.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/serviceAccounts/100682100138600860386/keys/d04a4ff33affc3a5124a8aef69152ab31ca7a091`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `なし`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud asset describe '//iam.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/serviceAccounts/100682100138600860386/keys/d04a4ff33affc3a5124a8aef69152ab31ca7a091' --project=<SRC_SERVICE_PROJECT_ID_3>
  # iam.googleapis.com/ServiceAccountKey は自動補完対象外。手動でドキュメント参照のうえ dst で再作成してください。
  ```

#### `7e0170835cd407104ac4f90797cc0b12402429a5` (location=`global`)

- full name: `//iam.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/serviceAccounts/104507197771240164503/keys/7e0170835cd407104ac4f90797cc0b12402429a5`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `なし`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud asset describe '//iam.googleapis.com/projects/<SRC_SERVICE_PROJECT_ID_3>/serviceAccounts/104507197771240164503/keys/7e0170835cd407104ac4f90797cc0b12402429a5' --project=<SRC_SERVICE_PROJECT_ID_3>
  # iam.googleapis.com/ServiceAccountKey は自動補完対象外。手動でドキュメント参照のうえ dst で再作成してください。
  ```

### `logging.googleapis.com/LogBucket` （2 件）

#### `_Default` (location=`global`)

- full name: `//logging.googleapis.com/projects/1033858800454/locations/global/buckets/_Default`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_logging_project_bucket_config`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_logging_project_bucket_config)
- 推奨コマンド:
  ```bash
  gcloud logging buckets describe _Default --location=global --project=1033858800454
  gcloud logging buckets create _Default --location=global --project=<DST_SERVICE_PROJECT_ID_3> --retention-days=<N>
  ```

#### `_Required` (location=`global`)

- full name: `//logging.googleapis.com/projects/1033858800454/locations/global/buckets/_Required`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_logging_project_bucket_config`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_logging_project_bucket_config)
- 推奨コマンド:
  ```bash
  gcloud logging buckets describe _Required --location=global --project=1033858800454
  gcloud logging buckets create _Required --location=global --project=<DST_SERVICE_PROJECT_ID_3> --retention-days=<N>
  ```

### `logging.googleapis.com/LogSink` （2 件）

#### `_Required` (location=`global`)

- full name: `//logging.googleapis.com/projects/1033858800454/sinks/_Required`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_logging_project_sink`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_logging_project_sink)
- 推奨コマンド:
  ```bash
  gcloud logging sinks describe _Required --project=1033858800454
  gcloud logging sinks create _Required <DESTINATION> --project=<DST_SERVICE_PROJECT_ID_3> --log-filter='<FILTER>'
  ```

#### `_Default` (location=`global`)

- full name: `//logging.googleapis.com/projects/1033858800454/sinks/_Default`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_logging_project_sink`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_logging_project_sink)
- 推奨コマンド:
  ```bash
  gcloud logging sinks describe _Default --project=1033858800454
  gcloud logging sinks create _Default <DESTINATION> --project=<DST_SERVICE_PROJECT_ID_3> --log-filter='<FILTER>'
  ```

### `serviceusage.googleapis.com/Service` （19 件）

#### `storage-component.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1033858800454/services/storage-component.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1033858800454 --filter='config.name:storage-component.googleapis.com'
  gcloud services enable storage-component.googleapis.com --project=<DST_SERVICE_PROJECT_ID_3>
  ```

#### `cloudtrace.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1033858800454/services/cloudtrace.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1033858800454 --filter='config.name:cloudtrace.googleapis.com'
  gcloud services enable cloudtrace.googleapis.com --project=<DST_SERVICE_PROJECT_ID_3>
  ```

#### `oslogin.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1033858800454/services/oslogin.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1033858800454 --filter='config.name:oslogin.googleapis.com'
  gcloud services enable oslogin.googleapis.com --project=<DST_SERVICE_PROJECT_ID_3>
  ```

#### `logging.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1033858800454/services/logging.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1033858800454 --filter='config.name:logging.googleapis.com'
  gcloud services enable logging.googleapis.com --project=<DST_SERVICE_PROJECT_ID_3>
  ```

#### `cloudapis.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1033858800454/services/cloudapis.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1033858800454 --filter='config.name:cloudapis.googleapis.com'
  gcloud services enable cloudapis.googleapis.com --project=<DST_SERVICE_PROJECT_ID_3>
  ```

#### `cloudasset.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1033858800454/services/cloudasset.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1033858800454 --filter='config.name:cloudasset.googleapis.com'
  gcloud services enable cloudasset.googleapis.com --project=<DST_SERVICE_PROJECT_ID_3>
  ```

#### `sql-component.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1033858800454/services/sql-component.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1033858800454 --filter='config.name:sql-component.googleapis.com'
  gcloud services enable sql-component.googleapis.com --project=<DST_SERVICE_PROJECT_ID_3>
  ```

#### `bigquerystorage.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1033858800454/services/bigquerystorage.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1033858800454 --filter='config.name:bigquerystorage.googleapis.com'
  gcloud services enable bigquerystorage.googleapis.com --project=<DST_SERVICE_PROJECT_ID_3>
  ```

#### `cloudaicompanion.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1033858800454/services/cloudaicompanion.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1033858800454 --filter='config.name:cloudaicompanion.googleapis.com'
  gcloud services enable cloudaicompanion.googleapis.com --project=<DST_SERVICE_PROJECT_ID_3>
  ```

#### `bigquerymigration.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1033858800454/services/bigquerymigration.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1033858800454 --filter='config.name:bigquerymigration.googleapis.com'
  gcloud services enable bigquerymigration.googleapis.com --project=<DST_SERVICE_PROJECT_ID_3>
  ```

#### `bigquery.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1033858800454/services/bigquery.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1033858800454 --filter='config.name:bigquery.googleapis.com'
  gcloud services enable bigquery.googleapis.com --project=<DST_SERVICE_PROJECT_ID_3>
  ```

#### `monitoring.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1033858800454/services/monitoring.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1033858800454 --filter='config.name:monitoring.googleapis.com'
  gcloud services enable monitoring.googleapis.com --project=<DST_SERVICE_PROJECT_ID_3>
  ```

#### `serviceusage.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1033858800454/services/serviceusage.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1033858800454 --filter='config.name:serviceusage.googleapis.com'
  gcloud services enable serviceusage.googleapis.com --project=<DST_SERVICE_PROJECT_ID_3>
  ```

#### `datastore.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1033858800454/services/datastore.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1033858800454 --filter='config.name:datastore.googleapis.com'
  gcloud services enable datastore.googleapis.com --project=<DST_SERVICE_PROJECT_ID_3>
  ```

#### `compute.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1033858800454/services/compute.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1033858800454 --filter='config.name:compute.googleapis.com'
  gcloud services enable compute.googleapis.com --project=<DST_SERVICE_PROJECT_ID_3>
  ```

#### `storage.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1033858800454/services/storage.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1033858800454 --filter='config.name:storage.googleapis.com'
  gcloud services enable storage.googleapis.com --project=<DST_SERVICE_PROJECT_ID_3>
  ```

#### `cloudresourcemanager.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1033858800454/services/cloudresourcemanager.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1033858800454 --filter='config.name:cloudresourcemanager.googleapis.com'
  gcloud services enable cloudresourcemanager.googleapis.com --project=<DST_SERVICE_PROJECT_ID_3>
  ```

#### `servicemanagement.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1033858800454/services/servicemanagement.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1033858800454 --filter='config.name:servicemanagement.googleapis.com'
  gcloud services enable servicemanagement.googleapis.com --project=<DST_SERVICE_PROJECT_ID_3>
  ```

#### `storage-api.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1033858800454/services/storage-api.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1033858800454 --filter='config.name:storage-api.googleapis.com'
  gcloud services enable storage-api.googleapis.com --project=<DST_SERVICE_PROJECT_ID_3>
  ```

### `storage.googleapis.com/Bucket` （2 件）

#### `<TEST_PROJECT_ID>` (location=`us`)

- full name: `//storage.googleapis.com/<TEST_PROJECT_ID>`
- 担当ステップ: `data_sync`
- 期待 TF 型: `google_storage_bucket`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_storage_bucket)
- 推奨コマンド:
  ```bash
  gcloud storage buckets describe gs://<TEST_PROJECT_ID>
  gcloud storage buckets create gs://<DST_BUCKET_NAME> --project=<DST_SERVICE_PROJECT_ID_3> --location=us  # 名前は rename_rules.gcs を適用すること
  ```

#### `<SRC_SERVICE_PROJECT_ID_3>` (location=`us`)

- full name: `//storage.googleapis.com/<SRC_SERVICE_PROJECT_ID_3>`
- 担当ステップ: `data_sync`
- 期待 TF 型: `google_storage_bucket`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_storage_bucket)
- 推奨コマンド:
  ```bash
  gcloud storage buckets describe gs://<SRC_SERVICE_PROJECT_ID_3>
  gcloud storage buckets create gs://<DST_BUCKET_NAME> --project=<DST_SERVICE_PROJECT_ID_3> --location=us  # 名前は rename_rules.gcs を適用すること
  ```

---
合計欠落候補: **327** 件
