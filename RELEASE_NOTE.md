# RELEASE_NOTE

このファイルは `copy-all-env` の変更のうち、**利用者（顧客エンジニア）が把握しておくべき内容**を
日付の新しい順に記録します。開発者向けの詳細な変更履歴は `HISTORY.md` を参照してください。

---

## 2026-08-16 — 実行フローの図解を追加

`architecture/` に実行フロー図を追加しました。`make run` が Step 0 から Step 99 まで
**何を判定して、どこで止まり・どこで複製をやめ・どこで警告だけ出すか**を 6 枚のフロー図で
確認できます。同じ内容を 2 形式で置いています。

- `architecture/sync-env-flow.md` … GitHub 上でそのまま図が表示されます。
- `architecture/sync-env-flow.html` … ブラウザで開く用。

- 起動前ガード（設定不備 / 二重起動 / `gcrane` 不在 / コピー先未作成 / コピー元への書込権限）の
  停止条件を図示しています。トラブル時にどのチェックで止まったか当たりを付けられます。
- 「失敗の扱い」一覧で、**実行を止める失敗**と**警告だけ出して先へ進む失敗**の区別が分かります。
- 「ツールが運ばないもの」一覧に、PV データ / Secret の値 / self-managed SSL 証明書など
  **手動移行が前提のもの**をまとめています。
- HTML 版は単体で完結しているため、オフラインでもそのまま開けます（追加インストール不要）。

---

## 2026-08-16 — GKE Gateway が作る LB を複製対象外に（apply 404 の修正）

GKE Gateway コントローラが自動生成する LB 一式（`gkegw1-*` の backend service /
URL map / target proxy と `k8s1-*` の NEG 等）を Terraform 複製の対象外にしました。

これまでは参照先の health check（`gkegw1-*`）だけが除外され、残った backend service が
`healthChecks/gkegw1-... was not found` の 404 で **`make run` が毎回失敗**していました。

- コピー先ではワークロード復元（Backup for GKE）後に Gateway コントローラが LB を
  再作成するため、複製しなくても機能は失われません。
- DIFF.md に Gateway ごとの「確認」注記を出します:
  **① LB の IP は新規払い出しになるため DNS の切替が必要**
  **② Certificate Manager の certificate map / SSL 証明書はクラスタ外リソースで
  複製されないため、コピー先に別途用意が必要**
- 利用者が自分で作った LB（名前が `gke-`/`k8s-` で始まるだけのもの含む）は
  従来どおり複製されます。
- **`SKIP_ON_RUN=1`（既存の `terraform/active` を再利用する実行）でも除外が効きます**。
  除外は適用直前にも実行されるため、コピー元の再エクスポートは不要です
  （前回作成された `k8s1-*` NEG は terraform が自動削除します）。

あわせて「**Backup for GKE の restore で再生成されるものはコピーしない**」方針で
複製対象を総点検し、次も対象外にしました:

- **`mcrt-<uuid>` の Google-managed 証明書**（GKE ManagedCertificate コントローラの
  発行物。restore 後に dst で発行し直されます。利用者作成の managed 証明書は
  従来どおり複製されます）
- **`gkegw1-*` の firewall ルール**（Gateway コントローラ生成。restore 後に再作成）
- **Backup for GKE の backup plan / restore plan**（DIFF.md の手順で手動作成する
  移行用リソースのため、src にあっても複製しません）
- **Cloud DNS for GKE のクラスタゾーン**（`gke-<クラスタ>-<ハッシュ>-dns` / `-rp`。
  コピー先クラスタが自分のハッシュで作り直します）
- `pvc-<uuid>` の PV ディスクは従来から複製対象外です（実データは Backup for GKE の
  volume restore がコピー先に新規ディスクとして作成します）

注意: **Filestore の PV データは Backup for GKE の volume backup 対象外**（PD のみ）
です。Filestore を使っている場合のデータ移行は手動になります。

---

## 2026-08-16 — Artifact Registry のイメージ複製を短縮

イメージ複製（Step 3.7）に時間がかかる場合の短縮手段を 2 つ用意しました。
**どちらもコピー先の動作に必要なイメージは落としません。**

### 1. `gcrane` が必須になりました（**要対応**）

イメージ複製に **`gcrane`（または `crane`）が必要**になり、無い場合は
**実行前チェックがエラーで停止**します（`make plan` でも検出）。

```bash
go install github.com/google/go-containerregistry/cmd/gcrane@latest
# バイナリ配布: https://github.com/google/go-containerregistry/releases
```

`make run` を実行するマシンに入れてください。イメージ複製自体が不要な場合は
`steps.data_sync.artifact_registry.enabled: false` にすればチェックされません。

**`docker` は代替になりません**（従来のフォールバックは廃止しました）。

| | docker | gcrane / crane |
|---|---|---|
| 転送 | pull で全レイヤをローカルに落としてから push | レジストリ間で直接転送 |
| コピー先に既にあるレイヤ | 再送する | 再送しない |
| マルチアーキイメージ | **digest が変わることがある** | digest を保つ |

**docker で digest が変わると実害が 2 つ出ます。** Cloud Run の `@sha256:` 固定参照が
解決できなくなるのと、再実行時に「コピー先に既にある」と判定されず**毎回同じイメージを
再送し続ける**ことです。実際に一部のイメージで発生していたため、docker 経路は廃止しました。

### 2. `scope: tagged` で過去ビルドを除外できます（任意）

```yaml
steps:
  data_sync:
    artifact_registry:
      scope: tagged     # 既定は all（従来どおり全 digest を複製）
```

Cloud Build は push のたびに tag を新しいイメージへ移すため、**tag の無い digest は
新しいビルドに置き換えられた過去ビルド**です。実測では **87 件中 64 件（74%）** が該当しました。

- **`.tf` が digest 固定で参照しているイメージは、tag が無くても必ず複製します**。
  そのため `tagged` にしても Terraform の適用が `Image ... not found` で失敗することはありません。
- 除外した件数はログに出ます。
- **既定は `all`（従来どおり）です。** `tagged` にすると、Cloud Run で過去リビジョンへ戻す操作と、
  GKE ワークロードが tag 無し digest を固定参照している場合の復元ができなくなります。
  該当する場合は `all` のままにしてください。
- 綴り誤り（`scope: tag` 等）は実行前にエラーで停止します。

---

## 2026-08-15 — 予約 IP の扱いを修正 / 複製できないリソースを整理（`make run` 失敗 0 件を達成）

`make run` を最後まで失敗なく完走させるための修正です。

### 修正
- **内部予約 IP を「元の IP のまま」複製するようにしました**。従来は外部・内部を問わず
  IP 指定を外して自動採番にしていたため、**サブネットの最若 IP（`10.100.1.2` 等）を
  先に確保してしまい、VM 復元が「IP は既に使用中 / 別プロジェクトが予約済み」で
  失敗**していました。
  - サブネットは同じ CIDR でコピーされるので元の IP はそのまま有効です。
    そもそも予約の意味は「その IP を押さえること」なので、値を変えた複製は
    取り置きとして機能しません。
  - **外部 IP は従来どおり自動採番**です（Google 採番のグローバル資源のため
    コピー元と同じ値は確保できません）。
- **コピー元で使用中（IN_USE）の内部 IP は Terraform から複製しないようにしました**。
  これらは Step 5（VM 復元）が VM と同じプロジェクトに `mig-<VM名>-<IP>` として
  予約し直すため、Terraform 側でも作ると二重予約になります。特に共有 VPC では
  **ホスト側の予約がサービスプロジェクトの VM 作成をブロック**していました。

### 仕様変更（要確認）
- **public な Cloud DNS ゾーンはコピー対象外になりました**。ドメインはグローバルに
  一意で、同じドメインのゾーンを別プロジェクトに作成できない場合があります
  （`The domain '...' may be reserved or registered already`）。
  作成できても NS 委任はコピー元を向いたままで機能しません。
  DIFF.md の**要対応**にゾーン作成と NS 委任切り替えの手順を掲載します
  （private ゾーンは従来どおりコピーされます）。
- **ドット入り（ドメイン形式）の GCS バケットはコピー対象外になりました**
  （`us.artifacts.<project>.appspot.com` など）。ドメイン検証済み TLD 配下でないと
  作成できず、`*.appspot.com` は Google 管理のシステムバケットのため複製自体が
  不可能です。データが必要な場合は `rename_rules.gcs.overrides` にドット無しの
  コピー先名を指定してください（DIFF.md に手順を掲載します）。

---

## 2026-08-15 — 大規模プロジェクトの bulk-export タイムアウト対策

リソース数の多いプロジェクトで
`Error executing export:: ... error waiting for operation:`（理由が空）が発生し、
**30 分 × 3 回の再試行で 90 分を浪費**していた問題への対策です。
これは Google 側 `config-connector` の内部タイムアウトで、
**延長するオプションは存在しません**（gcloud / config-connector の両方に無し）。
そのため「エクスポートする量を減らす」方向で対策しました。

### 新機能
- **`steps.bulk_export.export_resource_types: "auto"`（推奨）**: コピー元が対応する
  リソース種別を自動判定し、それだけを対象にエクスポートします。
  ```yaml
  steps:
    bulk_export:
      export_resource_types: "auto"
  ```
  - **クラスタ内の k8s オブジェクトが自動的に対象外**になります。
    実測でコピー元 `my-argolis` は Cloud Asset Inventory の 1,480 件中
    **908 件（61%）が k8s オブジェクト**で、これがエクスポートを重くしていました。
    k8s オブジェクトは Backup for GKE の担当なので、**移行範囲は変わりません**。
  - 種別を明示指定した場合、gcloud は内部的に別経路を使うため
    **タイムアウトの原因である「30 分待ちの操作」自体が発生しません**。
  - 種別一覧を取得できなかった場合は、絞り込みなしで続行します
    （移行範囲を勝手に狭めない安全側）。
- **`steps.bulk_export.export_resource_types` に明示リストも指定できます**。
  ```yaml
  steps:
    bulk_export:
      export_resource_types: ["ComputeInstance", "ComputeNetwork", "ContainerCluster"]
  ```
  指定できる Kind は `gcloud beta resource-config list-resource-types --project=<コピー元>`
  で確認できます（`ComputeInstance` のような**大文字始まり**）。
  - ⚠️ 既存の `resource_types`（`google_compute_*` = **Terraform 型**）とは**別の設定**です。
    取り違えると「何も一致せず全除外」という静かな事故になるため、
    実行前にエラーで停止するようにしました。
- **`steps.bulk_export.storage_path`**: CAI エクスポートに使う GCS バケットを指定します
  （省略時は毎回一時バケットを自動作成）。`export_resource_types` とは併用できません
  （gcloud の仕様。両方指定した場合は種別絞り込みを優先します）。
  - ⚠️ これは**書き出し先の指定であり、キャッシュではありません**。
    前回のエクスポート結果が再利用されることはなく、実行のたびに新しい
    エクスポートが走るため**タイムアウト対策にはなりません**
    （一時バケットの作成を省く用途です）。タイムアウト対策には
    `export_resource_types: "auto"` を使ってください。
  ```yaml
  steps:
    bulk_export:
      storage_path: "gs://my-bucket/prefix"
  ```

### 改善
- **再試行の既定を「3 回・5〜30 秒間隔」から「2 回・180 秒間隔」に変更**しました。
  タイムアウト起因の失敗に短い間隔で再試行しても同じ待ち時間を繰り返すだけのためです。
  `steps.bulk_export.retries` / `retry_wait_seconds` で調整できます。
- **失敗ログに経過秒数を表示**するようにしました。従来は理由が空で
  「30 分待った末のタイムアウト」だと分からなかったためです。

> 💡 **k8s オブジェクトについて**: bulk-export はもともと GCP リソースのみを対象とし、
> クラスタ内の k8s オブジェクト（Pod / Deployment 等）は含みません。
> それらは Backup for GKE のバックアップ／リストアが担当するため、
> 種別を絞り込んでも移行範囲は変わりません。

---

## 2026-08-14 — 「30 分走ってから失敗」を防ぐ実行前チェックを 2 つ追加

### 新機能
- **コピー先プロジェクトの実在チェック（fail-fast）**: config の dst を新しい ID に
  変えたまま `make projects` を忘れると、これまでは **30 分走った末に**
  `The resource 'projects/<dst>' was not found` で全滅していました。
  今後は `make plan` / `make run` の開始直後に全コピー先を確認し、
  存在しない場合は何も書き込まずに停止して `make projects` を案内します。
  - プロジェクト作成直後は権限反映に数分かかることがあり、その間もこのチェックで
    停止します（少し待ってから再実行してください）。
- **多重起動ガード**: 同じ作業ディレクトリで `make run` / `make plan` を
  二重に起動すると、Terraform の state が相互破壊されていました
  （`Error acquiring the state lock` / `Saved plan is stale`）。
  2 つ目の起動は即座にエラーで停止するようになりました。
  先行プロセスが（異常終了含め）終われば自動的に解除されます。

---

## 2026-08-14 — DIFF.md の要対応を 101 件 → 23 件に削減（誤検知・自動生成の分類）

### 改善
- **DIFF.md の「要対応」から、実際には手を動かす必要のないものを除去しました**
  （本環境の実測で 101 件 → 23 件）。要対応に残るのは本当に手動対応が必要なものだけです。
  - **誤検知の修正**: エクスポート済みの Artifact Registry リポジトリ等が
    「bulk-export が出力しなかった」と誤報告されていた照合バグを修正
    （`name` を持たない型は `repository_id` 等の ID 属性で照合）。
  - **自動生成物を「参考」へ**: Dataplex のシステムカタログ（`@bigquery` 等 25 件）、
    GKE/PSC が自動登録する Service Directory エントリ、GKE Pod range の
    InternalRange、Security Command Center の設定オブジェクトなど。
  - **二重計上を集約**: gen2 Cloud Functions（実体は Cloud Run）は Cloud Run 側の行へ、
    SecretVersion は Secret 本体へ、Cloud DNS ゾーンの数値 ID 表現は名前行へ集約。
  - **未登録 assetType の警告をゼロに**: 全 40 種をカバレッジ登録しました。
- 要対応に残る代表例（= 本当に手動対応が必要なもの）:
  Secret の値の投入（6 件・秘密情報はツールで自動転記しません）、
  us-central1 の Cloud Run サービス 2 件のデプロイ、Cloud Functions(gen1)、
  ログベース指標、Cloud Build トリガー、Firestore データベース、組織ポリシー等。
- 次回の `make plan` / `make run` から新しい分類で DIFF.md が出力されます。

---

## 2026-08-14 — GKE ノードプールの構成引き継ぎを明確化・修正

### 仕様の明確化
- **ノードプールの構成はコピー元からそのまま引き継がれます**。
  ノード台数（`node_count` / `initial_node_count`）・マシンタイプ・ディスク種別/サイズ・
  配置ゾーン（`node_locations`）・自動修復/自動アップグレード・アップグレード戦略・
  `max_pods_per_node` などがコピー元と同一の値で複製され、
  コピー先クラスタが**同じ構成のノードを作り直します**。
  - 引き継がないのは**ノードの GCE VM 実体だけ**です（スナップショットからの復元はしません）。
    ノードは使い捨てなので、GKE が作り直したものと同等になります。
  - これまで README の「ノードの GCE VM はコピー対象外」という記述が
    「ノード構成が引き継がれない」と読める表現だったため、明確化しました。

### 修正
- **ノードプールの Pod IP レンジ指定が二重になる問題を修正しました**。
  エクスポート結果には「既存レンジの名前参照（`pod_range`）」と
  「新規レンジ作成用の CIDR（`pod_ipv4_cidr_block`）」の両方が含まれますが、
  CIDR 側は新規レンジを作る場合にのみ有効な項目です。
  Pod 用レンジはサブネットごとコピー先に複製されるため、名前参照を残して
  CIDR 指定を除去します。

---

## 2026-08-14 — Backup for GKE で復元できるコピー先クラスタを作るように

GKE をコピーしたあと **Backup for GKE でワークロード・PV を復元する**運用に合わせて、
3 点を修正しました。

### 修正
- **コピー先クラスタで Backup for GKE エージェントを必ず有効化するようにしました**。
  従来はコピー元の設定（多くの場合 `無効`）をそのまま複製していたため、
  **復元できないクラスタが出来上がって**いました。エージェントは復元先クラスタの
  必須要件です（`addonsConfig.gkeBackupAgentConfig.enabled: true`）。
  - コピー元の有効化は本ツールの対象外（コピー元は読み取り専用）なので、
    DIFF.md の手順に沿って手動で実行してください。
- **DIFF.md の GKE 移行手順を、別プロジェクトへの移行に対応した内容に修正しました**。
  Backup for GKE は既定では**同一プロジェクト内でしか復元できません**
  （復元プランは別プロジェクトのバックアッププランを参照できない）。
  本ツールの用途は別プロジェクトへの移行なので、手順を
  **backup channel / restore channel + サービスエージェント権限**を含む形に
  差し替えました。従来の手順（backup-plans → restore-plans のみ）は
  そのままでは動作しません。
- **GKE クラスタが VPC より先に作られて失敗する可能性を修正しました**。
  クラスタの `network` / `subnetwork` は短縮パス形式（`projects/.../networks/...`）で
  出力されるため、作成順序が保証されていませんでした。Terraform 参照に変換しています。

---

## 2026-08-14 — GKE クラスタ作成エラーを修正 / 移行対象リソースを選べるように

### 修正
- **GKE クラスタの作成が
  `Cluster.initial_node_count must be greater than zero` で失敗する問題を修正しました。**
  ノードプールを別リソースとして管理する構成では、クラスタ側に
  `initial_node_count` と `remove_default_node_pool` の指定が必要ですが、
  エクスポート結果には含まれていませんでした。Terraform 公式の定石どおり
  「最小の既定プールを作って即削除する」形に自動補完します
  （Autopilot クラスタと、クラスタ内にノードプール定義を持つ構成は対象外）。
- 併せて、同じ構成で続けて発生する 2 つの問題も修正しました:
  - ノードプールがクラスタより先に作られて失敗する問題
    （`cluster = "名前"` の文字列参照を Terraform 参照に変換）
  - ノードプールのバージョン固定によるマスター版との不整合
    （バージョン指定を外し、クラスタのリリースチャンネルに追従）

### 新機能
- **移行対象のリソース種別を選べるようになりました**（`steps.bulk_export.resource_types`）。
  「GCE と VPC だけ移行し、Cloud Run や GKE は移さない」といった運用ができます。
  ```yaml
  steps:
    bulk_export:
      resource_types:
        exclude: ["google_cloud_run_*", "google_container_*"]   # 除外指定
        # include: ["google_compute_*"]                          # 許可リスト指定
  ```
  - 未指定なら**従来どおり全量コピー**です。
  - `exclude` が `include` より優先されます。パターンは `google_compute_*` のような
    Terraform リソース型のワイルドカードです。
  - 除外した種別は DIFF.md でも「参考（設定どおり）」に分類され、
    要対応リストに混ざりません。
  - `google_` で始まらないパターンは実行前にエラーで停止します
    （`compute_*` のような書き間違いは「全部除外」という静かな事故になるため）。

---

## 2026-08-14 — Cloud Run の公開設定を複製 / 未コピーの Run サービスを DIFF に掲載

### 修正
- **コピー元で「未認証アクセスを許可」の Cloud Run サービスが、コピー先では
  認証必須になっていた問題を修正しました**。公開設定はサービス個別の IAM
  （`allUsers → roles/run.invoker`）で、Terraform エクスポートにも
  プロジェクト IAM 複製にも含まれていませんでした。
  - コピー元と同じ公開設定（`allUsers` / `allAuthenticatedUsers` の invoker のみ）を
    自動複製します。**公開＝インターネット開放のため、付与した一覧と取消コマンドを
    実行ログ末尾に警告としてまとめて表示します**。必ずレビューしてください。
  - コピー先にサービスがまだ無い場合はスキップし、作成後の再実行で付与されます。
- **一部の Cloud Run サービスがコピーされない問題を DIFF.md で検出できるようにしました**
  （例: us-central1 の `www-1` / `test-1`。Terraform エクスポートがリージョンによって
  取りこぼすため）。DIFF.md の**要対応**に「bulk-export が出力しなかった」として
  掲載されるので、必要なら手動でデプロイしてください（コンテナイメージは
  Step 3.7 でコピー済みです）。

---

## 2026-08-14 — GKE の node_version エラー修正 / skip_on_run の実行時上書きを追加

### 修正
- **GKE クラスタ作成が
  `node_version and min_master_version must be set to equivalent values on create` で
  失敗する問題を修正しました**。エクスポートに `node_version` だけが含まれる場合が
  あり、そのままでは作成できません。バージョンはエクスポート済みの
  `release_channel` に追従させる方針とし、同値でない `node_version` は除去します。

### 新機能
- **`skip_on_run` を実行時に一度だけ上書きできるようになりました**（config.yaml は変更不要）:
  ```
  make run SKIP_ON_RUN=0   # 今回だけ export/customize を必ず再実行
  make run SKIP_ON_RUN=1   # 今回だけ既存 terraform/active を再利用
  ```
  ツールの更新後など「customize の修正を確実に反映してから適用したい」ときは
  `SKIP_ON_RUN=0` を指定してください。`YES=1` と同様、環境変数ではなく
  コマンドラインでの明示指定のみ有効です。

---

## 2026-08-14 — SSL 証明書待ちの HTTPS LB で `make run` が失敗し続ける問題を修正

### 修正
- **手動作成待ちの SSL 証明書を参照する HTTPS LB があると、証明書を作るまで
  毎回 `make run` が失敗していた問題を修正しました**
  （`Error creating TargetHttpsProxy: ... sslCertificates/... was not found`）。
  - コピー先に証明書が**まだ無い**場合: その target proxy と forwarding rule を
    今回の適用から**保留**し、DIFF.md の要対応に
    「証明書作成後の次回 `make run` で自動適用」と掲載します。run は失敗しません。
  - コピー先に証明書を**作成済み**の場合: 従来どおりそのまま適用されます。
  - つまり「`gcloud compute ssl-certificates create ...` で証明書を作る →
    `make run` を再実行」だけで LB フロントエンドまで揃います。

---

## 2026-08-14 — DIFF.md に GKE ワークロード移行手順（Backup for GKE）を掲載

### 新機能
- **GKE クラスタをコピーした場合、DIFF.md の「要対応」にクラスタごとの
  ワークロード・PV データ移行手順が載るようになりました**。
  本ツールはクラスタの**構成のみ**を複製するため、移行しない限りコピー先クラスタは
  空のままです。推奨手段である **Backup for GKE** の手順を具体的に案内します:
  - コピー元: `gcloud container backup-restore backup-plans create ...
    --all-namespaces --include-secrets --include-volume-data` → バックアップ作成
    （コピー元への操作はツールの対象外のため手動で実行してください）
  - コピー先: restore-plans 作成 → リストア実行
  - 両側で `gkebackup.googleapis.com` の有効化と、クラスタの
    Backup for GKE エージェント（`gke_backup_agent_config`）の有効化が必要です
- GKE が自動生成するリソース（参考 P3）や k8s オブジェクトの注記も
  「Backup for GKE の restore で復元される」前提の文言に統一しました。
- SSL 証明書の要対応には「**Backup for GKE では移行されない**」ことを明記しました
  （Cloud Run 前段 LB の証明書はクラスタ外の Compute リソースのため）。

---

## 2026-08-14 — Cloud Run の actAs 403 など「作成順序」起因の apply 失敗を修正

### 修正
- **`Permission 'iam.serviceaccounts.actAs' denied on service account ...
  (or it may not exist)` で Cloud Run の作成が失敗する問題を修正しました**。
  サービスアカウントは同じ apply の中で作成されるのに、メールアドレスの**文字列参照**の
  ため Terraform が順序を判断できず、SA 作成と並行して Cloud Run を作ろうとしていました。
  Terraform のリソース参照（`google_service_account.<名前>.email`）に自動変換し、
  **SA 作成 → Cloud Run 作成**の順を保証します。
- 同じ原因の以下も修正しました（いずれも参照を Terraform 参照へ自動変換）:
  - `The given security policy does not exist`（backend service → Cloud Armor ポリシー）
  - `Error creating GlobalForwardingRule: 404`（転送ルール → target proxy → URL map → backend）
- **アラートポリシーの通知チャネル参照を自動解決するようにしました**
  （`Notification channel not found: <数字>`）。チャネル ID はサーバー採番のため
  コピー先に同じ番号は存在しません。同じプロジェクト内にチャネルもコピーされる場合は
  Terraform 参照に変換し、解決できない場合はチャネル指定を外してアラート本体のみ
  コピーします（**DIFF.md に「確認」として掲載**。通知の再設定が必要です）。
- **コピー元の export に紛れ込む「別プロジェクトのリソース」を除外するようにしました**。
  monitoring workspace 経由などで project_mapping に無いプロジェクトのリソースが
  export に混入することがあり、そのまま適用すると**無関係なプロジェクトへ書き込んで
  しまう**ため、警告を出してスキップします。
  - ⚠️ 過去の実行で書き込まれていないか、次のコマンドで確認することをお勧めします:
    `gcloud beta monitoring channels list --project=shingo-ar-genai0718`
    （表示名 `email` のチャネルが意図せず増えていれば削除してください）

---

## 2026-08-14 — Cloud Run の `Image ... not found` を修正（イメージ複製を Terraform より前に実行）

### 修正
- **Cloud Run サービスの作成が
  `Error code 5, message: Image '...@sha256:...' not found.` で失敗する問題を修正しました。**
  - Artifact Registry のイメージ複製が **Step 6（データ同期）** にあり、
    Cloud Run を作成する **Step 4（Terraform 適用）より後**でした。
    Cloud Run はイメージを digest 固定で参照し、**リビジョン作成時に解決する**ため、
    後から複製しても間に合いません。
  - イメージ複製を **Step 3.7（Terraform 実行の直前）** に移動しました。
    ログでは `ステップ 37` として出力されます。
  - 設定キーは従来どおり `steps.data_sync.artifact_registry` です（位置だけの変更）。
  - コピー先のリポジトリがまだ無い場合はこのステップが作成します（Terraform 側は
    既存リポジトリを取り込むため衝突しません）。

> 💡 複製後に digest が変わった場合は警告とともに `gcrane cp` での再実行手順を表示します。
> その場合 Cloud Run の `@sha256:` 参照は解決できないため、警告が出たら対応してください。

### 改善
- **イメージ複製を高速化しました**（`global.parallel_jobs` をフルに活用）。
  - 従来はプロジェクトごとに順番にコピーしていたため、並列数が 1 リポジトリ内でしか
    効いていませんでした。全プロジェクト・全リポジトリのイメージを**ひとまとめにして
    並列コピー**します（ログ: `複製対象: N イメージ (parallel_jobs=X)`）。
  - 再実行時の「コピー済み判定」を 1 イメージずつの問い合わせから
    **リポジトリ単位の一括取得**に変更しました。2 回目以降はほぼ即完了します
    （ログ: `イメージ N 件（既存 M 件 / 複製対象 K 件）`）。
- **`✗ pull 失敗 ...: unsupported media type application/vnd.oci.empty.v1+json` の
  警告を出さないようにしました**。これは Cloud Build がビルドごとに生成する
  署名メタデータ（SLSA provenance / SBOM）で、実行可能イメージではないため
  docker では取得できず、**複製する必要もありません**（Cloud Run が参照する
  実イメージは別 digest で正常に複製されています）。
  今後は「非イメージ成果物のためスキップ」として情報表示のみになります。

---

## 2026-08-13 — 必要な API を Terraform 実行前に全て有効化する関門を追加（Step 3.5）

### 新機能
- **Terraform を実行する直前に、必要な API が全て有効かを確認する関門（Step 3.5）を
  追加しました**。`<API> has not been used in project ...` の 403 で
  `terraform apply` が止まらないようにするためです。
  - **なぜこの位置か**: 必要な API が確定するのは **Step 3（Terraform コード生成）
    完了後**です。それ以前は「コピー元で有効な API」しか分からず、
    生成された `.tf` に固有の API を取りこぼす可能性がありました。
  - Step 1.5（従来）はコピー元由来の API を**先行して**有効化し、
    Step 2〜3 の実行中に反映（伝播）時間を稼ぎます。
  - **Step 3.5（新規）**は生成された `.tf` から引いた API を含む**全量**を有効化し、
    **「全て有効として見えること」を確認してから** Step 4 に進みます。
    従来は新しく有効化した分しか確認していなかったため、
    元から無効だった API を素通りさせる可能性がありました。
  - 全コピー先プロジェクト分をまとめて確保するため、
    プロジェクトごとの直前有効化よりも反映時間に余裕ができます。
- 実行ログでは **`ステップ 35`** として出力され、末尾に
  `✓ Step 3.5 完了: N プロジェクトで必要 API を確保（必要 計 X 件 / 今回追加 Y 件）` を出します。
  有効化できなかった API があれば、プロジェクトごとに一覧と手動コマンドを警告表示します
  （従来どおり終了コードには影響しません）。

---

## 2026-08-13 — Artifact Registry 未使用プロジェクトで `make run` が失敗する問題を修正

### 修正
- **コピー元が Artifact Registry を使っていない場合に `make run` が失敗扱いになる問題を
  修正しました**。AR API が無効なプロジェクトでは一覧取得が 403 になりますが、
  これは「複製対象なし」であって移行の失敗ではありません。
  今後はログに `Artifact Registry API が無効（= AR 未使用）。複製対象なし` と出るだけで、
  **終了コードには影響しません**（イメージ一覧の取得失敗・コピー先リポジトリの
  作成失敗も同様に警告のみになりました）。
- **失敗詳細が `- '@type': type.googleapis.com/google.rpc.ErrorInfo` としか
  表示されない問題を修正しました**。gcloud が出す構造化エラー詳細ではなく、
  実際のエラーメッセージ（`ERROR: ...` の行）を表示します。
  この表示はサマリーの「失敗詳細」全体で使われているため、他のエラーも読みやすくなります。

---

## 2026-08-13 — k8s LoadBalancer リソースの誤コピー / FW ルールの誤除外を修正

### 修正
- **GKE 上の k8s Service (type=LoadBalancer) が作った LB リソースが中途半端にコピーされ、
  `terraform apply` が 404 で失敗する問題を修正しました**
  （`The resource '.../httpHealthChecks/k8s-...-node' was not found`）。
  ヘルスチェック（`k8s-*` 名）だけが除外され、それを参照するターゲットプール /
  転送ルール（16 進 ID 名のため除外判定に掛からなかった）が残っていました。
  今後は k8s が書き込む所有者情報（description の `kubernetes.io/...`）で判定し、
  **LB 一式をまとめて除外**します（コピー先クラスタへワークロードを再デプロイすれば
  k8s が再作成します。DIFF.md では「参考 P3: 対応不要」に分類されます）。
- **`k8s-` / `gke-` で始まる名前の利用者作成ファイアウォールルールが
  コピーされない問題を修正しました**。除外判定を「名前の前方一致」から
  「k8s の所有者情報 + GKE 機械命名の構造判定」に変更したため、
  `k8s-nodeport-allow` のような利用者ルール（DENY ルール含む）はコピーされます。
- **`terraform import` の失敗警告が理由なしで出ていた問題を修正しました**
  （`import 失敗: ... : `）。原因が読める形で表示し、
  「コピー先にまだ存在しない」だけの import 失敗（GKE クラスタ等）は
  警告に出さないようにしました（apply が作成するため正常系です）。

### 改善（設定の安全性）
- `steps.enable_apis.skip_apis` に基盤 API（cloudresourcemanager / serviceusage /
  iam / iamcredentials）を指定しても**除外されなくなりました**。
  誤って指定すると Terraform が一切動かないコピー先ができてしまうためです。
- `steps.enable_apis.enabled: false` にしたとき、Step 4 の apply 直前の API 有効化も
  基盤 4 API のみに縮小されるようになりました（従来は設定に関わらず全量有効化）。
- `wait_seconds: 0`（伝播を待たない）指定時に、毎回偽のタイムアウト警告が
  出ていたのを解消しました。設定値が不正な場合もエラーで停止せず既定値で動きます。

---

## 2026-08-13 — Artifact Registry のイメージをコピー / 削除保護による「詰み」を解消

### 新機能
- **Step 6 に Artifact Registry のコンテナイメージ同期を追加しました**。
  これまではリポジトリ（箱）だけが作られ**イメージ本体がコピーされない**ため、
  Cloud Run が `Image '<コピー先>/...@sha256:...' not found.` で起動できませんでした。
  - コピー元の DOCKER リポジトリを走査し、**同じ digest** でコピー先に複製します。
    既に同じ digest があるイメージは再送しません（再実行は即完了）。
  - タグの無いイメージ（digest 参照専用）は `migrated-<digest 先頭12桁>` を付けて複製します。
  - **要対応**: `scripts/bootstrap_cross_project.sh` の再実行が必要です
    （コピー先 SA にコピー元の `roles/artifactregistry.reader` を追加しました）。
  - **要対応**: 実行マシンに **`docker` が必要**です（`gcloud` にイメージコピー機能が無いため）。
    未導入の場合はスキップして警告のみ出します。
  - 不要な場合は `steps.data_sync.artifact_registry.enabled: false`、
    特定リポジトリだけ除外する場合は `skip_repos: ["repo-a"]` を指定してください。

> ⚠️ **digest について**: docker 経由のコピーはマルチアーキイメージなどで digest が
> 変わることがあります。その場合 Cloud Run の `@sha256:` 固定参照は解決できないため、
> コピー後に digest を検証し、変わっていれば警告と `gcrane cp` での再実行手順を案内します。

### 修正
- **失敗したリソースが二度と復旧できなくなる問題を修正しました**
  （`Error: cannot destroy service without setting deletion_protection=false`）。
  Cloud Run / GKE クラスタ / Cloud SQL は Terraform provider の既定で削除保護が有効になり、
  apply が途中で失敗したリソース（tainted）を次回以降**作り直せなくなっていました**。
  - エクスポート結果に `deletion_protection` の指定が**無い場合のみ**、コピー先では
    `false` を明示するようにしました（コピー元が明示している場合はその値を尊重します）。
  - コピー先の削除保護は実際に外れるため、**DIFF.md に「確認」として掲載**します。
    本番運用に切り替える際は削除保護を戻してください。

---

## 2026-08-13 — apply 時の 404（subnetwork 未作成 / Container Analysis note）を修正

### 修正
- **予約 IP などが subnetwork より先に作られて 404 になる問題を修正しました**
  （`Error creating Address: Error 404: The resource 'projects/<dst>/regions/<region>/
  subnetworks/<name>' was not found`）。
  エクスポートされた `.tf` は subnetwork を URL 直書きで参照するため、同じプロジェクトに
  subnetwork の定義があっても Terraform が作成順序を判断できていませんでした。
  同一プロジェクト内の参照を Terraform 参照（`google_compute_subnetwork.<name>.self_link`）に
  自動変換し、subnetwork → 予約 IP の順で作成されるようにしました
  （共有 VPC ホストなど**別プロジェクト**の subnetwork 参照は従来どおり URL のまま）。

### 仕様変更（要確認）
- **Container Analysis の occurrence はコピー対象外になりました**
  （`Error creating Occurrence: Error 404: note with ID "built-by-cloud-build" for project
  "<dst>" does not exist`）。
  occurrence は過去ビルドの来歴・署名レコードで、参照先の note は Cloud Build が
  自プロジェクトに作るため コピー先には存在せず、署名鍵も Google 管理プロジェクトを
  指しているため複製できません。
  - **コピー先で再ビルドすれば自動生成されます**。
  - Binary Authorization で attestation を必須にしている場合のみ、再ビルドか
    手動 attestation が必要です。DIFF.md の「確認」として掲載されます。

---

## 2026-08-13 — Terraform 適用の「Duplicate resource」と provider 非互換を修正

### 修正
- **同名リソースが複数リージョンにあると `terraform plan` が
  `Duplicate resource ... configuration` で停止する問題を修正しました**
  （例: Artifact Registry の `cloud-run-source-deploy` が asia-northeast1 と us-central1 の両方にある場合）。
  衝突したリソース定義のラベルを `<名前>_<リージョン>` に自動リネームして回避します
  （`terraform import` 用コメントも追従。ログに `重複ラベルを一意化:` として出力）。
- **エクスポートされた HCL が最新の Terraform google provider で通らない問題を修正しました**。
  適用前に以下を自動補正します（いずれもログに出力）:
  - GKE クラスタの廃止済みブロック（`cluster_telemetry` / `pod_security_policy_config` /
    `protect_config`）を除去
  - `iap` ブロックに必須化された `enabled = true` を補完
    （**IAP の認証壁を外さない安全側**。コピー先で IAP が不要なら適用後に手動で無効化してください）
  - GKE の `ip_allocation_policy` で排他になった CIDR 直指定を除去
    （Pod range は subnet 側に複製された secondary range を名前参照）

### 仕様変更（要確認）
- **自己管理 SSL 証明書（`google_compute_ssl_certificate`）はコピー対象外になりました**。
  秘密鍵は API からエクスポートできないため、Terraform では作成不能です。
  DIFF.md に**要対応**として出るので、鍵をお持ちの方がコピー先で手動作成してください。
  作成するまで、その証明書を参照する HTTPS ロードバランサ（target proxy）の適用は失敗します。
  （Google 管理証明書 `google_compute_managed_ssl_certificate` は従来どおりコピーされます）
- **上記のような「手動対応・確認が必要な自動補正」は DIFF.md に必ず掲載されるようになりました**。
  要対応テーブルの直後に「customize による補正・スキップ（手動対応・確認）」セクションが出ます。
  - **要対応**: SSL 証明書の手動作成（作成用の `gcloud` コマンド付き）
  - **確認**: IAP を `enabled = true` で複製した backend service
    （不要なら無効化する `gcloud` コマンド付き）
  - `bulk_export.skip_on_run: true` で export をスキップした `make run` でも、
    前回 customize 時の注記がそのまま DIFF.md に出ます（`.tf` と同じライフサイクルで更新）。

---

## 2026-08-13 — `make mock` の生成物が `make run` に混ざる問題を修正 / API 有効化を apply 直前にも実施

### 重要な修正（要確認）
- **`make mock` が実運用の Terraform 作業ディレクトリ (`terraform/active/`) を上書きしていた問題を
  修正しました**。修正前は `make mock` の直後に `make run`（`skip_on_run: true`）を実行すると、
  mock のダミー定義（`mock-cluster` / `org-bucket-shared-data` など）が**コピー先に実際に
  作成されて**いました。
  - `Kubernetes Engine API has not been used in project ...` の 403 は、この
    ダミー GKE クラスタ (`mock-cluster`) を作ろうとして出ていたケースがあります。
    **API を有効化して解決すべきエラーではありません**（有効化するとダミーのクラスタが
    本当に作られます）。
  - 今後 `make mock` の出力先は `terraform/mock/` に分離され、`terraform/active/` には
    一切書き込みません。
- **すでに mock の残骸がある環境向けの安全装置**を入れました。`terraform/active/` に mock 由来の
  `.tf` が残っていると、
  - `make run` は既存 active を再利用せず、**bulk-export からやり直します**。
  - それでも残っている場合は **apply せずエラー**にします（削除コマンドを案内）。

#### 過去に `make mock` → `make run` を実行した場合の対応
1. 残骸を削除して作り直してください（`terraform/raw` / `active` は再生成される派生物です）。
   ```
   rm -rf terraform/raw terraform/active
   make plan
   ```
2. コピー先に作られてしまったダミーリソースを確認・削除してください（例）。
   ```
   gcloud storage ls --project=<dst プロジェクト> | grep org-bucket-shared-data
   gcloud container clusters list --project=<dst プロジェクト>   # mock-cluster があれば削除
   ```

### 改善
- **Terraform で適用する `.tf` から必要な API を判定し、`terraform apply` の直前にも
  有効化する**ようにしました（Step 4）。Step 1.5（コピー元の有効 API を反映）で取りこぼしても、
  実際に適用するリソース定義から引き直すため、`<API> has not been used in project ...` の
  403 で止まりにくくなります。
  - 例: `google_container_cluster` → `container.googleapis.com`、
    `google_sql_database_instance` → `sqladmin.googleapis.com`。
  - 有効化できなかった場合も**エラーにはせず**警告と手動コマンドの案内に留めます。

---

## 2026-08-13 — コピー先の API を移行前に自動有効化（Step 1.5 追加）

### 新機能・仕様変更
- **Step 1.5「dst API 事前有効化」(`enable_apis`) を追加しました**。コピー元で有効な API を
  コピー先でも先に有効化するため、**API 無効による `make run` の失敗がなくなります**。
  - これまでは GKE のあるプロジェクトで `make run` すると、Step 4 の `terraform apply` が
    `Kubernetes Engine API (container.googleapis.com) has not been used in project ... before
    or it is disabled` の 403 で停止していました。
  - **設定は不要です**（`dst/config.yaml` に `enable_apis` キーが無くても既定で有効）。
    無効にしたい場合のみ `steps.enable_apis.enabled: false` を指定してください。
  - 有効化するのは「コピー元で有効な API」＋「有効なステップが必ず使う API」です。
    コピー先で既に有効な API は触らず**差分だけ**を有効化するため、再実行は即完了します。
  - 有効化直後は反映に時間がかかるため、既定 120 秒（`wait_seconds`）だけ伝播を待ちます。
- `make plan`（ドライラン）では**有効化予定の API を一覧表示するだけ**で、実際の有効化は行いません。

### 設定（任意）
```yaml
steps:
  enable_apis:
    enabled: true
    extra_apis: []      # コピー元に無くても必ず有効化したい API
    skip_apis: []       # コピー先では有効化したくない API
    wait_seconds: 120   # 有効化の伝播待ち秒数（0 で待たない）
```

### 注意
- 有効化に失敗した API は**エラーにせず**警告として一覧表示し、手動用の
  `gcloud services enable ... --project=<dst>` を案内します。移行に必要な API であれば、
  後続のステップが本来のエラーで停止するので見落としません。
- 権限が足りない場合（`serviceusage.services.enable`）も警告のみです。
  `bootstrap_dst_sa.sh` が付与する `roles/editor` に含まれています。
- 提供終了 API・旧エイリアス・親 API に伴って自動で有効化される内部サービスは
  コピー対象から除外します（除外した API はログに出力します）。
- コピー元の read に `serviceusage.services.list`（`roles/viewer` に含まれる）を使います。
  権限が無い場合は Step 1 の CAI 出力から有効 API を判定するため、動作は継続します
  （最小権限のカスタムロールを使っている場合は `make bootstrap-cross-project` を再実行すると
  この権限が追加されます）。
- 実行ログでは既存ステップと同じ表記ルールで **`ステップ 15`**（= Step 1.5）として出力されます
  （4.5 → `ステップ 45` / 5.7 → `ステップ 57` と同じ、小数点を省いた表記）。

---

## 2026-08-13 — GKE はクラスタ構成のみコピー / ノード VM をコピー対象外に

### 新機能・仕様変更
- **GKE クラスタの扱いを明確化しました**。クラスタ / ノードプールの**構成情報のみ** Terraform
  （Step 3 エクスポート → Step 4 適用）で複製し、**GKE ノードの GCE VM はコピーしません**。
  コピー先クラスタがノードを自分で作り直すためです。設定は不要（常時この挙動）です。
  - これまでは GKE ノードにスナップショットが無いため **Step 2 でエラー停止していました**。
    今後はノードを検証対象から除外して先に進みます。
  - GKE が自動生成するリソース（インスタンステンプレート / MIG / オートスケーラー、
    `gke-*` `k8s-*` のファイアウォールルール）もコピーしません。コピー先クラスタが再生成します。
  - 判定は GKE が付与する `goog-gke-node` ラベルで行います。`gke-` で始まる**利用者作成の VM は
    通常どおりコピーされます**（名前だけでは除外しません）。
- **コピー対象外（要注意）**: PersistentVolume のデータ、クラスタ内の k8s オブジェクト
  （Deployment / Service 等）、Fleet（gkehub）登録。コピー先にクラスタができた後、
  ワークロードは利用者側で再デプロイしてください。
- Terraform でのクラスタ作成は 5〜15 分かかります。src 固有の設定が原因で `apply` が失敗する場合は、
  `terraform/active/<src>/` の該当 `.tf` を修正して `make run` を再実行してください。

### 改善
- **DIFF.md のノイズを削減**。クラスタ内の k8s オブジェクト（`k8s.io/*`）を差分から除外し、
  GKE が自動生成する派生リソースは「参考（P3: 対応不要）」に分類するようにしました。

### バグ修正
- **DIFF.md が「bulk-export が出力しなかった」を誤報していた問題を修正**しました。
  差分判定が `terraform/raw/` をサブディレクトリまで見ておらず、実際には export 済みの
  リソース（GKE クラスタ / ノードプールを含む）まで「要対応」に計上されていました。
  過去の DIFF.md を根拠に手動作成を進めていた場合は、`make plan` をやり直して
  最新の DIFF.md で再確認してください。

---

## 2026-08-13 — IAM ロール自動複製 / 続行確認の承認方法変更

### 新機能
- **IAM ロール自動複製（Step 5.7 `iam_sync`）**: src プロジェクトの SA に付与された IAM ロールを、
  dst の同名 SA へ自動複製します。**デフォルトで有効**（`config.yaml` に何も書かなくても動作）。
  不要な場合は `steps.iam_sync.enabled: false` で無効化できます。
  - `roles/owner` など高権限ロールもコピーされる場合があります。付与した場合は実行ログ末尾に
    対象・理由・取消コマンドを WARNING でまとめて出力するので、必ずレビューしてください。
  - 別 ORG で ID が変わるもの（条件付きバインディング、ORG カスタムロール、project_mapping 外の
    プロジェクトの SA・カスタムロール等）は複製せず、WARNING を出してスキップします。

### 要対応（既存環境のみ）
- **`scripts/bootstrap_dst_sa.sh` の再実行が必要です**。dst SA に新しく
  `roles/resourcemanager.projectIamAdmin` を付与するようになりました（IAM ロール複製に必須）。
  このロールは「任意の principal に任意のロールを付与できる」強い権限である点に注意してください。
- **`scripts/bootstrap_cross_project.sh` の再実行が必要です**。src 側カスタムロール
  `migrationSrcReader` に `resourcemanager.projects.getIamPolicy` などの権限を追加しました。

### 破壊的変更
- **続行確認の自動承認方法が変わりました**。環境変数 `COPY_ALL_ENV_AUTO_APPROVE=1` は廃止しました。
  今後は `make plan YES=1` / `make run YES=1`（内部的には `--yes` / `-y`）をコマンドラインで
  明示指定してください。環境変数を export したまま忘れて意図せず自動承認され続ける事故を防ぐための変更です。

### 改善
- **DIFF.md の出力形式を変更**。「要対応」を実害があるものだけに絞り込み、それ以外は優先度
  （P1〜P3）付きの「参考」セクションに分離しました。本当に対応が必要な項目が埋もれにくくなりました。
  - **要対応**: 先頭に WHAT（何が無い）/ WHY（なぜ困る）/ HOW（どう直す）のテーブルで出力。
    ここだけ対応すれば移行としては足ります。
  - **参考**: `P1` = 確認推奨（別ステップが自動対応済みなので結果だけ確認）/ `P2` = 条件付き
    （src 側にカスタム・取り置きの意図がある場合のみ）/ `P3` = 対応不要。優先度の昇順で並びます。
  - 判定材料が無いものは「要対応」に倒すため、**過剰報告はあっても見落としはしません**。
- **README に最小権限 principal の推奨構成を追加**。src=read-only / dst=書込 専用アカウントを
  使う場合に必要な具体的ロール一覧を明記しました（`roles/viewer` だけでは不足する点を含む）。
