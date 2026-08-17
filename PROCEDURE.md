# 推奨運用フロー: 技術設定まとめ

`README.md` の「推奨運用フロー」に沿って `make` ターゲットを順に実行したとき、各ステップで何が起きるかをハイレベルな目的と技術粒度の両面でまとめた手順書。

> **全体像**: src ORG の GCP プロジェクト群を、別 ORG の dst プロジェクト群に「設定 + データ + VM 状態」ごと複製する。src 側は最後まで read-only、書き込みはすべて dst 側で完結する。

---

## 0. 事前準備（手動）

**目的**: 「どこから何を、どこへ複製するか」を宣言し、必要な認証・データ・設定を揃える。
ここが揃っていないと `make plan` の事前チェック (fail-fast) で停止し、dst には一切書き込まずに終わる。

### 0a. 把握しておく情報（先に集める）
| 情報 | 用途 / 反映先 |
|---|---|
| コピー元 (src) の全プロジェクト ID（host + svc） | `config.yaml: project_mapping.host_project.src` / `service_projects[].src` |
| コピー先 (dst) の **組織 ID** (`organizations/<id>`) | `config.yaml: bootstrap.org_id` |
| コピー先のフォルダ ID（任意） | `config.yaml: bootstrap.folder_id` |
| **請求先アカウント ID** (`billingAccounts/<id>`) | `config.yaml: bootstrap.billing_account` |
| 借用 SA メール（**両方オプション**。src を書き換えたくない場合は src/dst とも未指定=ローカル認証がおすすめ） | `config.yaml: project_mapping.*.{src,dst}_impersonate_service_account` |
| VPC SC を使うなら access_policy / perimeter / billing_project | `config.yaml: steps.vpc_sc.*`（**全部必須**） |

### 0b. ローカル環境
- `gcloud auth login` + `gcloud auth application-default login`（ADC）でログイン済み
  - **Impersonation を使う場合のみ**、実行ユーザーが対象 SA に対して `roles/iam.serviceAccountTokenCreator` を持つこと
  - ローカル認証（src/dst とも未指定）なら tokenCreator は不要
- `gcloud` / `bq` / `terraform` が PATH に通る（`bulk_export` を使うなら `gcloud components install config-connector`）
- Python 3.13 以上 / `uv`

### 0c. コピー元 (src) の準備
- **src を一切書き換えたくない場合のおすすめ（ローカル認証）**: `src_impersonate_service_account` は
  **空のまま**にし、実行ユーザー本人に src の `roles/viewer`（+ `roles/cloudasset.viewer`）相当の
  読取権限を用意する（既存の読取権限があればそれを流用）。
  - 利点: src 側に SA を作らずに済み、src の IAM を書き換えずに読める。
  - src への書き込みはコード上 `is_src_read_only` ガードで impersonate の有無に関わらず常時禁止。
  - 事前チェックで実行ユーザーに src 書込権が検出された場合は警告 + `[y/N]` 続行確認（非対話は `make plan YES=1` / `make run YES=1` で明示許可）。
- **impersonate 経路を使う場合（オプション）**: `scripts/bootstrap_src_sa.sh --apply` で read-only SA を各 src プロジェクトに作成
  - 付与: `roles/viewer` / `roles/cloudasset.viewer` / 実行ユーザーへ `roles/iam.serviceAccountTokenCreator`
  - **このスクリプトの実行は src(ORG) への IAM 書き込みを伴う**ため、src を一切変更したくない要件には不向き。`sync_env.py` の ORG 保護とは意図的に分離した手動セットアップ用
- **GCE VM の期限内スナップショット**（`gce_snapshot` 有効時の必須前提）:
  - 移行対象の全 VM について `steps.gce_snapshot.max_age_days`（既定 30 日）以内のスナップショットが必要
  - 不足していると **Step 2 `gce_snapshot` がエラー停止**（`make plan` でも検出）
  - 手動作成: `gcloud compute disks snapshot <disk> --snapshot-names=<name> --zone=<zone> --project=<src>`
  - `cai_scan` 結果と合わせて事前棚卸ししておくと、`make plan` 時の手戻りが減る

### 0d. 設定ファイルの編集
- `cp dst/config.yaml.template dst/config.yaml` してから 0a で集めた値を埋める:
  - `project_mapping`: src/dst プロジェクト ID、`host_project` + `service_projects`、`*_impersonate_service_account`（両方オプション。**src を書き換えたくない場合は src/dst とも未指定=ローカル認証がおすすめ**）
    - `host_project.skip: true`（オプション）: 既に構築済みの dst host を再利用する場合に指定。host を全ステップから除外（`create_projects` / SA preflight / cai_scan / enable_apis / bulk_export / terraform_apply / serverless_sync / network_firewall / gce_restore / iam_sync / data_sync）し、`terraform/active/<src_host>/` の state も孤児削除から保護して温存する。ID/番号置換マップには残るため service 側の host 参照（Shared VPC ネットワーク URL 等）は dst へ正しく書き換わる。
  - `rename_rules.gcs.value`: 固定文字列 or `"auto"`（日付ベース suffix `-dst-MMDDHHMM` を `terraform/.gcs_rename_value` に永続化）
  - `steps`: 各ステップ (`cai_scan` / `enable_apis` / `gce_snapshot` / `bulk_export` / `terraform_apply` / `serverless_sync` / `network_firewall` / `gce_restore` / `iam_sync` / `data_sync` / `vpc_sc`) の有効/無効、`gce_snapshot.max_age_days`、`bulk_export.skip_on_run`、`vpc_sc.billing_project`（**必須・明示指定**）等
    - **`enable_apis` / `serverless_sync` / `network_firewall` / `iam_sync` はキーを書かなくても有効**（既定 true）。止めたいときだけ `enabled: false` を書く。
    - `serverless_sync` の任意設定: `skip_services`（複製しないサービス / 関数名）、`source_bucket`（関数ソース受け渡し用バケット名）、`build_service_account`（自前のビルド SA を使う）、`grant_build_service_account: false`（ビルド専用 SA の自動用意を止める）
  - `bootstrap`: 0a で集めた `org_id` / `folder_id` / `billing_account`

---

## 1. `make projects-plan` → `make projects`

**目的**: コピー先となる「空の dst プロジェクト群」を組織内に確保する。後続の bootstrap/plan/run はここで作った箱に対して行う。

- `bootstrap.org_id` / `folder_id` / `billing_account` を読み込み、dst プロジェクト（host + svc）を新規作成
- 請求先アカウントを紐付け
- 必要 API を有効化（compute / storage / bigquery / cloudasset / iam など）
- `projects-plan` は dry-run、`projects` で実書き込み

---

## 2. `make bootstrap-plan` → `make bootstrap`

**目的**: 「誰が、どのプロジェクトに、何を読み書きできるか」を整える。ここで権限と Shared VPC のトポロジが確定する。

3 スクリプトを順次実行。`bootstrap-plan` で内容確認 → `bootstrap` で適用（`projects*` と同じく裸 = 実適用 / `-plan` = dry-run）。

> 💡 **認証パターンに関わらず `make bootstrap-plan` → `make bootstrap` で OK**。
> 借用 SA 未指定（ローカル認証・おすすめ）なら 2a (dst SA) / 2b (src 読取権限) はスクリプトが
> 「対象なし」と判定して自動スキップし、2c の Shared VPC 化だけが適用される。
> SA 指定時は 2a / 2b / 2c がすべて実行される。

### 2a. `bootstrap-dst-sa` (`scripts/bootstrap_dst_sa.sh`) — Impersonation を使う場合のみ（借用 SA 未指定なら自動スキップ）
**役割**: dst 側の書き込み用 SA を作る。dst を借用する場合、以降の書き込みはこの SA を impersonate して行う（ローカル認証なら不要）。
- 各 dst プロジェクトに dst SA を作成
- 付与: `roles/editor` / `roles/storage.admin` / `roles/bigquery.admin`
- 実行アカウントに `roles/iam.serviceAccountTokenCreator`（impersonate 用）

### 2b. `bootstrap-cross-project` (`scripts/bootstrap_cross_project.sh`) — Impersonation を使う場合のみ（借用 SA 未指定なら自動スキップ）
**役割**: dst SA が src を「覗ける」ようにする。src への書き込み権限は与えない（ローカル認証なら不要）。
- 各 src プロジェクトに read-only カスタムロール `migrationSrcReader` を作成（compute/GCS/BQ/CAI の read 権限のみ）
- 対応する dst SA に `migrationSrcReader` + `roles/bigquery.dataViewer` を付与
- **src(ORG) への read-only IAM 書き込み**を伴うため、`sync_env.py` の ORG 保護からは意図的に分離

### 2c. `bootstrap-shared-vpc` (`scripts/bootstrap_shared_vpc.sh`)
**役割**: dst 側のネットワーク構造（Shared VPC）を src と同型にする。
- dst host を Shared VPC ホスト化
- dst svc プロジェクトを host にアタッチ
- 各 svc の `cloudservices` / `compute` SA と svc 借用 SA に `roles/compute.networkUser` を付与

---

## 3. `make plan`（ドライラン + 差分出力）

**目的**: 本番実行 (`make run`) の前に「何を作る/変更するか」を全部見える化する。書き込みは一切しない。`DIFF.md` を眺めて意図通りか確認するためのステップ。

### 事前チェック（fail-fast）
**役割**: 権限・CLI・**設定不備**で途中失敗するのを防ぐ。全件列挙して即停止。
- **config.yaml 検証** (`load_config` 内、`make plan` / `make run` / `make mock` 共通):
  - `validate_config()` … ORG 保護: src/dst マッピングの欠落・src=dst・dst が src ID と衝突 等。
  - `validate_steps_config()` … **有効ステップの設定不備**を実行前に検出（自動補完で握り潰さず明示エラー）:
    - `vpc_sc.enabled=true` なのに `access_policy` / `perimeter` / `billing_project` のいずれかが空（`billing_project` は quota project。未設定だと `SERVICE_DISABLED` で必ず失敗するため必須）。
    - `bulk_export` / `data_sync` 有効時に `rename_rules.gcs.method` が `suffix|prefix|custom` 以外、または `suffix|prefix` で `value` 空（src と同名バケットになり衝突）。
    - `gce_snapshot` 有効時に `max_age_days` が正の整数でない。
    - `bulk_export.resource_types`（Terraform 型・`google_` 始まり必須）と `export_resource_types`（KRM Kind・大文字始まり or `"auto"`）の取り違え。typo は「何にも一致せず全除外」という静かな事故になるため実行前に弾く。
    - `data_sync.artifact_registry.scope` が `all|tagged` 以外（綴り誤りを黙って `all` に倒すと「絞ったつもりが全量」になる）。
    - `serverless_sync.skip_services` が文字列リストでない / `grant_build_service_account` が true・false でない / `build_service_account` が email 形式でない。
  - 不備は `[設定不備] ...` として全件列挙し `exit 1`（dst へ一切書き込まずに停止）。
- 有効ステップに必要な CLI を検査: `gcloud` / `terraform` / `bq` / `config-connector` / **`gcrane` または `crane`**（`data_sync` 有効かつ `artifact_registry.enabled != false` のとき。イメージ複製は digest を保つ必要があるため docker は不可）
- 借用 SA 検証:
  - `gcloud auth print-access-token` で SA 実在 + tokenCreator 権限を確認
  - `gcloud projects test-iam-permissions` で代表権限（src=read / dst=write）を確認
  - 不足は全件列挙して即停止（dst SA の不備もここで検出）
- 借用 SA 未指定のときのフォールバック:
  - `*_impersonate_service_account` 未指定はエラーにせず、ローカル認証（gcloud のアクティブアカウント / ADC）を使う
  - その認証主体が **src プロジェクトに書込相当の権限を持っていれば**、対象プロジェクトと付与権限を一覧で警告し `[y/N]` で続行確認（非対話セッションは `--yes` = `make plan/run YES=1` を明示指定した時のみ続行。環境変数での自動承認は不可）
  - src 側のコマンド書込動詞拒否ガード (`is_src_read_only`) は impersonate の有無に関わらず常時有効

### 計画ステップ（src は read-only）

> 番号は **Step 番号**で、上から**実行順**に並べている（`4.7` が `4.5` より先に走るのは、Step 4.5 が後から挿入されたため。番号順 ≠ 実行順）。

1. **cai_scan**: Cloud Asset Inventory で src の有効リソース一覧を取得（「何が存在するか」のスナップショット）
1.5. **enable_apis**（Step 3.5 で 2 回目）: dst で必要な API を有効化する。**dry-run でも「何を有効化する予定か」まで出す**（有効 API の一覧取得は read-only なので `make plan` でも実行される）。src 由来を先に、`.tf` が出揃った Step 3.5 で全量を有効化して伝播を待つ 2 段構え。
2. **gce_snapshot**: 各 VM に期限内（既定 30 日）の有効スナップショットがあるか検証（復元元の鮮度チェック）
3. **bulk_export**: `gcloud beta resource-config bulk-export --resource-format=terraform` で HCL 出力（並列）
   - src の現状を Terraform コードとして書き出し、dst 向けに書き換える工程
   - プロジェクト ID 置換（src → dst）
   - GCS バケットを `rename_rules` でリネーム
   - 同一プロジェクト内 network 参照を `google_compute_network.<label>.self_link` に書き換え
   - `boot_disk.source` 行を削除（Step 5 で管理するため）
   - 成果物: `terraform/active/<src>/`
3.7. **artifact_registry**（`data_sync` 配下）: 複製するコンテナイメージを列挙する計画。**Terraform より前**に置く（Cloud Run は `image = "...@sha256:<digest>"` を revision 作成時に解決するため、apply の後では間に合わない）。
4. **terraform_apply**: `terraform plan -out=tfplan` を生成（apply はしない）
   - ⚠️ **`make plan` は Terraform を一切実行しません**。`terraform init` / `plan` も dry-run では `[DRY RUN] 予定:` と表示して空振りするため、**tfplan は生成されず** `make run` は毎回ゼロから plan を計算します。import 可否や dst の API 有効化は `make run` で初めて検証されます。
4.7. **serverless_sync**: Cloud Run サービス / ジョブと Cloud Functions の複製計画。書き換え後の YAML と実行予定の `gcloud functions deploy` コマンドを表示する。移せない設定を含むものは「作らない」判断とその理由が出る。
4.5. **network_firewall**: classic firewall ルールと Network Firewall Policy を dst host VPC に複製する計画。`secure_tag_map` 未登録の tagValues 参照は skip + WARNING。
5. **gce_restore**: スナップショットから復元するディスク差し替え計画
5.7. **iam_sync**: src の project IAM を dst の同名 SA へ複製する計画 + Cloud Run の公開設定（`allUsers → run.invoker`）の複製計画。
6. **data_sync**: GCS（リネーム後名）/ BigQuery（src の location 継承）の同期計画
7. **vpc_sc**: 既存ペリメタへ dst プロジェクト（番号）を追記する計画。`access_policy` / `perimeter` / `billing_project` のいずれかが未設定なら skip + WARNING。

### 差分レポート
- 直後に **`logs/<タイムスタンプ>/DIFF.md`** を出力（CAI スキャン結果と bulk-export terraform の差分）
- リポジトリ直下の `DIFF.md` は最新版への相対 symlink に張り替え（過去実行と並べて比較可能）
- 「CAI で見つかったのに tf に出てこない」リソースをここで気付ける（要手動 / 自動・対象外を区別）

---

## 4. `make mock`（任意・ローカル試走）

**目的**: GCP に触らずにスクリプトのフロー自体を確認する。リファクタや CI、新規メンバーの動作理解用。

- GCP 未接続で `sync_env.py` のフロー全体をシミュレート
- 外部コマンドは実行せずダミーデータ
- **未対応コマンドは fail-closed**（本物実行に進ませない）
- 前提チェック（CLI / SA）はスキップ

---

## 5. `make run`（本番実行）

**目的**: `make plan` で確認した内容を dst にだけ実書き込みする。src には一切触れない。VM は「OS 状態・データごと」復元される。

`make plan` と同じ事前チェック後、dst にのみ書き込み（**番号は Step 番号。上から実行順**で、`4.7` が `4.5` より先に走る）:

3.7. **Artifact Registry**: src の AR イメージを dst へ複製する。**Terraform より前**に実行する（Cloud Run / Cloud Functions が `@sha256:<digest>` で固定参照するため、apply 時に実体が無いと `Image not found` で revision 作成が失敗し tainted で残る）。
   - 転送は **gcrane / crane のみ**（`check_prerequisites` が実行前に必須チェック）。registry→registry 転送で **digest が変わらない**ことが前提条件。docker の pull→push はマルチアーキイメージを単一プラットフォームに落として digest が変わるため使わない。
   - `steps.data_sync.artifact_registry.scope: tagged` で「タグ無し = 置き換えられた過去ビルド」を除外できる。`.tf` が digest 固定で参照しているイメージは scope に関わらず必ず残す。

4. **Terraform apply**: dst host に VPC / subnet / Cloud Router / Cloud NAT を再現
   - 冪等性: dst プロジェクト変更時は `terraform.tfstate` を破棄して import からやり直し（`active/<src>/.dst_project` マーカー判定）
   - `google_storage_bucket` はリネーム後の実名で import して adopt
   - VM/disk は Step 4 では作らず Step 5 で管理（責務分離）
   - **FW rules / Network Firewall Policy は Terraform では作らず Step 4.5 で gcloud 複製**（後述）。`google_compute_firewall` 等を bulk-export 由来の `.tf` に残しておくと Step 4.5 と二重定義になるため、`customize_hcl` 段階で除外している。

4.7. **サーバーレス複製 (`step_serverless_sync`)**: Cloud Run サービス / ジョブと Cloud Functions を、それぞれのサービス自身の仕組みで複製する。**bulk-export は Cloud Run を確実に出力できない**ため Terraform では扱わない（`serverless_tf_skip_reason()` が customize と apply 直前の両方で該当 `.tf` を落とし、二重所有を断つ）。実行順は **Step 4 の後**（SA / VPC / バケットが揃ってから）・**Step 5.7 の前**（Cloud Run の公開設定は Step 5.7 が付ける）。
   - **Cloud Run**: `describe --format=export` → dst 用に書き換え → `replace --dry-run` でサーバ側検証 → `replace`。`replace` は「送った spec が正」＝落としたフィールドが既定値に戻る破壊的 API のため、**未知のフィールドは触らない**。VPC コネクタ / Cloud SQL / Secret Manager 参照 / CMEK / サイドカー / GPU / リビジョン固定を含むものは**複製せず** DIFF の要対応に手順を出す（参照先が src のままの壊れたリソースを作らないため）。
   - **Cloud Functions**: ソース zip を「src 認証でローカルに download → dst 認証で upload」の 2 段で `<dst>-fn-source-migration` バケット（関数と同じリージョンに冪等作成）へ運び、`gcloud functions deploy` で **dst 側で再ビルド**する。HTTP トリガのみ対応。
   - **関数ビルド専用 SA を先に用意する**: deploy のビルドは Cloud Build が実行し、その既定 ID は dst の default compute SA (`<番号>-compute@developer.gserviceaccount.com`) だが、**2024-05-03 以降に作られたプロジェクトは組織ポリシー `constraints/iam.automaticIamGrantsForDefaultServiceAccounts` によりこの SA にロールが 1 つも付かない**。移行先は必ず新規プロジェクトなので毎回該当し、放置すると全関数が `Could not build the function due to a missing permission on the build service account` で失敗する。そこで dst プロジェクト単位で `fn-build@<dst>.iam.gserviceaccount.com` を冪等作成し、公式指定の最小ロール（`roles/logging.logWriter` / `roles/artifactregistry.writer` / `roles/storage.objectViewer`）のうち**不足分だけ**付与して、deploy に `--build-service-account` で明示する。
     - **既定 SA に `roles/cloudbuild.builds.builder` を足す方式は採らない**: 既定 SA は VM / Cloud Run / 全 Function の**実行 ID も兼ねる**ため移行と無関係の権限まで広がり、dst ORG が `cloudbuild.useComputeServiceAccount` を無効化していると付与しても動かない。
     - この SA は関数の設定 (`buildServiceAccount`) に記録されるため、**移行後も残す前提**（削除すると再デプロイが失敗する）。作成・付与した事実は WARNING + `DIFF.md` の「確認」に出る。
     - 自前の SA を使うなら `steps.serverless_sync.build_service_account: <email>`（作成・権限付与はツールでは行わない）、自動用意を止めるなら `grant_build_service_account: false`。用意できなかった場合は移行を止めず DIFF の要対応 + 手動コマンドに落とす。
     - 付与直後は IAM 伝播が間に合わないことがあるため、待機してから deploy し、**そのプロジェクトの deploy だけ 1 回再試行**する。
   - **ソース受け渡しバケットの作成は直列化**: 関数の複製は関数単位で並列なので、同一 dst で同時に作成すると片方が `409 ... you already own it` で落ちる。lock で直列化し、作成失敗時は実在を再確認して存在すれば成功として扱う。

4.5. **Network Firewall (`step_network_firewall`)**: Terraform で表現しきれない FW を `gcloud` で冪等複製する独立フェーズ。実行順は **Step 4.7 (serverless_sync) の直後・Step 5 (gce_restore) の前**。
   - **冒頭で `_replicate_host_networks()` を呼び、dst host の Shared VPC ネットワーク (例: `shared-vpc`) と subnet を src host と同型に複製**する。FW rule / FW policy association は `--network=<NAME>` を要求するため、これが無いと `Could not fetch resource: 'projects/<dst_host>/global/networks/<name>' was not found` で全 FW 操作が失敗する。冪等 (`_gcloud_exists` ガード) で、Step 5 から再度呼ばれても describe のみ。
   - 防御的に `_sync_classic_firewall_rules` は参照される dst network を一括 pre-flight チェック、`_sync_fw_policy_associations` は assoc 単位で existence チェックし、未存在の network を参照する rule/assoc は cryptic な API エラーを量産せず skip + WARNING に倒す。
   - `network-firewall-policies` のサブコマンドごとに scope flag が異なる: `list`=`--regions=`（複数形）/ `describe`・`create`=`--global`・`--region=`（ポリシー本体）/ `rules ...`・`associations create`=`--global-firewall-policy`・`--firewall-policy-region=`。誤ると `unrecognized arguments`。`fw_rule_scope_flag()` で変換する。
   - `fw_policy_rule_flags()` は REST API の FirewallPolicyRule 全フィールドに対応する。INGRESS ルールは `srcIpRanges / srcThreatIntelligences / srcAddressGroups / srcFqdns / srcSecureTags / srcRegionCodes / srcNetworkScope` のいずれかが必須（gcloud 仕様）。欠落すると `Must specify src_... for ingress direction` / `Could not fetch resource:` で失敗する。
   - **Secure tag**（`tagValues/<数値ID>`）は ORG スコープの permanent ID で別 ORG には存在しない。そのまま渡すと `rules create` が `Could not fetch resource:` で失敗する。`config steps.network_firewall.secure_tag_map` に src→dst の tagValues を登録すると変換して複製。未登録タグを参照するルールは FW を意図せず緩めないようエラーにせずスキップし WARNING を出す。

5. **gce_restore**: 期限内スナップショットから dst にディスクを復元 → boot disk を差し替え → **どの VM も一旦 RUNNING で残す**（OS 状態・データごと復元）
   - 並列化: `_replicate_host_networks()` の後、(project, vm) のフラット work unit に展開し VM 単位で並列復元（`parallel_jobs=8` 推奨）。VM 内の操作チェーン (stop → detach → delete → create disk → attach → start → secondary disks) は依存があるため直列。
   - snapshot 未検出時の挙動: 並列モードで `sys.exit(1)` すると他 VM の進行を巻き添えで止めるため、`stats.failed` に記録して return する（最終的に `main()` で exit 1）。

5.5. **電源状態反映 (`_finalize_vm_power_states`)**: 全 VM の復元完了後に、src と同じ電源状態 (`TERMINATED` / `SUSPENDED`) に揃える独立フェーズ。
   - **なぜ分離するか**: GCE suspend は guest OS が **ACPI S3 シグナルに 3 分以内に応答** する必要があり、boot 直後の VM では失敗しやすい。Step 5 の VM 復元ループの中で個別に suspend すると `Suspending instance(s) <name>....failed` が頻発する。
   - **待機**: `config.steps.gce_restore.power_state_wait_seconds` (既定 120 秒) だけ sleep してから状態反映を開始。`make plan` / `make mock` ではスキップ。
   - **TERMINATED 目標**: `gcloud compute instances stop --quiet` を `allow_fail=True` で発行（ACPI 失敗時は forceful fallback があるため通常成功）。
   - **SUSPENDED 目標**: `_try_dst_suspend` が `subprocess` を直接呼び、失敗しても `stats.failed` を増やさない（run 全体の exit code に影響させない）。失敗時は WARNING + 手動復旧コマンド (`gcloud compute instances suspend <name> --zone=<zone> --project=<dst>`) を案内するだけ。
   - **transient / 未対応 OS**: suspend 非対応構成（GPU/TPU 付き、Confidential VM、メモリ 208GB 超、CSEK 付きディスク、未設定の Debian 8/9/Windows）は WARNING のみで RUNNING のまま残る。
   - **並列**: pending リストを `_parallel_for_each` で `parallel_jobs` 並列実行。

5.7. **IAM 複製 (`step_iam_sync`)**: src 各プロジェクトの **project IAM ポリシー**を dst の同名 SA へ複製する（キー未指定でも有効）。dst SA が無ければ空 SA を冪等作成してから付与する。付与は **dst プロジェクト単位で並列 / プロジェクト内は直列**（`add-iam-policy-binding` は read-modify-write なので同一プロジェクトの並列実行は etag 競合になる）。
   - **Cloud Run の公開設定もここで複製する**: 「未認証アクセスを許可」= サービス個別の `allUsers → roles/run.invoker` は bulk-export にも project IAM にも現れず、放置すると src で公開のサービスが dst で認証必須になる。Step 4.7 でサービスが出来ているので **1 回の `make run` で公開設定まで入る**。付与内容は末尾に WARNING で一覧 + 取消コマンドを出す（公開 = インターネット開放のため必ず見せる）。
   - **複製しないもの**（いずれも dst の権限が緩む方向には作用しない）: 条件付きバインディング / ORG カスタムロール / `project_mapping` 外のプロジェクトの SA・カスタムロール / Google 管理 service agent / SA 自身の IAM / バケット・データセット等のリソース単位バインディング。
   - **`roles/owner` 等の超高権限ロールも src と同じなら複製する**（忠実再現が既定）。付与した場合は実行ログ末尾に「何を・どこに・なぜ・取消コマンド」を WARNING でまとめる。
   - **既定ランタイム SA（default compute / appspot）は複製しないが、差分を `DIFF.md` に出す**: これらはプロジェクトごとに ID が変わるため「dst にも同等物が既定で存在する」前提で除外している。しかし上と同じ組織ポリシーにより **dst の既定 SA には `roles/editor` すら付かない**ので、「存在は同等・権限は同等でない」状態になる。自動付与はせず（別 ORG に `roles/editor` を勝手に生やさないため）、**src の既定 SA が持っていたロールのうち dst に無いもの**を付与コマンド付きの「確認」として出す。既定 SA で動く VM / Cloud Run / Cloud Functions がある場合は必ず確認すること。
   - dst 側に `roles/resourcemanager.projectIamAdmin` が必要（`bootstrap_dst_sa.sh` が付与）。権限が無い場合はエラーにせず skip し、手動用の `add-iam-policy-binding` を案内する。

6. **data_sync**:
   - GCS: リネーム後バケットへ `gcloud storage rsync` で同期
   - BigQuery: src の location を継承してデータセット作成 → コピー
7. **VPC Service Controls**: 全データ移行の **最後** に、dst プロジェクト（番号）を既存ペリメタへ `--add-resources` で追記する（org / access policy 自体は触らない・冪等）。先に封じ込めると後続操作が境界で弾かれるため最後に実行する。
   - **`steps.vpc_sc.billing_project` は必須・明示指定**。`gcloud access-context-manager perimeters describe/update` は org/policy スコープで `--project` を持たないため、quota project を明示しないとローカル `gcloud config` の `core/project`（移行と無関係なプロジェクト）が quota に使われ、そこで API 無効 → `accesscontextmanager.googleapis.com ... SERVICE_DISABLED` で失敗する。
   - **誤ったプロジェクトを自動推測しない**安全方針: `access_policy` / `perimeter` / `billing_project` のいずれかが未設定なら「設定不足」として skip + WARNING（host dst や先頭 dst へ勝手にフォールバックしない）。`billing_project` には dst ORG 内で API を有効化できるプロジェクト（通常は dst ホスト）を明示する。
   - ステップ冒頭で `billing_project` に `accesscontextmanager` API を有効化（冪等 / allow_fail）してから describe/update を `--billing-project=<billing_project>` 付きで実行する。describe には `--quiet` を付け、API 無効時の対話プロンプトでハングしないようにする。

> 🔁 **State 温存と `bulk_export.skip_on_run: true` の挙動**
> - `make plan` は `clean` 依存を撤去済みで、`terraform/active/<src>/` と state / `.dst_project` マーカー / `.gcs_rename_value` を温存する。同一 dst project id なら次の `make run` は skip_on_run の高速パスと冪等 apply にそのまま乗る。
> - `terraform/active/<src>/.dst_project` マーカーが現 config の dst と一致するかで判定。一致すれば export と customize を**完全スキップ**、不一致でも `terraform/raw/` が残っていれば customize のみ再実行（bulk-export 自体は省略）。
> - マーカーは `customize_hcl` 末尾と Step 4 の `_reset_stale_state_if_needed` の両方が書く（plan/run・skip_on_run 間で整合）。dry_run では `customize_hcl` が `.tf` を実書き出ししないためマーカーも更新しない（plan で書き出すと .tf と marker が乖離するため）。
> - bulk-export は `raw/<src>/` を **プロジェクト単位で作り直す**（`make plan` の raw 全消しを撤去した後も src で削除済みリソースの `.tf` が残らないよう防御）。他プロジェクトの raw / active / state には影響しない。
> - 特定プロジェクトの state だけ明示的にリセットしたい場合は **`make clean-project P=<project_id>`**（src ID / dst ID / marker 値のいずれでも解決、他プロジェクトと `.gcs_rename_value` は温存、未知 ID 混在時は何も消さず exit 1）。config から削除済みの旧 dst の掃除もマーカー値で解決できる。
> - **`host_project.skip: true`** 指定時、`active/<src_host>/` はマーカー更新も孤児削除もされず丸ごと温存される（既に構築済みの dst host に対する不要な再 apply を回避）。

---

## 6. 事後

**目的**: 何が起きたかを後から追えるようにする。失敗時の原因切り分けもここを起点に行う。

- `logs/<timestamp>/{org,dst}.log` / `logs/<timestamp>/DIFF.md` をレビュー
  - ステップ単位 `━━━━` 区切り、`✓/+/−/✗` 記号、スレッドタグ `[main]` / `[cai-scan_0]`
  - `verbose_logging: true` で生コマンド + STDOUT を DEBUG レベルで記録
- **`DIFF.md` は「要対応」→「確認」→ 参考の順に読む**。冒頭の要対応テーブルが「放置すると実害が出るもの」、続く注記セクションがツール側の補正・スキップの記録で、**手を動かす必要があるものはすべてここに出る**（各ステップが `.customize_notes.json` / `.serverless_notes.json` / `.iam_notes.json` に書いたものを集約している）。
  - 今回の run で **新しく dst の権限を増やした / 増やさなかった**判断も「確認」に出る。代表例:
    - 関数ビルド専用 SA `fn-build@<dst>` の作成とロール付与（残す前提。不要なら取消コマンドあり）
    - 既定ランタイム SA（default compute / appspot）に **付けなかった**ロールの一覧と付与コマンド
    - 平文の環境変数に入っていた秘匿情報らしき値（src と同じ値で複製済み。差し替え / ローテーションの判断用）
    - `deletion_protection = false` の補完（本番切替時に戻す）
- リポジトリ直下の `DIFF.md` は最新実行の `logs/<timestamp>/DIFF.md` への相対 symlink（`cat DIFF.md` で常に最新版）。実体は日付付きで残るため、過去実行と並べて差分を比較できる。
- `logs/` は `.gitignore` 配下、`/DIFF.md`（symlink）も `.gitignore` に登録済み（fresh clone で dangling になるため）。

---

## 7. `make delete-projects-plan PATTERN=...` → `make delete-projects PATTERN=...`（任意・クリーンアップ）

**目的**: 試行錯誤で作った dst プロジェクト群を一括で片付ける。src は folder スコープ外なので対象に上がらない。

### 削除対象の決定（folder スコープ列挙）
- 母集団は **`bootstrap.folder_id` 配下の ACTIVE プロジェクト**。`gcloud projects list --filter="parent.id=<folder_id> parent.type=folder lifecycleState:ACTIVE"` で実機列挙する。
- `folder_id` が config に無い場合は起動時に **fail-fast**（org root 全体を対象にしない安全方針）。`ARGS="--folder-id <id>"` で一時上書き可。
- **config (`project_mapping.*.dst`) は kind(host/svc) と src の cross-reference 用にだけ使う**。母集団ではない。これにより `dst/config.yaml` を別の dst に書き換えた後でも、folder に残っている過去の dst を削除できる（旧仕様の regression: config 改変前の dst が消せなくなる問題への対策）。
- `PATTERN` (3 文字以上必須) で folder 列挙結果を project_id 部分一致で絞り込む。
- **PATTERN にマッチしないプロジェクトは出力しない**（folder 内の他用途プロジェクトをスキップ理由付きで列挙する仕様は廃止。出力ノイズ抑制のため。0 件時は「削除対象は 0 件です」の 1 行のみ）。
- 状態フィルタは gcloud 側 (`lifecycleState:ACTIVE`) + defense-in-depth でクライアント側でも再チェック。

### 多重安全策
1. **folder スコープ必須**: `folder_id` 未設定なら起動時 exit 2。org root 全体や任意プロジェクトを対象にできない構造。
2. **`PATTERN` 必須・3 文字未満は拒否**（make ターゲット側でも未指定なら即エラー）。
3. **削除前に一覧テーブル**を出力: `# / kind(host|svc|-) / project_id (dst) / name / state / lien 数 / src project / in_cfg(yes|no)` を桁揃え。`in_cfg=no` は「folder にあるが config に未登録」(過去の dst 等) の明示マーカー。
4. **6 桁ランダムコード**を端末に表示。**標準入力で一致するまで削除は実行されない**（一致しなければ exit 1 で中止）。
5. lien (`compute.googleapis.com/projects-delete-prevented` 等) が付いていれば `gcloud alpha resource-manager liens delete` で先に解除してから `gcloud projects delete --quiet`。
6. 既定は `--dry-run`（make ターゲット側で `--no-dry-run` を付与）。`delete-projects-plan` は dry-run 固定で表示のみ。
7. 並列度は `global.parallel_jobs`（既定 8）。worker 内で `sys.exit` せず、`threading.Lock` で success/fail カウンタを保護（他 worker を巻き添えで止めない）。

### ログ
- `logs/<timestamp>_delete-projects/dst.log` に独立出力（`create-projects` と同じ書式）
- サマリ: `削除済 / lien 解除 / 失敗` 件数 + ログパス。1 件でも失敗で exit 1

---

## 8. `make vmware-*`（任意・VMware VMDK → GCE インポート）

**目的**: VMware からエクスポートした VMDK を **Migrate to VMs API** でカスタムイメージ化し、指定構成の GCE インスタンスとして起動する。本体 (`dst/`) パイプラインとは独立した別ワークフローで、`vmware/config.yaml` を Single Source of Truth とする。`sync_env.py` を経由しないため `dst/config.yaml` / ORG 保護ガード (`is_src_read_only`) / SA 事前チェックは **適用されない**。

### 8a. 事前準備（手動）
- 対象 VMDK を GCS バケットへ配置（`source.disks[]` の `gcs_uri` で参照）
- `gcloud auth login` 済み、対象 project に Compute / Storage / Migration / IAM 権限を持つアカウントで実行
- `cp vmware/config.yaml.template vmware/config.yaml` してから以下を埋める:
  - `global`: 出力先 project_id / region / zone / dry_run / ログ設定
  - `source.disks[]`: VMDK の GCS URI（`boot: true` を 1 本、`boot: false` をデータディスクとして複数可）
  - `image_import`: image 名 prefix、ライセンスタイプ（任意）、Migration host/target project 分離（任意）
  - `instance`: machine_type / SA / labels / tags / metadata
  - `network`: VPC / subnetwork（Shared VPC は `host_project` 指定）、内部 IP（予約 address 名 or 直接）、外部 IP 有無
- 設定ファイル切替は `VMWARE_CONFIG=vmware/other.yaml`（例: `make vmware-setup-apply VMWARE_CONFIG=vmware/prod.yaml`）

### 8b. `make vmware-setup` → `make vmware-setup-apply`
**役割**: VMware import に必要な API・SA 権限・IP 予約を整える。冪等（既存リソースは describe のみ）。
- 必要 API の有効化: `compute` / `storage` / `vmmigration` / `iam`
- Migrate to VMs の TargetProject 登録
- vmmigration SA に source bucket への `roles/storage.objectViewer` を付与
- 内部固定 IP / 外部 static IP の予約

### 8c. `make vmware-import` → `make vmware-import-apply`
**役割**: VMDK をカスタムイメージ化する（非同期投入）。
- `source.disks[]` の各 VMDK について `gcloud migration vms image-imports create` を発行
  - `boot: true` は OS イメージ（OS 適応あり）
  - `boot: false` は `--skip-os-adaptation` 付きでデータディスクとして
- 完了確認は `gcloud migration vms image-imports describe` をポーリング（長時間化することあり）

### 8d. `make vmware-start` → `make vmware-start-apply`
**役割**: 出来上がったカスタムイメージから GCE インスタンスを起動する。
- boot image から `gcloud compute instances create`（machine_type / SA / labels / tags / 内部 static IP / 外部 IP は config に従う）
- データディスクがあれば対応イメージから `gcloud compute disks create` → `gcloud compute instances attach-disk`

### 8e. `make vmware-all` / `make vmware-all-apply`
**役割**: 8b → 8c → 8d を一気通貫で実行する。`-apply` の有無は個別ターゲットと同じ（`-apply` なし = dry-run、あり = `--apply`）。

### 8f. `make vmware-clean`
**役割**: `vmware/logs/` を削除（試行錯誤後のクリーンアップ用）。dry-run / apply の区別なし。

### 8g. `dst/` パイプラインとの関係
- VMware VMDK の出力先 project は `vmware/config.yaml` の `global.project_id` で完全独立に指定する。dst host / svc と同じ project を指定することも可能だが、リソース名やサブネット衝突に注意。
- `image_import.target_project_host` / `target_project_name` で Migration host project と target project を分離可能（省略時は `global.project_id` を使用）。
