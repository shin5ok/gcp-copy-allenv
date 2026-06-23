# CAI ↔ Terraform bulk-export 差分レポート

Cloud Asset Inventory（CAI）が観測した src 側リソースのうち、
bulk-export / terraform で **自動再現されず、手動で dst 作成・調整が必要なもの** だけを
プロジェクトごとに列挙し、dst 側に再現するための gcloud コマンドを併記します。
（read 操作の describe / list は省き、作成系コマンドのみ掲載）

掲載対象（要手動対応）:
- 「未登録」: `_ASSET_COVERAGE` に無い assetType（複製漏れの可能性）。
- 「bulk-export が出力しなかった」: terraform_apply 担当のはずが TF 出力に無い。

非掲載（自動処理 / 対象外。件数のみ集計）:
- 専用ステップ（Step 4.5 network_firewall / Step 5 gce_restore / Step 6 data_sync）が複製。
- `_ASSET_COVERAGE` で None 指定の意図的対象外（実害なし）。

## プロジェクト: `shingo-ar-sharedhost0926` → `shingo-ar-host2026062302`

- CAI 検出リソース: **69** 件 / TF 出力リソース: **3** 件 / 一致: **0** 件 / 要手動対応: **13** 件 / 自動処理・対象外: **56** 件

### `compute.googleapis.com/Address` （6 件）

#### `svc2-ip1` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedhost0926/regions/asia-northeast1/addresses/svc2-ip1`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses create svc2-ip1 --project=shingo-ar-host2026062302 --region=asia-northeast1
  ```

#### `svc1-fix1` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedhost0926/regions/asia-northeast1/addresses/svc1-fix1`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses create svc1-fix1 --project=shingo-ar-host2026062302 --region=asia-northeast1
  ```

#### `fix-tokyo2-1` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedhost0926/regions/asia-northeast1/addresses/fix-tokyo2-1`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses create fix-tokyo2-1 --project=shingo-ar-host2026062302 --region=asia-northeast1
  ```

#### `fix-tokyo1` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedhost0926/regions/asia-northeast1/addresses/fix-tokyo1`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses create fix-tokyo1 --project=shingo-ar-host2026062302 --region=asia-northeast1
  ```

#### `nat-auto-ip-10281266-0-1781794550182258` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedhost0926/regions/asia-northeast1/addresses/nat-auto-ip-10281266-0-1781794550182258`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses create nat-auto-ip-10281266-0-1781794550182258 --project=shingo-ar-host2026062302 --region=asia-northeast1
  ```

#### `coordinator` (location=`global`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedhost0926/global/addresses/coordinator`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses create coordinator --project=shingo-ar-host2026062302 --global
  ```

### `iam.googleapis.com/Role` （1 件）

#### `migrationSrcReader` (location=`global`)

- full name: `//iam.googleapis.com/projects/shingo-ar-sharedhost0926/roles/migrationSrcReader`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_project_iam_custom_role`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_project_iam_custom_role)
- 推奨コマンド:
  ```bash
  gcloud iam roles create migrationSrcReader --project=shingo-ar-host2026062302 --title=<TITLE> --permissions=<PERM1,PERM2,...> --stage=GA
  ```

### `iam.googleapis.com/ServiceAccount` （2 件）

#### `org-host-viewer@shingo-ar-sharedhost0926.iam.gserviceaccount.com` (location=`global`)

- full name: `//iam.googleapis.com/projects/shingo-ar-sharedhost0926/serviceAccounts/org-host-viewer@shingo-ar-sharedhost0926.iam.gserviceaccount.com`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_service_account`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_service_account)
- 推奨コマンド:
  ```bash
  gcloud iam service-accounts create org-host-viewer --project=shingo-ar-host2026062302 --display-name=<DISPLAY_NAME>
  ```

#### `1035210593832-compute@developer.gserviceaccount.com` (location=`global`)

- full name: `//iam.googleapis.com/projects/shingo-ar-sharedhost0926/serviceAccounts/1035210593832-compute@developer.gserviceaccount.com`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_service_account`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_service_account)
- 推奨コマンド:
  ```bash
  gcloud iam service-accounts create 1035210593832-compute --project=shingo-ar-host2026062302 --display-name=<DISPLAY_NAME>
  ```

### `logging.googleapis.com/LogBucket` （2 件）

#### `_Default` (location=`global`)

- full name: `//logging.googleapis.com/projects/1035210593832/locations/global/buckets/_Default`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_logging_project_bucket_config`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_logging_project_bucket_config)
- 推奨コマンド:
  ```bash
  gcloud logging buckets create _Default --location=global --project=shingo-ar-host2026062302 --retention-days=<N>
  ```

#### `_Required` (location=`global`)

- full name: `//logging.googleapis.com/projects/1035210593832/locations/global/buckets/_Required`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_logging_project_bucket_config`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_logging_project_bucket_config)
- 推奨コマンド:
  ```bash
  gcloud logging buckets create _Required --location=global --project=shingo-ar-host2026062302 --retention-days=<N>
  ```

### `logging.googleapis.com/LogSink` （2 件）

#### `_Required` (location=`global`)

- full name: `//logging.googleapis.com/projects/1035210593832/sinks/_Required`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_logging_project_sink`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_logging_project_sink)
- 推奨コマンド:
  ```bash
  gcloud logging sinks create _Required <DESTINATION> --project=shingo-ar-host2026062302 --log-filter='<FILTER>'
  ```

#### `_Default` (location=`global`)

- full name: `//logging.googleapis.com/projects/1035210593832/sinks/_Default`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_logging_project_sink`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_logging_project_sink)
- 推奨コマンド:
  ```bash
  gcloud logging sinks create _Default <DESTINATION> --project=shingo-ar-host2026062302 --log-filter='<FILTER>'
  ```

## プロジェクト: `shingo-ar-sharedservice0926-1` → `shingo-ar-service2026062302-1`

- CAI 検出リソース: **118** 件 / TF 出力リソース: **3** 件 / 一致: **1** 件 / 要手動対応: **14** 件 / 自動処理・対象外: **103** 件

### `compute.googleapis.com/Address` （7 件）

#### `org-svc1-deb-e2-mic-101-ip` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/regions/asia-northeast1/addresses/org-svc1-deb-e2-mic-101-ip`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses create org-svc1-deb-e2-mic-101-ip --project=shingo-ar-service2026062302-1 --region=asia-northeast1
  ```

#### `sharedvpcip` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/regions/asia-northeast1/addresses/sharedvpcip`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses create sharedvpcip --project=shingo-ar-service2026062302-1 --region=asia-northeast1
  ```

#### `org-svc1-deb-n2-std2-02-ip` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/regions/asia-northeast1/addresses/org-svc1-deb-n2-std2-02-ip`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses create org-svc1-deb-n2-std2-02-ip --project=shingo-ar-service2026062302-1 --region=asia-northeast1
  ```

#### `org-svc1-deb-n2-std2-01-ip` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/regions/asia-northeast1/addresses/org-svc1-deb-n2-std2-01-ip`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses create org-svc1-deb-n2-std2-01-ip --project=shingo-ar-service2026062302-1 --region=asia-northeast1
  ```

#### `org-svc1-deb-e2-mic-01-ip` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/regions/asia-northeast1/addresses/org-svc1-deb-e2-mic-01-ip`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses create org-svc1-deb-e2-mic-01-ip --project=shingo-ar-service2026062302-1 --region=asia-northeast1
  ```

#### `org-svc1-deb-e2-mic-02-ip` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/regions/asia-northeast1/addresses/org-svc1-deb-e2-mic-02-ip`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses create org-svc1-deb-e2-mic-02-ip --project=shingo-ar-service2026062302-1 --region=asia-northeast1
  ```

#### `org-svc1-deb-e2-mic-03-ip` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-1/regions/asia-northeast1/addresses/org-svc1-deb-e2-mic-03-ip`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses create org-svc1-deb-e2-mic-03-ip --project=shingo-ar-service2026062302-1 --region=asia-northeast1
  ```

### `iam.googleapis.com/Role` （1 件）

#### `migrationSrcReader` (location=`global`)

- full name: `//iam.googleapis.com/projects/shingo-ar-sharedservice0926-1/roles/migrationSrcReader`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_project_iam_custom_role`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_project_iam_custom_role)
- 推奨コマンド:
  ```bash
  gcloud iam roles create migrationSrcReader --project=shingo-ar-service2026062302-1 --title=<TITLE> --permissions=<PERM1,PERM2,...> --stage=GA
  ```

### `iam.googleapis.com/ServiceAccount` （2 件）

#### `org-svc1-viewer@shingo-ar-sharedservice0926-1.iam.gserviceaccount.com` (location=`global`)

- full name: `//iam.googleapis.com/projects/shingo-ar-sharedservice0926-1/serviceAccounts/org-svc1-viewer@shingo-ar-sharedservice0926-1.iam.gserviceaccount.com`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_service_account`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_service_account)
- 推奨コマンド:
  ```bash
  gcloud iam service-accounts create org-svc1-viewer --project=shingo-ar-service2026062302-1 --display-name=<DISPLAY_NAME>
  ```

#### `1007606807581-compute@developer.gserviceaccount.com` (location=`global`)

- full name: `//iam.googleapis.com/projects/shingo-ar-sharedservice0926-1/serviceAccounts/1007606807581-compute@developer.gserviceaccount.com`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_service_account`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_service_account)
- 推奨コマンド:
  ```bash
  gcloud iam service-accounts create 1007606807581-compute --project=shingo-ar-service2026062302-1 --display-name=<DISPLAY_NAME>
  ```

### `logging.googleapis.com/LogBucket` （2 件）

#### `_Default` (location=`global`)

- full name: `//logging.googleapis.com/projects/1007606807581/locations/global/buckets/_Default`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_logging_project_bucket_config`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_logging_project_bucket_config)
- 推奨コマンド:
  ```bash
  gcloud logging buckets create _Default --location=global --project=shingo-ar-service2026062302-1 --retention-days=<N>
  ```

#### `_Required` (location=`global`)

- full name: `//logging.googleapis.com/projects/1007606807581/locations/global/buckets/_Required`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_logging_project_bucket_config`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_logging_project_bucket_config)
- 推奨コマンド:
  ```bash
  gcloud logging buckets create _Required --location=global --project=shingo-ar-service2026062302-1 --retention-days=<N>
  ```

### `logging.googleapis.com/LogSink` （2 件）

#### `_Required` (location=`global`)

- full name: `//logging.googleapis.com/projects/1007606807581/sinks/_Required`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_logging_project_sink`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_logging_project_sink)
- 推奨コマンド:
  ```bash
  gcloud logging sinks create _Required <DESTINATION> --project=shingo-ar-service2026062302-1 --log-filter='<FILTER>'
  ```

#### `_Default` (location=`global`)

- full name: `//logging.googleapis.com/projects/1007606807581/sinks/_Default`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_logging_project_sink`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_logging_project_sink)
- 推奨コマンド:
  ```bash
  gcloud logging sinks create _Default <DESTINATION> --project=shingo-ar-service2026062302-1 --log-filter='<FILTER>'
  ```

## プロジェクト: `shingo-ar-sharedservice0926-3` → `shingo-ar-service2026062302-3`

- CAI 検出リソース: **190** 件 / TF 出力リソース: **3** 件 / 一致: **0** 件 / 要手動対応: **25** 件 / 自動処理・対象外: **165** 件

### `compute.googleapis.com/Address` （13 件）

#### `org-svc3-ub-e2-med-303-ip` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/asia-northeast1/addresses/org-svc3-ub-e2-med-303-ip`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses create org-svc3-ub-e2-med-303-ip --project=shingo-ar-service2026062302-3 --region=asia-northeast1
  ```

#### `org-svc3-ub-e2-med-302-ip` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/asia-northeast1/addresses/org-svc3-ub-e2-med-302-ip`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses create org-svc3-ub-e2-med-302-ip --project=shingo-ar-service2026062302-3 --region=asia-northeast1
  ```

#### `org-svc3-ub-e2-mic-301-ip` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/asia-northeast1/addresses/org-svc3-ub-e2-mic-301-ip`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses create org-svc3-ub-e2-mic-301-ip --project=shingo-ar-service2026062302-3 --region=asia-northeast1
  ```

#### `org-svc3-ub-e2-mic-302-ip` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/asia-northeast1/addresses/org-svc3-ub-e2-mic-302-ip`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses create org-svc3-ub-e2-mic-302-ip --project=shingo-ar-service2026062302-3 --region=asia-northeast1
  ```

#### `org-svc3-ub-e2-med-301-ip` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/asia-northeast1/addresses/org-svc3-ub-e2-med-301-ip`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses create org-svc3-ub-e2-med-301-ip --project=shingo-ar-service2026062302-3 --region=asia-northeast1
  ```

#### `org-svc3-ub-c2-std4-301-ip` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/asia-northeast1/addresses/org-svc3-ub-c2-std4-301-ip`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses create org-svc3-ub-c2-std4-301-ip --project=shingo-ar-service2026062302-3 --region=asia-northeast1
  ```

#### `org-svc3-ub-c2-std4-01-ip` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/asia-northeast1/addresses/org-svc3-ub-c2-std4-01-ip`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses create org-svc3-ub-c2-std4-01-ip --project=shingo-ar-service2026062302-3 --region=asia-northeast1
  ```

#### `org-svc3-ub-e2-med-02-ip` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/asia-northeast1/addresses/org-svc3-ub-e2-med-02-ip`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses create org-svc3-ub-e2-med-02-ip --project=shingo-ar-service2026062302-3 --region=asia-northeast1
  ```

#### `org-svc3-ub-e2-med-01-ip` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/asia-northeast1/addresses/org-svc3-ub-e2-med-01-ip`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses create org-svc3-ub-e2-med-01-ip --project=shingo-ar-service2026062302-3 --region=asia-northeast1
  ```

#### `org-svc3-ub-e2-mic-01-ip` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/asia-northeast1/addresses/org-svc3-ub-e2-mic-01-ip`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses create org-svc3-ub-e2-mic-01-ip --project=shingo-ar-service2026062302-3 --region=asia-northeast1
  ```

#### `org-svc3-ub-e2-mic-02-ip` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/asia-northeast1/addresses/org-svc3-ub-e2-mic-02-ip`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses create org-svc3-ub-e2-mic-02-ip --project=shingo-ar-service2026062302-3 --region=asia-northeast1
  ```

#### `org-svc3-ub-e2-med-03-ip` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/asia-northeast1/addresses/org-svc3-ub-e2-med-03-ip`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses create org-svc3-ub-e2-med-03-ip --project=shingo-ar-service2026062302-3 --region=asia-northeast1
  ```

#### `test` (location=`asia-northeast1`)

- full name: `//compute.googleapis.com/projects/shingo-ar-sharedservice0926-3/regions/asia-northeast1/addresses/test`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_compute_address/google_compute_global_address`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_compute_address/google_compute_global_address)
- 推奨コマンド:
  ```bash
  gcloud compute addresses create test --project=shingo-ar-service2026062302-3 --region=asia-northeast1
  ```

### `iam.googleapis.com/Role` （5 件）

#### `incre3` (location=`global`)

- full name: `//iam.googleapis.com/projects/shingo-ar-sharedservice0926-3/roles/incre3`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_project_iam_custom_role`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_project_iam_custom_role)
- 推奨コマンド:
  ```bash
  gcloud iam roles create incre3 --project=shingo-ar-service2026062302-3 --title=<TITLE> --permissions=<PERM1,PERM2,...> --stage=GA
  ```

#### `Incre` (location=`global`)

- full name: `//iam.googleapis.com/projects/shingo-ar-sharedservice0926-3/roles/Incre`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_project_iam_custom_role`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_project_iam_custom_role)
- 推奨コマンド:
  ```bash
  gcloud iam roles create Incre --project=shingo-ar-service2026062302-3 --title=<TITLE> --permissions=<PERM1,PERM2,...> --stage=GA
  ```

#### `migrationSrcReader` (location=`global`)

- full name: `//iam.googleapis.com/projects/shingo-ar-sharedservice0926-3/roles/migrationSrcReader`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_project_iam_custom_role`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_project_iam_custom_role)
- 推奨コマンド:
  ```bash
  gcloud iam roles create migrationSrcReader --project=shingo-ar-service2026062302-3 --title=<TITLE> --permissions=<PERM1,PERM2,...> --stage=GA
  ```

#### `incre2` (location=`global`)

- full name: `//iam.googleapis.com/projects/shingo-ar-sharedservice0926-3/roles/incre2`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_project_iam_custom_role`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_project_iam_custom_role)
- 推奨コマンド:
  ```bash
  gcloud iam roles create incre2 --project=shingo-ar-service2026062302-3 --title=<TITLE> --permissions=<PERM1,PERM2,...> --stage=GA
  ```

#### `incre` (location=`global`)

- full name: `//iam.googleapis.com/projects/shingo-ar-sharedservice0926-3/roles/incre`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_project_iam_custom_role`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_project_iam_custom_role)
- 推奨コマンド:
  ```bash
  gcloud iam roles create incre --project=shingo-ar-service2026062302-3 --title=<TITLE> --permissions=<PERM1,PERM2,...> --stage=GA
  ```

### `iam.googleapis.com/ServiceAccount` （3 件）

#### `org-svc3-viewer@shingo-ar-sharedservice0926-3.iam.gserviceaccount.com` (location=`global`)

- full name: `//iam.googleapis.com/projects/shingo-ar-sharedservice0926-3/serviceAccounts/org-svc3-viewer@shingo-ar-sharedservice0926-3.iam.gserviceaccount.com`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_service_account`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_service_account)
- 推奨コマンド:
  ```bash
  gcloud iam service-accounts create org-svc3-viewer --project=shingo-ar-service2026062302-3 --display-name=<DISPLAY_NAME>
  ```

#### `incredibuild@shingo-ar-sharedservice0926-3.iam.gserviceaccount.com` (location=`global`)

- full name: `//iam.googleapis.com/projects/shingo-ar-sharedservice0926-3/serviceAccounts/incredibuild@shingo-ar-sharedservice0926-3.iam.gserviceaccount.com`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_service_account`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_service_account)
- 推奨コマンド:
  ```bash
  gcloud iam service-accounts create incredibuild --project=shingo-ar-service2026062302-3 --display-name=<DISPLAY_NAME>
  ```

#### `1033858800454-compute@developer.gserviceaccount.com` (location=`global`)

- full name: `//iam.googleapis.com/projects/shingo-ar-sharedservice0926-3/serviceAccounts/1033858800454-compute@developer.gserviceaccount.com`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_service_account`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_service_account)
- 推奨コマンド:
  ```bash
  gcloud iam service-accounts create 1033858800454-compute --project=shingo-ar-service2026062302-3 --display-name=<DISPLAY_NAME>
  ```

### `logging.googleapis.com/LogBucket` （2 件）

#### `_Default` (location=`global`)

- full name: `//logging.googleapis.com/projects/1033858800454/locations/global/buckets/_Default`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_logging_project_bucket_config`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_logging_project_bucket_config)
- 推奨コマンド:
  ```bash
  gcloud logging buckets create _Default --location=global --project=shingo-ar-service2026062302-3 --retention-days=<N>
  ```

#### `_Required` (location=`global`)

- full name: `//logging.googleapis.com/projects/1033858800454/locations/global/buckets/_Required`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_logging_project_bucket_config`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_logging_project_bucket_config)
- 推奨コマンド:
  ```bash
  gcloud logging buckets create _Required --location=global --project=shingo-ar-service2026062302-3 --retention-days=<N>
  ```

### `logging.googleapis.com/LogSink` （2 件）

#### `_Required` (location=`global`)

- full name: `//logging.googleapis.com/projects/1033858800454/sinks/_Required`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_logging_project_sink`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_logging_project_sink)
- 推奨コマンド:
  ```bash
  gcloud logging sinks create _Required <DESTINATION> --project=shingo-ar-service2026062302-3 --log-filter='<FILTER>'
  ```

#### `_Default` (location=`global`)

- full name: `//logging.googleapis.com/projects/1033858800454/sinks/_Default`
- 担当ステップ: `terraform_apply`
- 期待 TF 型: `google_logging_project_sink`
- 判定理由: bulk-export が出力しなかった (期待 TF 型: google_logging_project_sink)
- 推奨コマンド:
  ```bash
  gcloud logging sinks create _Default <DESTINATION> --project=shingo-ar-service2026062302-3 --log-filter='<FILTER>'
  ```

---
合計（要手動対応）: **52** 件
