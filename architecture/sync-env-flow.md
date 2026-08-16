# sync_env 実行フロー

> `scripts/sync_env.py` の実行フロー図。GitHub 上でそのまま図が表示されます。

`make run` は一本道ではなく、**関所の連なり**です。各ステップが「通す / 落とす / 止める」の
どれかを判定し、判定を誤った過去の事故がそのまま今の分岐条件になっています。
ここではその分岐を具体的な条件で描きます。

| 記号 | 意味 |
|---|---|
| 判定（菱形・琥珀） | 条件分岐 |
| 通す（翠） | コピー先に書き込む |
| 落とす（菫） | 複製しない・警告のみ |
| 止める（朱） | `exit 1` |

---

## Step 0 → 99 — 全体の骨格

矢印のラベルは**順序の制約そのもの**です。番号が 1.5 / 3.5 / 3.7 と半端なのは、
「この位置でないと落ちる」と分かって後から挿入されたためです。

```mermaid
%%{init: {"theme":"base","themeVariables":{"fontFamily":"ui-monospace, Menlo, Consolas, 'Hiragino Kaku Gothic ProN', 'Yu Gothic', sans-serif","fontSize":"13px","lineColor":"#7C8D92","textColor":"#16242A","edgeLabelBackground":"#EDF1F1"}}}%%
flowchart TD
  A["make run"] --> G{"Step 0<br/>起動前ガード"}
  G -->|"どれか失格"| X["exit 1<br/>dst に一切書き込まない"]
  G -->|"全通過"| S1["Step 1  cai_scan<br/>src の全資産を棚卸し"]
  S1 --> S15["Step 1.5  enable_apis<br/>src 由来の API を dst で有効化"]
  S15 -->|"有効化の伝播時間を稼ぐ"| S2["Step 2  gce_snapshot<br/>GKE ノードは検証対象外"]
  S2 --> S3["Step 3  bulk_export → customize<br/>.tf を書き換え / 間引き"]
  S3 -->|".tf が出揃って初めて<br/>必要 API が確定する"| S35["Step 3.5  enable_apis final<br/>全量 + enabled になるまで待つ"]
  S35 -->|"Cloud Run は image の @sha256 を<br/>revision 作成時に解決する"| S37["Step 3.7  artifact_registry<br/>gcrane でイメージ複製"]
  S37 --> S4["Step 4  terraform_apply<br/>import → plan → apply"]
  S4 -->|"SA / VPC / バケットが<br/>dst に揃ってから"| S47["Step 4.7  serverless_sync<br/>Cloud Run サービス・ジョブ<br/>Cloud Functions"]
  S47 -->|"FW は dst network の実在が前提"| S45["Step 4.5  network_firewall<br/>classic rules / policy rules"]
  S45 --> S5["Step 5  gce_restore<br/>snapshot から VM を復元"]
  S5 -->|"guest OS の boot 完了を待つ"| S55["Step 5.5  電源状態を src に合わせる"]
  S55 -->|"SA が dst に出来てから付与<br/>Run の公開設定は Step 4.7 の後でないと付かない"| S57["Step 5.7  iam_sync"]
  S57 --> S6["Step 6  data_sync  GCS / BQ"]
  S6 --> S7["Step 7  vpc_sc"]
  S7 --> S99["Step 99  DIFF.md<br/>CAI と .tf を突き合わせて<br/>要対応 / 参考 に仕分け"]
  S99 --> E{"失敗 0 件?"}
  E -->|"はい"| OK["exit 0"]
  E -->|"いいえ"| NG["exit 1<br/>有効なステップは走り切っている"]
  S1 & S2 & S3 & S4 & S5 & S6 -.->|"fail-fast の sys.exit(1)<br/>CAI 未カバー / snapshot 不足 / customize 例外 /<br/>active_dir 不在 / allow_fail=False のコマンド失敗"| XF["即時終了<br/>失敗一覧は出るが<br/>後続ステップと DIFF.md は出ない"]

  classDef gate fill:#FAF0DA,stroke:#8A6612,stroke-width:1.5px,color:#3D2E08;
  classDef pass fill:#DDEEE7,stroke:#0D6A53,stroke-width:1.5px,color:#0A382C;
  classDef halt fill:#F7DFDB,stroke:#9F3428,stroke-width:1.5px,color:#48160F;
  classDef step fill:#E4EAEC,stroke:#3E5257,stroke-width:1.2px,color:#16242A;
  class G,E gate;
  class X,NG,XF halt;
  class OK pass;
  class A,S1,S15,S2,S3,S35,S37,S4,S47,S45,S5,S55,S57,S6,S7,S99 step;
```

*図 1 — ステップの順序は依存関係で決まっている。半端な番号 = 後から割り込ませた位置。*

> **「記録して進む」は並列ワーカーの中だけの話です。** `_parallel_for_each` の worker は
> 失敗しても `sys.exit(1)` せず `stats.add_failure()` に記録し、`main()` が終端で exit 1 に
> します。一方、ステップ本体には今も即 `sys.exit(1)` する箇所があります
> （`step_cai_scan` の未カバー / `step_gce_snapshot` の snapshot 不足 / `customize_hcl` の例外 /
> `step_terraform_apply` の active_dir 不在 / `allow_fail=True` を付けていない `run_command`）。
>
> ただし**失敗一覧そのものは必ず出ます** — `execute()` は `finally: self._print_summary()` で
> 締めるため、中断してもサマリーと失敗詳細はログ末尾に残ります。失われるのは
> **未実行のステップと Step 99 の DIFF.md** だけです。
>
> なお図の各ステップは `step_enabled()` による opt-in で、既定 true は
> `network_firewall` / `iam_sync` / `enable_apis` / `serverless_sync` の 4 つだけです。Step 99 はさらに
> **`cai_scan` と `bulk_export` の両方が有効**なときしか走りません。
> つまり **DIFF.md が無いことは異常終了の証拠になりません**。

---

## Step 0 — 起動前ガード（書き込む前に止める）

ここを抜けたあとの失敗は「30 分走ってから全滅」になります。だから 5 つとも
**コピー先に触る前**に置いてあります（`make mock` では下 3 つが早期 return するため機能しません）。

```mermaid
%%{init: {"theme":"base","themeVariables":{"fontFamily":"ui-monospace, Menlo, Consolas, 'Hiragino Kaku Gothic ProN', 'Yu Gothic', sans-serif","fontSize":"13px","lineColor":"#7C8D92","textColor":"#16242A","edgeLabelBackground":"#EDF1F1"}}}%%
flowchart TD
  A["make run / make plan"] --> C1{"config.yaml は妥当か<br/>validate_config + validate_steps_config"}
  C1 -->|"ORG 保護違反 / 設定不備"| H1["exit 1<br/>不備を全件列挙"]
  C1 -->|"OK"| C2{"tf_base/.sync_env.lock を<br/>flock できるか"}
  C2 -->|"取れない = 二重起動"| H2["exit 1<br/>state 破壊を防ぐ"]
  C2 -->|"取れた"| C3{"gcrane または crane が PATH にあるか<br/>data_sync 有効 かつ artifact_registry が false でない場合のみ判定"}
  C3 -->|"無い"| H3["exit 1<br/>イメージ未複製のまま<br/>apply させない"]
  C3 -->|"ある / 判定対象外"| C6{"SA 実在 + impersonation は成立するか"}
  C6 -->|"失敗"| H6["exit 1"]
  C6 -->|"OK"| C5{"認証主体が src に<br/>書込相当の権限を持つか"}
  C5 -->|"持つ + --yes 無し"| Q{"対話で y/N"}
  C5 -->|"持つ + --yes 指定"| C4{"全 dst プロジェクトが<br/>ACTIVE か"}
  C5 -->|"持たない"| C4
  Q -->|"N / 非対話"| H5["exit 1"]
  Q -->|"y"| C4
  C4 -->|"1 つでも不在"| H4["exit 1<br/>make projects を案内"]
  C4 -->|"全て ACTIVE"| GO["Step 1 へ"]

  classDef gate fill:#FAF0DA,stroke:#8A6612,stroke-width:1.5px,color:#3D2E08;
  classDef halt fill:#F7DFDB,stroke:#9F3428,stroke-width:1.5px,color:#48160F;
  classDef pass fill:#DDEEE7,stroke:#0D6A53,stroke-width:1.5px,color:#0A382C;
  classDef step fill:#E4EAEC,stroke:#3E5257,stroke-width:1.2px,color:#16242A;
  class C1,C2,C3,C4,C5,C6,Q gate;
  class H1,H2,H3,H4,H5,H6 halt;
  class GO pass;
  class A step;
```

*図 2 — 5 つの関所はすべて「コピー先に 1 バイトも書く前」。承認プロンプトはコピー先の実在チェックより先に出る。*

> **承認は必ず起動コマンドに現れる形で。** コピー元への書込権限がある場合の続行は
> `--yes` をコマンドラインで明示したときだけです。環境変数による自動承認は
> 「export したまま忘れる」事故を生むため採用していません。

---

## Step 3 — customize（`.tf` 1 ファイルごとの判定チェーン）

`raw/` の 1 ファイルが `active/` に届くまでに通る関門。上から順に評価し、
**どれかに当たった時点で捨てる**（後続の判定はしない）。

```mermaid
%%{init: {"theme":"base","themeVariables":{"fontFamily":"ui-monospace, Menlo, Consolas, 'Hiragino Kaku Gothic ProN', 'Yu Gothic', sans-serif","fontSize":"13px","lineColor":"#7C8D92","textColor":"#16242A","edgeLabelBackground":"#EDF1F1"}}}%%
flowchart TD
  F["raw/(src)/**/*.tf を 1 件読む"] --> R1["1. src ID / 番号 → dst に置換<br/>network URL を .self_link 参照へ"]
  R1 --> R2["2-3.7. バケット名リネーム / boot_disk.source 除去<br/>固定 IP 解除 / UBLA 統一 / provider 非互換の吸収"]
  R2 --> D1{"3.8 project = が<br/>dst ID 集合に無い非数値か"}
  D1 -->|"はい = 越境出力"| K1["捨てる<br/>無関係な実プロジェクトへの<br/>書き込みを防ぐ"]
  D1 -->|"いいえ"| D2{"4. _skip_reason_for_file<br/>複製不能 or GKE 管理か"}
  D2 -->|"self-managed 証明書 / occurrence<br/>GKE 管理 / Cloud Run・Functions"| K2["捨てる + DIFF に注記<br/>サーバーレスは Step 4.7 が所有"]
  D2 -->|"いいえ"| D3{"3.85 src で IN_USE の<br/>内部アドレスか"}
  D3 -->|"はい"| K3["捨てる<br/>Step 5 が mig-(vm)-(ip) で予約し直す"]
  D3 -->|"いいえ"| D4{"3.9 resource_types の<br/>include / exclude に外れるか"}
  D4 -->|"全型が対象外"| K4["捨てる<br/>1 型でも残るなら通す"]
  D4 -->|"いいえ"| D5{"google_container_cluster か"}
  D5 -->|"はい"| N1["DIFF に Backup for GKE の<br/>移行手順を要対応で追記"]
  D5 -->|"いいえ"| R3["4.5 ラベル重複を (label)_(location) に改名<br/>deletion_protection = false を補完"]
  N1 --> R3
  R3 --> W["active/(src)/ へ平坦に書き出し"]
  W --> P2["2 パス目<br/>subnet / SA / URL 参照を<br/>terraform 参照へ書き換え<br/>証明書待ちの LB フロントを保留"]

  classDef gate fill:#FAF0DA,stroke:#8A6612,stroke-width:1.5px,color:#3D2E08;
  classDef skip fill:#E8E2F6,stroke:#5C4F91,stroke-width:1.5px,color:#2F2758;
  classDef pass fill:#DDEEE7,stroke:#0D6A53,stroke-width:1.5px,color:#0A382C;
  classDef step fill:#E4EAEC,stroke:#3E5257,stroke-width:1.2px,color:#16242A;
  class D1,D2,D3,D4,D5 gate;
  class K1,K2,K3,K4 skip;
  class W,P2 pass;
  class F,R1,R2,R3,N1 step;
```

*図 3 — 判定順には意味がある。範囲外の除外（3.9）は GKE 移行手順の追記より前に置く。*

---

## Step 3 + 4 — GKE 除外の二段構え

同じ純粋関数 `gke_managed_tf_skip_reason()` を Step 3 と Step 4 の**両方**から呼びます。
customize 側だけに置くと `skip_on_run: true` が customize ごとスキップし、
古い `active/` をそのまま apply して同じ 404 が再発しました。

> **Cloud Run / Cloud Functions も同じ二段構えです。** こちらは 404 対策ではなく
> **二重所有の防止**で、`serverless_tf_skip_reason()` を customize と
> `_purge_serverless_tf_files` の両方から呼びます。Step 4.7 と Terraform が
> 同じリソースを持つと、実行のたびに互いの設定を巻き戻すためです。

```mermaid
%%{init: {"theme":"base","themeVariables":{"fontFamily":"ui-monospace, Menlo, Consolas, 'Hiragino Kaku Gothic ProN', 'Yu Gothic', sans-serif","fontSize":"13px","lineColor":"#7C8D92","textColor":"#16242A","edgeLabelBackground":"#EDF1F1"}}}%%
flowchart TD
  C["HCL の中身"] --> D1{"GKE 管理型 かつ name が<br/>gke- / gk3- / k8s- 接頭辞"}
  D1 -->|"はい"| S1["捨てる<br/>instance template / MIG /<br/>autoscaler / health check / route"]
  D1 -->|"いいえ"| D2{"description に kubernetes.io の<br/>所有者マーカー"}
  D2 -->|"はい"| S2["捨てる<br/>k8s Service LB<br/>名前は a(31hex) で接頭辞に掛からない"]
  D2 -->|"いいえ"| D3{"Gateway マーカー k8sResource+k8sCluster<br/>または gkegw1- / k8s1- / k8s-be-"}
  D3 -->|"はい"| S3["捨てる<br/>backend service / URL map /<br/>proxy / forwarding rule / NEG"]
  D3 -->|"いいえ"| D4{"google_compute_managed_ssl_certificate<br/>かつ name が mcrt-(uuid)"}
  D4 -->|"はい"| S4["捨てる<br/>ManagedCertificate 復元で dst が再発行"]
  D4 -->|"いいえ"| D5{"gke-(cluster)-(hash)-dns ゾーン<br/>または backup / restore plan"}
  D5 -->|"はい"| S5["捨てる"]
  D5 -->|"いいえ"| K["残す<br/>google_container_cluster と<br/>node_pool は絶対に落とさない"]

  classDef gate fill:#FAF0DA,stroke:#8A6612,stroke-width:1.5px,color:#3D2E08;
  classDef skip fill:#E8E2F6,stroke:#5C4F91,stroke-width:1.5px,color:#2F2758;
  classDef pass fill:#DDEEE7,stroke:#0D6A53,stroke-width:1.5px,color:#0A382C;
  classDef step fill:#E4EAEC,stroke:#3E5257,stroke-width:1.2px,color:#16242A;
  class D1,D2,D3,D4,D5 gate;
  class S1,S2,S3,S4,S5 skip;
  class K pass;
  class C step;
```

*図 4 — `gke_managed_tf_skip_reason()` の判定順。マーカー優先、接頭辞は保険。*

> **`pvc-(uuid)` のディスクはこの関数では落ちません。** 除外しているのは
> `_skip_reason_for_file` の `google_compute_disk` 判定で、これは Step 5（`gce_restore`）が
> 管理する前提の無条件 skip です。次の 2 点に注意してください。
>
> - **`google_compute_region_disk` は除外対象外**です。regional PD の PV
>   （`pvc-(uuid)`）はコピー先に複製され、Backup for GKE の volume restore が作る
>   ディスクと衝突するか孤児になります。
> - **Step 5 は既定で無効**（`_STEP_ENABLED_DEFAULTS` に含まれない）です。config で
>   `gce_restore` を有効にしていない場合、ディスクは `.tf` から落とされたまま
>   誰も作らず、run は成功として終わります。

> **判定を誤ったときの損害が非対称です。** 落とし損ねると宙ぶらりんの参照で apply が 404、
> 落とし過ぎると利用者リソースのコピー漏れ。後者のほうが気付きにくいので、
> 迷ったら**コピーする側**に倒します。ノード VM の判定（`is_gke_node_vm`）が
> 「`goog-gke-node` ラベル」を第一判定にし、名前接頭辞を metadata との AND に
> しているのはこのためです。

---

## Step 4 — `terraform apply` の内部

Terraform ルート（`active/<src>/`）ごとに並列。state は分離済みです。

```mermaid
%%{init: {"theme":"base","themeVariables":{"fontFamily":"ui-monospace, Menlo, Consolas, 'Hiragino Kaku Gothic ProN', 'Yu Gothic', sans-serif","fontSize":"13px","lineColor":"#7C8D92","textColor":"#16242A","edgeLabelBackground":"#EDF1F1"}}}%%
flowchart TD
  A["_terraform_one_project"] --> D1{"mock 生成物が混じっているか<br/>_MOCK_TF_MARK / 旧ラベル"}
  D1 -->|"ある"| F1["このルートだけ失敗<br/>rm -rf を案内して return<br/>他ルートは続行"]
  D1 -->|"ない"| P["_purge_gke_managed_tf_files<br/>_purge_serverless_tf_files<br/>最終防衛線 / dry_run でも実行"]
  P --> D0{"実行モード"}
  D0 -->|"make plan（dry_run）"| I["provider.tf だけ書き出す<br/>init / plan は run_command が<br/>[DRY RUN] 予定: と出して空振り"]
  I --> STOP["何も実行されず終了<br/>tfplan は生成されない<br/>state reset / API 有効化 / import も走らない"]
  D0 -->|"make mock"| MK["provider.tf / state reset /<br/>API 有効化 / import はスキップ<br/>init → plan → apply を擬似実行"]
  D0 -->|"make run"| D2{"state の dst が今回の dst と違う<br/>または marker 一致でも state 本文に無い"}
  D2 -->|"stale"| R["state を破棄して import からやり直す"]
  D2 -->|"一致"| E["_ensure_dst_prereq_apis<br/>.tf から必要 API を引き直して有効化"]
  R --> E
  E --> I2["provider.tf 書き出し + terraform init"]
  I2 --> IM["terraform import 既存リソース"]
  IM --> D3{"import_error_kind"}
  D3 -->|"already = state 取込済み"| OKI["無視"]
  D3 -->|"missing = リモート実体なし"| OKI
  D3 -->|"None = 本当の失敗"| W1["WARNING<br/>_first_meaningful_line で理由を 1 行表示"]
  OKI --> PL2["terraform plan -out=tfplan"]
  W1 --> PL2
  PL2 --> AP["terraform apply -auto-approve tfplan"]

  classDef gate fill:#FAF0DA,stroke:#8A6612,stroke-width:1.5px,color:#3D2E08;
  classDef skip fill:#E8E2F6,stroke:#5C4F91,stroke-width:1.5px,color:#2F2758;
  classDef halt fill:#F7DFDB,stroke:#9F3428,stroke-width:1.5px,color:#48160F;
  classDef pass fill:#DDEEE7,stroke:#0D6A53,stroke-width:1.5px,color:#0A382C;
  classDef step fill:#E4EAEC,stroke:#3E5257,stroke-width:1.2px,color:#16242A;
  class D0,D1,D2,D3 gate;
  class F1 halt;
  class OKI,W1,STOP,MK skip;
  class AP pass;
  class A,P,R,E,I,I2,IM,PL2 step;
```

*図 5 — `make plan` は Terraform を一切実行しない。実行されるのは purge と `provider.tf` の書き出しだけ。*

> **`make plan` は tfplan を作りません。** `run_command` は dry_run のとき
> `side != "src"` のコマンドをすべて `[DRY RUN] 予定:` とログに出して実行せず返します。
> `terraform init` も `terraform plan -out=tfplan` も `side="local"` なので走らず、
> **`make run` は毎回ゼロから plan を計算し直します**。
> 実行ログの「→ tfplan を生成しました」は誤解を招く出力で、実体はありません。
>
> したがって `make plan` が通っても、既存リソースの import 可否・stale state の破棄・
> コピー先の API 有効化は検証されていません。`make run` で初めて 403 や 409 が出ることがあります
> （ただし有効 API の一覧取得だけは dry_run でも走るため、有効化対象の API 名は `make plan` でも表示されます）。

---

## Step 4.7 — サーバーレス複製（Terraform を使わない理由と、作らない判断）

`bulk-export` は **Cloud Run を 1 件も出力しません**。gcloud の asset type → KRM Kind 変換表に
`run.googleapis.com/Service` が無く、`export_resource_types` を渡す経路では
CAI のリストを作る時点で落ちるためです。そこで Terraform ではなく、
**それぞれのサービス自身の仕組み**で複製します。

Cloud Run の `replace` は「**送った spec が正**」＝ YAML から落ちたフィールドは
コピー先で既定値に戻る破壊的 API です。だからこのステップは
「移せないものは中途半端に作らない」方針に倒しています。

```mermaid
%%{init: {"theme":"base","themeVariables":{"fontFamily":"ui-monospace, Menlo, Consolas, 'Hiragino Kaku Gothic ProN', 'Yu Gothic', sans-serif","fontSize":"13px","lineColor":"#7C8D92","textColor":"#16242A","edgeLabelBackground":"#EDF1F1"}}}%%
flowchart TD
  L["src の Cloud Run サービス / ジョブ / Functions を列挙"] --> D0{"API が無効 = 未使用か"}
  D0 -->|"はい"| SK0["INFO 複製対象なし<br/>run を失敗にしない"]
  D0 -->|"いいえ"| SP{"リソース種別"}

  SP -->|"Cloud Run サービス / ジョブ"| D1{"gen2 Function の実体か<br/>serviceConfig.service で判定"}
  D1 -->|"はい"| FN["Run としては複製しない<br/>Function 側が担当する分担"]
  D1 -->|"いいえ"| D2{"移せない設定を含むか<br/>VPC / Cloud SQL / Secret / CMEK /<br/>サイドカー / GPU / リビジョン固定"}
  D2 -->|"含む"| K1["作らない + DIFF に要対応<br/>参照先が src のままの<br/>壊れたリソースを作らない"]
  D2 -->|"含まない"| RW["YAML を書き換え<br/>namespace / イメージ / 実行 SA / env<br/>未知のフィールドは触らない"]
  RW --> D3{"参照イメージが dst に在るか"}
  D3 -->|"無い"| K2["作らない + DIFF に要対応<br/>Image not found で<br/>tainted 化するのを防ぐ"]
  D3 -->|"在る"| V{"replace --dry-run の<br/>サーバ側検証"}
  V -->|"失敗"| ERR["このリソースだけ失敗<br/>他は続行し終端で exit 1"]
  V -->|"成功"| AP["replace を実行"]

  SP -->|"Cloud Functions"| D4{"複製できるか<br/>HTTP トリガ / ソース zip 取得可 /<br/>Secret・VPC・CMEK なし"}
  D4 -->|"いいえ"| K3["作らない + DIFF に要対応<br/>イベントトリガ / gen1 の<br/>sourceUploadUrl のみ 等"]
  D4 -->|"はい"| CP["ソース zip を<br/>src 認証で download →<br/>dst 認証で upload"]
  CP --> DP["gcloud functions deploy<br/>dst 側で再ビルド"]

  classDef gate fill:#FAF0DA,stroke:#8A6612,stroke-width:1.5px,color:#3D2E08;
  classDef skip fill:#E8E2F6,stroke:#5C4F91,stroke-width:1.5px,color:#2F2758;
  classDef halt fill:#F7DFDB,stroke:#9F3428,stroke-width:1.5px,color:#48160F;
  classDef pass fill:#DDEEE7,stroke:#0D6A53,stroke-width:1.5px,color:#0A382C;
  classDef step fill:#E4EAEC,stroke:#3E5257,stroke-width:1.2px,color:#16242A;
  class D0,SP,D1,D2,D3,D4,V gate;
  class SK0,FN,K1,K2,K3 skip;
  class ERR halt;
  class AP,DP pass;
  class L,RW,CP step;
```

*図 6 — 「作らない」判断が 3 箇所ある。いずれも DIFF.md に手順つきで出る。*

> **中途半端に作らないのは、気付けなくなるからです。** 参照先が src を指したまま
> コピー先にリソースを作ると、CAI とコピー先の差分には現れません
> （**存在はしている**ため）。実行時に初めて壊れていることが分かります。
> 一方「作らなかった」ものは DIFF.md の要対応に必ず出ます。
> だから迷ったら**作らない**側に倒します — GKE の除外判定（迷ったらコピーする）とは
> **逆向き**である点に注意してください。GKE は「落とし過ぎ = コピー漏れ」が損害でしたが、
> ここは「作り過ぎ = 気付けない故障」が損害だからです。

> **Cloud Run ジョブはテンプレートが 1 段深い。** サービスは
> `spec.template.spec`（RevisionSpec）ですが、ジョブは Execution を挟むため
> `spec.template.spec.template.spec`（TaskSpec）です。書き換えをパス決め打ちにすると
> **ジョブだけ素通りして src 参照のまま複製**されるため、`containers` を持つ dict を
> 走査する実装にしてあります。

> **Cloud Functions は HTTP トリガのみ対応です。** イベントトリガは参照先の
> トピック名やバケット名が `rename_rules` でコピー先では変わるうえ、Eventarc の
> チャネル設定も要るため、機械的に写すと**誤ったイベント源を購読する関数**に
> なりかねません。第 1 世代でソース zip が `sourceUploadUrl`（署名付きアップロード URL）
> しか無い場合も、gcloud にダウンロード手段が無いため複製できません。

---

## Step 5 / 5.5 — VM 復元と電源状態

復元中は**必ず RUNNING で残し**、全 VM が揃ってから電源状態を合わせます。
boot 途中の VM を suspend すると ACPI に応答できず失敗するためです。

```mermaid
%%{init: {"theme":"base","themeVariables":{"fontFamily":"ui-monospace, Menlo, Consolas, 'Hiragino Kaku Gothic ProN', 'Yu Gothic', sans-serif","fontSize":"13px","lineColor":"#7C8D92","textColor":"#16242A","edgeLabelBackground":"#EDF1F1"}}}%%
flowchart TD
  L["list_worker: src の VM 一覧"] --> D1{"is_gke_node_vm<br/>goog-gke-node ラベル、または<br/>gke-/gk3- かつ kube-* metadata"}
  D1 -->|"ノード"| S1["skipped<br/>dst クラスタが作り直す"]
  D1 -->|"利用者 VM"| U["復元 unit へ / VM 単位で並列"]
  U --> D2{"アタッチされた SA は"}
  D2 -->|"src の user-managed SA"| SA1["dst の同 ID SA に読み替え<br/>無ければ空 SA を冪等作成<br/>ロールは複製せず WARNING"]
  D2 -->|"proj_map 外の SA"| SA2["dst 既定 SA に落として WARNING"]
  D2 -->|"default compute"| SA3["除去"]
  SA1 --> C["instances create → RUNNING"]
  SA2 --> C
  SA3 --> C
  C --> SD["二次ディスク作成は並列<br/>attach は同一 VM 内で直列 / 409 回避"]
  SD --> WAIT["全 VM 完了後<br/>power_state_wait_seconds 待機"]
  WAIT --> D3{"src.status は"}
  D3 -->|"TERMINATED"| ST["stop / 失敗時は forceful"]
  D3 -->|"SUSPENDED"| SU{"suspend 成功?"}
  D3 -->|"transient / 不明"| RUN["RUNNING のまま残す"]
  SU -->|"成功"| OK["完了"]
  SU -->|"失敗"| WN["WARNING + 手動復旧コマンド<br/>exit code には影響させない"]

  classDef gate fill:#FAF0DA,stroke:#8A6612,stroke-width:1.5px,color:#3D2E08;
  classDef skip fill:#E8E2F6,stroke:#5C4F91,stroke-width:1.5px,color:#2F2758;
  classDef pass fill:#DDEEE7,stroke:#0D6A53,stroke-width:1.5px,color:#0A382C;
  classDef step fill:#E4EAEC,stroke:#3E5257,stroke-width:1.2px,color:#16242A;
  class D1,D2,D3,SU gate;
  class S1,SA2,SA3,WN,RUN skip;
  class C,OK,ST pass;
  class L,U,SA1,SD,WAIT step;
```

*図 7 — GKE ノードの除外は `list_worker` の 1 箇所だけ。復元と電源処理の両方に効く。*

---

## 失敗の扱い — 何が run を止めるか

扱いは大きく **止める / 記録して進む / 黙って落とす** に分かれます。走り出したあとは
**記録して進む**のが原則ですが、**実行中でも即座に止まる箇所があります**（下表の「実行中でも止める」）。
そこで止まっても失敗一覧は `finally` で必ず出力され、失われるのは未実行のステップと DIFF.md です。

| 事象 | 扱い | 実装 | 結果 |
|---|---|---|---|
| config.yaml の不備 / ORG 保護違反 | 止める | `validate_steps_config` | 全件列挙して即 exit 1 |
| 同じ作業ディレクトリで多重起動 | 止める | `_acquire_run_lock` | state 破壊を防ぐ |
| gcrane / crane が無い | 止める | `check_prerequisites` | `data_sync` 有効 かつ `artifact_registry.enabled != false` のときだけ判定。`make plan` でも止まる |
| コピー先プロジェクトが未作成 | 止める | `check_dst_projects_exist` | 30 分後の全滅を先回りする |
| mock 生成の `.tf` が残存 | そのルートのみ失敗 | `_terraform_one_project` | 他ルートは続行し、終端で exit 1 |
| API 有効化に失敗 | soft fail | `_soft_run` | WARNING + 手動コマンド案内。ただし `_soft_run` 自体は src 書込ガードと mock の未知コマンド検出で `sys.exit(1)` する |
| コピー元で AR / Cloud Run / Functions 未使用（API 無効の 403） | soft fail | `is_api_disabled_error` | INFO「複製対象なし」に落とす |
| サーバーレスが移せない設定を含む | 作らない + 要対応 | `run_service_unsupported_reasons` / `function_unsupported_reasons` | 壊れたリソースを作らない。DIFF に手順を出す |
| `replace --dry-run` の検証に失敗 | そのリソースのみ失敗 | `_sync_one_run_resource` | 他は続行し、終端で exit 1 |
| suspend が ACPI に失敗 | 警告のみ | `_try_dst_suspend` | exit code に影響させない |
| secure tag が未マッピング | skip + 警告 | `fw_policy_rule_flags` | FW を意図せず緩めない |
| コピー先で setIamPolicy 権限が無い | skip + 案内 | `_dst_can_set_iam_policy` | failed に積まない |
| import 失敗（already / missing） | 無視 | `import_error_kind` | 想定内。apply が作る |
| import 失敗（その他） | 警告のみ | `_first_meaningful_line` | 理由を 1 行で表示して続行 |
| コマンド失敗（`allow_fail=True` 未指定） | 実行中でも止める | `run_command` | 未実行のステップと DIFF.md は生成されない |
| CAI 未カバー / snapshot 不足 / customize の例外 | 実行中でも止める | `step_cai_scan` / `step_gce_snapshot` / `customize_hcl` | 同上 |
| active_dir 不在・対象 `.tf` なし（Step 4） | 実行中でも止める | `step_terraform_apply` | **dry_run / mock では停止せず**「✓ Step 4 完了」と表示して return する |

---

## ツールが運ばないもの

これらは不具合ではなく設計です。すべて `DIFF.md` に手順つきで出ます。

| 対象 | 理由 | 誰がやるか |
|---|---|---|
| GKE ノード VM の実体 | 構成のみ複製する方針。台数もマシンタイプもコピー元のまま引き継ぐ | コピー先クラスタが自分で作る |
| PV データ / k8s オブジェクト | クラスタ内は bulk-export の対象外 | Backup for GKE の restore、または再デプロイ |
| コピー元側の backup-plans 作成 | コピー元は read-only。ツールから実行しない | 利用者（コマンドは DIFF に掲載） |
| クロスプロジェクト restore | restore plan は別プロジェクトの backup plan を直接参照できない | backup-channels / restore-channels を作る |
| self-managed SSL 証明書 | 秘密鍵は API から export 不能 | 鍵を持つ利用者がコピー先で作成 |
| Secret Manager の値 | 秘密情報を自動で写さない方針 | 利用者 |
| イベントトリガの Cloud Functions | 参照先のトピック / バケット名がコピー先で変わる。誤ったイベント源を購読させない | 利用者（手順は DIFF に掲載） |
| 第 1 世代 Functions のうちソース取得不能なもの | `sourceUploadUrl` のみで gcloud にダウンロード手段が無い | 利用者（コンソールから zip を取得） |
| Filestore のデータ | Backup for GKE の volume backup は PD のみ | 利用者 |

---

出典: `scripts/sync_env.py` / `CLAUDE.md`
