# ISSUES — リファクタリング課題一覧（優先度順）

> **このファイルの使い方（実装者 = Claude 向け）**
> - 各 ISSUE は独立して着手可能。ただし **ISSUE-01 を最初に実装すると、以降の漏れ検出が自動化される**ため強く推奨。
> - 「根拠」の `file:line` は 2026-06-12 時点の `scripts/sync_env.py`（3134 行）基準。行番号がずれていたら関数名で grep すること。
> - 「CAI 実測」は `cai_export/cai_resources_*.txt`（src 3 プロジェクトの実スキャン結果）の `assetType` 集計に基づく。実環境の正解データとして参照可能。
> - 実装後は必ず `make mock`（fail-closed なので新コマンドは Mock パターン登録が必要）と `PYTHONPATH=. uv run pytest` を通すこと。
> - 修正対象は dst 側のみ。**src への書き込みコードを書いてはならない**（`run_command(side="src")` は read-only 強制）。

## サマリ表

| ID | 優先度 | 一言 | 区分 |
|----|--------|------|------|
| ~~01~~ | ~~**P0**~~ | ~~CAI アセット種別と複製カバレッジの突合せがなく、漏れが「静かに」発生する~~ ✅完了 | ~~検証基盤~~ |
| ~~02~~ | ~~**P0**~~ | ~~FW ポリシールール複製に複数ポート欠落などの忠実度バグ~~ ✅完了 | ~~バグ~~ |
| 03 | **P0** | Cloud Router / Cloud NAT が複製されず、private VM が外部通信不能 | 機能欠落 |
| 04 | **P1** | network_firewall ステップが host project のみ対象（service projects 未対応） | 機能欠落 |
| 05 | **P1** | 予約 IP（Address）を削除しており VM の内部 IP が変わってしまう | 設計課題 |
| 06 | **P1** | IAM カスタムロール・プロジェクト IAM バインディングが複製されない | 機能欠落 |
| 07 | **P2** | カスタム静的ルート（Route）が複製されない | 機能欠落 |
| 08 | **P2** | ResourcePolicy（スナップショットスケジュール）が複製されない | 機能欠落 |
| 09 | **P2** | classic FW ルールが terraform と Step 4.5 の二重管理になっている | 設計課題 |
| 10 | **P2** | 実行後の src ↔ dst 構成差分レポートがない | 検証基盤 |
| 11 | **P3** | LogSink / LogBucket（カスタムのみ）が複製されない | 機能欠落 |
| 12 | **P3** | Mock パターン登録が手動 2 箇所で保守漏れしやすい | 保守性 |
| 13 | **P3** | skip_on_run 時に stale な terraform/active を検出できない | 保守性 |

---

## ~~ISSUE-01 [P0] CAI スキャン結果と複製カバレッジの自動突合せ（漏れの可視化）~~ ✅完了

> **解決済み (2026-06-12)**: `_ASSET_COVERAGE` 定数と `diff_coverage()` 関数を導入。
> `step_cai_scan` 完了時に CAI 出力テキストから assetType を集計し、未登録種別は
> WARNING でログ列挙、`steps.cai_scan.fail_on_uncovered: true` で exit 1 化も可能。
> 単体テスト 5 件追加（TestAssetCoverage）。

### 問題
Step 1 (`step_cai_scan`, sync_env.py:1016) は CAI で src の全リソースを列挙して
`cai_export/cai_resources_<proj>.txt` に保存するが、**その結果はどのステップからも参照されていない**。
bulk-export（Config Connector）が対応していないアセット種別はエクスポートされず、
terraform にも Step 4.5 にも現れないため、**設定漏れが警告ゼロで発生する**。
firewall policy が漏れていた問題（commit 5f015dc で対処）はこの構造的欠陥の一例にすぎない。

### CAI 実測（src 3 プロジェクト合計）と現行カバレッジ
| assetType | 件数 | 現在の複製手段 | 漏れ |
|---|---|---|---|
| compute/Route | 53 | なし（自動生成分を除くカスタムルートが対象） | ⚠️ ISSUE-07 |
| compute/Subnetwork | 50 | terraform + `_replicate_host_networks` | OK |
| compute/Address | 14 | `_strip_reserved_ip` で**意図的に削除** | ⚠️ ISSUE-05 |
| compute/Firewall | 12 | terraform + Step 4.5 | OK（二重管理 → ISSUE-09） |
| compute/FirewallPolicy | 2 | Step 4.5 (host のみ) | ⚠️ ISSUE-04 |
| compute/Router | 1 | **なし** | ⚠️ ISSUE-03 |
| compute/ResourcePolicy | 1 | なし | ⚠️ ISSUE-08 |
| iam/Role (custom) | 7 | なし | ⚠️ ISSUE-06 |
| logging/LogSink・LogBucket | 12 | `_Default`/`_Required` はスキップ、カスタムは漏れ | ⚠️ ISSUE-11 |
| storage/Bucket, bigquery/* | 8 | terraform + data_sync | OK |
| compute/Instance・Disk・Snapshot | 56 | Step 5 (gce_restore) | OK |

### 実装方針
1. `sync_env.py` に **カバレッジマップ**（モジュールレベル定数）を追加:
   ```python
   # assetType → 複製を担当するステップ名。None = 意図的に対象外（理由をコメント）
   _ASSET_COVERAGE = {
       "compute.googleapis.com/Instance":   "gce_restore",
       "compute.googleapis.com/Firewall":   "network_firewall",
       "serviceusage.googleapis.com/Service": None,  # create_projects.py が有効化
       ...
   }
   ```
2. `step_cai_scan` の最後に、スキャン結果の assetType 集合と `_ASSET_COVERAGE` を突合せ、
   **未知の assetType を WARNING で列挙**する（`⚠ 未対応アセット: compute.googleapis.com/Router x1 — 複製されません`）。
3. config に `steps.cai_scan.fail_on_uncovered: false`（既定 false）を追加し、true なら未知アセット検出で exit 1。
4. mock 用: `_simulate_command` の `gcloud asset search-all-resources` 応答に Router/Route 等を含むダミーを追加し、WARNING 経路をテスト可能にする。

### 受け入れ条件
- [ ] src に未対応アセットがあると `make plan` のログに種別と件数が WARNING 表示される
- [ ] `_ASSET_COVERAGE` に CAI 実測の全 30 種が登録済み（対象外には理由コメント必須）
- [ ] pytest: カバレッジ突合せ関数の単体テスト（未知種別 → 警告リスト返却）

---

## ~~ISSUE-02 [P0] FW ポリシールール複製の忠実度バグ修正~~ ✅完了

> **解決済み (2026-06-12)**: `fw_policy_rule_layer4()` と `fw_policy_rule_flags()` を
> 純粋関数として外出しし、複数ポート展開・全フィールド対応・SA email の dst 置換を統一実装。
> `_sync_network_firewall_policies` は global + region 両 scope を回るよう全面書き直し、
> association の存在判定も list ベースに変更（_sync_one_fw_policy / _sync_fw_policy_rules /
> _sync_fw_policy_associations / _discover_src_regions に分割）。
> 単体テスト 11 件追加（TestFwPolicyRuleConversion）。

### 問題
`_sync_network_firewall_policies` (sync_env.py:2226) のルール複製が不完全で、
**dst に「似て非なる」セキュリティ設定が作られる**。サイレントな設定差分は P0。

### 確認済みのバグ（sync_env.py:2300-2316 周辺）
1. **複数ポート欠落**: `m.get('ports',[''])[0]` が先頭ポートしか取らない。
   `tcp:80,443` のルールが `tcp:80` になる。→ `";".join(ports)` 形式
   （gcloud の layer4-configs は `tcp:80,tcp:443` のようにプロトコルごと列挙）に修正。
2. **未複製フィールド**: `targetSecureTags` / `targetServiceAccounts` / `disabled` /
   `enableLogging` / `description` / `srcSecureTags` を読み取っておらず付与もしない。
3. **regional ポリシー未対応**: `--global` がハードコード。CAI で `location: asia-northeast1` の
   FirewallPolicy が来た場合 list にすら表れない（`network-firewall-policies list` は `--regions` 指定が別）。
   src の list を `--global` と各 region で行い、describe/create にも同じスコープを伝播させる。
4. **association の describe 構文**: `associations describe --name=...` は gcloud バージョンによって
   `list` からのフィルタしかできない。`associations list --firewall-policy=... --format=json` の結果から
   name 一致で存在判定する方式に変える（_gcloud_exists ではなく run_command + JSON parse）。

### 実装方針
- ルール複製を「フィールドごとの flag 変換テーブル」方式にリファクタ:
  ```python
  _POLICY_RULE_FLAGS = [
      ("match.srcIpRanges",   "--src-ip-ranges",  ","),
      ("targetServiceAccounts", "--target-service-accounts", ","),
      ("disabled",            "--disabled",       None),  # bool flag
      ...
  ]
  ```
- 比較用に「src ルールの正規化 dict」→「dst describe の正規化 dict」を突き合わせ、
  既存でも**内容が違えば WARNING**（更新はしない。冪等の原則は維持）。

### 受け入れ条件
- [ ] 複数ポート (`tcp:80`,`tcp:443`) のルールが dst でも同一の layer4Configs になる
- [ ] disabled / logging / description / target SA が保持される
- [ ] regional ポリシーが存在する mock データで create コマンドに `--region` が付く
- [ ] pytest: ルール → gcloud フラグ変換の単体テスト（変換テーブル駆動）

---

## ISSUE-03 [P0] Cloud Router / Cloud NAT の複製

### 問題
CAI 実測で src host に `compute.googleapis.com/Router` が 1 件存在する（NAT 設定を内包）。
bulk-export は Router/NAT を出力せず、`_replicate_host_networks` (sync_env.py:2830) も
VPC とサブネットのみ。**README:198 は「VPC・サブネット・NAT・FW 等を再現」と謳っており実装と乖離**。
SPEC.md も「Cloud Router および Cloud NAT ゲートウェイを配置・管理する」と明記している。
NAT がないと外部 IP なし VM（この構成の標準）は OS アップデート等の外部通信が全滅する。

### 実装方針
1. `_replicate_host_networks` の直後（または独立メソッド `_replicate_routers_and_nat`）で:
   - `gcloud compute routers list --project=<src_host> --format=json`（side=src）
   - 各 router: `gcloud compute routers describe <name> --region=<region> --project=<src_host> --format=json` で `nats[]` を取得
   - dst に `gcloud compute routers create <name> --network=<net> --region=<region> --asn=<asn>`（既存なら skip）
   - 各 NAT: `gcloud compute routers nats create <nat_name> --router=<name> --region=<region> --auto-allocate-nat-external-ips --nat-all-subnet-ip-ranges`
     （src の `sourceSubnetworkIpRangesToNat` / `natIpAllocateOption` を反映。手動 IP 割当は dst では auto に落とす — 理由コメント必須）
2. `_MOCK_KNOWN_PATTERNS` に `gcloud compute routers list/describe/create` と `gcloud compute routers nats create` を追加、
   `_simulate_command` に router 1 件 + NAT 1 件のダミー応答を追加。
3. `_SRC_PERMS_BY_STEP` / `_DST_PERMS_BY_STEP` に `compute.routers.list` / `compute.routers.create` を追加。

### 受け入れ条件
- [ ] `make mock` で Step に router/NAT 複製ログが出る
- [ ] 冪等: 2 回目の実行で `既存。スキップ` になる
- [ ] ISSUE-01 のカバレッジマップで `compute.googleapis.com/Router` が covered になる

---

## ISSUE-04 [P1] network_firewall ステップの service projects 対応

### 問題
`step_network_firewall` (sync_env.py:2106) は `project_mapping.host_project` のみ処理する。
classic FW ルールは VPC 単位なので Shared VPC 構成なら host 中心で正しいが、
**service project 固有の VPC（default 等）に付いたルールと、service project の
FirewallPolicy は漏れる**。CAI 実測では Firewall 12 件が 3 プロジェクトに分散している。

### 実装方針
- `step_network_firewall` のループを `self._iter_project_pairs()`（sync_env.py:986 付近、全ペア yield）に変更。
- `_sync_classic_firewall_rules` / `_sync_network_firewall_policies` は既に (src, dst, sa) を引数に取る設計なので、
  呼び出し側のループ拡張だけで対応可能（メソッド署名変更不要）。
- 注意: service project の default VPC 上のルールは、dst 側に default VPC が存在する前提。
  network 不在で create が失敗するケースは `allow_fail=True`（現状踏襲）+ WARNING ログで通知。

### 受け入れ条件
- [ ] mock 実行で host + service 2 つ = 3 プロジェクト分の FW 同期ログが出る
- [ ] 既存テスト 35 件が引き続き green

---

## ISSUE-05 [P1] 内部 IP 保持戦略（_strip_reserved_ip の再設計）

### 問題
`_strip_reserved_ip` (sync_env.py:1649) は予約アドレスの固定 IP 指定を**一律削除して自動採番**にしている。
CAI 実測で Address は 14 件。dst の VM が src と異なる内部 IP になるため、
IP 直書きのアプリ設定・/etc/hosts・FW ルールの IP 条件が**移行後に壊れる**。
SPEC.md の「完全な同期コピー」要件と矛盾。

### 実装方針（段階的）
1. config に `rename_rules.internal_ip.preserve: true`（既定 true）を追加。
2. preserve=true の場合:
   - サブネット CIDR が src/dst で同一（`_replicate_host_networks` が同一 CIDR で複製している）なら
     固定 IP をそのまま残す（= `_strip_reserved_ip` を呼ばない）。
   - `purpose=GCE_ENDPOINT` 等の予約アドレスも terraform にそのまま通す
     （クロスプロジェクト参照のみ既存 skip ロジック維持: sync_env.py:1615 付近）。
3. preserve=false なら現行動作（自動採番）。
4. Step 5 (`_build_restore_nic`, gce_restore 系) も同様に、src VM の `networkIP` を
   `--private-network-ip` に引き継ぐ経路を確認・統一する（vmware/ 側には既に同等機能あり、参考実装: `vmware/scripts/vmdk_run.py` の `internal_ip.mode == "ip"`）。

### 受け入れ条件
- [ ] preserve=true（既定）で .tf 内の `address = "10.x.x.x"` が保持される
- [ ] pytest: `_strip_reserved_ip` の分岐テスト（preserve true/false）
- [ ] README の「予約 IP の固定 IP 指定を外し自動採番」記述を更新

---

## ISSUE-06 [P1] IAM カスタムロールとプロジェクト IAM バインディングの複製

### 問題
CAI 実測で `iam.googleapis.com/Role`（カスタムロール）が 7 件。bulk-export の出力に
custom role が含まれる場合の import 処理は存在する（sync_env.py:2029 付近の
`google_project_iam_custom_role` ID 変換）が、**プロジェクトレベルの IAM バインディング
（誰にどのロールが付いているか）は CAI にも bulk-export にも現れず、完全に漏れる**。

### 実装方針
1. 新ステップ `iam_sync`（Step 4.7、config: `steps.iam_sync.enabled`、既定 false で opt-in）を追加。
   既定 false の理由: src の IAM メンバー（人間ユーザー）を dst にそのまま付けるのが
   正しいとは限らないため、明示的有効化とする。
2. 処理:
   - `gcloud projects get-iam-policy <src> --format=json`（side=src、`get-iam-policy` は
     `_READ_ONLY_VERBS` 登録済み: sync_env.py:34）
   - bindings から `deleted:` メンバーと Google 管理 SA（`@gcp-sa-`, `@cloudservices`,
     `<number>-compute@developer`）を除外
   - SA メンバーのうち src プロジェクトの SA は dst の対応 SA に email 置換
     （`_build_proj_id_map` + プロジェクト番号 map `proj_num_map` を利用）
   - dst へ `gcloud projects add-iam-policy-binding <dst> --member=... --role=...`（冪等: add は重複可）
3. config に `steps.iam_sync.exclude_members: []`（正規表現リスト）を用意。

### 受け入れ条件
- [ ] mock で get-iam-policy → add-iam-policy-binding の流れがシミュレートされる
- [ ] 人間ユーザー (`user:`) を含めるかは `include_users: false`（既定）で制御
- [ ] ORG 保護: src への書き込みが発生しないことをテストで保証

---

## ISSUE-07 [P2] カスタム静的ルートの複製

### 問題
CAI 実測で Route 53 件。大半はサブネット自動生成ルート（複製不要）だが、
`nextHopGateway`（default-internet-gateway）等の**手動作成ルートが混在していても区別せず全て漏れる**。

### 実装方針
- `_replicate_host_networks` 内（または ISSUE-03 と同じ新メソッド）で:
  - `gcloud compute routes list --project=<src_host> --format=json`（side=src）
  - **自動生成の除外条件**: `name` が `default-route-` で始まる、または
    `nextHopNetwork`/`nextHopSubnetwork` 由来のものは skip
  - 残り（カスタムルート）を dst に `gcloud compute routes create`（network 名置換、冪等 describe→create）
- Peering/VPN 由来の動的ルートは対象外（理由コメントを書く）。

### 受け入れ条件
- [ ] default-route-* が複製対象から除外される（単体テスト）
- [ ] カスタムルート 1 件を含む mock データで create が走る

---

## ISSUE-08 [P2] ResourcePolicy（スナップショットスケジュール）の複製

### 問題
CAI 実測で `compute.googleapis.com/ResourcePolicy` 1 件。スナップショットスケジュールは
bulk-export 対象外で、Step 5 で復元したディスクにもアタッチされない。
**dst 環境で日次バックアップが黙って止まる**運用リスク。

### 実装方針
1. `gcloud compute resource-policies list/describe`（side=src）→ dst に
   `gcloud compute resource-policies create snapshot-schedule`（schedule/retention を引き継ぎ）。
2. Step 5 のディスク復元後、src で当該ディスクにアタッチされていた policy
   （`disks describe` の `resourcePolicies[]`）を
   `gcloud compute disks add-resource-policies` で dst ディスクに付け直す。
3. region は policy の self_link から取得し dst でも同 region に作成。

### 受け入れ条件
- [ ] policy 本体の複製とディスクへの再アタッチが mock で確認できる
- [ ] 冪等: 既存 policy / アタッチ済みはスキップ

---

## ISSUE-09 [P2] classic FW ルールの管理主体統一（terraform と Step 4.5 の二重管理解消)

### 問題
classic FW ルール（CAI 実測 12 件）は bulk-export → terraform (Step 4) でも作られ、
Step 4.5 (`_sync_classic_firewall_rules`) でも describe→create される。
現状は「既存ならスキップ」で衝突こそしないが、**所有者が 2 つあるため
（a) terraform state にあるルールを 4.5 が再作成しようとする紛らわしいログ、
（b) src 側の変更がどちらの経路で反映されるか不定**という保守性問題がある。

### 実装方針（どちらかを選択。推奨は A）
- **A: terraform から FW を外し、Step 4.5 に一本化**
  - `_skip_reason_for_file` (sync_env.py:1572) に
    `resource "google_compute_firewall"` → `"FW は Step4.5 (network_firewall) が管理"` を追加。
  - VM/disk を Step 5 に寄せた既存パターン（sync_env.py:1603 付近）と同型なので一貫性が高い。
- B: Step 4.5 から classic FW を外し、FirewallPolicy 専任にする
  - bulk-export が FW を確実に出力すること（Shared VPC ネット参照の self_link 化込み）が前提。
    過去に 404 問題があった経緯（HISTORY.md / commit ee0abec 参照）があり非推奨。

### 受け入れ条件
- [ ] FW ルールの作成経路が 1 つになり、`make run` 2 回目で FW 関連の no-op が明確にログされる
- [ ] 既存テストと mock が green

---

## ISSUE-10 [P2] 実行後の src ↔ dst 構成差分レポート（verify ステップ）

### 問題
`make run` 完了後、複製が「どこまで一致したか」を確認する手段がログの目視しかない。
漏れの検出が次回トラブル時まで遅延する。

### 実装方針
1. 新ステップ `verify`（Step 7、`steps.verify.enabled` 既定 true、read-only なので安全）:
   - src/dst 両方で同種 list を実行（side=src / side=dst、いずれも read-only）:
     `networks list` / `subnets list` / `firewall-rules list` / `routers list` /
     `instances list` / `storage buckets list` / `bq ls`
   - 名前ベース（GCS はリネームルール適用後の期待名）で突合せ、
     `一致 / dst欠落 / dst余剰` を表形式でログ + `logs/<ts>/verify_report.md` に出力。
2. 差分があっても exit 0（レポートが目的）。`steps.verify.fail_on_diff: true` で CI 用に exit 1。
3. ISSUE-01 のカバレッジマップとセットで「構造的漏れ（種別ごと）」と「個別漏れ（リソースごと）」の双方を検出できる。

### 受け入れ条件
- [ ] mock 実行で verify_report.md が生成される
- [ ] dst 欠落リソースが表に現れる単体テスト（list 結果の突合せ関数）

---

## ISSUE-11 [P3] カスタム LogSink / LogBucket の複製

### 問題
`_skip_reason_for_file` は `_Default`/`_Required` シンクを正しく除外するが（sync_env.py:1590 付近）、
**カスタムシンク・カスタムログバケットは bulk-export 出力に含まれれば通る一方、
含まれない環境では漏れる**。CAI 実測 12 件は全て `_Default`/`_Required` 系のため現環境では実害なしだが、
カバレッジマップ (ISSUE-01) 上は「条件付き」になっている。

### 実装方針
- ISSUE-01 のカバレッジマップで `logging.googleapis.com/LogSink` を `bulk_export(partial)` と注記し、
  `step_cai_scan` の突合せで `displayName` が `_Default`/`_Required` 以外のシンクを検出したら
  個別 WARNING を出す（複製自体は bulk-export 経路に任せる）。

### 受け入れ条件
- [ ] カスタムシンク入りの CAI mock データで WARNING が出る

---

## ISSUE-12 [P3] Mock コマンド登録の一元化

### 問題
新しい gcloud コマンドを追加するたびに `_MOCK_KNOWN_PATTERNS` (sync_env.py:47) と
`_simulate_command` (sync_env.py:798) の **2 箇所を手で同期**する必要がある。
fail-closed 設計自体は正しいが、登録漏れで `make mock` が落ちるまで気づけない。

### 実装方針
- パターンと応答生成関数をペアにしたレジストリへ統合:
  ```python
  _MOCK_REGISTRY: list[tuple[str, Callable[[str, str], str] | str]] = [
      ("gcloud compute routers list", _mock_routers_list),
      ("gcloud compute routers create", "Success"),
      ...
  ]
  ```
  `is_known_mock_command` と `_simulate_command` は同じレジストリを参照。
- 追加で pytest: 「sync_env.py 内で組み立てられる全 gcloud コマンド文字列のプレフィックスが
  レジストリに存在する」ことを静的に検査するテスト（grep ベースで `f"gcloud` リテラルを抽出）。

### 受け入れ条件
- [ ] パターン追加が 1 箇所で済む
- [ ] レジストリ網羅性テストが存在し、登録漏れで CI が落ちる

---

## ISSUE-13 [P3] skip_on_run 時の stale な terraform/active 検出

### 問題
`bulk_export.skip_on_run: true`（dst/config.yaml:121）の場合、`make run` は
`terraform/active/` を再利用する。**src が plan 実行後に変更されていても気づけない**。
dst 切替検出（`.dst_project` マーカー, sync_env.py:1841 付近）はあるが鮮度検証はない。

### 実装方針
- `make plan`（customize 完了時）に `terraform/active/.generated_at`（UTC ISO8601）を書き出す。
- `make run` の skip 判定時に経過時間を確認し、`steps.bulk_export.max_active_age_hours`
  （既定 24）を超えていたら WARNING + 「`make plan` の再実行を推奨」をログ表示
  （停止はしない。既存ワークフローを壊さないため）。

### 受け入れ条件
- [ ] 閾値超過の active を再利用すると WARNING が出る
- [ ] マーカー欠如（旧世代の active）でも安全に WARNING 扱い

---

## 付録: 実装時の共通注意（全 ISSUE 共通）

1. **ORG 保護を壊さない**: src 向け新コマンドは list/describe/get のみ。
   `_READ_ONLY_VERBS` (sync_env.py:31) との整合を確認。
2. **新 gcloud コマンドは 3 点セット**で追加:
   ① `_MOCK_KNOWN_PATTERNS`（ISSUE-12 実装後はレジストリ）
   ② `_simulate_command` のダミー応答
   ③ `_SRC_PERMS_BY_STEP` / `_DST_PERMS_BY_STEP` の代表権限
3. **冪等の型**: `_gcloud_exists`（describe）→ 不在のみ create、`allow_fail=True`。
   既存の `_sync_classic_firewall_rules` (sync_env.py:2129) が参考実装。
4. **ログは日本語**で `desc` / `explanation` を必ず付ける（logs/<ts>/{org,dst}.log の規約）。
5. **README / SPEC.md の同期**: ステップ追加・挙動変更時は README の
   「ドライランで計画・検証される項目」「クローンのメカニズム」、SPEC.md の該当節を更新。
6. テスト: `PYTHONPATH=. uv run pytest`（現在 35 passed が基準線）。
