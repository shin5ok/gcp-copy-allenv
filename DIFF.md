# CAI ↔ Terraform bulk-export 差分レポート

Cloud Asset Inventory（CAI）が観測した src 側リソースのうち、
`gcloud beta resource-config bulk-export` の出力に**含まれなかった**ものを
プロジェクトごとに列挙し、dst 側に再現するための gcloud コマンドを併記します。

- 「意図的に対象外」: `_ASSET_COVERAGE` で None 指定。実害なしとして除外可。
- 「別ステップが担当」: Step 4.5 / Step 5 / Step 6 等で複製。bulk-export 単体での欠落は想定通り。
- 「未登録」「bulk-export が出力しなかった」: 対応の検討が必要。

## プロジェクト: `shingo-ar-sharedhost0926` → `shingo-ar-host2026061901`

- CAI 検出リソース: **67** 件 / TF 出力リソース: **47** 件 / 一致: **16** 件 / 欠落候補: **51** 件

### `cloudbilling.googleapis.com/ProjectBillingInfo` （1 件）

#### `billingInfo` (location=`global`)

- full name: `//cloudbilling.googleapis.com/projects/shingo-ar-sharedhost0926/billingInfo`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `なし`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud asset describe '//cloudbilling.googleapis.com/projects/shingo-ar-sharedhost0926/billingInfo' --project=shingo-ar-sharedhost0926
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

#### `shingo-ar-sharedhost0926` (location=`global`)

- full name: `//cloudresourcemanager.googleapis.com/projects/shingo-ar-sharedhost0926`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `なし`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud asset describe '//cloudresourcemanager.googleapis.com/projects/shingo-ar-sharedhost0926' 
  # cloudresourcemanager.googleapis.com/Project は自動補完対象外。手動でドキュメント参照のうえ dst で再作成してください。
  ```

### `compute.googleapis.com/Address` （1 件）

#### `nat-auto-ip-10281266-0-1781794550182258` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedhost0926/regions/asia-northeast1/addresses/nat-auto-ip-10281266-0-1781794550182258`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses describe nat-auto-ip-10281266-0-1781794550182258 --region=asia-northeast1 --project=shingo-ar-sharedhost0926
  gcloud compute addresses create nat-auto-ip-10281266-0-1781794550182258 --project=shingo-ar-host2026061901 --region=asia-northeast1
  ```

### `compute.googleapis.com/FirewallPolicy` （2 件）

#### `test8000` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedhost0926/global/firewallPolicies/test8000`
- 担当ステップ: `network_firewall`
- 期待 TF 型: `google_compute_network_firewall_policy/google_compute_firewall_policy`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_network_firewall_policy/google_compute_firewall_policy)
- 推奨コマンド:
  ```bash
  gcloud compute network-firewall-policies describe test8000 --global --project=shingo-ar-sharedhost0926
  gcloud compute network-firewall-policies create test8000 --global --project=shingo-ar-host2026061901 --description=<DESC>
  ```

#### `ssh-from-all` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedhost0926/regions/asia-northeast1/firewallPolicies/ssh-from-all`
- 担当ステップ: `network_firewall`
- 期待 TF 型: `google_compute_network_firewall_policy/google_compute_firewall_policy`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_network_firewall_policy/google_compute_firewall_policy)
- 推奨コマンド:
  ```bash
  gcloud compute network-firewall-policies describe ssh-from-all --global --project=shingo-ar-sharedhost0926
  gcloud compute network-firewall-policies create ssh-from-all --global --project=shingo-ar-host2026061901 --description=<DESC>
  ```

### `compute.googleapis.com/InstanceSettings` （3 件）

#### `InstanceSettings` (location=`asia-northeast1-c`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedhost0926/zones/asia-northeast1-c/instanceSettings/InstanceSettings`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `なし`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud asset describe '//compute.googleapis.com/projects/shingo-ar-sharedhost0926/zones/asia-northeast1-c/instanceSettings/InstanceSettings' --project=shingo-ar-sharedhost0926
  # compute.googleapis.com/InstanceSettings は自動補完対象外。手動でドキュメント参照のうえ dst で再作成してください。
  ```

#### `InstanceSettings` (location=`asia-northeast1-a`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedhost0926/zones/asia-northeast1-a/instanceSettings/InstanceSettings`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `なし`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud asset describe '//compute.googleapis.com/projects/shingo-ar-sharedhost0926/zones/asia-northeast1-a/instanceSettings/InstanceSettings' --project=shingo-ar-sharedhost0926
  # compute.googleapis.com/InstanceSettings は自動補完対象外。手動でドキュメント参照のうえ dst で再作成してください。
  ```

#### `InstanceSettings` (location=`asia-northeast1-b`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedhost0926/zones/asia-northeast1-b/instanceSettings/InstanceSettings`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `なし`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud asset describe '//compute.googleapis.com/projects/shingo-ar-sharedhost0926/zones/asia-northeast1-b/instanceSettings/InstanceSettings' --project=shingo-ar-sharedhost0926
  # compute.googleapis.com/InstanceSettings は自動補完対象外。手動でドキュメント参照のうえ dst で再作成してください。
  ```

### `compute.googleapis.com/Project` （1 件）

#### `shingo-ar-sharedhost0926` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedhost0926`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `なし`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud asset describe '//compute.googleapis.com/projects/shingo-ar-sharedhost0926' 
  # compute.googleapis.com/Project は自動補完対象外。手動でドキュメント参照のうえ dst で再作成してください。
  ```

### `compute.googleapis.com/Route` （5 件）

#### `default-route-r-4461f276b01d2f9b` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedhost0926/global/routes/default-route-r-4461f276b01d2f9b`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-r-4461f276b01d2f9b --project=shingo-ar-sharedhost0926
  gcloud compute routes create default-route-r-4461f276b01d2f9b --project=shingo-ar-host2026061901 --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-r-5b0ce4d4d24c5d20` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedhost0926/global/routes/default-route-r-5b0ce4d4d24c5d20`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-r-5b0ce4d4d24c5d20 --project=shingo-ar-sharedhost0926
  gcloud compute routes create default-route-r-5b0ce4d4d24c5d20 --project=shingo-ar-host2026061901 --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-e7b27198104c4cc0` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedhost0926/global/routes/default-route-e7b27198104c4cc0`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-e7b27198104c4cc0 --project=shingo-ar-sharedhost0926
  gcloud compute routes create default-route-e7b27198104c4cc0 --project=shingo-ar-host2026061901 --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-2d5c5b7662d1a301` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedhost0926/global/routes/default-route-2d5c5b7662d1a301`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-2d5c5b7662d1a301 --project=shingo-ar-sharedhost0926
  gcloud compute routes create default-route-2d5c5b7662d1a301 --project=shingo-ar-host2026061901 --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-4a82a4f6a6983b3d` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedhost0926/global/routes/default-route-4a82a4f6a6983b3d`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-4a82a4f6a6983b3d --project=shingo-ar-sharedhost0926
  gcloud compute routes create default-route-4a82a4f6a6983b3d --project=shingo-ar-host2026061901 --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

### `iam.googleapis.com/Role` （1 件）

#### `migrationSrcReader` (location=`global`)

- full name: `//iam.googleapis.com/projects/shingo-ar-sharedhost0926/roles/migrationSrcReader`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_project_iam_custom_role`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_project_iam_custom_role)
- 推奨コマンド:
  ```bash
  gcloud iam roles describe migrationSrcReader --project=shingo-ar-sharedhost0926
  gcloud iam roles create migrationSrcReader --project=shingo-ar-host2026061901 --title=<TITLE> --permissions=<PERM1,PERM2,...> --stage=GA
  ```

### `iam.googleapis.com/ServiceAccount` （2 件）

#### `org-host-viewer@shingo-ar-sharedhost0926.iam.gserviceaccount.com` (location=`global`)

- full name: `//iam.googleapis.com/projects/shingo-ar-sharedhost0926/serviceAccounts/org-host-viewer@shingo-ar-sharedhost0926.iam.gserviceaccount.com`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_service_account`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_service_account)
- 推奨コマンド:
  ```bash
  gcloud iam service-accounts describe org-host-viewer@shingo-ar-sharedhost0926.iam.gserviceaccount.com --project=shingo-ar-sharedhost0926
  gcloud iam service-accounts create org-host-viewer --project=shingo-ar-host2026061901 --display-name=<DISPLAY_NAME>
  ```

#### `1035210593832-compute@developer.gserviceaccount.com` (location=`global`)

- full name: `//iam.googleapis.com/projects/shingo-ar-sharedhost0926/serviceAccounts/1035210593832-compute@developer.gserviceaccount.com`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_service_account`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_service_account)
- 推奨コマンド:
  ```bash
  gcloud iam service-accounts describe 1035210593832-compute@developer.gserviceaccount.com --project=shingo-ar-sharedhost0926
  gcloud iam service-accounts create 1035210593832-compute --project=shingo-ar-host2026061901 --display-name=<DISPLAY_NAME>
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
  gcloud logging buckets create _Default --location=global --project=shingo-ar-host2026061901 --retention-days=<N>
  ```

#### `_Required` (location=`global`)

- full name: `//logging.googleapis.com/projects/1035210593832/locations/global/buckets/_Required`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_logging_project_bucket_config`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_logging_project_bucket_config)
- 推奨コマンド:
  ```bash
  gcloud logging buckets describe _Required --location=global --project=1035210593832
  gcloud logging buckets create _Required --location=global --project=shingo-ar-host2026061901 --retention-days=<N>
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
  gcloud logging sinks create _Required <DESTINATION> --project=shingo-ar-host2026061901 --log-filter='<FILTER>'
  ```

#### `_Default` (location=`global`)

- full name: `//logging.googleapis.com/projects/1035210593832/sinks/_Default`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_logging_project_sink`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_logging_project_sink)
- 推奨コマンド:
  ```bash
  gcloud logging sinks describe _Default --project=1035210593832
  gcloud logging sinks create _Default <DESTINATION> --project=shingo-ar-host2026061901 --log-filter='<FILTER>'
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
  gcloud services enable cloudtrace.googleapis.com --project=shingo-ar-host2026061901
  ```

#### `artifactregistry.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1035210593832/services/artifactregistry.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1035210593832 --filter='config.name:artifactregistry.googleapis.com'
  gcloud services enable artifactregistry.googleapis.com --project=shingo-ar-host2026061901
  ```

#### `compute.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1035210593832/services/compute.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1035210593832 --filter='config.name:compute.googleapis.com'
  gcloud services enable compute.googleapis.com --project=shingo-ar-host2026061901
  ```

#### `cloudapis.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1035210593832/services/cloudapis.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1035210593832 --filter='config.name:cloudapis.googleapis.com'
  gcloud services enable cloudapis.googleapis.com --project=shingo-ar-host2026061901
  ```

#### `storage.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1035210593832/services/storage.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1035210593832 --filter='config.name:storage.googleapis.com'
  gcloud services enable storage.googleapis.com --project=shingo-ar-host2026061901
  ```

#### `oslogin.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1035210593832/services/oslogin.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1035210593832 --filter='config.name:oslogin.googleapis.com'
  gcloud services enable oslogin.googleapis.com --project=shingo-ar-host2026061901
  ```

#### `iamcredentials.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1035210593832/services/iamcredentials.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1035210593832 --filter='config.name:iamcredentials.googleapis.com'
  gcloud services enable iamcredentials.googleapis.com --project=shingo-ar-host2026061901
  ```

#### `logging.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1035210593832/services/logging.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1035210593832 --filter='config.name:logging.googleapis.com'
  gcloud services enable logging.googleapis.com --project=shingo-ar-host2026061901
  ```

#### `telemetry.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1035210593832/services/telemetry.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1035210593832 --filter='config.name:telemetry.googleapis.com'
  gcloud services enable telemetry.googleapis.com --project=shingo-ar-host2026061901
  ```

#### `cloudasset.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1035210593832/services/cloudasset.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1035210593832 --filter='config.name:cloudasset.googleapis.com'
  gcloud services enable cloudasset.googleapis.com --project=shingo-ar-host2026061901
  ```

#### `storage-component.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1035210593832/services/storage-component.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1035210593832 --filter='config.name:storage-component.googleapis.com'
  gcloud services enable storage-component.googleapis.com --project=shingo-ar-host2026061901
  ```

#### `iam.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1035210593832/services/iam.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1035210593832 --filter='config.name:iam.googleapis.com'
  gcloud services enable iam.googleapis.com --project=shingo-ar-host2026061901
  ```

#### `servicemanagement.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1035210593832/services/servicemanagement.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1035210593832 --filter='config.name:servicemanagement.googleapis.com'
  gcloud services enable servicemanagement.googleapis.com --project=shingo-ar-host2026061901
  ```

#### `serviceusage.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1035210593832/services/serviceusage.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1035210593832 --filter='config.name:serviceusage.googleapis.com'
  gcloud services enable serviceusage.googleapis.com --project=shingo-ar-host2026061901
  ```

#### `sql-component.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1035210593832/services/sql-component.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1035210593832 --filter='config.name:sql-component.googleapis.com'
  gcloud services enable sql-component.googleapis.com --project=shingo-ar-host2026061901
  ```

#### `bigquery.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1035210593832/services/bigquery.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1035210593832 --filter='config.name:bigquery.googleapis.com'
  gcloud services enable bigquery.googleapis.com --project=shingo-ar-host2026061901
  ```

#### `cloudbuild.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1035210593832/services/cloudbuild.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1035210593832 --filter='config.name:cloudbuild.googleapis.com'
  gcloud services enable cloudbuild.googleapis.com --project=shingo-ar-host2026061901
  ```

#### `bigquerymigration.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1035210593832/services/bigquerymigration.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1035210593832 --filter='config.name:bigquerymigration.googleapis.com'
  gcloud services enable bigquerymigration.googleapis.com --project=shingo-ar-host2026061901
  ```

#### `monitoring.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1035210593832/services/monitoring.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1035210593832 --filter='config.name:monitoring.googleapis.com'
  gcloud services enable monitoring.googleapis.com --project=shingo-ar-host2026061901
  ```

#### `containerregistry.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1035210593832/services/containerregistry.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1035210593832 --filter='config.name:containerregistry.googleapis.com'
  gcloud services enable containerregistry.googleapis.com --project=shingo-ar-host2026061901
  ```

#### `servicecontrol.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1035210593832/services/servicecontrol.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1035210593832 --filter='config.name:servicecontrol.googleapis.com'
  gcloud services enable servicecontrol.googleapis.com --project=shingo-ar-host2026061901
  ```

#### `datastore.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1035210593832/services/datastore.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1035210593832 --filter='config.name:datastore.googleapis.com'
  gcloud services enable datastore.googleapis.com --project=shingo-ar-host2026061901
  ```

#### `cloudresourcemanager.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1035210593832/services/cloudresourcemanager.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1035210593832 --filter='config.name:cloudresourcemanager.googleapis.com'
  gcloud services enable cloudresourcemanager.googleapis.com --project=shingo-ar-host2026061901
  ```

#### `bigquerystorage.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1035210593832/services/bigquerystorage.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1035210593832 --filter='config.name:bigquerystorage.googleapis.com'
  gcloud services enable bigquerystorage.googleapis.com --project=shingo-ar-host2026061901
  ```

#### `pubsub.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1035210593832/services/pubsub.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1035210593832 --filter='config.name:pubsub.googleapis.com'
  gcloud services enable pubsub.googleapis.com --project=shingo-ar-host2026061901
  ```

#### `storage-api.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1035210593832/services/storage-api.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1035210593832 --filter='config.name:storage-api.googleapis.com'
  gcloud services enable storage-api.googleapis.com --project=shingo-ar-host2026061901
  ```

#### `vmmigration.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1035210593832/services/vmmigration.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1035210593832 --filter='config.name:vmmigration.googleapis.com'
  gcloud services enable vmmigration.googleapis.com --project=shingo-ar-host2026061901
  ```

### `storage.googleapis.com/Bucket` （2 件）

#### `shingo-ar-sharedhost0926` (location=`us-central1`)

- full name: `//storage.googleapis.com/shingo-ar-sharedhost0926`
- 担当ステップ: `data_sync`
- 期待 TF 型: `google_storage_bucket`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_storage_bucket)
- 推奨コマンド:
  ```bash
  gcloud storage buckets describe gs://shingo-ar-sharedhost0926
  gcloud storage buckets create gs://<DST_BUCKET_NAME> --project=shingo-ar-host2026061901 --location=us-central1  # 名前は rename_rules.gcs を適用すること
  ```

#### `gcs-test-shingo-ar-sharedhost0926` (location=`asia`)

- full name: `//storage.googleapis.com/gcs-test-shingo-ar-sharedhost0926`
- 担当ステップ: `data_sync`
- 期待 TF 型: `google_storage_bucket`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_storage_bucket)
- 推奨コマンド:
  ```bash
  gcloud storage buckets describe gs://gcs-test-shingo-ar-sharedhost0926
  gcloud storage buckets create gs://<DST_BUCKET_NAME> --project=shingo-ar-host2026061901 --location=asia  # 名前は rename_rules.gcs を適用すること
  ```

## プロジェクト: `shingo-ar-sharedservice0926-1` → `shingo-ar-service2026061901-1`

- CAI 検出リソース: **112** 件 / TF 出力リソース: **24** 件 / 一致: **1** 件 / 欠落候補: **111** 件

### `cloudbilling.googleapis.com/ProjectBillingInfo` （1 件）

#### `billingInfo` (location=`global`)

- full name: `//cloudbilling.googleapis.com/projects/shingo-ar-sharedservice0926-1/billingInfo`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `なし`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud asset describe '//cloudbilling.googleapis.com/projects/shingo-ar-sharedservice0926-1/billingInfo' --project=shingo-ar-sharedservice0926-1
  # cloudbilling.googleapis.com/ProjectBillingInfo は自動補完対象外。手動でドキュメント参照のうえ dst で再作成してください。
  ```

### `cloudresourcemanager.googleapis.com/Project` （1 件）

#### `shingo-ar-sharedservice0926-1` (location=`global`)

- full name: `//cloudresourcemanager.googleapis.com/projects/shingo-ar-sharedservice0926-1`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `なし`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud asset describe '//cloudresourcemanager.googleapis.com/projects/shingo-ar-sharedservice0926-1' 
  # cloudresourcemanager.googleapis.com/Project は自動補完対象外。手動でドキュメント参照のうえ dst で再作成してください。
  ```

### `compute.googleapis.com/Address` （7 件）

#### `org-svc1-deb-e2-mic-101-ip` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/regions/asia-northeast1/addresses/org-svc1-deb-e2-mic-101-ip`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses describe org-svc1-deb-e2-mic-101-ip --region=asia-northeast1 --project=shingo-ar-sharedservice0926-1
  gcloud compute addresses create org-svc1-deb-e2-mic-101-ip --project=shingo-ar-service2026061901-1 --region=asia-northeast1
  ```

#### `sharedvpcip` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/regions/asia-northeast1/addresses/sharedvpcip`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses describe sharedvpcip --region=asia-northeast1 --project=shingo-ar-sharedservice0926-1
  gcloud compute addresses create sharedvpcip --project=shingo-ar-service2026061901-1 --region=asia-northeast1
  ```

#### `org-svc1-deb-n2-std2-02-ip` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/regions/asia-northeast1/addresses/org-svc1-deb-n2-std2-02-ip`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses describe org-svc1-deb-n2-std2-02-ip --region=asia-northeast1 --project=shingo-ar-sharedservice0926-1
  gcloud compute addresses create org-svc1-deb-n2-std2-02-ip --project=shingo-ar-service2026061901-1 --region=asia-northeast1
  ```

#### `org-svc1-deb-n2-std2-01-ip` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/regions/asia-northeast1/addresses/org-svc1-deb-n2-std2-01-ip`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses describe org-svc1-deb-n2-std2-01-ip --region=asia-northeast1 --project=shingo-ar-sharedservice0926-1
  gcloud compute addresses create org-svc1-deb-n2-std2-01-ip --project=shingo-ar-service2026061901-1 --region=asia-northeast1
  ```

#### `org-svc1-deb-e2-mic-01-ip` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/regions/asia-northeast1/addresses/org-svc1-deb-e2-mic-01-ip`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses describe org-svc1-deb-e2-mic-01-ip --region=asia-northeast1 --project=shingo-ar-sharedservice0926-1
  gcloud compute addresses create org-svc1-deb-e2-mic-01-ip --project=shingo-ar-service2026061901-1 --region=asia-northeast1
  ```

#### `org-svc1-deb-e2-mic-02-ip` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/regions/asia-northeast1/addresses/org-svc1-deb-e2-mic-02-ip`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses describe org-svc1-deb-e2-mic-02-ip --region=asia-northeast1 --project=shingo-ar-sharedservice0926-1
  gcloud compute addresses create org-svc1-deb-e2-mic-02-ip --project=shingo-ar-service2026061901-1 --region=asia-northeast1
  ```

#### `org-svc1-deb-e2-mic-03-ip` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/regions/asia-northeast1/addresses/org-svc1-deb-e2-mic-03-ip`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses describe org-svc1-deb-e2-mic-03-ip --region=asia-northeast1 --project=shingo-ar-sharedservice0926-1
  gcloud compute addresses create org-svc1-deb-e2-mic-03-ip --project=shingo-ar-service2026061901-1 --region=asia-northeast1
  ```

### `compute.googleapis.com/Disk` （9 件）

#### `org-svc1-deb-e2-mic-101` (location=`asia-northeast1-a`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/zones/asia-northeast1-a/disks/org-svc1-deb-e2-mic-101`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_disk/google_compute_region_disk`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_disk/google_compute_region_disk)
- 推奨コマンド:
  ```bash
  gcloud compute disks describe org-svc1-deb-e2-mic-101 --zone=asia-northeast1-a --project=shingo-ar-sharedservice0926-1
  gcloud compute disks create org-svc1-deb-e2-mic-101 --project=shingo-ar-service2026061901-1 --zone=asia-northeast1-a --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore)
  ```

#### `fix-ip-vm` (location=`asia-northeast1-b`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/zones/asia-northeast1-b/disks/fix-ip-vm`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_disk/google_compute_region_disk`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_disk/google_compute_region_disk)
- 推奨コマンド:
  ```bash
  gcloud compute disks describe fix-ip-vm --zone=asia-northeast1-b --project=shingo-ar-sharedservice0926-1
  gcloud compute disks create fix-ip-vm --project=shingo-ar-service2026061901-1 --zone=asia-northeast1-b --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore)
  ```

#### `centos8-from-vmv` (location=`asia-northeast1-a`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/zones/asia-northeast1-a/disks/centos8-from-vmv`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_disk/google_compute_region_disk`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_disk/google_compute_region_disk)
- 推奨コマンド:
  ```bash
  gcloud compute disks describe centos8-from-vmv --zone=asia-northeast1-a --project=shingo-ar-sharedservice0926-1
  gcloud compute disks create centos8-from-vmv --project=shingo-ar-service2026061901-1 --zone=asia-northeast1-a --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore)
  ```

#### `windows` (location=`asia-northeast1-c`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/zones/asia-northeast1-c/disks/windows`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_disk/google_compute_region_disk`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_disk/google_compute_region_disk)
- 推奨コマンド:
  ```bash
  gcloud compute disks describe windows --zone=asia-northeast1-c --project=shingo-ar-sharedservice0926-1
  gcloud compute disks create windows --project=shingo-ar-service2026061901-1 --zone=asia-northeast1-c --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore)
  ```

#### `org-svc1-deb-e2-mic-03` (location=`asia-northeast1-a`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/zones/asia-northeast1-a/disks/org-svc1-deb-e2-mic-03`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_disk/google_compute_region_disk`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_disk/google_compute_region_disk)
- 推奨コマンド:
  ```bash
  gcloud compute disks describe org-svc1-deb-e2-mic-03 --zone=asia-northeast1-a --project=shingo-ar-sharedservice0926-1
  gcloud compute disks create org-svc1-deb-e2-mic-03 --project=shingo-ar-service2026061901-1 --zone=asia-northeast1-a --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore)
  ```

#### `org-svc1-deb-n2-std2-01` (location=`asia-northeast1-a`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/zones/asia-northeast1-a/disks/org-svc1-deb-n2-std2-01`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_disk/google_compute_region_disk`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_disk/google_compute_region_disk)
- 推奨コマンド:
  ```bash
  gcloud compute disks describe org-svc1-deb-n2-std2-01 --zone=asia-northeast1-a --project=shingo-ar-sharedservice0926-1
  gcloud compute disks create org-svc1-deb-n2-std2-01 --project=shingo-ar-service2026061901-1 --zone=asia-northeast1-a --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore)
  ```

#### `org-svc1-deb-e2-mic-02` (location=`asia-northeast1-a`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/zones/asia-northeast1-a/disks/org-svc1-deb-e2-mic-02`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_disk/google_compute_region_disk`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_disk/google_compute_region_disk)
- 推奨コマンド:
  ```bash
  gcloud compute disks describe org-svc1-deb-e2-mic-02 --zone=asia-northeast1-a --project=shingo-ar-sharedservice0926-1
  gcloud compute disks create org-svc1-deb-e2-mic-02 --project=shingo-ar-service2026061901-1 --zone=asia-northeast1-a --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore)
  ```

#### `org-svc1-deb-e2-mic-01` (location=`asia-northeast1-a`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/zones/asia-northeast1-a/disks/org-svc1-deb-e2-mic-01`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_disk/google_compute_region_disk`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_disk/google_compute_region_disk)
- 推奨コマンド:
  ```bash
  gcloud compute disks describe org-svc1-deb-e2-mic-01 --zone=asia-northeast1-a --project=shingo-ar-sharedservice0926-1
  gcloud compute disks create org-svc1-deb-e2-mic-01 --project=shingo-ar-service2026061901-1 --zone=asia-northeast1-a --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore)
  ```

#### `instance-1` (location=`asia-northeast1-b`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/zones/asia-northeast1-b/disks/instance-1`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_disk/google_compute_region_disk`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_disk/google_compute_region_disk)
- 推奨コマンド:
  ```bash
  gcloud compute disks describe instance-1 --zone=asia-northeast1-b --project=shingo-ar-sharedservice0926-1
  gcloud compute disks create instance-1 --project=shingo-ar-service2026061901-1 --zone=asia-northeast1-b --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore)
  ```

### `compute.googleapis.com/Image` （8 件）

#### `vmdk-imported-20260608-centos8t-boot` (location=`asia`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/global/images/vmdk-imported-20260608-centos8t-boot`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_image`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute images describe vmdk-imported-20260608-centos8t-boot --project=shingo-ar-sharedservice0926-1
  # image は使用しない方針（snapshot 由来）。必要なら gcloud compute images create vmdk-imported-20260608-centos8t-boot --project=shingo-ar-service2026061901-1 --source-snapshot=<SNAPSHOT>
  ```

#### `vmdk-imported-20260608-centos8v-boot` (location=`asia`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/global/images/vmdk-imported-20260608-centos8v-boot`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_image`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute images describe vmdk-imported-20260608-centos8v-boot --project=shingo-ar-sharedservice0926-1
  # image は使用しない方針（snapshot 由来）。必要なら gcloud compute images create vmdk-imported-20260608-centos8v-boot --project=shingo-ar-service2026061901-1 --source-snapshot=<SNAPSHOT>
  ```

#### `vmdk-imported-20260608-boot` (location=`asia`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/global/images/vmdk-imported-20260608-boot`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_image`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute images describe vmdk-imported-20260608-boot --project=shingo-ar-sharedservice0926-1
  # image は使用しない方針（snapshot 由来）。必要なら gcloud compute images create vmdk-imported-20260608-boot --project=shingo-ar-service2026061901-1 --source-snapshot=<SNAPSHOT>
  ```

#### `img-org-svc1-deb-n2-std4-02` (location=`asia`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/global/images/img-org-svc1-deb-n2-std4-02`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_image`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute images describe img-org-svc1-deb-n2-std4-02 --project=shingo-ar-sharedservice0926-1
  # image は使用しない方針（snapshot 由来）。必要なら gcloud compute images create img-org-svc1-deb-n2-std4-02 --project=shingo-ar-service2026061901-1 --source-snapshot=<SNAPSHOT>
  ```

#### `img-org-svc1-deb-n2-std4-01` (location=`asia`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/global/images/img-org-svc1-deb-n2-std4-01`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_image`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute images describe img-org-svc1-deb-n2-std4-01 --project=shingo-ar-sharedservice0926-1
  # image は使用しない方針（snapshot 由来）。必要なら gcloud compute images create img-org-svc1-deb-n2-std4-01 --project=shingo-ar-service2026061901-1 --source-snapshot=<SNAPSHOT>
  ```

#### `img-org-svc1-deb-e2-std4-03` (location=`asia`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/global/images/img-org-svc1-deb-e2-std4-03`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_image`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute images describe img-org-svc1-deb-e2-std4-03 --project=shingo-ar-sharedservice0926-1
  # image は使用しない方針（snapshot 由来）。必要なら gcloud compute images create img-org-svc1-deb-e2-std4-03 --project=shingo-ar-service2026061901-1 --source-snapshot=<SNAPSHOT>
  ```

#### `img-org-svc1-deb-e2-std4-02` (location=`asia`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/global/images/img-org-svc1-deb-e2-std4-02`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_image`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute images describe img-org-svc1-deb-e2-std4-02 --project=shingo-ar-sharedservice0926-1
  # image は使用しない方針（snapshot 由来）。必要なら gcloud compute images create img-org-svc1-deb-e2-std4-02 --project=shingo-ar-service2026061901-1 --source-snapshot=<SNAPSHOT>
  ```

#### `img-org-svc1-deb-e2-std4-01` (location=`asia`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/global/images/img-org-svc1-deb-e2-std4-01`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_image`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute images describe img-org-svc1-deb-e2-std4-01 --project=shingo-ar-sharedservice0926-1
  # image は使用しない方針（snapshot 由来）。必要なら gcloud compute images create img-org-svc1-deb-e2-std4-01 --project=shingo-ar-service2026061901-1 --source-snapshot=<SNAPSHOT>
  ```

### `compute.googleapis.com/Instance` （9 件）

#### `org-svc1-deb-e2-mic-101` (location=`asia-northeast1-a`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/zones/asia-northeast1-a/instances/org-svc1-deb-e2-mic-101`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_instance`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_instance)
- 推奨コマンド:
  ```bash
  gcloud compute instances describe org-svc1-deb-e2-mic-101 --zone=asia-northeast1-a --project=shingo-ar-sharedservice0926-1
  gcloud compute instances create org-svc1-deb-e2-mic-101 --project=shingo-ar-service2026061901-1 --zone=asia-northeast1-a --machine-type=<MACHINE_TYPE> --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore) が担当
  ```

#### `fix-ip-vm` (location=`asia-northeast1-b`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/zones/asia-northeast1-b/instances/fix-ip-vm`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_instance`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_instance)
- 推奨コマンド:
  ```bash
  gcloud compute instances describe fix-ip-vm --zone=asia-northeast1-b --project=shingo-ar-sharedservice0926-1
  gcloud compute instances create fix-ip-vm --project=shingo-ar-service2026061901-1 --zone=asia-northeast1-b --machine-type=<MACHINE_TYPE> --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore) が担当
  ```

#### `centos8-from-vmv` (location=`asia-northeast1-a`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/zones/asia-northeast1-a/instances/centos8-from-vmv`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_instance`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_instance)
- 推奨コマンド:
  ```bash
  gcloud compute instances describe centos8-from-vmv --zone=asia-northeast1-a --project=shingo-ar-sharedservice0926-1
  gcloud compute instances create centos8-from-vmv --project=shingo-ar-service2026061901-1 --zone=asia-northeast1-a --machine-type=<MACHINE_TYPE> --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore) が担当
  ```

#### `windows` (location=`asia-northeast1-c`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/zones/asia-northeast1-c/instances/windows`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_instance`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_instance)
- 推奨コマンド:
  ```bash
  gcloud compute instances describe windows --zone=asia-northeast1-c --project=shingo-ar-sharedservice0926-1
  gcloud compute instances create windows --project=shingo-ar-service2026061901-1 --zone=asia-northeast1-c --machine-type=<MACHINE_TYPE> --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore) が担当
  ```

#### `org-svc1-deb-e2-mic-02` (location=`asia-northeast1-a`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/zones/asia-northeast1-a/instances/org-svc1-deb-e2-mic-02`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_instance`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_instance)
- 推奨コマンド:
  ```bash
  gcloud compute instances describe org-svc1-deb-e2-mic-02 --zone=asia-northeast1-a --project=shingo-ar-sharedservice0926-1
  gcloud compute instances create org-svc1-deb-e2-mic-02 --project=shingo-ar-service2026061901-1 --zone=asia-northeast1-a --machine-type=<MACHINE_TYPE> --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore) が担当
  ```

#### `org-svc1-deb-n2-std2-01` (location=`asia-northeast1-a`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/zones/asia-northeast1-a/instances/org-svc1-deb-n2-std2-01`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_instance`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_instance)
- 推奨コマンド:
  ```bash
  gcloud compute instances describe org-svc1-deb-n2-std2-01 --zone=asia-northeast1-a --project=shingo-ar-sharedservice0926-1
  gcloud compute instances create org-svc1-deb-n2-std2-01 --project=shingo-ar-service2026061901-1 --zone=asia-northeast1-a --machine-type=<MACHINE_TYPE> --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore) が担当
  ```

#### `org-svc1-deb-e2-mic-03` (location=`asia-northeast1-a`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/zones/asia-northeast1-a/instances/org-svc1-deb-e2-mic-03`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_instance`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_instance)
- 推奨コマンド:
  ```bash
  gcloud compute instances describe org-svc1-deb-e2-mic-03 --zone=asia-northeast1-a --project=shingo-ar-sharedservice0926-1
  gcloud compute instances create org-svc1-deb-e2-mic-03 --project=shingo-ar-service2026061901-1 --zone=asia-northeast1-a --machine-type=<MACHINE_TYPE> --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore) が担当
  ```

#### `org-svc1-deb-e2-mic-01` (location=`asia-northeast1-a`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/zones/asia-northeast1-a/instances/org-svc1-deb-e2-mic-01`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_instance`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_instance)
- 推奨コマンド:
  ```bash
  gcloud compute instances describe org-svc1-deb-e2-mic-01 --zone=asia-northeast1-a --project=shingo-ar-sharedservice0926-1
  gcloud compute instances create org-svc1-deb-e2-mic-01 --project=shingo-ar-service2026061901-1 --zone=asia-northeast1-a --machine-type=<MACHINE_TYPE> --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore) が担当
  ```

#### `instance-1` (location=`asia-northeast1-b`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/zones/asia-northeast1-b/instances/instance-1`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_instance`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_instance)
- 推奨コマンド:
  ```bash
  gcloud compute instances describe instance-1 --zone=asia-northeast1-b --project=shingo-ar-sharedservice0926-1
  gcloud compute instances create instance-1 --project=shingo-ar-service2026061901-1 --zone=asia-northeast1-b --machine-type=<MACHINE_TYPE> --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore) が担当
  ```

### `compute.googleapis.com/InstanceSettings` （3 件）

#### `InstanceSettings` (location=`asia-northeast1-a`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/zones/asia-northeast1-a/instanceSettings/InstanceSettings`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `なし`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud asset describe '//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/zones/asia-northeast1-a/instanceSettings/InstanceSettings' --project=shingo-ar-sharedservice0926-1
  # compute.googleapis.com/InstanceSettings は自動補完対象外。手動でドキュメント参照のうえ dst で再作成してください。
  ```

#### `InstanceSettings` (location=`asia-northeast1-c`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/zones/asia-northeast1-c/instanceSettings/InstanceSettings`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `なし`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud asset describe '//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/zones/asia-northeast1-c/instanceSettings/InstanceSettings' --project=shingo-ar-sharedservice0926-1
  # compute.googleapis.com/InstanceSettings は自動補完対象外。手動でドキュメント参照のうえ dst で再作成してください。
  ```

#### `InstanceSettings` (location=`asia-northeast1-b`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/zones/asia-northeast1-b/instanceSettings/InstanceSettings`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `なし`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud asset describe '//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/zones/asia-northeast1-b/instanceSettings/InstanceSettings' --project=shingo-ar-sharedservice0926-1
  # compute.googleapis.com/InstanceSettings は自動補完対象外。手動でドキュメント参照のうえ dst で再作成してください。
  ```

### `compute.googleapis.com/Project` （1 件）

#### `shingo-ar-sharedservice0926-1` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `なし`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud asset describe '//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1' 
  # compute.googleapis.com/Project は自動補完対象外。手動でドキュメント参照のうえ dst で再作成してください。
  ```

### `compute.googleapis.com/Snapshot` （36 件）

#### `org-svc1-deb-e2-mic-101-init-snap` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/global/snapshots/org-svc1-deb-e2-mic-101-init-snap`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe org-svc1-deb-e2-mic-101-init-snap --project=shingo-ar-sharedservice0926-1
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `windows-asia-northeast1-c-20260618184701-7zerv6lq` (location=`asia`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/global/snapshots/windows-asia-northeast1-c-20260618184701-7zerv6lq`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe windows-asia-northeast1-c-20260618184701-7zerv6lq --project=shingo-ar-sharedservice0926-1
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `fix-ip-vm-asia-northeast1-b-20260618184701-fppz5xml` (location=`asia`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/global/snapshots/fix-ip-vm-asia-northeast1-b-20260618184701-fppz5xml`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe fix-ip-vm-asia-northeast1-b-20260618184701-fppz5xml --project=shingo-ar-sharedservice0926-1
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `backup-for-fix-ip-vm` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/global/snapshots/backup-for-fix-ip-vm`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe backup-for-fix-ip-vm --project=shingo-ar-sharedservice0926-1
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `windows-asia-northeast1-c-20260617184701-00v6d4t3` (location=`asia`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/global/snapshots/windows-asia-northeast1-c-20260617184701-00v6d4t3`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe windows-asia-northeast1-c-20260617184701-00v6d4t3 --project=shingo-ar-sharedservice0926-1
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `windows-asia-northeast1-c-20260616184701-hm0tx33z` (location=`asia`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/global/snapshots/windows-asia-northeast1-c-20260616184701-hm0tx33z`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe windows-asia-northeast1-c-20260616184701-hm0tx33z --project=shingo-ar-sharedservice0926-1
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `windows-asia-northeast1-c-20260615184701-v1ll0ank` (location=`asia`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/global/snapshots/windows-asia-northeast1-c-20260615184701-v1ll0ank`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe windows-asia-northeast1-c-20260615184701-v1ll0ank --project=shingo-ar-sharedservice0926-1
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `windows-asia-northeast1-c-20260614184701-566hr1iw` (location=`asia`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/global/snapshots/windows-asia-northeast1-c-20260614184701-566hr1iw`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe windows-asia-northeast1-c-20260614184701-566hr1iw --project=shingo-ar-sharedservice0926-1
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `windows-asia-northeast1-c-20260613184701-lgbhbc1p` (location=`asia`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/global/snapshots/windows-asia-northeast1-c-20260613184701-lgbhbc1p`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe windows-asia-northeast1-c-20260613184701-lgbhbc1p --project=shingo-ar-sharedservice0926-1
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `windows-asia-northeast1-c-20260612184701-v6bf8n0w` (location=`asia`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/global/snapshots/windows-asia-northeast1-c-20260612184701-v6bf8n0w`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe windows-asia-northeast1-c-20260612184701-v6bf8n0w --project=shingo-ar-sharedservice0926-1
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `windows-asia-northeast1-c-20260611184701-ml1eca73` (location=`asia`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/global/snapshots/windows-asia-northeast1-c-20260611184701-ml1eca73`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe windows-asia-northeast1-c-20260611184701-ml1eca73 --project=shingo-ar-sharedservice0926-1
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `windows-asia-northeast1-c-20260610184701-pgx90urt` (location=`asia`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/global/snapshots/windows-asia-northeast1-c-20260610184701-pgx90urt`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe windows-asia-northeast1-c-20260610184701-pgx90urt --project=shingo-ar-sharedservice0926-1
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `windows-asia-northeast1-c-20260609184701-4o7yirke` (location=`asia`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/global/snapshots/windows-asia-northeast1-c-20260609184701-4o7yirke`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe windows-asia-northeast1-c-20260609184701-4o7yirke --project=shingo-ar-sharedservice0926-1
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `windows-asia-northeast1-c-20260608184701-m36l3p3a` (location=`asia`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/global/snapshots/windows-asia-northeast1-c-20260608184701-m36l3p3a`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe windows-asia-northeast1-c-20260608184701-m36l3p3a --project=shingo-ar-sharedservice0926-1
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `centos8-snapshot` (location=`asia`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/global/snapshots/centos8-snapshot`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe centos8-snapshot --project=shingo-ar-sharedservice0926-1
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `windows-asia-northeast1-c-20260607184701-4itlpj1e` (location=`asia`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/global/snapshots/windows-asia-northeast1-c-20260607184701-4itlpj1e`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe windows-asia-northeast1-c-20260607184701-4itlpj1e --project=shingo-ar-sharedservice0926-1
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `windows-asia-northeast1-c-20260606184701-588u0esr` (location=`asia`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/global/snapshots/windows-asia-northeast1-c-20260606184701-588u0esr`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe windows-asia-northeast1-c-20260606184701-588u0esr --project=shingo-ar-sharedservice0926-1
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `windows-asia-northeast1-c-20260605184701-hbjv5u1f` (location=`asia`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/global/snapshots/windows-asia-northeast1-c-20260605184701-hbjv5u1f`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe windows-asia-northeast1-c-20260605184701-hbjv5u1f --project=shingo-ar-sharedservice0926-1
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `snapshot-3` (location=`asia`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/global/snapshots/snapshot-3`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe snapshot-3 --project=shingo-ar-sharedservice0926-1
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `org-svc1-deb-n2-std2-02` (location=`asia-east1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/global/snapshots/org-svc1-deb-n2-std2-02`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe org-svc1-deb-n2-std2-02 --project=shingo-ar-sharedservice0926-1
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `snapshotorg-svc1-deb-n2-std2-02` (location=`asia`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/global/snapshots/snapshotorg-svc1-deb-n2-std2-02`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe snapshotorg-svc1-deb-n2-std2-02 --project=shingo-ar-sharedservice0926-1
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `snapshot-org-svc1-deb-n2-std2-01` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/global/snapshots/snapshot-org-svc1-deb-n2-std2-01`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe snapshot-org-svc1-deb-n2-std2-01 --project=shingo-ar-sharedservice0926-1
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `snapshot-for-org-svc1-deb-e2-mic-03` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/global/snapshots/snapshot-for-org-svc1-deb-e2-mic-03`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe snapshot-for-org-svc1-deb-e2-mic-03 --project=shingo-ar-sharedservice0926-1
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `snapshot-org-svc1-deb-e2-mic-02` (location=`asia`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/global/snapshots/snapshot-org-svc1-deb-e2-mic-02`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe snapshot-org-svc1-deb-e2-mic-02 --project=shingo-ar-sharedservice0926-1
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `snapshot-2` (location=`asia`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/global/snapshots/snapshot-2`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe snapshot-2 --project=shingo-ar-sharedservice0926-1
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `snapshot-1` (location=`asia`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/global/snapshots/snapshot-1`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe snapshot-1 --project=shingo-ar-sharedservice0926-1
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `instance-20260528-0-asia-northeast1-c-20260601184701-le4cnjms` (location=`asia`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/global/snapshots/instance-20260528-0-asia-northeast1-c-20260601184701-le4cnjms`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe instance-20260528-0-asia-northeast1-c-20260601184701-le4cnjms --project=shingo-ar-sharedservice0926-1
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `instance-20260528-0-asia-northeast1-c-20260531184701-dx2f6fdv` (location=`asia`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/global/snapshots/instance-20260528-0-asia-northeast1-c-20260531184701-dx2f6fdv`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe instance-20260528-0-asia-northeast1-c-20260531184701-dx2f6fdv --project=shingo-ar-sharedservice0926-1
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `instance-20260528-0-asia-northeast1-c-20260530184701-8uxmmxmw` (location=`asia`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/global/snapshots/instance-20260528-0-asia-northeast1-c-20260530184701-8uxmmxmw`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe instance-20260528-0-asia-northeast1-c-20260530184701-8uxmmxmw --project=shingo-ar-sharedservice0926-1
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `instance-20260528-0-asia-northeast1-c-20260529184701-8m3p4np0` (location=`asia`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/global/snapshots/instance-20260528-0-asia-northeast1-c-20260529184701-8m3p4np0`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe instance-20260528-0-asia-northeast1-c-20260529184701-8m3p4np0 --project=shingo-ar-sharedservice0926-1
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `instance-20260528-0-asia-northeast1-c-20260528184701-72pgudlw` (location=`asia`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/global/snapshots/instance-20260528-0-asia-northeast1-c-20260528184701-72pgudlw`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe instance-20260528-0-asia-northeast1-c-20260528184701-72pgudlw --project=shingo-ar-sharedservice0926-1
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `org-svc1-deb-e2-std4-01` (location=`asia`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/global/snapshots/org-svc1-deb-e2-std4-01`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe org-svc1-deb-e2-std4-01 --project=shingo-ar-sharedservice0926-1
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `org-svc1-deb-n2-std4-01` (location=`asia`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/global/snapshots/org-svc1-deb-n2-std4-01`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe org-svc1-deb-n2-std4-01 --project=shingo-ar-sharedservice0926-1
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `org-svc1-deb-n2-std4-02` (location=`asia`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/global/snapshots/org-svc1-deb-n2-std4-02`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe org-svc1-deb-n2-std4-02 --project=shingo-ar-sharedservice0926-1
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `org-svc1-deb-e2-std4-03` (location=`asia`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/global/snapshots/org-svc1-deb-e2-std4-03`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe org-svc1-deb-e2-std4-03 --project=shingo-ar-sharedservice0926-1
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `org-svc1-deb-e2-std4-02` (location=`asia`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/global/snapshots/org-svc1-deb-e2-std4-02`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe org-svc1-deb-e2-std4-02 --project=shingo-ar-sharedservice0926-1
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

### `iam.googleapis.com/Role` （1 件）

#### `migrationSrcReader` (location=`global`)

- full name: `//iam.googleapis.com/projects/shingo-ar-sharedservice0926-1/roles/migrationSrcReader`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_project_iam_custom_role`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_project_iam_custom_role)
- 推奨コマンド:
  ```bash
  gcloud iam roles describe migrationSrcReader --project=shingo-ar-sharedservice0926-1
  gcloud iam roles create migrationSrcReader --project=shingo-ar-service2026061901-1 --title=<TITLE> --permissions=<PERM1,PERM2,...> --stage=GA
  ```

### `iam.googleapis.com/ServiceAccount` （2 件）

#### `org-svc1-viewer@shingo-ar-sharedservice0926-1.iam.gserviceaccount.com` (location=`global`)

- full name: `//iam.googleapis.com/projects/shingo-ar-sharedservice0926-1/serviceAccounts/org-svc1-viewer@shingo-ar-sharedservice0926-1.iam.gserviceaccount.com`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_service_account`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_service_account)
- 推奨コマンド:
  ```bash
  gcloud iam service-accounts describe org-svc1-viewer@shingo-ar-sharedservice0926-1.iam.gserviceaccount.com --project=shingo-ar-sharedservice0926-1
  gcloud iam service-accounts create org-svc1-viewer --project=shingo-ar-service2026061901-1 --display-name=<DISPLAY_NAME>
  ```

#### `1007606807581-compute@developer.gserviceaccount.com` (location=`global`)

- full name: `//iam.googleapis.com/projects/shingo-ar-sharedservice0926-1/serviceAccounts/1007606807581-compute@developer.gserviceaccount.com`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_service_account`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_service_account)
- 推奨コマンド:
  ```bash
  gcloud iam service-accounts describe 1007606807581-compute@developer.gserviceaccount.com --project=shingo-ar-sharedservice0926-1
  gcloud iam service-accounts create 1007606807581-compute --project=shingo-ar-service2026061901-1 --display-name=<DISPLAY_NAME>
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
  gcloud logging buckets create _Default --location=global --project=shingo-ar-service2026061901-1 --retention-days=<N>
  ```

#### `_Required` (location=`global`)

- full name: `//logging.googleapis.com/projects/1007606807581/locations/global/buckets/_Required`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_logging_project_bucket_config`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_logging_project_bucket_config)
- 推奨コマンド:
  ```bash
  gcloud logging buckets describe _Required --location=global --project=1007606807581
  gcloud logging buckets create _Required --location=global --project=shingo-ar-service2026061901-1 --retention-days=<N>
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
  gcloud logging sinks create _Required <DESTINATION> --project=shingo-ar-service2026061901-1 --log-filter='<FILTER>'
  ```

#### `_Default` (location=`global`)

- full name: `//logging.googleapis.com/projects/1007606807581/sinks/_Default`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_logging_project_sink`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_logging_project_sink)
- 推奨コマンド:
  ```bash
  gcloud logging sinks describe _Default --project=1007606807581
  gcloud logging sinks create _Default <DESTINATION> --project=shingo-ar-service2026061901-1 --log-filter='<FILTER>'
  ```

### `osconfig.googleapis.com/OSPolicyAssignment` （2 件）

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

#### `goog-ops-agent-v2-template-1-7-0-asia-northeast1-b` (location=`asia-northeast1-b`)

- full name: `//osconfig.googleapis.com/projects/1007606807581/locations/asia-northeast1-b/osPolicyAssignments/goog-ops-agent-v2-template-1-7-0-asia-northeast1-b`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `なし`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud asset describe '//osconfig.googleapis.com/projects/1007606807581/locations/asia-northeast1-b/osPolicyAssignments/goog-ops-agent-v2-template-1-7-0-asia-northeast1-b' --project=1007606807581
  # osconfig.googleapis.com/OSPolicyAssignment は自動補完対象外。手動でドキュメント参照のうえ dst で再作成してください。
  ```

### `osconfig.googleapis.com/OSPolicyAssignmentReport` （2 件）

#### `report` (location=`asia-northeast1-b`)

- full name: `//osconfig.googleapis.com/projects/1007606807581/locations/asia-northeast1-b/instances/3955167452418652164/osPolicyAssignments/goog-ops-agent-v2-template-1-7-0-asia-northeast1-b/report`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `なし`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud asset describe '//osconfig.googleapis.com/projects/1007606807581/locations/asia-northeast1-b/instances/3955167452418652164/osPolicyAssignments/goog-ops-agent-v2-template-1-7-0-asia-northeast1-b/report' --project=1007606807581
  # osconfig.googleapis.com/OSPolicyAssignmentReport は自動補完対象外。手動でドキュメント参照のうえ dst で再作成してください。
  ```

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
  gcloud services enable bigquerystorage.googleapis.com --project=shingo-ar-service2026061901-1
  ```

#### `sql-component.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1007606807581/services/sql-component.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1007606807581 --filter='config.name:sql-component.googleapis.com'
  gcloud services enable sql-component.googleapis.com --project=shingo-ar-service2026061901-1
  ```

#### `storage-component.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1007606807581/services/storage-component.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1007606807581 --filter='config.name:storage-component.googleapis.com'
  gcloud services enable storage-component.googleapis.com --project=shingo-ar-service2026061901-1
  ```

#### `logging.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1007606807581/services/logging.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1007606807581 --filter='config.name:logging.googleapis.com'
  gcloud services enable logging.googleapis.com --project=shingo-ar-service2026061901-1
  ```

#### `cloudasset.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1007606807581/services/cloudasset.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1007606807581 --filter='config.name:cloudasset.googleapis.com'
  gcloud services enable cloudasset.googleapis.com --project=shingo-ar-service2026061901-1
  ```

#### `serviceusage.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1007606807581/services/serviceusage.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1007606807581 --filter='config.name:serviceusage.googleapis.com'
  gcloud services enable serviceusage.googleapis.com --project=shingo-ar-service2026061901-1
  ```

#### `compute.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1007606807581/services/compute.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1007606807581 --filter='config.name:compute.googleapis.com'
  gcloud services enable compute.googleapis.com --project=shingo-ar-service2026061901-1
  ```

#### `servicemanagement.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1007606807581/services/servicemanagement.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1007606807581 --filter='config.name:servicemanagement.googleapis.com'
  gcloud services enable servicemanagement.googleapis.com --project=shingo-ar-service2026061901-1
  ```

#### `vmmigration.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1007606807581/services/vmmigration.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1007606807581 --filter='config.name:vmmigration.googleapis.com'
  gcloud services enable vmmigration.googleapis.com --project=shingo-ar-service2026061901-1
  ```

#### `osconfig.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1007606807581/services/osconfig.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1007606807581 --filter='config.name:osconfig.googleapis.com'
  gcloud services enable osconfig.googleapis.com --project=shingo-ar-service2026061901-1
  ```

#### `monitoring.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1007606807581/services/monitoring.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1007606807581 --filter='config.name:monitoring.googleapis.com'
  gcloud services enable monitoring.googleapis.com --project=shingo-ar-service2026061901-1
  ```

#### `iam.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1007606807581/services/iam.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1007606807581 --filter='config.name:iam.googleapis.com'
  gcloud services enable iam.googleapis.com --project=shingo-ar-service2026061901-1
  ```

#### `cloudapis.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1007606807581/services/cloudapis.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1007606807581 --filter='config.name:cloudapis.googleapis.com'
  gcloud services enable cloudapis.googleapis.com --project=shingo-ar-service2026061901-1
  ```

#### `datastore.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1007606807581/services/datastore.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1007606807581 --filter='config.name:datastore.googleapis.com'
  gcloud services enable datastore.googleapis.com --project=shingo-ar-service2026061901-1
  ```

#### `oslogin.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1007606807581/services/oslogin.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1007606807581 --filter='config.name:oslogin.googleapis.com'
  gcloud services enable oslogin.googleapis.com --project=shingo-ar-service2026061901-1
  ```

#### `bigquerymigration.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1007606807581/services/bigquerymigration.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1007606807581 --filter='config.name:bigquerymigration.googleapis.com'
  gcloud services enable bigquerymigration.googleapis.com --project=shingo-ar-service2026061901-1
  ```

#### `storage-api.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1007606807581/services/storage-api.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1007606807581 --filter='config.name:storage-api.googleapis.com'
  gcloud services enable storage-api.googleapis.com --project=shingo-ar-service2026061901-1
  ```

#### `cloudtrace.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1007606807581/services/cloudtrace.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1007606807581 --filter='config.name:cloudtrace.googleapis.com'
  gcloud services enable cloudtrace.googleapis.com --project=shingo-ar-service2026061901-1
  ```

#### `iamcredentials.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1007606807581/services/iamcredentials.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1007606807581 --filter='config.name:iamcredentials.googleapis.com'
  gcloud services enable iamcredentials.googleapis.com --project=shingo-ar-service2026061901-1
  ```

#### `storage.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1007606807581/services/storage.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1007606807581 --filter='config.name:storage.googleapis.com'
  gcloud services enable storage.googleapis.com --project=shingo-ar-service2026061901-1
  ```

#### `bigquery.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1007606807581/services/bigquery.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1007606807581 --filter='config.name:bigquery.googleapis.com'
  gcloud services enable bigquery.googleapis.com --project=shingo-ar-service2026061901-1
  ```

### `vmmigration.googleapis.com/ImageImport` （3 件）

#### `vmdk-imported-20260608-centos8v-boot` (location=`asia-northeast1`)

- full name: `//vmmigration.googleapis.com/projects/shingo-ar-sharedservice0926-1/locations/asia-northeast1/imageImports/vmdk-imported-20260608-centos8v-boot`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `なし`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud asset describe '//vmmigration.googleapis.com/projects/shingo-ar-sharedservice0926-1/locations/asia-northeast1/imageImports/vmdk-imported-20260608-centos8v-boot' --project=shingo-ar-sharedservice0926-1
  # vmmigration.googleapis.com/ImageImport は自動補完対象外。手動でドキュメント参照のうえ dst で再作成してください。
  ```

#### `vmdk-imported-20260608-centos8t-boot` (location=`asia-northeast1`)

- full name: `//vmmigration.googleapis.com/projects/shingo-ar-sharedservice0926-1/locations/asia-northeast1/imageImports/vmdk-imported-20260608-centos8t-boot`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `なし`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud asset describe '//vmmigration.googleapis.com/projects/shingo-ar-sharedservice0926-1/locations/asia-northeast1/imageImports/vmdk-imported-20260608-centos8t-boot' --project=shingo-ar-sharedservice0926-1
  # vmmigration.googleapis.com/ImageImport は自動補完対象外。手動でドキュメント参照のうえ dst で再作成してください。
  ```

#### `vmdk-imported-20260608-boot` (location=`asia-northeast1`)

- full name: `//vmmigration.googleapis.com/projects/shingo-ar-sharedservice0926-1/locations/asia-northeast1/imageImports/vmdk-imported-20260608-boot`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `なし`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud asset describe '//vmmigration.googleapis.com/projects/shingo-ar-sharedservice0926-1/locations/asia-northeast1/imageImports/vmdk-imported-20260608-boot' --project=shingo-ar-sharedservice0926-1
  # vmmigration.googleapis.com/ImageImport は自動補完対象外。手動でドキュメント参照のうえ dst で再作成してください。
  ```

### `vmmigration.googleapis.com/TargetProject` （1 件）

#### `shingo-ar-sharedservice0926-1` (location=`global`)

- full name: `//vmmigration.googleapis.com/projects/shingo-ar-sharedservice0926-1/locations/global/targetProjects/shingo-ar-sharedservice0926-1`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `なし`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud asset describe '//vmmigration.googleapis.com/projects/shingo-ar-sharedservice0926-1/locations/global/targetProjects/shingo-ar-sharedservice0926-1' --project=shingo-ar-sharedservice0926-1
  # vmmigration.googleapis.com/TargetProject は自動補完対象外。手動でドキュメント参照のうえ dst で再作成してください。
  ```

## プロジェクト: `shingo-ar-sharedservice0926-3` → `shingo-ar-service2026061901-3`

- CAI 検出リソース: **189** 件 / TF 出力リソース: **37** 件 / 一致: **13** 件 / 欠落候補: **176** 件

### `bigquery.googleapis.com/Dataset` （2 件）

#### `dataset_bar` (location=`US`)

- full name: `//bigquery.googleapis.com/projects/shingo-ar-sharedservice0926-3/datasets/dataset_bar`
- 担当ステップ: `data_sync`
- 期待 TF 型: `google_bigquery_dataset`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_bigquery_dataset)
- 推奨コマンド:
  ```bash
  bq --project_id=shingo-ar-sharedservice0926-3 show --format=prettyjson dataset_bar
  bq --project_id=shingo-ar-service2026061901-3 mk --location=US --dataset shingo-ar-service2026061901-3:dataset_bar
  ```

#### `dataset_foo` (location=`US`)

- full name: `//bigquery.googleapis.com/projects/shingo-ar-sharedservice0926-3/datasets/dataset_foo`
- 担当ステップ: `data_sync`
- 期待 TF 型: `google_bigquery_dataset`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_bigquery_dataset)
- 推奨コマンド:
  ```bash
  bq --project_id=shingo-ar-sharedservice0926-3 show --format=prettyjson dataset_foo
  bq --project_id=shingo-ar-service2026061901-3 mk --location=US --dataset shingo-ar-service2026061901-3:dataset_foo
  ```

### `bigquery.googleapis.com/Table` （2 件）

#### `item_purchase_logs_all_json` (location=`US`)

- full name: `//bigquery.googleapis.com/projects/shingo-ar-sharedservice0926-3/datasets/dataset_bar/tables/item_purchase_logs_all_json`
- 担当ステップ: `data_sync`
- 期待 TF 型: `google_bigquery_table`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_bigquery_table)
- 推奨コマンド:
  ```bash
  bq --project_id=shingo-ar-sharedservice0926-3 show --format=prettyjson shingo-ar-sharedservice0926-3:dataset_bar.item_purchase_logs_all_json
  bq --project_id=shingo-ar-service2026061901-3 cp shingo-ar-sharedservice0926-3:dataset_bar.item_purchase_logs_all_json shingo-ar-service2026061901-3:dataset_bar.item_purchase_logs_all_json  # 通常は Step 6 (data_sync) が担当
  ```

#### `game_players_json` (location=`US`)

- full name: `//bigquery.googleapis.com/projects/shingo-ar-sharedservice0926-3/datasets/dataset_foo/tables/game_players_json`
- 担当ステップ: `data_sync`
- 期待 TF 型: `google_bigquery_table`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_bigquery_table)
- 推奨コマンド:
  ```bash
  bq --project_id=shingo-ar-sharedservice0926-3 show --format=prettyjson shingo-ar-sharedservice0926-3:dataset_foo.game_players_json
  bq --project_id=shingo-ar-service2026061901-3 cp shingo-ar-sharedservice0926-3:dataset_foo.game_players_json shingo-ar-service2026061901-3:dataset_foo.game_players_json  # 通常は Step 6 (data_sync) が担当
  ```

### `cloudbilling.googleapis.com/ProjectBillingInfo` （1 件）

#### `billingInfo` (location=`global`)

- full name: `//cloudbilling.googleapis.com/projects/shingo-ar-sharedservice0926-3/billingInfo`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `なし`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud asset describe '//cloudbilling.googleapis.com/projects/shingo-ar-sharedservice0926-3/billingInfo' --project=shingo-ar-sharedservice0926-3
  # cloudbilling.googleapis.com/ProjectBillingInfo は自動補完対象外。手動でドキュメント参照のうえ dst で再作成してください。
  ```

### `cloudresourcemanager.googleapis.com/Project` （1 件）

#### `shingo-ar-sharedservice0926-3` (location=`global`)

- full name: `//cloudresourcemanager.googleapis.com/projects/shingo-ar-sharedservice0926-3`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `なし`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud asset describe '//cloudresourcemanager.googleapis.com/projects/shingo-ar-sharedservice0926-3' 
  # cloudresourcemanager.googleapis.com/Project は自動補完対象外。手動でドキュメント参照のうえ dst で再作成してください。
  ```

### `compute.googleapis.com/Address` （12 件）

#### `org-svc3-ub-e2-med-303-ip` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/asia-northeast1/addresses/org-svc3-ub-e2-med-303-ip`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses describe org-svc3-ub-e2-med-303-ip --region=asia-northeast1 --project=shingo-ar-sharedservice0926-3
  gcloud compute addresses create org-svc3-ub-e2-med-303-ip --project=shingo-ar-service2026061901-3 --region=asia-northeast1
  ```

#### `org-svc3-ub-e2-med-302-ip` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/asia-northeast1/addresses/org-svc3-ub-e2-med-302-ip`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses describe org-svc3-ub-e2-med-302-ip --region=asia-northeast1 --project=shingo-ar-sharedservice0926-3
  gcloud compute addresses create org-svc3-ub-e2-med-302-ip --project=shingo-ar-service2026061901-3 --region=asia-northeast1
  ```

#### `org-svc3-ub-e2-mic-301-ip` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/asia-northeast1/addresses/org-svc3-ub-e2-mic-301-ip`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses describe org-svc3-ub-e2-mic-301-ip --region=asia-northeast1 --project=shingo-ar-sharedservice0926-3
  gcloud compute addresses create org-svc3-ub-e2-mic-301-ip --project=shingo-ar-service2026061901-3 --region=asia-northeast1
  ```

#### `org-svc3-ub-e2-mic-302-ip` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/asia-northeast1/addresses/org-svc3-ub-e2-mic-302-ip`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses describe org-svc3-ub-e2-mic-302-ip --region=asia-northeast1 --project=shingo-ar-sharedservice0926-3
  gcloud compute addresses create org-svc3-ub-e2-mic-302-ip --project=shingo-ar-service2026061901-3 --region=asia-northeast1
  ```

#### `org-svc3-ub-e2-med-301-ip` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/asia-northeast1/addresses/org-svc3-ub-e2-med-301-ip`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses describe org-svc3-ub-e2-med-301-ip --region=asia-northeast1 --project=shingo-ar-sharedservice0926-3
  gcloud compute addresses create org-svc3-ub-e2-med-301-ip --project=shingo-ar-service2026061901-3 --region=asia-northeast1
  ```

#### `org-svc3-ub-c2-std4-301-ip` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/asia-northeast1/addresses/org-svc3-ub-c2-std4-301-ip`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses describe org-svc3-ub-c2-std4-301-ip --region=asia-northeast1 --project=shingo-ar-sharedservice0926-3
  gcloud compute addresses create org-svc3-ub-c2-std4-301-ip --project=shingo-ar-service2026061901-3 --region=asia-northeast1
  ```

#### `org-svc3-ub-c2-std4-01-ip` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/asia-northeast1/addresses/org-svc3-ub-c2-std4-01-ip`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses describe org-svc3-ub-c2-std4-01-ip --region=asia-northeast1 --project=shingo-ar-sharedservice0926-3
  gcloud compute addresses create org-svc3-ub-c2-std4-01-ip --project=shingo-ar-service2026061901-3 --region=asia-northeast1
  ```

#### `org-svc3-ub-e2-med-02-ip` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/asia-northeast1/addresses/org-svc3-ub-e2-med-02-ip`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses describe org-svc3-ub-e2-med-02-ip --region=asia-northeast1 --project=shingo-ar-sharedservice0926-3
  gcloud compute addresses create org-svc3-ub-e2-med-02-ip --project=shingo-ar-service2026061901-3 --region=asia-northeast1
  ```

#### `org-svc3-ub-e2-med-01-ip` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/asia-northeast1/addresses/org-svc3-ub-e2-med-01-ip`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses describe org-svc3-ub-e2-med-01-ip --region=asia-northeast1 --project=shingo-ar-sharedservice0926-3
  gcloud compute addresses create org-svc3-ub-e2-med-01-ip --project=shingo-ar-service2026061901-3 --region=asia-northeast1
  ```

#### `org-svc3-ub-e2-mic-01-ip` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/asia-northeast1/addresses/org-svc3-ub-e2-mic-01-ip`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses describe org-svc3-ub-e2-mic-01-ip --region=asia-northeast1 --project=shingo-ar-sharedservice0926-3
  gcloud compute addresses create org-svc3-ub-e2-mic-01-ip --project=shingo-ar-service2026061901-3 --region=asia-northeast1
  ```

#### `org-svc3-ub-e2-mic-02-ip` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/asia-northeast1/addresses/org-svc3-ub-e2-mic-02-ip`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses describe org-svc3-ub-e2-mic-02-ip --region=asia-northeast1 --project=shingo-ar-sharedservice0926-3
  gcloud compute addresses create org-svc3-ub-e2-mic-02-ip --project=shingo-ar-service2026061901-3 --region=asia-northeast1
  ```

#### `org-svc3-ub-e2-med-03-ip` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/asia-northeast1/addresses/org-svc3-ub-e2-med-03-ip`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses describe org-svc3-ub-e2-med-03-ip --region=asia-northeast1 --project=shingo-ar-sharedservice0926-3
  gcloud compute addresses create org-svc3-ub-e2-med-03-ip --project=shingo-ar-service2026061901-3 --region=asia-northeast1
  ```

### `compute.googleapis.com/Disk` （6 件）

#### `org-svc3-ub-e2-med-302` (location=`asia-northeast1-a`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/zones/asia-northeast1-a/disks/org-svc3-ub-e2-med-302`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_disk/google_compute_region_disk`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_disk/google_compute_region_disk)
- 推奨コマンド:
  ```bash
  gcloud compute disks describe org-svc3-ub-e2-med-302 --zone=asia-northeast1-a --project=shingo-ar-sharedservice0926-3
  gcloud compute disks create org-svc3-ub-e2-med-302 --project=shingo-ar-service2026061901-3 --zone=asia-northeast1-a --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore)
  ```

#### `org-svc3-ub-e2-med-303` (location=`asia-northeast1-a`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/zones/asia-northeast1-a/disks/org-svc3-ub-e2-med-303`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_disk/google_compute_region_disk`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_disk/google_compute_region_disk)
- 推奨コマンド:
  ```bash
  gcloud compute disks describe org-svc3-ub-e2-med-303 --zone=asia-northeast1-a --project=shingo-ar-sharedservice0926-3
  gcloud compute disks create org-svc3-ub-e2-med-303 --project=shingo-ar-service2026061901-3 --zone=asia-northeast1-a --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore)
  ```

#### `org-svc3-ub-e2-mic-302` (location=`asia-northeast1-a`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/zones/asia-northeast1-a/disks/org-svc3-ub-e2-mic-302`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_disk/google_compute_region_disk`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_disk/google_compute_region_disk)
- 推奨コマンド:
  ```bash
  gcloud compute disks describe org-svc3-ub-e2-mic-302 --zone=asia-northeast1-a --project=shingo-ar-sharedservice0926-3
  gcloud compute disks create org-svc3-ub-e2-mic-302 --project=shingo-ar-service2026061901-3 --zone=asia-northeast1-a --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore)
  ```

#### `org-svc3-ub-e2-mic-301` (location=`asia-northeast1-a`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/zones/asia-northeast1-a/disks/org-svc3-ub-e2-mic-301`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_disk/google_compute_region_disk`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_disk/google_compute_region_disk)
- 推奨コマンド:
  ```bash
  gcloud compute disks describe org-svc3-ub-e2-mic-301 --zone=asia-northeast1-a --project=shingo-ar-sharedservice0926-3
  gcloud compute disks create org-svc3-ub-e2-mic-301 --project=shingo-ar-service2026061901-3 --zone=asia-northeast1-a --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore)
  ```

#### `org-svc3-ub-e2-med-301` (location=`asia-northeast1-a`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/zones/asia-northeast1-a/disks/org-svc3-ub-e2-med-301`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_disk/google_compute_region_disk`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_disk/google_compute_region_disk)
- 推奨コマンド:
  ```bash
  gcloud compute disks describe org-svc3-ub-e2-med-301 --zone=asia-northeast1-a --project=shingo-ar-sharedservice0926-3
  gcloud compute disks create org-svc3-ub-e2-med-301 --project=shingo-ar-service2026061901-3 --zone=asia-northeast1-a --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore)
  ```

#### `org-svc3-ub-c2-std4-301` (location=`asia-northeast1-a`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/zones/asia-northeast1-a/disks/org-svc3-ub-c2-std4-301`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_disk/google_compute_region_disk`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_disk/google_compute_region_disk)
- 推奨コマンド:
  ```bash
  gcloud compute disks describe org-svc3-ub-c2-std4-301 --zone=asia-northeast1-a --project=shingo-ar-sharedservice0926-3
  gcloud compute disks create org-svc3-ub-c2-std4-301 --project=shingo-ar-service2026061901-3 --zone=asia-northeast1-a --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore)
  ```

### `compute.googleapis.com/Image` （4 件）

#### `img-org-svc3-ub-e2-med-03` (location=`asia`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/images/img-org-svc3-ub-e2-med-03`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_image`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute images describe img-org-svc3-ub-e2-med-03 --project=shingo-ar-sharedservice0926-3
  # image は使用しない方針（snapshot 由来）。必要なら gcloud compute images create img-org-svc3-ub-e2-med-03 --project=shingo-ar-service2026061901-3 --source-snapshot=<SNAPSHOT>
  ```

#### `img-org-svc3-ub-e2-med-02` (location=`asia`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/images/img-org-svc3-ub-e2-med-02`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_image`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute images describe img-org-svc3-ub-e2-med-02 --project=shingo-ar-sharedservice0926-3
  # image は使用しない方針（snapshot 由来）。必要なら gcloud compute images create img-org-svc3-ub-e2-med-02 --project=shingo-ar-service2026061901-3 --source-snapshot=<SNAPSHOT>
  ```

#### `img-org-svc3-ub-e2-med-01` (location=`asia`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/images/img-org-svc3-ub-e2-med-01`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_image`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute images describe img-org-svc3-ub-e2-med-01 --project=shingo-ar-sharedservice0926-3
  # image は使用しない方針（snapshot 由来）。必要なら gcloud compute images create img-org-svc3-ub-e2-med-01 --project=shingo-ar-service2026061901-3 --source-snapshot=<SNAPSHOT>
  ```

#### `img-org-svc3-ub-c2-std4-01` (location=`asia`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/images/img-org-svc3-ub-c2-std4-01`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_image`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute images describe img-org-svc3-ub-c2-std4-01 --project=shingo-ar-sharedservice0926-3
  # image は使用しない方針（snapshot 由来）。必要なら gcloud compute images create img-org-svc3-ub-c2-std4-01 --project=shingo-ar-service2026061901-3 --source-snapshot=<SNAPSHOT>
  ```

### `compute.googleapis.com/Instance` （6 件）

#### `org-svc3-ub-e2-med-302` (location=`asia-northeast1-a`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/zones/asia-northeast1-a/instances/org-svc3-ub-e2-med-302`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_instance`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_instance)
- 推奨コマンド:
  ```bash
  gcloud compute instances describe org-svc3-ub-e2-med-302 --zone=asia-northeast1-a --project=shingo-ar-sharedservice0926-3
  gcloud compute instances create org-svc3-ub-e2-med-302 --project=shingo-ar-service2026061901-3 --zone=asia-northeast1-a --machine-type=<MACHINE_TYPE> --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore) が担当
  ```

#### `org-svc3-ub-e2-med-303` (location=`asia-northeast1-a`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/zones/asia-northeast1-a/instances/org-svc3-ub-e2-med-303`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_instance`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_instance)
- 推奨コマンド:
  ```bash
  gcloud compute instances describe org-svc3-ub-e2-med-303 --zone=asia-northeast1-a --project=shingo-ar-sharedservice0926-3
  gcloud compute instances create org-svc3-ub-e2-med-303 --project=shingo-ar-service2026061901-3 --zone=asia-northeast1-a --machine-type=<MACHINE_TYPE> --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore) が担当
  ```

#### `org-svc3-ub-e2-mic-301` (location=`asia-northeast1-a`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/zones/asia-northeast1-a/instances/org-svc3-ub-e2-mic-301`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_instance`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_instance)
- 推奨コマンド:
  ```bash
  gcloud compute instances describe org-svc3-ub-e2-mic-301 --zone=asia-northeast1-a --project=shingo-ar-sharedservice0926-3
  gcloud compute instances create org-svc3-ub-e2-mic-301 --project=shingo-ar-service2026061901-3 --zone=asia-northeast1-a --machine-type=<MACHINE_TYPE> --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore) が担当
  ```

#### `org-svc3-ub-e2-mic-302` (location=`asia-northeast1-a`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/zones/asia-northeast1-a/instances/org-svc3-ub-e2-mic-302`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_instance`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_instance)
- 推奨コマンド:
  ```bash
  gcloud compute instances describe org-svc3-ub-e2-mic-302 --zone=asia-northeast1-a --project=shingo-ar-sharedservice0926-3
  gcloud compute instances create org-svc3-ub-e2-mic-302 --project=shingo-ar-service2026061901-3 --zone=asia-northeast1-a --machine-type=<MACHINE_TYPE> --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore) が担当
  ```

#### `org-svc3-ub-e2-med-301` (location=`asia-northeast1-a`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/zones/asia-northeast1-a/instances/org-svc3-ub-e2-med-301`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_instance`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_instance)
- 推奨コマンド:
  ```bash
  gcloud compute instances describe org-svc3-ub-e2-med-301 --zone=asia-northeast1-a --project=shingo-ar-sharedservice0926-3
  gcloud compute instances create org-svc3-ub-e2-med-301 --project=shingo-ar-service2026061901-3 --zone=asia-northeast1-a --machine-type=<MACHINE_TYPE> --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore) が担当
  ```

#### `org-svc3-ub-c2-std4-301` (location=`asia-northeast1-a`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/zones/asia-northeast1-a/instances/org-svc3-ub-c2-std4-301`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_instance`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_instance)
- 推奨コマンド:
  ```bash
  gcloud compute instances describe org-svc3-ub-c2-std4-301 --zone=asia-northeast1-a --project=shingo-ar-sharedservice0926-3
  gcloud compute instances create org-svc3-ub-c2-std4-301 --project=shingo-ar-service2026061901-3 --zone=asia-northeast1-a --machine-type=<MACHINE_TYPE> --source-snapshot=<SNAPSHOT>  # 通常は Step 5 (gce_restore) が担当
  ```

### `compute.googleapis.com/InstanceSettings` （3 件）

#### `InstanceSettings` (location=`asia-northeast1-a`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/zones/asia-northeast1-a/instanceSettings/InstanceSettings`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `なし`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud asset describe '//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/zones/asia-northeast1-a/instanceSettings/InstanceSettings' --project=shingo-ar-sharedservice0926-3
  # compute.googleapis.com/InstanceSettings は自動補完対象外。手動でドキュメント参照のうえ dst で再作成してください。
  ```

#### `InstanceSettings` (location=`asia-northeast1-c`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/zones/asia-northeast1-c/instanceSettings/InstanceSettings`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `なし`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud asset describe '//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/zones/asia-northeast1-c/instanceSettings/InstanceSettings' --project=shingo-ar-sharedservice0926-3
  # compute.googleapis.com/InstanceSettings は自動補完対象外。手動でドキュメント参照のうえ dst で再作成してください。
  ```

#### `InstanceSettings` (location=`asia-northeast1-b`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/zones/asia-northeast1-b/instanceSettings/InstanceSettings`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `なし`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud asset describe '//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/zones/asia-northeast1-b/instanceSettings/InstanceSettings' --project=shingo-ar-sharedservice0926-3
  # compute.googleapis.com/InstanceSettings は自動補完対象外。手動でドキュメント参照のうえ dst で再作成してください。
  ```

### `compute.googleapis.com/Network` （1 件）

#### `default` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/networks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_network`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_network)
- 推奨コマンド:
  ```bash
  gcloud compute networks describe default --project=shingo-ar-sharedservice0926-3
  gcloud compute networks create default --project=shingo-ar-service2026061901-3 --subnet-mode=custom
  ```

### `compute.googleapis.com/Project` （1 件）

#### `shingo-ar-sharedservice0926-3` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `なし`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud asset describe '//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3' 
  # compute.googleapis.com/Project は自動補完対象外。手動でドキュメント参照のうえ dst で再作成してください。
  ```

### `compute.googleapis.com/Route` （48 件）

#### `default-route-r-98d048215189550b` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/routes/default-route-r-98d048215189550b`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-r-98d048215189550b --project=shingo-ar-sharedservice0926-3
  gcloud compute routes create default-route-r-98d048215189550b --project=shingo-ar-service2026061901-3 --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-r-11f907f3279696b5` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/routes/default-route-r-11f907f3279696b5`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-r-11f907f3279696b5 --project=shingo-ar-sharedservice0926-3
  gcloud compute routes create default-route-r-11f907f3279696b5 --project=shingo-ar-service2026061901-3 --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-d11f2034c4aeb51e` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/routes/default-route-d11f2034c4aeb51e`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-d11f2034c4aeb51e --project=shingo-ar-sharedservice0926-3
  gcloud compute routes create default-route-d11f2034c4aeb51e --project=shingo-ar-service2026061901-3 --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-3fe82b14c98b7cdf` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/routes/default-route-3fe82b14c98b7cdf`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-3fe82b14c98b7cdf --project=shingo-ar-sharedservice0926-3
  gcloud compute routes create default-route-3fe82b14c98b7cdf --project=shingo-ar-service2026061901-3 --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-de5c154989722050` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/routes/default-route-de5c154989722050`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-de5c154989722050 --project=shingo-ar-sharedservice0926-3
  gcloud compute routes create default-route-de5c154989722050 --project=shingo-ar-service2026061901-3 --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-522dfd5a9228c0e4` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/routes/default-route-522dfd5a9228c0e4`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-522dfd5a9228c0e4 --project=shingo-ar-sharedservice0926-3
  gcloud compute routes create default-route-522dfd5a9228c0e4 --project=shingo-ar-service2026061901-3 --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-893caa5ad4a6657c` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/routes/default-route-893caa5ad4a6657c`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-893caa5ad4a6657c --project=shingo-ar-sharedservice0926-3
  gcloud compute routes create default-route-893caa5ad4a6657c --project=shingo-ar-service2026061901-3 --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-57660cdbff324af4` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/routes/default-route-57660cdbff324af4`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-57660cdbff324af4 --project=shingo-ar-sharedservice0926-3
  gcloud compute routes create default-route-57660cdbff324af4 --project=shingo-ar-service2026061901-3 --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-09c2c7b1ab514ff6` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/routes/default-route-09c2c7b1ab514ff6`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-09c2c7b1ab514ff6 --project=shingo-ar-sharedservice0926-3
  gcloud compute routes create default-route-09c2c7b1ab514ff6 --project=shingo-ar-service2026061901-3 --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-c8973eb1c13ac479` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/routes/default-route-c8973eb1c13ac479`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-c8973eb1c13ac479 --project=shingo-ar-sharedservice0926-3
  gcloud compute routes create default-route-c8973eb1c13ac479 --project=shingo-ar-service2026061901-3 --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-42c467ae5fed1ac0` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/routes/default-route-42c467ae5fed1ac0`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-42c467ae5fed1ac0 --project=shingo-ar-sharedservice0926-3
  gcloud compute routes create default-route-42c467ae5fed1ac0 --project=shingo-ar-service2026061901-3 --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-e755856d9b20ba36` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/routes/default-route-e755856d9b20ba36`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-e755856d9b20ba36 --project=shingo-ar-sharedservice0926-3
  gcloud compute routes create default-route-e755856d9b20ba36 --project=shingo-ar-service2026061901-3 --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-0a66d0cc9c75cc8b` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/routes/default-route-0a66d0cc9c75cc8b`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-0a66d0cc9c75cc8b --project=shingo-ar-sharedservice0926-3
  gcloud compute routes create default-route-0a66d0cc9c75cc8b --project=shingo-ar-service2026061901-3 --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-2c3846332a2bc3e0` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/routes/default-route-2c3846332a2bc3e0`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-2c3846332a2bc3e0 --project=shingo-ar-sharedservice0926-3
  gcloud compute routes create default-route-2c3846332a2bc3e0 --project=shingo-ar-service2026061901-3 --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-2369d72760b8807f` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/routes/default-route-2369d72760b8807f`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-2369d72760b8807f --project=shingo-ar-sharedservice0926-3
  gcloud compute routes create default-route-2369d72760b8807f --project=shingo-ar-service2026061901-3 --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-b7740d025b045e64` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/routes/default-route-b7740d025b045e64`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-b7740d025b045e64 --project=shingo-ar-sharedservice0926-3
  gcloud compute routes create default-route-b7740d025b045e64 --project=shingo-ar-service2026061901-3 --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-4402e07ee1f2aeec` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/routes/default-route-4402e07ee1f2aeec`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-4402e07ee1f2aeec --project=shingo-ar-sharedservice0926-3
  gcloud compute routes create default-route-4402e07ee1f2aeec --project=shingo-ar-service2026061901-3 --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-51f46281a5f33c88` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/routes/default-route-51f46281a5f33c88`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-51f46281a5f33c88 --project=shingo-ar-sharedservice0926-3
  gcloud compute routes create default-route-51f46281a5f33c88 --project=shingo-ar-service2026061901-3 --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-ba1b19c510ed59d0` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/routes/default-route-ba1b19c510ed59d0`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-ba1b19c510ed59d0 --project=shingo-ar-sharedservice0926-3
  gcloud compute routes create default-route-ba1b19c510ed59d0 --project=shingo-ar-service2026061901-3 --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-60c15ba7ae600fc8` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/routes/default-route-60c15ba7ae600fc8`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-60c15ba7ae600fc8 --project=shingo-ar-sharedservice0926-3
  gcloud compute routes create default-route-60c15ba7ae600fc8 --project=shingo-ar-service2026061901-3 --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-9dde7ae8184c3852` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/routes/default-route-9dde7ae8184c3852`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-9dde7ae8184c3852 --project=shingo-ar-sharedservice0926-3
  gcloud compute routes create default-route-9dde7ae8184c3852 --project=shingo-ar-service2026061901-3 --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-55547ff6ba2ae8e8` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/routes/default-route-55547ff6ba2ae8e8`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-55547ff6ba2ae8e8 --project=shingo-ar-sharedservice0926-3
  gcloud compute routes create default-route-55547ff6ba2ae8e8 --project=shingo-ar-service2026061901-3 --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-485b9b21cd18f53c` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/routes/default-route-485b9b21cd18f53c`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-485b9b21cd18f53c --project=shingo-ar-sharedservice0926-3
  gcloud compute routes create default-route-485b9b21cd18f53c --project=shingo-ar-service2026061901-3 --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-67a144c3c4144632` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/routes/default-route-67a144c3c4144632`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-67a144c3c4144632 --project=shingo-ar-sharedservice0926-3
  gcloud compute routes create default-route-67a144c3c4144632 --project=shingo-ar-service2026061901-3 --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-10327149af16388b` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/routes/default-route-10327149af16388b`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-10327149af16388b --project=shingo-ar-sharedservice0926-3
  gcloud compute routes create default-route-10327149af16388b --project=shingo-ar-service2026061901-3 --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-bd63b42c414571ce` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/routes/default-route-bd63b42c414571ce`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-bd63b42c414571ce --project=shingo-ar-sharedservice0926-3
  gcloud compute routes create default-route-bd63b42c414571ce --project=shingo-ar-service2026061901-3 --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-9e9d115beaec855b` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/routes/default-route-9e9d115beaec855b`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-9e9d115beaec855b --project=shingo-ar-sharedservice0926-3
  gcloud compute routes create default-route-9e9d115beaec855b --project=shingo-ar-service2026061901-3 --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-05ea1a0ec1214c63` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/routes/default-route-05ea1a0ec1214c63`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-05ea1a0ec1214c63 --project=shingo-ar-sharedservice0926-3
  gcloud compute routes create default-route-05ea1a0ec1214c63 --project=shingo-ar-service2026061901-3 --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-5b4b0c3510dd4c63` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/routes/default-route-5b4b0c3510dd4c63`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-5b4b0c3510dd4c63 --project=shingo-ar-sharedservice0926-3
  gcloud compute routes create default-route-5b4b0c3510dd4c63 --project=shingo-ar-service2026061901-3 --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-3cb551462fd6d6d5` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/routes/default-route-3cb551462fd6d6d5`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-3cb551462fd6d6d5 --project=shingo-ar-sharedservice0926-3
  gcloud compute routes create default-route-3cb551462fd6d6d5 --project=shingo-ar-service2026061901-3 --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-0c0a16c7a37a0d3f` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/routes/default-route-0c0a16c7a37a0d3f`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-0c0a16c7a37a0d3f --project=shingo-ar-sharedservice0926-3
  gcloud compute routes create default-route-0c0a16c7a37a0d3f --project=shingo-ar-service2026061901-3 --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-17612d48b7875af0` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/routes/default-route-17612d48b7875af0`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-17612d48b7875af0 --project=shingo-ar-sharedservice0926-3
  gcloud compute routes create default-route-17612d48b7875af0 --project=shingo-ar-service2026061901-3 --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-11e87903139ccd22` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/routes/default-route-11e87903139ccd22`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-11e87903139ccd22 --project=shingo-ar-sharedservice0926-3
  gcloud compute routes create default-route-11e87903139ccd22 --project=shingo-ar-service2026061901-3 --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-45f2ff727e2416b8` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/routes/default-route-45f2ff727e2416b8`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-45f2ff727e2416b8 --project=shingo-ar-sharedservice0926-3
  gcloud compute routes create default-route-45f2ff727e2416b8 --project=shingo-ar-service2026061901-3 --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-58fd01a24169e46d` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/routes/default-route-58fd01a24169e46d`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-58fd01a24169e46d --project=shingo-ar-sharedservice0926-3
  gcloud compute routes create default-route-58fd01a24169e46d --project=shingo-ar-service2026061901-3 --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-8367b740ba1fb361` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/routes/default-route-8367b740ba1fb361`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-8367b740ba1fb361 --project=shingo-ar-sharedservice0926-3
  gcloud compute routes create default-route-8367b740ba1fb361 --project=shingo-ar-service2026061901-3 --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-b3369bd0128f75e6` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/routes/default-route-b3369bd0128f75e6`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-b3369bd0128f75e6 --project=shingo-ar-sharedservice0926-3
  gcloud compute routes create default-route-b3369bd0128f75e6 --project=shingo-ar-service2026061901-3 --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-111246bc0783214c` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/routes/default-route-111246bc0783214c`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-111246bc0783214c --project=shingo-ar-sharedservice0926-3
  gcloud compute routes create default-route-111246bc0783214c --project=shingo-ar-service2026061901-3 --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-2de92a3dadc51467` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/routes/default-route-2de92a3dadc51467`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-2de92a3dadc51467 --project=shingo-ar-sharedservice0926-3
  gcloud compute routes create default-route-2de92a3dadc51467 --project=shingo-ar-service2026061901-3 --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-b74117b3eb2f1ec9` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/routes/default-route-b74117b3eb2f1ec9`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-b74117b3eb2f1ec9 --project=shingo-ar-sharedservice0926-3
  gcloud compute routes create default-route-b74117b3eb2f1ec9 --project=shingo-ar-service2026061901-3 --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-3c185c4503f8f32f` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/routes/default-route-3c185c4503f8f32f`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-3c185c4503f8f32f --project=shingo-ar-sharedservice0926-3
  gcloud compute routes create default-route-3c185c4503f8f32f --project=shingo-ar-service2026061901-3 --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-e73e5fcce9e01700` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/routes/default-route-e73e5fcce9e01700`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-e73e5fcce9e01700 --project=shingo-ar-sharedservice0926-3
  gcloud compute routes create default-route-e73e5fcce9e01700 --project=shingo-ar-service2026061901-3 --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-988a7668582a422b` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/routes/default-route-988a7668582a422b`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-988a7668582a422b --project=shingo-ar-sharedservice0926-3
  gcloud compute routes create default-route-988a7668582a422b --project=shingo-ar-service2026061901-3 --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-fb0320b87f0aa00d` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/routes/default-route-fb0320b87f0aa00d`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-fb0320b87f0aa00d --project=shingo-ar-sharedservice0926-3
  gcloud compute routes create default-route-fb0320b87f0aa00d --project=shingo-ar-service2026061901-3 --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-7ca7d814326a7c78` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/routes/default-route-7ca7d814326a7c78`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-7ca7d814326a7c78 --project=shingo-ar-sharedservice0926-3
  gcloud compute routes create default-route-7ca7d814326a7c78 --project=shingo-ar-service2026061901-3 --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-d3cb1dfc35875d6f` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/routes/default-route-d3cb1dfc35875d6f`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-d3cb1dfc35875d6f --project=shingo-ar-sharedservice0926-3
  gcloud compute routes create default-route-d3cb1dfc35875d6f --project=shingo-ar-service2026061901-3 --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-90e4a484caccf593` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/routes/default-route-90e4a484caccf593`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-90e4a484caccf593 --project=shingo-ar-sharedservice0926-3
  gcloud compute routes create default-route-90e4a484caccf593 --project=shingo-ar-service2026061901-3 --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

#### `default-route-2c546851f7c5d132` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/routes/default-route-2c546851f7c5d132`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_compute_route`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud compute routes describe default-route-2c546851f7c5d132 --project=shingo-ar-sharedservice0926-3
  gcloud compute routes create default-route-2c546851f7c5d132 --project=shingo-ar-service2026061901-3 --network=<NETWORK> --destination-range=<CIDR> --next-hop-gateway=<GATEWAY>
  ```

### `compute.googleapis.com/Snapshot` （12 件）

#### `org-svc3-ub-e2-med-302-init-snap` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/snapshots/org-svc3-ub-e2-med-302-init-snap`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe org-svc3-ub-e2-med-302-init-snap --project=shingo-ar-sharedservice0926-3
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `org-svc3-ub-e2-med-301-init-snap` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/snapshots/org-svc3-ub-e2-med-301-init-snap`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe org-svc3-ub-e2-med-301-init-snap --project=shingo-ar-sharedservice0926-3
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `org-svc3-ub-e2-med-303-init-snap` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/snapshots/org-svc3-ub-e2-med-303-init-snap`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe org-svc3-ub-e2-med-303-init-snap --project=shingo-ar-sharedservice0926-3
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `org-svc3-ub-c2-std4-301-init-snap` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/snapshots/org-svc3-ub-c2-std4-301-init-snap`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe org-svc3-ub-c2-std4-301-init-snap --project=shingo-ar-sharedservice0926-3
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `org-svc3-ub-e2-mic-302-init-snap` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/snapshots/org-svc3-ub-e2-mic-302-init-snap`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe org-svc3-ub-e2-mic-302-init-snap --project=shingo-ar-sharedservice0926-3
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `org-svc3-ub-e2-mic-301-init-snap` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/snapshots/org-svc3-ub-e2-mic-301-init-snap`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe org-svc3-ub-e2-mic-301-init-snap --project=shingo-ar-sharedservice0926-3
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `org-svc3-ub-c2-std4-01` (location=`asia`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/snapshots/org-svc3-ub-c2-std4-01`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe org-svc3-ub-c2-std4-01 --project=shingo-ar-sharedservice0926-3
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `org-svc3-ub-e2-mic-02` (location=`asia`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/snapshots/org-svc3-ub-e2-mic-02`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe org-svc3-ub-e2-mic-02 --project=shingo-ar-sharedservice0926-3
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `org-svc3-ub-e2-med-01` (location=`asia`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/snapshots/org-svc3-ub-e2-med-01`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe org-svc3-ub-e2-med-01 --project=shingo-ar-sharedservice0926-3
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `org-svc3-ub-e2-med-03` (location=`asia`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/snapshots/org-svc3-ub-e2-med-03`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe org-svc3-ub-e2-med-03 --project=shingo-ar-sharedservice0926-3
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `org-svc3-ub-e2-mic-01` (location=`asia`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/snapshots/org-svc3-ub-e2-mic-01`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe org-svc3-ub-e2-mic-01 --project=shingo-ar-sharedservice0926-3
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

#### `org-svc3-ub-e2-med-02` (location=`asia`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/global/snapshots/org-svc3-ub-e2-med-02`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_snapshot`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_snapshot)
- 推奨コマンド:
  ```bash
  gcloud compute snapshots describe org-svc3-ub-e2-med-02 --project=shingo-ar-sharedservice0926-3
  # snapshot は src 側からの参照で復元する設計のため dst 作成は不要 (Step 5 gce_restore が source-snapshot として直接使用)
  ```

### `compute.googleapis.com/Subnetwork` （45 件）

#### `default` (location=`asia-southeast3`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/asia-southeast3/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=asia-southeast3 --project=shingo-ar-sharedservice0926-3
  gcloud compute networks subnets create default --project=shingo-ar-service2026061901-3 --region=asia-southeast3 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`europe-north2`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/europe-north2/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=europe-north2 --project=shingo-ar-sharedservice0926-3
  gcloud compute networks subnets create default --project=shingo-ar-service2026061901-3 --region=europe-north2 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`northamerica-south1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/northamerica-south1/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=northamerica-south1 --project=shingo-ar-sharedservice0926-3
  gcloud compute networks subnets create default --project=shingo-ar-service2026061901-3 --region=northamerica-south1 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`us-west8`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/us-west8/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=us-west8 --project=shingo-ar-sharedservice0926-3
  gcloud compute networks subnets create default --project=shingo-ar-service2026061901-3 --region=us-west8 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`africa-south1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/africa-south1/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=africa-south1 --project=shingo-ar-sharedservice0926-3
  gcloud compute networks subnets create default --project=shingo-ar-service2026061901-3 --region=africa-south1 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`me-central2`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/me-central2/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=me-central2 --project=shingo-ar-sharedservice0926-3
  gcloud compute networks subnets create default --project=shingo-ar-service2026061901-3 --region=me-central2 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`europe-west10`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/europe-west10/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=europe-west10 --project=shingo-ar-sharedservice0926-3
  gcloud compute networks subnets create default --project=shingo-ar-service2026061901-3 --region=europe-west10 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`me-central1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/me-central1/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=me-central1 --project=shingo-ar-sharedservice0926-3
  gcloud compute networks subnets create default --project=shingo-ar-service2026061901-3 --region=me-central1 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`europe-west12`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/europe-west12/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=europe-west12 --project=shingo-ar-sharedservice0926-3
  gcloud compute networks subnets create default --project=shingo-ar-service2026061901-3 --region=europe-west12 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`us-east7`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/us-east7/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=us-east7 --project=shingo-ar-sharedservice0926-3
  gcloud compute networks subnets create default --project=shingo-ar-service2026061901-3 --region=us-east7 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`europe-north1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/europe-north1/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=europe-north1 --project=shingo-ar-sharedservice0926-3
  gcloud compute networks subnets create default --project=shingo-ar-service2026061901-3 --region=europe-north1 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`southamerica-east1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/southamerica-east1/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=southamerica-east1 --project=shingo-ar-sharedservice0926-3
  gcloud compute networks subnets create default --project=shingo-ar-service2026061901-3 --region=southamerica-east1 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`europe-west2`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/europe-west2/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=europe-west2 --project=shingo-ar-sharedservice0926-3
  gcloud compute networks subnets create default --project=shingo-ar-service2026061901-3 --region=europe-west2 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`europe-west4`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/europe-west4/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=europe-west4 --project=shingo-ar-sharedservice0926-3
  gcloud compute networks subnets create default --project=shingo-ar-service2026061901-3 --region=europe-west4 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`asia-northeast2`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/asia-northeast2/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=asia-northeast2 --project=shingo-ar-sharedservice0926-3
  gcloud compute networks subnets create default --project=shingo-ar-service2026061901-3 --region=asia-northeast2 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`asia-south1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/asia-south1/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=asia-south1 --project=shingo-ar-sharedservice0926-3
  gcloud compute networks subnets create default --project=shingo-ar-service2026061901-3 --region=asia-south1 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`europe-central2`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/europe-central2/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=europe-central2 --project=shingo-ar-sharedservice0926-3
  gcloud compute networks subnets create default --project=shingo-ar-service2026061901-3 --region=europe-central2 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`us-west1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/us-west1/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=us-west1 --project=shingo-ar-sharedservice0926-3
  gcloud compute networks subnets create default --project=shingo-ar-service2026061901-3 --region=us-west1 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`asia-east1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/asia-east1/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=asia-east1 --project=shingo-ar-sharedservice0926-3
  gcloud compute networks subnets create default --project=shingo-ar-service2026061901-3 --region=asia-east1 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`us-south1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/us-south1/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=us-south1 --project=shingo-ar-sharedservice0926-3
  gcloud compute networks subnets create default --project=shingo-ar-service2026061901-3 --region=us-south1 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`us-east4`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/us-east4/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=us-east4 --project=shingo-ar-sharedservice0926-3
  gcloud compute networks subnets create default --project=shingo-ar-service2026061901-3 --region=us-east4 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`northamerica-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/northamerica-northeast1/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=northamerica-northeast1 --project=shingo-ar-sharedservice0926-3
  gcloud compute networks subnets create default --project=shingo-ar-service2026061901-3 --region=northamerica-northeast1 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`us-west4`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/us-west4/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=us-west4 --project=shingo-ar-sharedservice0926-3
  gcloud compute networks subnets create default --project=shingo-ar-service2026061901-3 --region=us-west4 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`europe-west6`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/europe-west6/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=europe-west6 --project=shingo-ar-sharedservice0926-3
  gcloud compute networks subnets create default --project=shingo-ar-service2026061901-3 --region=europe-west6 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`europe-southwest1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/europe-southwest1/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=europe-southwest1 --project=shingo-ar-sharedservice0926-3
  gcloud compute networks subnets create default --project=shingo-ar-service2026061901-3 --region=europe-southwest1 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`asia-southeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/asia-southeast1/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=asia-southeast1 --project=shingo-ar-sharedservice0926-3
  gcloud compute networks subnets create default --project=shingo-ar-service2026061901-3 --region=asia-southeast1 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`europe-west9`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/europe-west9/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=europe-west9 --project=shingo-ar-sharedservice0926-3
  gcloud compute networks subnets create default --project=shingo-ar-service2026061901-3 --region=europe-west9 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`me-west1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/me-west1/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=me-west1 --project=shingo-ar-sharedservice0926-3
  gcloud compute networks subnets create default --project=shingo-ar-service2026061901-3 --region=me-west1 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`us-west3`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/us-west3/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=us-west3 --project=shingo-ar-sharedservice0926-3
  gcloud compute networks subnets create default --project=shingo-ar-service2026061901-3 --region=us-west3 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`us-east5`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/us-east5/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=us-east5 --project=shingo-ar-sharedservice0926-3
  gcloud compute networks subnets create default --project=shingo-ar-service2026061901-3 --region=us-east5 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`australia-southeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/australia-southeast1/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=australia-southeast1 --project=shingo-ar-sharedservice0926-3
  gcloud compute networks subnets create default --project=shingo-ar-service2026061901-3 --region=australia-southeast1 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`asia-south2`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/asia-south2/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=asia-south2 --project=shingo-ar-sharedservice0926-3
  gcloud compute networks subnets create default --project=shingo-ar-service2026061901-3 --region=asia-south2 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`us-west2`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/us-west2/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=us-west2 --project=shingo-ar-sharedservice0926-3
  gcloud compute networks subnets create default --project=shingo-ar-service2026061901-3 --region=us-west2 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`europe-west8`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/europe-west8/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=europe-west8 --project=shingo-ar-sharedservice0926-3
  gcloud compute networks subnets create default --project=shingo-ar-service2026061901-3 --region=europe-west8 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`asia-southeast2`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/asia-southeast2/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=asia-southeast2 --project=shingo-ar-sharedservice0926-3
  gcloud compute networks subnets create default --project=shingo-ar-service2026061901-3 --region=asia-southeast2 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`australia-southeast2`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/australia-southeast2/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=australia-southeast2 --project=shingo-ar-sharedservice0926-3
  gcloud compute networks subnets create default --project=shingo-ar-service2026061901-3 --region=australia-southeast2 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`asia-east2`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/asia-east2/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=asia-east2 --project=shingo-ar-sharedservice0926-3
  gcloud compute networks subnets create default --project=shingo-ar-service2026061901-3 --region=asia-east2 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`northamerica-northeast2`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/northamerica-northeast2/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=northamerica-northeast2 --project=shingo-ar-sharedservice0926-3
  gcloud compute networks subnets create default --project=shingo-ar-service2026061901-3 --region=northamerica-northeast2 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`us-east1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/us-east1/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=us-east1 --project=shingo-ar-sharedservice0926-3
  gcloud compute networks subnets create default --project=shingo-ar-service2026061901-3 --region=us-east1 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`europe-west1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/europe-west1/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=europe-west1 --project=shingo-ar-sharedservice0926-3
  gcloud compute networks subnets create default --project=shingo-ar-service2026061901-3 --region=europe-west1 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/asia-northeast1/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=asia-northeast1 --project=shingo-ar-sharedservice0926-3
  gcloud compute networks subnets create default --project=shingo-ar-service2026061901-3 --region=asia-northeast1 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`europe-west3`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/europe-west3/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=europe-west3 --project=shingo-ar-sharedservice0926-3
  gcloud compute networks subnets create default --project=shingo-ar-service2026061901-3 --region=europe-west3 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`southamerica-west1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/southamerica-west1/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=southamerica-west1 --project=shingo-ar-sharedservice0926-3
  gcloud compute networks subnets create default --project=shingo-ar-service2026061901-3 --region=southamerica-west1 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`asia-northeast3`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/asia-northeast3/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=asia-northeast3 --project=shingo-ar-sharedservice0926-3
  gcloud compute networks subnets create default --project=shingo-ar-service2026061901-3 --region=asia-northeast3 --network=<NETWORK> --range=<CIDR>
  ```

#### `default` (location=`us-central1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/us-central1/subnetworks/default`
- 担当ステップ: `gce_restore`
- 期待 TF 型: `google_compute_subnetwork`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_subnetwork)
- 推奨コマンド:
  ```bash
  gcloud compute networks subnets describe default --region=us-central1 --project=shingo-ar-sharedservice0926-3
  gcloud compute networks subnets create default --project=shingo-ar-service2026061901-3 --region=us-central1 --network=<NETWORK> --range=<CIDR>
  ```

### `iam.googleapis.com/Role` （2 件）

#### `Incre` (location=`global`)

- full name: `//iam.googleapis.com/projects/shingo-ar-sharedservice0926-3/roles/Incre`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_project_iam_custom_role`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_project_iam_custom_role)
- 推奨コマンド:
  ```bash
  gcloud iam roles describe Incre --project=shingo-ar-sharedservice0926-3
  gcloud iam roles create Incre --project=shingo-ar-service2026061901-3 --title=<TITLE> --permissions=<PERM1,PERM2,...> --stage=GA
  ```

#### `migrationSrcReader` (location=`global`)

- full name: `//iam.googleapis.com/projects/shingo-ar-sharedservice0926-3/roles/migrationSrcReader`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_project_iam_custom_role`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_project_iam_custom_role)
- 推奨コマンド:
  ```bash
  gcloud iam roles describe migrationSrcReader --project=shingo-ar-sharedservice0926-3
  gcloud iam roles create migrationSrcReader --project=shingo-ar-service2026061901-3 --title=<TITLE> --permissions=<PERM1,PERM2,...> --stage=GA
  ```

### `iam.googleapis.com/ServiceAccount` （3 件）

#### `org-svc3-viewer@shingo-ar-sharedservice0926-3.iam.gserviceaccount.com` (location=`global`)

- full name: `//iam.googleapis.com/projects/shingo-ar-sharedservice0926-3/serviceAccounts/org-svc3-viewer@shingo-ar-sharedservice0926-3.iam.gserviceaccount.com`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_service_account`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_service_account)
- 推奨コマンド:
  ```bash
  gcloud iam service-accounts describe org-svc3-viewer@shingo-ar-sharedservice0926-3.iam.gserviceaccount.com --project=shingo-ar-sharedservice0926-3
  gcloud iam service-accounts create org-svc3-viewer --project=shingo-ar-service2026061901-3 --display-name=<DISPLAY_NAME>
  ```

#### `incredibuild@shingo-ar-sharedservice0926-3.iam.gserviceaccount.com` (location=`global`)

- full name: `//iam.googleapis.com/projects/shingo-ar-sharedservice0926-3/serviceAccounts/incredibuild@shingo-ar-sharedservice0926-3.iam.gserviceaccount.com`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_service_account`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_service_account)
- 推奨コマンド:
  ```bash
  gcloud iam service-accounts describe incredibuild@shingo-ar-sharedservice0926-3.iam.gserviceaccount.com --project=shingo-ar-sharedservice0926-3
  gcloud iam service-accounts create incredibuild --project=shingo-ar-service2026061901-3 --display-name=<DISPLAY_NAME>
  ```

#### `1033858800454-compute@developer.gserviceaccount.com` (location=`global`)

- full name: `//iam.googleapis.com/projects/shingo-ar-sharedservice0926-3/serviceAccounts/1033858800454-compute@developer.gserviceaccount.com`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_service_account`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_service_account)
- 推奨コマンド:
  ```bash
  gcloud iam service-accounts describe 1033858800454-compute@developer.gserviceaccount.com --project=shingo-ar-sharedservice0926-3
  gcloud iam service-accounts create 1033858800454-compute --project=shingo-ar-service2026061901-3 --display-name=<DISPLAY_NAME>
  ```

### `iam.googleapis.com/ServiceAccountKey` （2 件）

#### `d04a4ff33affc3a5124a8aef69152ab31ca7a091` (location=`global`)

- full name: `//iam.googleapis.com/projects/shingo-ar-sharedservice0926-3/serviceAccounts/100682100138600860386/keys/d04a4ff33affc3a5124a8aef69152ab31ca7a091`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `なし`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud asset describe '//iam.googleapis.com/projects/shingo-ar-sharedservice0926-3/serviceAccounts/100682100138600860386/keys/d04a4ff33affc3a5124a8aef69152ab31ca7a091' --project=shingo-ar-sharedservice0926-3
  # iam.googleapis.com/ServiceAccountKey は自動補完対象外。手動でドキュメント参照のうえ dst で再作成してください。
  ```

#### `7e0170835cd407104ac4f90797cc0b12402429a5` (location=`global`)

- full name: `//iam.googleapis.com/projects/shingo-ar-sharedservice0926-3/serviceAccounts/104507197771240164503/keys/7e0170835cd407104ac4f90797cc0b12402429a5`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `なし`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud asset describe '//iam.googleapis.com/projects/shingo-ar-sharedservice0926-3/serviceAccounts/104507197771240164503/keys/7e0170835cd407104ac4f90797cc0b12402429a5' --project=shingo-ar-sharedservice0926-3
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
  gcloud logging buckets create _Default --location=global --project=shingo-ar-service2026061901-3 --retention-days=<N>
  ```

#### `_Required` (location=`global`)

- full name: `//logging.googleapis.com/projects/1033858800454/locations/global/buckets/_Required`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_logging_project_bucket_config`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_logging_project_bucket_config)
- 推奨コマンド:
  ```bash
  gcloud logging buckets describe _Required --location=global --project=1033858800454
  gcloud logging buckets create _Required --location=global --project=shingo-ar-service2026061901-3 --retention-days=<N>
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
  gcloud logging sinks create _Required <DESTINATION> --project=shingo-ar-service2026061901-3 --log-filter='<FILTER>'
  ```

#### `_Default` (location=`global`)

- full name: `//logging.googleapis.com/projects/1033858800454/sinks/_Default`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_logging_project_sink`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_logging_project_sink)
- 推奨コマンド:
  ```bash
  gcloud logging sinks describe _Default --project=1033858800454
  gcloud logging sinks create _Default <DESTINATION> --project=shingo-ar-service2026061901-3 --log-filter='<FILTER>'
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
  gcloud services enable storage-component.googleapis.com --project=shingo-ar-service2026061901-3
  ```

#### `cloudtrace.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1033858800454/services/cloudtrace.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1033858800454 --filter='config.name:cloudtrace.googleapis.com'
  gcloud services enable cloudtrace.googleapis.com --project=shingo-ar-service2026061901-3
  ```

#### `oslogin.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1033858800454/services/oslogin.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1033858800454 --filter='config.name:oslogin.googleapis.com'
  gcloud services enable oslogin.googleapis.com --project=shingo-ar-service2026061901-3
  ```

#### `logging.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1033858800454/services/logging.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1033858800454 --filter='config.name:logging.googleapis.com'
  gcloud services enable logging.googleapis.com --project=shingo-ar-service2026061901-3
  ```

#### `cloudapis.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1033858800454/services/cloudapis.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1033858800454 --filter='config.name:cloudapis.googleapis.com'
  gcloud services enable cloudapis.googleapis.com --project=shingo-ar-service2026061901-3
  ```

#### `cloudasset.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1033858800454/services/cloudasset.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1033858800454 --filter='config.name:cloudasset.googleapis.com'
  gcloud services enable cloudasset.googleapis.com --project=shingo-ar-service2026061901-3
  ```

#### `sql-component.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1033858800454/services/sql-component.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1033858800454 --filter='config.name:sql-component.googleapis.com'
  gcloud services enable sql-component.googleapis.com --project=shingo-ar-service2026061901-3
  ```

#### `bigquerystorage.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1033858800454/services/bigquerystorage.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1033858800454 --filter='config.name:bigquerystorage.googleapis.com'
  gcloud services enable bigquerystorage.googleapis.com --project=shingo-ar-service2026061901-3
  ```

#### `cloudaicompanion.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1033858800454/services/cloudaicompanion.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1033858800454 --filter='config.name:cloudaicompanion.googleapis.com'
  gcloud services enable cloudaicompanion.googleapis.com --project=shingo-ar-service2026061901-3
  ```

#### `bigquerymigration.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1033858800454/services/bigquerymigration.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1033858800454 --filter='config.name:bigquerymigration.googleapis.com'
  gcloud services enable bigquerymigration.googleapis.com --project=shingo-ar-service2026061901-3
  ```

#### `bigquery.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1033858800454/services/bigquery.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1033858800454 --filter='config.name:bigquery.googleapis.com'
  gcloud services enable bigquery.googleapis.com --project=shingo-ar-service2026061901-3
  ```

#### `monitoring.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1033858800454/services/monitoring.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1033858800454 --filter='config.name:monitoring.googleapis.com'
  gcloud services enable monitoring.googleapis.com --project=shingo-ar-service2026061901-3
  ```

#### `serviceusage.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1033858800454/services/serviceusage.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1033858800454 --filter='config.name:serviceusage.googleapis.com'
  gcloud services enable serviceusage.googleapis.com --project=shingo-ar-service2026061901-3
  ```

#### `datastore.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1033858800454/services/datastore.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1033858800454 --filter='config.name:datastore.googleapis.com'
  gcloud services enable datastore.googleapis.com --project=shingo-ar-service2026061901-3
  ```

#### `compute.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1033858800454/services/compute.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1033858800454 --filter='config.name:compute.googleapis.com'
  gcloud services enable compute.googleapis.com --project=shingo-ar-service2026061901-3
  ```

#### `storage.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1033858800454/services/storage.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1033858800454 --filter='config.name:storage.googleapis.com'
  gcloud services enable storage.googleapis.com --project=shingo-ar-service2026061901-3
  ```

#### `cloudresourcemanager.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1033858800454/services/cloudresourcemanager.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1033858800454 --filter='config.name:cloudresourcemanager.googleapis.com'
  gcloud services enable cloudresourcemanager.googleapis.com --project=shingo-ar-service2026061901-3
  ```

#### `servicemanagement.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1033858800454/services/servicemanagement.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1033858800454 --filter='config.name:servicemanagement.googleapis.com'
  gcloud services enable servicemanagement.googleapis.com --project=shingo-ar-service2026061901-3
  ```

#### `storage-api.googleapis.com` (location=`global`)

- full name: `//serviceusage.googleapis.com/projects/1033858800454/services/storage-api.googleapis.com`
- 担当ステップ: `意図的対象外 (None)`
- 期待 TF 型: `google_project_service`
- 判定理由: 意図的に対象外（マップで None 指定）
- 推奨コマンド:
  ```bash
  gcloud services list --enabled --project=1033858800454 --filter='config.name:storage-api.googleapis.com'
  gcloud services enable storage-api.googleapis.com --project=shingo-ar-service2026061901-3
  ```

### `storage.googleapis.com/Bucket` （2 件）

#### `shingo-ar-test` (location=`us`)

- full name: `//storage.googleapis.com/shingo-ar-test`
- 担当ステップ: `data_sync`
- 期待 TF 型: `google_storage_bucket`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_storage_bucket)
- 推奨コマンド:
  ```bash
  gcloud storage buckets describe gs://shingo-ar-test
  gcloud storage buckets create gs://<DST_BUCKET_NAME> --project=shingo-ar-service2026061901-3 --location=us  # 名前は rename_rules.gcs を適用すること
  ```

#### `shingo-ar-sharedservice0926-3` (location=`us`)

- full name: `//storage.googleapis.com/shingo-ar-sharedservice0926-3`
- 担当ステップ: `data_sync`
- 期待 TF 型: `google_storage_bucket`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_storage_bucket)
- 推奨コマンド:
  ```bash
  gcloud storage buckets describe gs://shingo-ar-sharedservice0926-3
  gcloud storage buckets create gs://<DST_BUCKET_NAME> --project=shingo-ar-service2026061901-3 --location=us  # 名前は rename_rules.gcs を適用すること
  ```

---
合計欠落候補: **338** 件
