# CLAUDE.md

## プロジェクト概要

GCP プロジェクトを丸ごと別 ORG にコピーするオーケストレータ。  
`scripts/sync_env.py` が中心。src プロジェクトは read-only、書き込みは dst のみ。

**Tech stack**: Python 3.13 / uv, GCP (gcloud/bq/terraform), VMware (VMDK → GCE import)

**主要コマンド**:
```
make mock       # ローカル試走（GCP 接続不要）
make plan       # ドライラン
make run        # 本番実行（dst に書き込む）
make test       # pytest
make vmware-all # VMware → GCE フル処理
```

**テスト**: `PYTHONPATH=. uv run pytest`  
**Mock**: `uv run python3 scripts/sync_env.py --mock --no-dry-run`

---

## トークン節約（必須）

- **返答は簡潔に**。コードを書いたら「何をした」の説明は 1 文以内。
- **ファイル読み直し禁止**。Edit/Write 直後に同じファイルを Read しない。
- **ツール前の前置き禁止**。ツールを呼ぶだけ。
- **grep/find は Bash で直接**。単純な検索に Agent/Explore を使わない。
- **バックグラウンド実行を積極活用**。長時間コマンドは `run_in_background: true`。
- **並列ツール呼び出しを積極活用**。依存関係のない操作は同一メッセージで並列実行。
- **コメントは書かない**。WHY が非自明な場合のみ。
- **テスト結果の全行出力禁止**。成功/失敗のサマリー行だけ示す。

## セキュリティ注意事項

- src プロジェクトへの書き込みは禁止（コード上も強制 = `is_src_read_only` ガード）。これは impersonate の有無に関わらず常時適用される最終防衛線。
- SA impersonation は `CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT` 経由のみ（推奨だが必須ではない）。`config.yaml` の `*_impersonate_service_account` 未指定はエラーにせず、ローカル認証（gcloud のアクティブアカウント / ADC）にフォールバックする。
  - その認証主体が **src プロジェクトに書込相当の権限**（`_SRC_DANGEROUS_PERMS`）を持っていれば、`check_service_accounts` が事前に対象プロジェクトと付与権限を列挙して警告し、`[y/N]` で続行確認する。
  - 非対話セッションは `COPY_ALL_ENV_AUTO_APPROVE=1` を明示指定したときのみ続行（デフォルトは abort）。
- `.env` / `*.key` / `*.json`（サービスアカウントキー）は絶対に編集・コミットしない

## 禁止事項

- 勝手に変更を加える・デプロイしない（テストであっても）
- 変更を行う場合は、ユーザーにコマンド操作を促してログを貼るよう促す

## 並列化方針（パフォーマンス）

各ステップの直列処理が長時間化するため `_parallel_for_each` で並列化済み。`config global.parallel_jobs` (推奨 8) を上限としたスレッドプールで実行。

| ステップ | 並列単位 | 備考 |
|---|---|---|
| 0a create-projects (provision) | dst プロジェクト | `create_projects.py:_provision_one` を ThreadPoolExecutor で並列化。counter は `self._lock` 保護 |
| 0b SA preflight (`check_service_accounts`) | (SA, project) | `make plan` / `make run` の最初に毎回走る。token 発行 + testIamPermissions を並列化 |
| 1 cai_scan | プロジェクト | src read-only |
| 2 gce_snapshot | プロジェクト | 既存 |
| 3 bulk_export | プロジェクト | 既存 |
| 4 terraform_apply (init / plan / apply) | プロジェクト (Terraform ルート) | 各 `terraform/active/<src>/` は state 分離済。`_terraform_one_project` を `_parallel_for_each` で並列化 |
| 4.5 network_firewall (classic rules) | ルール | `_sync_classic_firewall_rules` 内 |
| 4.5 network_firewall (policy rules) | ルール | `_sync_fw_policy_rules` 内 |
| 5 gce_restore (listing) | プロジェクト | VM/snap 一覧並列取得 |
| 5 gce_restore (restore) | **VM** | flat (project, vm) units で並列 |
| 5 secondary disks (create) | ディスク | attach は同一 VM 内で直列（409 回避） |
| 5.5 power state (stop / suspend) | VM | `_finalize_vm_power_states` の pending を並列実行 |
| 5.7 iam_sync (src policy 取得) | プロジェクト | src read-only |
| 5.7 iam_sync (付与) | **dst プロジェクト** | プロジェクト内は直列（add-iam-policy-binding は read-modify-write で etag 競合する） |
| 6 data_sync (GCS/BQ) | バケット / テーブル | 既存 |

実装ルール:
- 共有可変状態（dict など）に書き込む場合は `threading.Lock()` で保護。`StageStats` は組み込み Lock 済み。
- 並列 worker から `sys.exit(1)` しない（他 worker を巻き添えで止める）。代わりに `stats.add_failure()` + `stats.incr("failed")` で記録し return。`main()` が最終的に exit 1 で抜ける。
- 同一リソースへの並列操作は API レベルで競合する場合があるので注意（例: 同一 VM への並列 attach-disk は 409 になる → attach は直列）。
- worker 内では `dst_logger` / `org_logger` をそのまま使ってよい（Python logging はスレッドセーフ）。

## ハマりどころ（既知の落とし穴）

### Terraform skip_on_run と `.dst_project` マーカー（Step 3 / 4）

- `terraform/active/<src>/.dst_project` は **customize_hcl と `_reset_stale_state_if_needed` の両方が書く**。意味は「active/ が customize 済みの dst」と「state が apply 済みの dst」を兼ねる。
- 旧実装では `_reset_stale_state_if_needed` (Step 4) だけが書いていたため、`make plan` (dry_run) では Step 4 がガード (`if not self.dry_run`) で素通りしてマーカーが残らず、次の `make run` で Step 3 skip_on_run が必ず stale 判定になり customize が毎回再実行されていた (regression)。
- 現在は `customize_hcl` 末尾で `proj_map[name]` を書き込み、`make plan → make run` (同一 dst) で確実にスキップパスに乗る。
- dry_run では `customize_hcl` が `.tf` を実書き出ししない (`if self.dry_run: continue`) ので、マーカーも更新しないこと。.tf と marker の整合が崩れる。
- マーカーが「customize 済み」と「apply 済み」の両方の意味を持つようになったため、`_reset_stale_state_if_needed` は **マーカー一致でも state 本文に現 dst が無ければ stale** と判定する (state は apply でしか更新されない)。両判定を OR で評価する。

### GCE 復元と電源状態（Step 5 / 5.5）

- `_restore_one_vm` は src.status に関わらず **常に VM を RUNNING で残す**（新規作成は `instances create` 直後、既存差し替えは末尾の `instances start`）。
- 電源状態の反映は Step 5 の最終フェーズ `_finalize_vm_power_states` で実施: 全 VM の復元完了後にまとめて TERMINATED / SUSPENDED に揃える。`config.steps.gce_restore.power_state_wait_seconds` (既定 120) だけ待ってから実行することで、guest OS の boot 完了を待つ。
- **suspend は失敗しやすい**: GCE suspend は guest OS が ACPI S3 シグナルに 3 分以内に応答する必要があり、boot 直後 / 非対応 OS / GPU・TPU 付き / Confidential VM / メモリ 208GB 超 / CSEK 付き等で失敗する。`_try_dst_suspend` は `subprocess` を直接呼んで stats を汚さず、失敗時は WARNING + 手動復旧コマンドを案内するだけ（run 全体の exit code は影響を受けない）。
- TERMINATED は `run_command(allow_fail=True)` のまま（stop は ACPI 失敗時に forceful fallback があり通常成功）。
- transient (`PROVISIONING / STAGING / STOPPING / REPAIRING / SUSPENDING`) と不明値は pending リストに入れず RUNNING のまま残す。
- 新しい状態遷移コマンド（`suspend` / `resume` など）を増やす時は **`_WRITE_VERBS`（src 拒否リスト）と `_MOCK_KNOWN_PATTERNS`（mock 許容リスト）の両方** に追加。片方だけだと src で実行される / mock が fail-closed で止まる。
- **user-managed SA は src email のまま dst VM にアタッチできない**（SA は project スコープ。cross-project attach は org policy `iam.disableCrossProjectServiceAccountUsage` 既定 enforced + actAs で "does not have access to service account" になる。regression: my-osaka）。`_resolve_dst_vm_service_account` が proj_map で `<id>@<dst_proj>.iam.gserviceaccount.com` に置換し、dst に無ければ**空 SA を冪等作成**（IAM ロールは複製せず WARNING で手動付与を案内）。proj_map 外プロジェクトの SA は dst 既定 SA に落として WARNING（secure_tag と同じ安全側パターン）。default compute SA（`<番号>-compute@`）は従来どおり除去。

### Network Firewall Policy（Step 4.5）

- **scope flag はサブコマンドごとに違う**。誤ると `unrecognized arguments`。
  - `list` → `--regions=R1,R2`（複数形）
  - `describe` / `create`（ポリシー本体）→ `--global` / `--region=R`
  - `rules ...` / `associations create` → `--global-firewall-policy` / `--firewall-policy-region=R`
  - 変換は `fw_rule_scope_flag()`（`scripts/sync_env.py`）に集約。新規コマンド追加時は必ず通すこと。
- **`fw_policy_rule_flags()` は REST API の FirewallPolicyRule 全フィールドに対応させること**。
  INGRESS ルールは `srcIpRanges / srcThreatIntelligences / srcAddressGroups / srcFqdns / srcSecureTags / srcRegionCodes / srcNetworkScope` のいずれかが必須、EGRESS ルールは対応する `dest*` が必須（gcloud 仕様）。
  フィールドを取りこぼすと `Must specify src_... for ingress direction` / `Could not fetch resource:` で失敗する。
  特に Threat Intelligence（`srcThreatIntelligences` → `--src-threat-intelligence`）は頻出。
- **別 ORG コピーで secure tag はそのまま使えない**。`tagValues/<数値ID>` は ORG スコープの permanent ID で dst ORG に存在せず、`rules create` が `Could not fetch resource:` で失敗する。
  - dst ORG で同等タグを作成し `config steps.network_firewall.secure_tag_map` に `src tagValues → dst tagValues` を登録すると変換して複製。
  - 未登録タグを参照するルールは **FW を意図せず緩めないようスキップ + WARNING**（エラーにしない）。`fw_policy_rule_flags(rule, proj_map, secure_tag_map)` / `_fw_secure_tag_map()` 参照。
- 同種の「別 ORG では ID が変わるリソース」（org policy 制約、tag key/value、IAM の org スコープロール等）は同じパターンで config マッピング or スキップ＋WARNING を検討する。
- **dst host VPC topology は Step 4.5 開始時点で存在している必要がある**。FW rule の `--network=` と FW policy association の `--network=` は dst host の `shared-vpc` 等を参照するため、未作成だと `Could not fetch resource: 'projects/<dst_host>/global/networks/<name>' was not found` で全 FW 操作が失敗する（regression）。
  - dst host VPC の作成は `_replicate_host_networks()` の責務。元々は `step_gce_restore` (Step 5) からしか呼ばれず Step 4.5 が先に走って詰んでいたため、現在は **`step_network_firewall` の冒頭でも呼ぶ**。冪等 (`_gcloud_exists` ガード) なので Step 5 で再度呼ばれても describe のみで安全。
  - 追加の防御として `_sync_classic_firewall_rules` 冒頭で参照される dst network を一括 pre-flight チェックし、`_sync_fw_policy_associations` でも assoc 単位で existence チェックして、未存在なら skip + WARNING に倒す（cryptic な API エラー量産を防ぐ）。
  - Shared VPC ホスト化・サービスプロジェクト関連付け・networkUser 付与は別途 `bootstrap_shared_vpc.sh` の担当。VPC 自体は作らない点に注意。

### IAM ロール複製（Step 5.7 / `step_iam_sync`）

- **既定で有効**（`steps.iam_sync` キーが無くても走る）。`_STEP_ENABLED_DEFAULTS` + `step_enabled()` に集約。**`execute()` と `check_service_accounts()` の両方がこの関数を通ること** — 片方だけ既定が違うと「preflight は権限を要求しないのに本体は走る」不整合になる（network_firewall が実際にそうなっていた）。
- 複製元は **src 各プロジェクトの project IAM ポリシーのみ**。バインディングは SA ではなくリソース側にあるので、バケット / データセット / SA 自身の IAM は対象外（対象にすると走査範囲が無限定になる）。
- 変換は純粋関数に分離してテストする: `parse_user_managed_sa()` / `remap_sa_email()` / `remap_iam_role()` / `build_iam_replication_plan()`。
- **別 ORG で ID が変わるものはスキップ + WARNING**（secure tag と同じパターン）:
  - ORG カスタムロール `organizations/<id>/roles/<r>` … dst ORG に同 ID が無い
  - project_mapping 外のプロジェクトのカスタムロール / SA
  - 条件付きバインディング（条件式が src のリソース名を参照しうる）
  - いずれも **dst の権限が src より緩くならない方向**にだけ倒すこと。
- default compute (`<番号>-compute@developer`) / appspot / Google 管理 service agent (`service-<番号>@` / `gcp-sa-*`) は `parse_user_managed_sa()` の時点で弾く。dst に同等物が既定で存在するため複製してはいけない（warning も出さない。出すと大量ノイズになる）。
- **`roles/owner` 等も src と同じなら複製する**（＝忠実再現が既定）。ただし `_IAM_HIGH_PRIVILEGE_ROLES` に載っているロールを付与したら `_warn_high_privilege_grants()` が実行ログ末尾に「何を・どこに・なぜ・取消コマンド」を WARNING でまとめて出す。高権限ロールを増やすときはこの集合に足す。
- **`resourcemanager.projects.setIamPolicy` は `_DST_PERMS_BY_STEP` に入れない**。既存の dst SA（roles/editor 等）には無く、preflight で fail-fast にすると bootstrap 再実行まで移行全体が止まる。代わりに `_dst_can_set_iam_policy()` が dst プロジェクト単位で確認し、無ければ **スキップ + 手動 `add-iam-policy-binding` コマンド案内**（failed カウントしない）。判定不能（トークン不可・API 不通）は `None` を返して「あるものとして続行」。
- 付与に必要なロールは `bootstrap_dst_sa.sh` の `ROLES` に `roles/resourcemanager.projectIamAdmin` として追加済み。src 側の read は `resourcemanager.projects.getIamPolicy`（`roles/viewer` に含まれる。絞ったカスタムロールを使う `bootstrap_cross_project.sh` には明示追加済み）。
- 付与は **dst プロジェクト単位で並列 / プロジェクト内は直列**。`add-iam-policy-binding` は read-modify-write なので同一プロジェクトへ並列実行すると etag 競合（ABORTED）になる。
- `--condition=None` を必ず付ける（既存ポリシーに条件付きバインディングがあると gcloud が対話プロンプトを出してハングする）。

### VPC Service Controls の quota project（Step 7）

- `gcloud access-context-manager perimeters describe/update` は **org/policy スコープのコマンドで `--project` を持たない**。quota/billing project を明示しないと gcloud が **ローカル `gcloud config` の `core/project`（移行と無関係なプロジェクト）** を quota に使い、そのプロジェクトで API 無効のまま `accesscontextmanager.googleapis.com ... SERVICE_DISABLED` / `(y/N)?` プロンプトで失敗する（regression）。
- 対策: `steps.vpc_sc.billing_project`（**必須・明示指定。自動補完/フォールバックしない**）を `--billing-project=` で両コマンドに付与し、`step_vpc_sc` 冒頭でその project に `accesscontextmanager` API を有効化（冪等 / allow_fail）してから describe/update する。`_get_perimeter_resources(..., billing)` 経由。
- **誤ったプロジェクトを自動推測しない**のが安全方針: `billing_project` は明示必須（host dst や先頭 dst へ勝手にフォールバックしない）。`vpc_sc.enabled=true` で `access_policy` / `perimeter` / `billing_project` のいずれかが空なら、`validate_steps_config()` が **`make plan`/`make run`/`make mock` 開始時に fail-fast でエラー**（`load_config` で `exit 1`）。`step_vpc_sc` 側の未設定 skip は多層防御として残してある。同種の「無関係 default を掴むと事故になる」設定は明示必須にし、`validate_steps_config()` に検査を足す。

### config.yaml の実行前検証（fail-fast）

- 設定不備で `make run` の終盤や途中で失敗 / 黙ってスキップするのを防ぐため、`load_config` が **`validate_config()`（ORG 保護）** と **`validate_steps_config()`（有効ステップの設定不備）** の両方を実行し、`[ORG 保護]` / `[設定不備]` を全件列挙して `exit 1`。
- `validate_steps_config()` は純粋関数（config dict → エラー文字列 list）。`enabled` なステップだけ検査する（無効ステップの未設定は無視）。現在: `vpc_sc`（3 項目必須）/ `rename_rules.gcs`（method 列挙・suffix/prefix の value 空）/ `gce_snapshot.max_age_days`（正の整数）。
- **新ステップに必須設定を足したらここに検査を追加する**。自動補完で握り潰さず、未設定は明示エラーにして実行前に気付かせる方針。
- `make mock` でも走る（mock 用の不完全 config を許容しない）。テストは純粋関数を直接叩く（`TestValidateStepsConfig`）。
- describe には `--quiet` を付けて API 無効時の対話プロンプトでハングしないようにする。
- 同種の「`--project` を取らない org/policy スコープ gcloud コマンド」（org policies, access policies 等）は同様に quota project を明示すること。

## Git コミット

- コミットメッセージに Claude の名前や署名を付けないこと
  - `Co-Authored-By: Claude ...` 行を付けない
  - `🤖 Generated with Claude Code` などの行も付けない
  - 勝手にコミットやプッシュしない

## ツール
以下のツールを積極的に使う
- GCP関連のコードは、Developer Knowledge APIを積極的に使う
- それ以外はContext7で最新のドキュメントを確認する

