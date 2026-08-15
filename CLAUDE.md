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
  - 続行確認をスキップしたい場合は `--yes` / `-y`（`make plan YES=1` / `make run YES=1`）を**コマンドラインで明示指定**する。非対話セッションは `--yes` があるときのみ続行（デフォルトは abort）。
  - **環境変数による自動承認は採用しない**（過去の `COPY_ALL_ENV_AUTO_APPROVE` は廃止）。export したまま忘れると「気付かないうちに毎回承認済み」になるため、承認は必ず起動コマンドに現れる形にする。同種の危険操作の承認フラグを増やすときもこの方針に従う（Makefile 側も `YES :=` で環境変数を無視し、コマンドライン指定のみ有効にしてある）。
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
| 1.5 enable_apis | (src, dst) ペア | プロジェクト内は chunk 直列（batchEnable が 20 件上限 + 失敗時は 1 件ずつ再試行） |
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
| 3.7 artifact_registry (列挙) | プロジェクト | `ar-list`。repos/images の list + dst 既存 digest の一括取得 |
| 3.7 artifact_registry (コピー) | **イメージ** | flat (project × repo × image) units で並列（`ar-copy`）。プロジェクト間の直列待ちを無くす |
| 6 data_sync (GCS/BQ) | バケット / テーブル | 既存 |

実装ルール:
- 共有可変状態（dict など）に書き込む場合は `threading.Lock()` で保護。`StageStats` は組み込み Lock 済み。
- 並列 worker から `sys.exit(1)` しない（他 worker を巻き添えで止める）。代わりに `stats.add_failure()` + `stats.incr("failed")` で記録し return。`main()` が最終的に exit 1 で抜ける。
- 同一リソースへの並列操作は API レベルで競合する場合があるので注意（例: 同一 VM への並列 attach-disk は 409 になる → attach は直列）。
- worker 内では `dst_logger` / `org_logger` をそのまま使ってよい（Python logging はスレッドセーフ）。

## ハマりどころ（既知の落とし穴）

### 実行前の fail-fast（dst 実在 / 多重起動）

- **`check_dst_projects_exist()`**: config の dst を新番号に変えて `make projects` を忘れると、Step 1〜3（src read + soft fail）は素通りし **30 分後の Step 4 apply で初めて** `The resource 'projects/<dst>' was not found` で全滅する（regression: 081401 系）。execute 冒頭で全 dst を `projects describe` し、ACTIVE でなければ全件列挙 + `make projects` 案内で即 exit 1（mock はスキップ、plan でも実行）。新規プロジェクト直後は describe が権限エラーになる伝播期間があり、その間もこのチェックで止まる（待ってから再実行でよい）。
- **`_acquire_run_lock()`（多重起動ガード）**: 同じ terraform 作業ディレクトリで `make run`/`make plan` が並走すると、state lock 競合・`Saved plan is stale`・`-lock=false` import の並走で**両方の run が壊れる**（regression: run 二重起動で 3 ルート失敗）。`<tf_base>/.sync_env.lock` を非ブロッキング flock し、取れなければ即エラー。プロセス終了で OS が自動解放するので stale lock 掃除は不要。mock は `terraform/mock/` の別ロックで実行系と競合しない。

### Terraform skip_on_run と `.dst_project` マーカー（Step 3 / 4）

- **実行時上書き**: `make run SKIP_ON_RUN=0`（= `--no-skip-on-run`）で config を触らず今回だけ export/customize を強制再実行できる（`SKIP_ON_RUN=1` は逆）。customize 側の修正を active に反映させたいときは 0 を使う。YES と同じく Makefile は `:=` で環境変数を無視（コマンドライン指定のみ有効）。

- `terraform/active/<src>/.dst_project` は **customize_hcl と `_reset_stale_state_if_needed` の両方が書く**。意味は「active/ が customize 済みの dst」と「state が apply 済みの dst」を兼ねる。
- 旧実装では `_reset_stale_state_if_needed` (Step 4) だけが書いていたため、`make plan` (dry_run) では Step 4 がガード (`if not self.dry_run`) で素通りしてマーカーが残らず、次の `make run` で Step 3 skip_on_run が必ず stale 判定になり customize が毎回再実行されていた (regression)。
- 現在は `customize_hcl` 末尾で `proj_map[name]` を書き込み、`make plan → make run` (同一 dst) で確実にスキップパスに乗る。
- dry_run では `customize_hcl` が `.tf` を実書き出ししない (`if self.dry_run: continue`) ので、マーカーも更新しないこと。.tf と marker の整合が崩れる。
- マーカーが「customize 済み」と「apply 済み」の両方の意味を持つようになったため、`_reset_stale_state_if_needed` は **マーカー一致でも state 本文に現 dst が無ければ stale** と判定する (state は apply でしか更新されない)。両判定を OR で評価する。

### terraform import の失敗分類（Step 4 / `_terraform_import_existing`）

- import 失敗の無視判定は `import_error_kind()`（純粋関数）に集約。"already"（state 取り込み済み）と "missing"（リモート実体なし = apply が作る）だけ無視し、それ以外を警告に出す。
- **404 の表現は provider ごとに違う**: compute は `Error 404 ... notFound`、GKE は `googleapi: Error 404: Not found:`、terraform 本体は `Cannot import non-existent remote object`、containeranalysis は `does not exist`。`status code: 404` だけ見ていた頃は GKE クラスタの import 失敗が「本当の失敗」扱いで警告に出ていた。
- **`_first_meaningful_line` は gcloud の構造化エラー詳細（`- '@type': ...google.rpc.ErrorInfo` の YAML ダンプ）を除外し、`ERROR:`（gcloud, 大文字）と `Error:`（terraform）の両方を拾う**。旧実装は `'Error' in ln` の部分一致だったため大文字 `ERROR:` を取り逃し、代わりに `ErrorInfo` を含む詳細行を返して**失敗詳細が全部 `- '@type': ...` になっていた**（regression）。
- 警告の理由は `_first_meaningful_line(err, out)` で出す。**末尾行だと terraform のエラー枠（╵ + 空行）で必ず空文字になり原因が読めない**（regression: `import 失敗: ... : ` と理由空欄で出ていた）。stderr / stdout どちらに出るかも provider 依存なので両方を見る。

### GCE 復元と電源状態（Step 5 / 5.5）

- `_restore_one_vm` は src.status に関わらず **常に VM を RUNNING で残す**（新規作成は `instances create` 直後、既存差し替えは末尾の `instances start`）。
- 電源状態の反映は Step 5 の最終フェーズ `_finalize_vm_power_states` で実施: 全 VM の復元完了後にまとめて TERMINATED / SUSPENDED に揃える。`config.steps.gce_restore.power_state_wait_seconds` (既定 120) だけ待ってから実行することで、guest OS の boot 完了を待つ。
- **suspend は失敗しやすい**: GCE suspend は guest OS が ACPI S3 シグナルに 3 分以内に応答する必要があり、boot 直後 / 非対応 OS / GPU・TPU 付き / Confidential VM / メモリ 208GB 超 / CSEK 付き等で失敗する。`_try_dst_suspend` は `subprocess` を直接呼んで stats を汚さず、失敗時は WARNING + 手動復旧コマンドを案内するだけ（run 全体の exit code は影響を受けない）。
- TERMINATED は `run_command(allow_fail=True)` のまま（stop は ACPI 失敗時に forceful fallback があり通常成功）。
- transient (`PROVISIONING / STAGING / STOPPING / REPAIRING / SUSPENDING`) と不明値は pending リストに入れず RUNNING のまま残す。
- 新しい状態遷移コマンド（`suspend` / `resume` など）を増やす時は **`_WRITE_VERBS`（src 拒否リスト）と `_MOCK_KNOWN_PATTERNS`（mock 許容リスト）の両方** に追加。片方だけだと src で実行される / mock が fail-closed で止まる。
- **user-managed SA は src email のまま dst VM にアタッチできない**（SA は project スコープ。cross-project attach は org policy `iam.disableCrossProjectServiceAccountUsage` 既定 enforced + actAs で "does not have access to service account" になる。regression: my-osaka）。`_resolve_dst_vm_service_account` が proj_map で `<id>@<dst_proj>.iam.gserviceaccount.com` に置換し、dst に無ければ**空 SA を冪等作成**（IAM ロールは複製せず WARNING で手動付与を案内）。proj_map 外プロジェクトの SA は dst 既定 SA に落として WARNING（secure_tag と同じ安全側パターン）。default compute SA（`<番号>-compute@`）は従来どおり除去。

### GKE は構成のみコピー / ノード VM は除外（Step 2 / 3 / 4.5 / 5 / 99）

- **方針**: クラスタ / ノードプールの構成は Terraform（Step 3 export → Step 4 apply）で複製し（**ノード台数 `node_count` / `initial_node_count`・マシンタイプ・ディスク・`node_locations`・management/upgrade_settings まで src のまま引き継ぐ**。引き継がないのはノード VM の実体だけ）、**GKE が自分で作り直すもの（ノード VM・インスタンステンプレート・MIG・オートスケーラー・`gke-*` / `k8s-*` FW ルール）は一切コピーしない**。PV データとクラスタ内 k8s オブジェクトは対象外（利用者が再デプロイ）。config フラグは持たず常時この挙動。
- **ノード判定は `is_gke_node_vm()` に集約。名前だけで判定しない**。第一判定は GKE が必ず付ける `goog-gke-node` ラベル。ラベルが無い場合のフォールバックは `gke-` / `gk3-` 接頭辞 **かつ** ノード metadata キー（`kube-labels` / `kube-env` / `cluster-name`）の AND。名前だけで切ると `gke-` で始まる**利用者 VM を誤って除外**する（コピー漏れ = 実害。安全側は「コピーする」方向）。
- **k8s Service (type=LoadBalancer) が作る LB リソース（target pool / forwarding rule / health check / FW）は名前が `a<31hex>`（hex UID）で接頭辞判定に掛からない**。第一判定は description の **kubernetes.io 所有者マーカー**（`{"kubernetes.io/service-name":...}` 等。service controller が必ず書く）= `has_k8s_owner_marker()` / `is_gke_managed_fw_rule()`。名前側の保険は `is_k8s_lb_resource_name()`（a+31hex）と hex 構造正規表現。**マーカー無しで落とすと利用者リソースの誤除外**（regression 教訓と同じ）、**落とし損ねると除外済み `k8s-*` health check への宙ぶらりん参照で apply 404**（regression: my-argolis の target pool `a0cb2a...`）。
  - `google_compute_forwarding_rule` は `_GKE_MANAGED_TF_RESOURCE_TYPES` に**入れない**（接頭辞判定させない）。マーカーがあるときだけ `_skip_reason_for_file` が落とす。
  - classic FW ルールの除外は `is_gke_managed_fw_rule()`（マーカー + 構造）に変更済み。`k8s-nodeport-allow` / `gke-admin-bastion` のような**利用者ルール（DENY かもしれない）は落とさない**。
- **GKE Gateway（`gkegw1-`）/ NEG（`k8s1-`）/ Ingress GLBC が作る LB 一式（backend service / URL map / target proxy / forwarding rule / NEG）は `_GKE_GATEWAY_TF_RESOURCE_TYPES` で落とす**。health check だけが `_GKE_MANAGED_TF_RESOURCE_TYPES` の接頭辞判定で除外されるため、これらを残すと宙ぶらりん参照で apply が毎回 404 になる（regression: my-argolis の `gkegw1-gqew-*` BackendService → `healthChecks/gkegw1-* was not found`）。判定は所有者マーカー（Gateway: description の `k8sResource`+`k8sCluster` = `has_gke_gateway_marker()` / NEG: `cluster-uid`+`service-name` / Ingress: `kubernetes.io/*`）**または**コントローラ専用の狭い接頭辞 `_GKE_LB_CONTROLLER_NAME_PREFIXES`（`gkegw1-` / `k8s1-` / `k8s2-` / `k8s-be-` 等）。**広い `gke-`/`k8s-` は使わない**（利用者 LB の誤除外 = コピー漏れ）。**Gateway の backend service は description を持たない**ため接頭辞が唯一の判定材料。skip 時は `_add_customize_note("gke_gateway", ..., resource=<Gateway パス>)` で **Gateway 単位に 1 行へ畳んだ「確認」note**（IP 変更に伴う DNS 切替 / certificate map・SSL 証明書はクラスタ外で複製されない）を DIFF に出す。CAI 側も `_GKE_DERIVED_ASSET_TYPES` に BackendService / UrlMap / TargetHttp(s)Proxy を登録済み（GKE 命名なら P3）。
- **判定は `gke_managed_tf_skip_reason()`（純粋関数）に集約し、customize (Step 3) と apply 直前 (Step 4 `_purge_gke_managed_tf_files`) の 2 箇所から呼ぶ**。customize だけに置くと **`skip_on_run: true` が customize ごとスキップして古い `active/` をそのまま apply する**ため、修正しても同じ 404 が出続ける（regression: 修正後の `make run` が SKIP_ON_RUN=1 で再発）。purge は dry_run でも実行する（`make plan` の plan をそのまま `make run` が使うので、消さないと plan と apply がずれる）。`_MOCK_TF_MARK` 検出と同じ「Step 3 で落とす + Step 4 で最終防衛」の二段構え。
- **Backup for GKE の restore で dst に再生成されるものは複製しない（総点検済みのルール）**。k8s オブジェクト復元→コントローラ再生成の連鎖で戻るリソースをツールが先回りコピーすると、宙ぶらりん参照 404 か孤児リソースになる。上記 LB 一式のほか:
  - **動的プロビジョニング命名は `is_k8s_provisioned_name()`（`pvc-<uuid>` / `mcrt-<uuid>`、接頭辞 + uuid の AND）**。`mcrt-<uuid>` の `google_compute_managed_ssl_certificate` は skip（ManagedCertificate 復元で dst が発行し直す。利用者の managed cert は uuid 命名でないので残す）。DIFF は SslCertificate × mcrt-uuid → P3。`pvc-*` ディスクは disk 全 skip（Step 5 管理）で元から複製されず、volume restore が dst で新規ディスクを作る。
  - **Gateway の L7 FW ルール（`gkegw1-gqew-l7-<network>-global`）は description が固定文言でマーカー無し・hex 構造無し** → `is_gke_managed_fw_rule()` に `gkegw1-` 接頭辞判定を追加済み（Step 4.5 のすり抜け対策。コントローラ専用接頭辞なので接頭辞だけで判定してよい）。
  - **`google_gke_backup_backup_plan` / `_restore_plan` は skip**（DIFF の gke_backup_restore 手順で利用者が手動作成する移行用リソースそのもの。複製すると二重管理 + cluster 文字列参照の順序 404）。CAI の `gkebackup.googleapis.com/*` 8 種は `_ASSET_COVERAGE` で None。
  - **Cloud DNS for GKE のクラスタゾーン（`gke-<cluster>-<hash>-dns` / `-rp`）**は skip（`_GKE_DNS_ZONE_NAME_RE` の構造判定。利用者の `gke-` 始まりゾーンは残す）。
  - **Filestore（`pvc-*` 含む）は Backup for GKE の volume backup 対象外**（PD のみ）なので、この除外ファミリーに入れてはいけない（データ移行は利用者の手動作業）。
- **Step 2 (`step_gce_snapshot`) は GKE ノードを検証対象から外す**。ノードには移行用スナップショットが存在しないため、除外しないと `errors` に積まれて `sys.exit(1)` し、**GKE がある src では `make plan` / `make run` が丸ごと止まる**（実際そうなっていた）。除外は INFO のみで `errors` に入れない。
- **Step 5 (`step_gce_restore`) のフィルタは `list_worker` の 1 箇所だけ**。復元 unit 展開と `_finalize_vm_power_states` の pending は同じ `project_data["vms"]` を読むので、ここで落とせば両方に効く。除外数は `stats.incr("skipped")`。
- **Step 3 の `.tf` 間引きは `_skip_reason_for_file`**。`_GKE_MANAGED_TF_RESOURCE_TYPES`（instance_template / (region_)instance_group_manager / (region_)autoscaler / instance_group / health_check / target_pool / route / firewall）に該当し、かつ `name = "..."` が `is_gke_managed_name()` を満たすファイルだけ落とす。**`google_container_cluster` / `google_container_node_pool` は絶対に落とさない**（これが複製本体）。
- `_GKE_MANAGED_NAME_PREFIXES`（`gke-` / `gk3-` / `k8s-` / `k8s1-` / `k8s2-` / `gkegw1-`）に接頭辞を足すと **3 箇所（`_skip_reason_for_file` / `_sync_classic_firewall_rules` の skip / `classify_missing_asset`）に同時に効く**。追加時は 3 箇所すべての影響を確認すること。
- Step 4.5 の GKE FW ルール除外は **dst network の pre-flight より前**に置く（存在しない dst network 参照で cryptic なエラーを出す前に落とす）。
- **Backup for GKE の前提を dst 側で満たすのはツールの責務**（`_fix_provider_compat`）。`gke_backup_agent_config.enabled` を **必ず true** にする（src が false ならフリップ、`addons_config` ごと無ければ追加）。**復元先クラスタにエージェントが無いと restore できない**（`addonsConfig.gkeBackupAgentConfig.enabled: true` が公式の必須要件）ため、src の値をそのまま複製すると「復元できないクラスタ」を作ってしまう。バックアップ機能の追加であって「dst が緩くなる」変更ではないので例外的に src と変えてよい。src 側の有効化は read-only なので DIFF の手順で案内する。
- **クロスプロジェクト restore は channel が必須**（公式: *restore plan は別プロジェクトの backup plan を参照できない*）。別 ORG / 別プロジェクトへの移行が前提の本ツールでは、DIFF の手順を **backup-channels / restore-channels + サービスエージェント権限**（`roles/gkebackup.serviceAgent` / `roles/gkebackup.crossProjectServiceAgent`）込みで書くこと。同一プロジェクト前提の手順（backup-plans → restore-plans だけ）は**そのままでは動かない**。
- **GKE の手動移行アドバイスは「Backup for GKE 前提」で書く（ルール）**。クラスタ構成のみ複製する方針のため、ワークロード・PV・Secret の移行は利用者の手動作業になる。その案内は:
  - customize がクラスタ `.tf` を書くたびに `_add_customize_note("gke_backup_restore", ...)` → DIFF に**要対応**でクラスタごとに掲載（src 側 backup-plans/backups create + dst 側 restore、`gkebackup.googleapis.com` 有効化、`gke_backup_agent_config` の確認まで）。**src 側の backup 作成はツールから実行しない**（src read-only）— コマンド案内のみ。
  - `classify_missing_asset` の GKE 派生 P3 と `format_diff_report` の k8s.io 除外注記も「Backup for GKE の restore（または再デプロイ）」の文言に統一。
  - SSL 証明書 note には「クラスタ外 Compute リソースなので Backup for GKE でも移行されない」と明記（Cloud Run 前段 LB の証明書を GKE 移行で解決できると誤解しないように）。
  - 同種の「ツール対象外の手動移行」を増やすときも、DIFF に手順つき note を出すこと。
- **DIFF (Step 99)**: `k8s.io/*` / `*.k8s.io` の asset type（`_is_k8s_asset_type()`）は `diff_coverage` と `analyze_cai_tf_diff` の両方で差分から除外（クラスタ内オブジェクトは数百件出てノイズになる）。`_GKE_DERIVED_ASSET_TYPES`（InstanceTemplate / InstanceGroupManager / InstanceGroup / NetworkEndpointGroup / TargetPool / ForwardingRule / HttpHealthCheck / HealthCheck / Autoscaler）は名前が GKE 管理接頭辞 **または k8s LB の hex UID 命名（`is_k8s_lb_resource_name`）** なら **reference P3（対応不要）**、そうでなければ action のまま（利用者テンプレートを握り潰さない）。
- **Cloud Run のサーバーレス NEG は bulk-export が出力しない**（region NEG の照合漏れではなく本当に未出力。`_CAI_TO_TF_RESOURCE` には region/global 変種も登録済み）。DIFF で action になるのは正しい（利用者が LB を手動再構築する）。
- 新しい gcloud コマンドは増やしていないので `_WRITE_VERBS` / `_MOCK_KNOWN_PATTERNS` は変更不要。mock には GKE ノード VM（`goog-gke-node` ラベル付き・スナップショット無し）と `gke-*` インスタンステンプレート `.tf` を仕込んであるので、除外が壊れると `make mock` が落ちる。

### dst API 事前有効化（Step 1.5 / `step_enable_apis`）

- **なぜ要るか**: dst で API が無効だと Step 4 の `terraform apply` が `<API> has not been used in project ... before or it is disabled` の 403 で止まる。**GKE (`container.googleapis.com`) が典型**（regression: GKE ありの src で `make run` が Step 4 で全滅）。`_ensure_dst_prereq_apis`（Step 4 冒頭）は CRM/ServiceUsage/IAM の 4 つだけなので足りない。
- **既定で有効**（`steps.enable_apis` キーが無くても走る）。`_STEP_ENABLED_DEFAULTS` + `step_enabled()`。
- **必要 API が確定する時点は Step 3 完了後**（bulk_export + customize で `active/<src>/*.tf` が出揃った時）。それ以前は「src で有効な API」しか分からず、export された `.tf` 固有の API を取りこぼしうる。そのため `step_enable_apis()` を **2 回**呼ぶ（`final` 引数で分岐）:
  - **Step 1.5（`cai_scan` 直後 / `final=False`）** … src 由来を先行有効化し、Step 2/3 の実行中に伝播時間を稼ぐ。
  - **Step 3.5（`bulk_export` 直後・terraform の直前 / `final=True`）** … `.tf` 由来を含めた**全量**を有効化し、**want 全体**が enabled として見えるまで `_wait_for_apis_enabled` で確認してから Step 4 に進む（新規有効化分だけの確認では「元から無効だったのに誰も気付かない」を許してしまう）。全プロジェクト分をまとめてここで確保するので、apply 直前の per-project 有効化（`_ensure_dst_prereq_apis`）は通常なにも見つけない最終防衛線になる。
  - 実測（本リポジトリの src）: src の `services list` が読める通常時は Step 3.5 の追加は 0 件（src 由来が上位集合）。**src の一覧が読めない場合に my-argolis で 7 件を `.tf` 由来だけで救える**。
  - **mock 生成物ガードは実行時のみ**（`not self.mock and tf_dir_has_mock_artifacts(...)`）。mock では `_tf_base_dir()` が `terraform/mock/` を指すのでそこの `.tf` が正であり、無視すると mock が TF 由来パスの回帰テストにならない。dry_run は customize が `.tf` を書かないため「引けなかった」警告を出さない。
- 有効 API の取得元は 2 系統で、片方が欠けても動く:
  1. `gcloud services list --enabled`（src read-only。`serviceusage.services.list` が要る）
  2. Step 1 の CAI 出力の `serviceusage.googleapis.com/Service`（`cai_api_hints()`。**追加権限なしで有効 API 一覧が取れる**のでフォールバックとして重要）
  さらに `_STEP_DST_APIS`（有効ステップが dst で必ず叩く API）と `_BASE_DST_APIS` を必ず足す。
- **soft fail に徹する**（`stats.failed` に積まない = `make run` の exit code を落とさない）。`run_command` ではなく `_soft_run()`（`_try_dst_suspend` と同じ方針）を使う。本当に必要な API なら後続ステップが本来のエラーで止めるので二重報告しない。
- `gcloud services enable` は **20 件/回**（batchEnable の上限 = `_API_ENABLE_BATCH`）。**batch は 1 件でも不正だと chunk 全体が失敗する**ので、chunk 失敗時は 1 件ずつ再試行して「本当にダメな API」だけ WARNING + 手動コマンド案内に残す。
- `_DST_API_SKIP` に入れてよいのは **単体では有効化できない API だけ**（廃止・旧エイリアス・親 API に伴って自動で付く内部サービス）。契約や申請が要るだけの実 API（`edgecache` 等）は入れない — 黙って消すより有効化を試して WARNING で見せる方が安全側。除外した API は必ず INFO でログに出す（skip 判断の誤りに気付けるように）。
- 権限は **preflight（`_SRC_PERMS_BY_STEP` / `_DST_PERMS_BY_STEP`）に足さない**。`serviceusage.services.list`（src）も `serviceusage.services.enable`（dst、`roles/editor` に含む）も、無い環境で fail-fast にすると bootstrap 再実行まで移行全体が止まる。iam_sync の `setIamPolicy` と同じ扱いで「その場でスキップ + 案内」に倒す（`bootstrap_cross_project.sh` の `CUSTOM_PERMS` には追加済み）。
- **基盤 API（`_BASE_DST_APIS` = CRM / ServiceUsage / IAM / IAMCreds）は `skip_apis` でも `enabled: false` でも外させない**。enable 失敗時の案内が「不要なら skip_apis へ」と勧めるため、transient 失敗の対処で基盤 API を skip に入れると terraform が一切動かない dst を作り続ける。`build_api_enable_plan` は減算後に必ず再加算し、Step 4 の `_ensure_dst_prereq_apis` も同じ扱い（off-switch で止まるのは `.tf` 由来分だけ）。
- **`gcloud services list/enable` には必ず `--quiet`**。serviceusage API 自体が無効なプロジェクトでは「enable and retry? (y/N)」の対話プロンプトが出る。src 側で y と答えると src への書き込みになり、`is_src_read_only` はコマンド文字列しか見ないため防げない（非対話でも timeout までハング）。
- `steps.enable_apis` の設定値は **enabled: false でも Step 4 が読む**が、`validate_steps_config` は有効ステップしか検査しない。`wait_seconds` は `coerce_nonneg_int()` で読んで型不正でも落とさない（worker 内 ValueError は run 全体を traceback で落とす）。`wait_seconds: 0` は「待たない」= `_wait_for_apis_enabled` を呼ばない（呼ぶと必ず偽の timeout 警告が出る）。
- 純粋関数（`api_from_asset_type` / `cai_api_hints` / `build_api_enable_plan`）に分離してテストする。mock には src と dst で差が出る `gcloud services list` を仕込んであるので、差分計算が壊れると `make mock` の Step 1.5 で「追加 0 件」になる。

### mock 生成物と実行用 terraform ディレクトリの分離（Step 3 / 4）

- **mock は `terraform/mock/{raw,active}` に出力する**（`_tf_base_dir()` が `self.mock` のとき `<output_dir>/mock` を返す）。同じ `terraform/active/` を使うと `make mock` のダミー `.tf` が残り、直後の `make run` が `skip_on_run: true` で「既存 active を再利用」して **dst にダミーリソースを本当に作る**（regression: mock 直後の run で `org-bucket-shared-data-*` バケットが 4 プロジェクトに作成され、`mock-cluster` は container API 無効の 403 で失敗した ＝ **403 は症状であって原因ではない**）。
- `_tf_base_dir()` は **terraform 配下を参照する全箇所**（`step_bulk_export` / `step_terraform_apply` / `_emit_cai_tf_diff` / `_resolve_gcs_rename_value`）で使うこと。1 箇所でも config 直読みが残ると mock と実行が同じ dir を共有して元の事故に戻る。
- 分離前に汚染された環境のため、**内容からも検出する**（`tf_dir_has_mock_artifacts()`）。判定は `_MOCK_TF_MARK`（`_write_dummy_tf_files` が各ダミー `.tf` の先頭に入れるコメント。customize の置換を通っても残る）と、マーク導入前の残骸用の `_LEGACY_MOCK_TF_LABELS`（mock_vm / mock_bucket / mock_cluster / mock_gke_template）。**照合は宣言の 2 番目ラベル（`_LEGACY_MOCK_DECL_RE`）に限定**する。裸の部分文字列だと実リソースの `name = "mock_bucket"`（GCS はアンダースコア可）まで誤検知し、apply 拒否 + `rm -rf` 案内で正当な移行が止まる。マーカーファイルではなく `.tf` の中身で判定するので、`.tf` を作り直せば自動的に解消する（マーカーの寿命管理が要らない）。
- 検出時の挙動は 2 段:
  - Step 3 の `skip_on_run` 再利用パス … active（と raw 再利用パスでは raw）に mock 生成物があれば再利用せず bulk-export からやり直す（自己修復）。
  - Step 4 `_terraform_one_project` 冒頭 … それでも残っていれば **apply せず** `stats.add_failure` + 削除コマンド案内（最終防衛線。worker なので `sys.exit` しない）。
- **mock に新しいダミー `.tf` を足すときは必ず `_MOCK_TF_MARK` 行を入れる**。入れ忘れると検出をすり抜けて dst に実リソースが作られる。

### apply 直前の API 有効化（Step 4 / `_ensure_dst_prereq_apis`）

- Step 1.5（src の有効 API を dst に反映）だけでは、src の `services list` が読めない / export された `.tf` の親 API が src で無効だった等で取りこぼし、`terraform apply` が 403 で止まる。**これから apply する `.tf` から必要 API を引き直す**のが最も確実。
- `tf_type_to_api()` は `google_` を除いた型名の**前方一致**。`_TF_TYPE_API_PREFIX_MAP` はモジュールロード時に**長い順へソート**されるので dict の記述順は問わない（`container_registry` が `container` より先に当たる）。**未知の型は None＝何も有効化しない**（誤った API を有効化しない安全側）。
- 走査は Terraform ルート直下の `.tf` のみ（`tf_required_apis()`）。terraform 自体がサブディレクトリを再帰しないので active/<src> は平坦。
- `_ensure_dst_prereq_apis(dst_proj, dst_sa, proj_dir)` が `_BASE_DST_APIS` + TF 由来 API の差分だけ有効化し、`_wait_for_apis_enabled` で伝播を待つ。**soft fail**（`_enable_apis_on_dst` 経由で `stats.failed` に積まない）。Step 1.5 と同じ方針。
- Step 1.5 でも active/<src> があれば TF 由来 API を `extra_apis` として足す（`make plan` 済みなら早い段階で有効化できる）。ただし mock 生成物のディレクトリは無視する。

### flatten 時の resource ラベル重複と provider 非互換（Step 3 / customize_hcl）

- **bulk-export はラベルをリソース名だけから作る**ため、同名リソースが複数 location にあると（例: Artifact Registry の `cloud-run-source-deploy` が asia-northeast1 と us-central1）、customize の平坦化で同一 Terraform ルートに同じ (type, label) が同居し `Duplicate resource ... configuration` で init/plan ごと落ちる（regression: my-argolis）。`dedupe_tf_resource_labels()`（純粋関数）が衝突ブロックだけ `<label>_<location>` に改名し、同一ファイル内の参照（`# terraform import` コメント / `<type>.<label>` / `data.<type>.<label>`）も追従させる。**`data` ブロックも対象**（resource と data は別名前空間なので seen のキーは (kind, type, label) の 3 要素）。宣言の改名は findall+全文 re.sub ではなく **span 置換 + 参照の 2 段階トークン置換**: 同一ファイルに `x` と `x_asia` が並ぶと、全文 sub は既存ブロックを巻き込み両方が同一ラベルに潰れて Duplicate を自ら作る（regression）。
- **改名は走査順依存（先勝ちで元ラベル維持）なので walk は必ずソート**（`dirs.sort()` + `sorted(files)`）。順序が実行ごとに変わると前回 state のアドレスと食い違い destroy/create 差分が出る。dedupe の呼び出し位置は `_skip_reason_for_file` の**後**（skip されたファイルのラベルを登録しない）。
- **provider 非互換の吸収は `_fix_provider_compat`**。bulk-export (config-connector) は古い provider スキーマ相当の HCL を出すため、現行 provider では落ちるものがある。provider の版固定ではなく customize で内容を直す（他リソースの新フィールドを巻き添えにしないため）。現在の補正:
  - GKE 廃止ブロック除去: `_GKE_REMOVED_TF_BLOCKS`（`cluster_telemetry` / `pod_security_policy_config` / `protect_config`）。クラスタ .tf は複製本体なので**ファイルごと skip せず**ブロックだけ落とす（`strip_hcl_blocks()`）。
  - 必須化された引数の補完（`ensure_hcl_block_arg()`）: `iap.enabled = true`（**false に倒すと dst で認証壁が外れて公開される**。「緩くならない方向」原則）/ `advanced_datapath_observability_config.enable_relay = false`（API 既定値）。
  - GKE の別リソース node_pool 運用: cluster に `initial_node_count = 1` + `remove_default_node_pool = true` を補完（GKE API は「ノードプール 0 個のクラスタ」を作れず `Cluster.initial_node_count must be greater than zero` の 400 になる。既定プールを残すと別リソース側の同名 `default-pool` 作成が 409）。**inline `node_pool {}` を持つクラスタと Autopilot は対象外**（`remove_default_node_pool` は `enable_autopilot` と ConflictsWith）。
  - GKE の node pool `network_config`: `pod_range`（既存 secondary range を名前参照）がある場合、`pod_ipv4_cidr_block` を除去（provider 上 **CIDR は `create_pod_range = true` のときだけ有効**。export は両方出す）。クラスタ側 `ip_allocation_policy` と同じ判断。
  - GKE の node pool `version`: master 版と食い違うと「Node version must be <= master version」で落ちるため除去し master 追従にする（cluster の `node_version` 除去と同じ理由）。
  - **node pool の `cluster = "<name>"` は文字列なので依存が張られない** → `_rewrite_resource_refs_one_root` の 2 パス目で `google_container_cluster.<label>.name` に変換（クラスタより先に node pool を作って 404 / 既定プール削除と競合するのを防ぐ）。
  - GKE の `node_version`: create 時に `min_master_version` と同値必須（provider 検証）。export は node 版だけを出すことがあり、同値でなければ `node_version` を除去（版は export 済みの `release_channel` に追従させる。regression: `node_version and min_master_version must be set to equivalent values on create`）。
  - GKE の排他引数: `cluster_ipv4_cidr` は `ip_allocation_policy` と排他 → 除去。`*_secondary_range_name` があれば `cluster_ipv4_cidr_block` / `services_ipv4_cidr_block` を除去（GKE が subnet に作った secondary range は **subnet の .tf ごと dst に複製される**ので range 名参照が正。CIDR 側を残すと同じ CIDR で range 二重作成になる）。
- **未作成 SSL 証明書に依存する LB フロントは `_drop_cert_blocked_lb_files` が保留する**（2 パス目の参照書き換えより前）。skip した証明書を参照する target proxy を文字列参照のまま残すと、**証明書を手動作成するまで毎回 `make run` が 404 で exit 1** になる（regression: TargetHttpsProxy の sslCertificates/notify-api 404）。`_gcloud_exists` で dst に証明書が実在するかを確認し、実在すれば残し（URL は API で解決できる）、無ければ proxy と参照元 forwarding rule を active から外して DIFF 要対応（kind: `lb_blocked_on_cert`）。**次回 customize が再判定するので証明書を作れば自動的に適用対象へ戻る**（skip にすると証明書は export に永遠に無いため恒久保留になってしまう。実在確認ベースが正）。region 変種と同ルート内定義（Google-managed 等）にも対応。
- **self-managed SSL 証明書（`google_compute_ssl_certificate`）は `_skip_reason_for_file` でファイルごと skip**。秘密鍵は API から export 不能で provider 上 `private_key` 必須のため apply 不能。DIFF に要対応として出て、利用者が鍵を持って手動作成する（Google-managed の `google_compute_managed_ssl_certificate` は別型で対象外）。
- 新しい非互換を見つけたら: ブロック廃止 → `_GKE_REMOVED_TF_BLOCKS` 等 + `strip_hcl_blocks`、引数必須化 → `ensure_hcl_block_arg`（補完値は「dst が緩くならない側」）、複製不能リソース → `_skip_reason_for_file`。検証は `terraform validate`（scratchpad に customize 出力を作って流すのが速い）。
- **文字列参照の → terraform 参照 変換は `_rewrite_resource_refs_in_active`（2 パス目）に集約**。bulk-export は参照を URL / SA email / リソースパスの文字列で出すため依存が張れず、参照先より先に参照元が作られて落ちる。現在の対応: SA email → `.email`（Cloud Run の actAs 403。**email/project の正規表現は 30 文字上限に注意** — `{4,28}` に切り詰めて 30 文字 ID で不一致になった regression あり）/ `securityPolicies`・`targetHttp(s)Proxies`・`urlMaps`・`backendServices`・`sslCertificates` URL → `.self_link` / 通知チャネル `projects/*/notificationChannels/<旧ID>` → import コメントの旧 ID 経由で `.name`。**同一ルートに定義が無い参照は文字列のまま残す**（skip 済み SSL 証明書は apply の本来のエラーで露見させる）。解決不能な通知チャネル行は除去 + DIFF「確認」note（ID は server 採番で dst に同番号は存在し得ない）。
- **bulk-export は monitoring workspace 等を介して project_mapping 外プロジェクトのリソースを越境出力する**（例: my-argolis の export に shingo-ar-genai0718 の notification channel）。ID 置換は mapping 外を変えないため、そのまま apply すると**無関係な実プロジェクトへ書き込む**。customize ループの 3.8 で `project =` が dst ID 集合に無い非数値なら skip + WARNING（数値 project は proj_num_map 置換済みのため対象外。dry_run では番号 map が無く誤検知するため）。
- **URL 直書き参照は依存関係が伝わらず作成順で 404 になる**。bulk-export は `network` / `subnetwork` 等を `https://.../projects/<p>/...` のハードコード URL で出すため、同一ルートに定義があっても Terraform が順序を決められない（regression: `google_compute_address.fix-tokyo1` が subnetwork `tokyo` より先に作られて 404）。network は `_rewrite_network_refs`（per-file）、subnetwork は `_rewrite_subnet_refs_in_active`（**全書き出し後の 2 パス目**）で `.self_link` 参照に変換する。
  - **短縮パス形式（`projects/<p>/global/networks/<n>` = URL でない）も変換対象**。GKE の `network` / `subnetwork` はこの形式で出るため、URL 版だけ変換していると依存が張られず VPC より先にクラスタが作られて 404 になる。
  - **subnet 側を 2 パスにしているのはラベルが `dedupe_tf_resource_labels` で改名されうるから**（subnet 名は region ごとに一意なので、同名 subnet が複数 region にあると改名が起きる）。確定後の active を読まないと存在しないラベルを指す。同種の参照（health check / instance group 等）を足すときも 2 パス側に置く。
  - 別プロジェクト（Shared VPC host）の URL は別 root module なので **変換せず URL のまま残す**。
- **Container Analysis occurrence（`google_container_analysis_occurrence`）は `_skip_reason_for_file` で除外**。過去ビルドの来歴・署名レコードで、参照先 note（`built-by-cloud-build`）は Cloud Build が自プロジェクトに作るため dst に存在せず `note with ID ... does not exist` の 404 になる。署名鍵も Google 管理プロジェクト（`verified-builder`）を指す。dst で再ビルドすれば再生成されるので DIFF には「確認」で出す（kind: `container_analysis_occurrence`）。
- **注記は `_add_customize_note` 内で重複排除している**。`name` を持たないリソース（occurrence 等）は `resource="?"` に潰れるため、素直に積むと同じ行が件数分並ぶ。
- **手動対応・確認が要る補正/スキップは DIFF.md に明記する（ルール）**。ログに流すだけだと埋もれるため、実装箇所で `self._add_customize_note(kind, content, rel)` を呼ぶ → customize_hcl 末尾が `active/<src>/.customize_notes.json` に永続化（**skip_on_run で customize を飛ばす run でも DIFF に出すため、メモリでなくファイルが正**。customize したプロジェクトだけ更新し、原因が消えたら注記も消える = .tf と同じライフサイクル）→ Step 99 が `load_customize_notes()` で読み、`format_diff_report(manual_notes=...)` が要対応テーブル直後の専用セクションに出す。行の整形は `customize_note_row()`（純粋関数）に kind ごとの (種別 要対応/確認, 対象, 理由, 対応コマンド) を追加する。未知 kind は握り潰さず「確認」で出る（登録漏れ検知）。現在の kind: `ssl_certificate`（要対応: dst で手動作成）/ `iap_enabled`（確認: 不要なら `--iap=disabled`）。

### bulk-export の timeout 対策（Step 3）

- **`error waiting for operation:`（理由が空）は config-connector の内部 timeout**。実測で **きっちり 30 分 ×3 回** 失敗し 90 分溶かした（regression: my-argolis）。**timeout を延ばすフラグは gcloud にも config-connector バイナリにも存在しない**（`--filter-deleted-iam-members` / `--iam-format` / `-i,--input` / `--oauth2-token` / `--on-error` / `-s,--storage-key` / `--output` のみ）。延長ではなく**エクスポート規模を減らす**か**待ちを無くす**のが正しい対処。
- **`export_resource_types`（KRM Kind）で CAI クエリ自体を絞る**のが最も効く。`gcloud beta resource-config list-resource-types --project=<src>` の Kind 名（`ComputeInstance` 等、**大文字始まり**）で指定する。customize 側の `resource_types`（**Terraform 型** `google_compute_*`）とは**別物**なので、`validate_steps_config` が取り違えを実行前エラーにする（`google_` 始まり / 小文字始まりを拒否）。
- **`--resource-types` と `--storage-path` は gcloud 上で排他**（help に明記）。両方指定されたら Kind 絞り込みを優先し WARNING を出す。`storage_path` は毎回の一時バケット作成を省く用途で、timeout 自体は縮まらない。
- **再試行は「回数 2・間隔 180 秒」が既定**（`retries` / `retry_wait_seconds` で調整可）。timeout 起因の失敗に 5 秒後の即再試行を 3 回ぶつけると 30 分 ×3 を溶かすだけなので、短間隔リトライは有害。`run_command` に `retry_wait_seconds` を持たせ、**失敗ログに経過秒数を出す**（旧実装は理由が空で「30 分待って落ちた」ことが読めなかった）。
- **`export_resource_types: "auto"` が推奨形**。`gcloud beta resource-config list-resource-types --format=json` を src ごとに引き、`SupportsBulkExport: true` の Kind を全指定する（`parse_krm_kinds()`）。移行範囲を狭めずに **CAI クエリだけを小さくできる**のが要点で、gcloud 側も `--resource-types` 指定時は `_CallBulkExportFromAssetList` 経路（gcloud が CAI を list して binary に stdin で渡す）に切り替わるため、**binary 内部の 30 分待ちオペレーション自体が発生しない**。Kind 一覧を取得できなかったら絞り込みなしで続行（移行範囲を勝手に狭めない安全側）。
- **`Service` Kind は k8s ではない**（`serviceusage.cnrm.cloud.google.com/Service` = 有効化 API）。Kind 名だけで k8s と判定しないこと。
- **`storage_path` はキャッシュではない**。gcloud SDK 実装（`declarative_client_base.py`）で `--storage-key` にそのまま渡されるだけで、意味は「CAI エクスポートの**書き出し先**」。既存ファイルを読む処理は無く、再実行のたびに新しい export が走るので **timeout 対策にはならない**（一時バケット作成を省くだけ）。再利用したいなら binary の `-i/--input` だが gcloud は露出しておらず、HCL 変換も効かないので本ツールでは採らない。
- **k8s オブジェクトは元から bulk-export の対象外**（`list-resource-types` は GCP の KRM Kind のみ返す）。my-argolis の CAI 1,480 件のうち **908 件が k8s オブジェクト**で、これらは Backup for GKE の担当。Kind 絞り込みで GCP リソース側だけを対象にしても移行範囲は変わらない。

### 移行範囲の絞り込み（`steps.bulk_export.resource_types`）

- **既定は全量コピー**（`resource_types` 未指定 = include/exclude とも空）。CAI と bulk-export が src で見つけたものは全部移行する、が原則。
- `include` / `exclude` は **Terraform リソース型**の fnmatch パターン（`google_compute_*` 等）。`exclude` が `include` より強い。判定は `tf_type_kept()`（純粋関数）。
- ファイル単位の除外は `resource_type_filter_reason()`。**1 つでも対象の型が残るファイルは落とさない**（安全側 = コピーする）。customize の 3.9 で判定し、**GKE 移行手順 note より前**に置く（除外したクラスタの手順を出さないため）。
- **DIFF (Step 99) も同じ設定を見る**（`classify_missing_asset(rt_include=, rt_exclude=)`）。除外した型の欠落は「参考 P3（設定どおり）」になり、要対応に混ざらない。
- `validate_steps_config` で **`google_` で始まらないパターンを実行前エラー**にする。`include: ["compute_*"]` のような typo は「何にも一致せず全除外」という静かな事故になるため。

### Artifact Registry イメージ複製（Step 3.7 / `step_artifact_registry`）

- **実行位置は terraform (Step 4) より前**（`execute()` で Step 3.5 の直後）。Cloud Run は `image = "...@sha256:<digest>"` を **revision 作成時に解決する**ため、apply の後に複製しても間に合わず `Error code 5, message: Image '...' not found.` で落ちる（regression: Step 6 data_sync に置いていた）。設定キーは `steps.data_sync.artifact_registry` のままで、**位置だけ前倒し**している（gate も `data_sync` の enabled）。
- dst リポジトリは通常 terraform が作るが、この step が先に走るので**無ければ自前で作る**（冪等）。terraform 側は `# terraform import` コメント経由で既存リポジトリを adopt するので衝突しない。
- **Terraform が作るのはリポジトリ（箱）だけでイメージ本体は複製されない**。Cloud Run は `image = "...@sha256:<digest>"` で固定参照するため、イメージが無いと revision 作成が `Image '...' not found.` で失敗 → サービスが **tainted** で state に残り、次回 replace が `deletion_protection` で詰む（regression: my-argolis の Cloud Run 3 件）。GCS/BQ と同じ「データ移行」として Step 6 で複製する。
- **`gcloud artifacts docker images copy` は SDK 580 に存在しない**（`delete/describe/list/scan` のみ）。crane/gcrane/buildx も前提にできないため **docker CLI で pull → tag → push**。docker が PATH に無ければ WARNING + skip（soft fail）。
- **docker 経由は digest が変わりうる**（マルチアーキ index を単一プラットフォームに落とす等）。Cloud Run の digest 固定参照が壊れるので、push 後に `images describe <dst>@<digest>` で**必ず同一 digest の存在を確認**し、無ければ WARNING で `gcrane cp` を案内する。`_gcloud_exists` は mock/dry-run で常に False を返すため、**検証は `if self.mock or self.dry_run: return` で飛ばす**（飛ばさないと mock が誤警告だらけになる）。
- tag の無いイメージ（digest 参照専用）は push できないので `migrated-<digest 先頭 12 桁>` を合成する。複製自体は必要なので落とさない。
- **Cloud Build の SLSA provenance / SBOM（attestation）が実イメージと同列の digest として list に並ぶ**。実行可能イメージではなく（config が `application/vnd.oci.empty.v1+json`）docker では構造的に pull できない。cosign 形式タグ（`sha256-<digest>.att/.sig/.sbom`）のみの version はプラン段階で除外（`_AR_ARTIFACT_TAG_RE`）、タグ無し attestation は list 時点で見分けられないため **pull 失敗が `unsupported media type` なら INFO + skipped**（WARNING にしない。Cloud Run が参照するのは実イメージの digest なので複製不要）。
- **転送ツールは gcrane / crane のみ（docker は廃止）。`check_prerequisites` が `data_sync` 有効かつ `artifact_registry.enabled != false` のとき gcrane|crane を必須として実行前に exit 1**（`make plan` でも止まる）。`required` の要素はツール名 str または「どれか 1 つあれば OK」の tuple。gcrane/crane は registry→registry 転送で **digest を保つ**（マニフェストリストごと運ぶ）。docker の pull→push はマルチアーキを単一プラットフォームに落として digest が変わることがあり、そうなると (1) Cloud Run の `@sha256:` 固定参照が解決不能 (2) 既存 digest 判定が一致せず**毎回全件再送**（実測: gcf-artifacts の 4 件が毎回再送されていた）。crane 経路は digest 不変が保証されるので **describe による検証もローカル後片付けも行わない**（docker 経路にあった検証コードごと削除済み）。Step 3.7 でツールが見つからない場合は soft skip ではなく `stats.add_failure` で失敗にする（スキップすると「イメージが無いまま apply して Image not found」になるだけ）。mock は `_ar_copy_tool()` が常に `gcrane` を返す（PATH に依存させるとマシンごとに mock の出力が変わる）。**テストでツール経路を検証するときは `_ar_copy_tool` を patch して固定する** — `shutil.which` 依存のままだと gcrane 導入済みマシンでだけ落ちる。
- **`scope: tagged`（`steps.data_sync.artifact_registry.scope`）で tag 無し digest を除外できる**。Cloud Build は push のたびに tag を移すので tag 無し = 置き換えられた過去ビルド（実測 87 件中 64 件）。**`.tf` が digest 固定で参照するものは `tf_referenced_image_digests()` で拾って必ず残す**（落とすと Step 4 が `Image ... not found`）。Step 3.7 は Step 3 の後なので active/<src> の `.tf` は「これから apply される内容」で確定しており、この集合が apply の必要十分。既定は `all`（GKE ワークロードの image 参照は `.tf` から引けない = 判定材料が無いので安全側）。除外件数は必ずログに出す（no silent caps）。`filter_ar_plan_by_scope` / `tf_referenced_image_digests` は純粋関数。scope の綴り誤りは `validate_steps_config` が実行前に弾く（黙って `all` に倒すと「絞ったつもりが全量」で気付けない）。
- **並列構造は 2 フェーズ**: 列挙（`_collect_ar_copy_work` をプロジェクト並列 `ar-list`）→ flat な (project × repo × image) 単位で一括コピー（`ar-copy`）。プロジェクトごとに直列でコピーすると parallel_jobs が 1 リポジトリ内でしか効かない。`gcloud auth configure-docker` は `~/.docker/config.json` への書き込みで並列安全でないため、**コピー開始前に対象ホスト分を直列で**済ませる。
- **既存 digest の除外はリポジトリ単位の一括 list**（`--format='value(version)'`）で行う。イメージごとの describe だと再実行時に件数分の API 往復が積み上がる。
- 純粋関数（`parse_ar_repositories` / `build_ar_image_copy_plan`）に分離してテストする。package 置換は**パス区切りで分解して 2 番目の要素（project）だけ**差し替える（イメージ名に src プロジェクト ID が入っていても壊さない）。
- 権限: dst SA が src の AR を読む必要があるため `bootstrap_cross_project.sh` の `PREDEFINED_ROLES` に `roles/artifactregistry.reader` を追加済み。**既存環境は再実行が要る**。
- **src の一覧取得は `run_command(allow_fail=True)` ではなく `_soft_run` を使う**。`allow_fail=True` でも `stats.failed` には積まれるため、**AR を使っていない src（API 無効で 403）だけで `make run` が exit 1 になる**（regression: サービスプロジェクト 2 件）。API 無効は `is_api_disabled_error()` で判定して INFO「複製対象なし」に落とし、それ以外の取得失敗だけ WARNING にする。同種の「src がその機能を使っていないだけ」の一覧取得を足すときも同じ扱いにすること。
- docker の有無チェックは**リポジトリが実在すると分かってから**行う（AR 未使用の src で無意味な警告を出さない）。
- `_WRITE_VERBS` に `copy` / `push` / `tag` / `rmi` を追加済み（src で誤実行されないように）。`_MOCK_KNOWN_PATTERNS` にも `gcloud artifacts *` と `docker *` を追加。mock は DOCKER と PYTHON のリポジトリ、tag 付き/無しのイメージを返すので、除外や合成タグが壊れると `make mock` で気付ける。

### provider 既定の deletion_protection（Step 3 / customize_hcl）

- `google_cloud_run_v2_service` / `_job` / `google_container_cluster` / `google_sql_database_instance` は **provider 既定で `deletion_protection = true`** だが **export には出てこない**。既定 true のままだと、apply が途中で失敗して tainted になったリソースを次回 replace できず `cannot destroy service without setting deletion_protection=false` で移行が恒久的に詰む。
- `_DELETION_PROTECTION_DEFAULT_TRUE_TYPES` の型は `ensure_tf_resource_arg()` で **export に無いときだけ** `deletion_protection = false` を補完する。**export が明示していれば触らない**（src の意図を上書きしない）。
- これは「dst が緩くならない方向」の例外。terraform 側のガードであってアクセス制御ではなく、値が src 由来でないため。ただし dst の保護は実際に外れるので **DIFF に「確認」で出す**（kind: `deletion_protection`。本番切替時に戻す案内付き）。
- `ensure_tf_resource_arg` は `ensure_hcl_block_arg`（`name {` 形式のネストブロック用）とは別物。**depth 1 の行だけ**引数の有無を見る（ネストブロック内の同名引数を「あり」と誤判定しないため）。

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
- **例外: Cloud Run サービス個別の公開設定は複製する**（`_sync_run_service_invokers`。step_iam_sync 冒頭・早期 return より前）。「未認証アクセスを許可」= サービスリソースの `allUsers → roles/run.invoker` で、bulk-export（IAM 非出力）にも project IAM にも乗らず、放置すると **src で公開のサービスが dst で認証必須**になる（regression: any-method-api）。対象は `allUsers` / `allAuthenticatedUsers` × `run.invoker` のみ（SA 個別付与・条件付きは対象外 = iam_sync と同方針）。付与したら末尾に **WARNING で一覧 + 取消コマンド**（公開 = インターネット開放なので必ず見せる。roles/owner と同じ「忠実再現 + 警告」）。dst に同名サービスが無ければスキップ + WARNING（作成後の再実行で付与）。soft fail。新コマンド 4 種は `_MOCK_KNOWN_PATTERNS` 登録済み・mock は公開サービスを返すので複製パスが壊れると気付ける。
- **bulk-export は Cloud Run をリージョンによって取りこぼす**（regression: us-central1 の www-1 / test-1 が未出力。CAI には 5 件、export は asia-northeast1 の 3 件のみ）。`run.googleapis.com/Service` を `_ASSET_COVERAGE`（terraform_apply）と `_CAI_TO_TF_RESOURCE` に登録済みで、欠落は DIFF **要対応**「bulk-export が出力しなかった」で出る。Revision は None（履歴。デプロイで再生成）。
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

### DIFF.md の要対応 / 参考 分類（Step 99）

- DIFF.md は放置すると 50 件超になり、**実際に手を動かす必要があるものが埋もれる**。`classify_missing_asset()` が欠落 1 件を `action` / `reference` に分け、`format_diff_report()` が先頭に **WHAT / WHY / HOW テーブル（action のみ）** → 参考テーブル → プロジェクト別詳細の順で出す。
- **action は「dst の動作に必要で放置すると実害が出るもの」だけに絞る**。reference には優先度（`_DIFF_PRIORITY_LABELS`: 1=確認推奨〔別ステップが自動対応済み、結果確認のみ〕 / 2=条件付き〔src にカスタム・取り置きの意図がある場合のみ〕 / 3=対応不要）を付け、参考テーブルと詳細を**優先度昇順でソート**して出す。
- reference に落とす条件（＝実害が無いと言い切れるものだけ）:
  - user-managed SA … `iam_sync` 有効なら Step 5.7 が dst に作成 + ロール複製する（**無効なら action**）→ P1
  - default compute / appspot / Google 管理 service agent … dst に dst 自身の番号を持つ同等物が既定で存在 → P3
  - `_MANAGED_LOG_RESOURCE_NAMES`（`_Default` / `_Required`）… GCP 自動生成。create は「already exists」で失敗する → P2
  - `_MIGRATION_TOOL_ROLE_IDS`（`migrationSrcReader`）… 移行ツール自身が src に作った借用 SA 用ロール → P3
  - src の project IAM ポリシーで**誰にも付与されていない**カスタムロール（`bound_custom_role_ids()` で判定）→ P2
  - Address（CAI の `state` と `additionalAttributes.address` を `parse_cai_resources()` が拾って判定）:
    - `nat-auto-ip-*` … Cloud NAT の自動割当。手動作成は不可能かつ無意味 → P3
    - `state=RESERVED` … 未使用の取り置き。dst に無くても壊れない → P2
    - `state=IN_USE` の**内部** IP（RFC1918 判定）+ `gce_restore` 有効 … Step 5 が同じ IP を `mig-<vm>-<ip>` 名で dst に静的予約するため機能等価 → P1
    - `state` 不明 / 使用中の**外部** IP（nat-auto 以外）は action のまま
- **TF 側の走査 (`parse_tf_resources`) は必ず再帰 (`os.walk`)**。`_emit_cai_tf_diff` は `terraform/raw/<src>` と `terraform/active/<src>` の両方を渡すが、**raw は `<src>/projects/<proj>/<Kind>/<location>/<name>.tf` の深いツリー**（フラットなのは customize 後の active だけ）。`os.listdir` でフラット走査していた頃は raw から 1 件も拾えず、`TF 0 件 / 要対応 278 件` のように **export 済みリソース（GKE クラスタ含む）まで「bulk-export が出力しなかった」と誤検知**していた（regression）。`.terraform/`（provider / module キャッシュ）は除外すること。
- **DIFF ノイズ削減の分類知識**（誤検知・自動生成・二重計上を action に混ぜない）:
  - `parse_tf_resources` は name の無い型で **型固有 ID 属性**（`repository_id` / `account_id` / `secret_id` 等）→ ラベル + **ハイフン逆変換の別名**の順にフォールバック。ラベルだけだと `cloud_run_source_deploy` ≠ `cloud-run-source-deploy` で **export 済み AR リポジトリ 5 件が要対応に誤検知**されていた（regression）。
  - Dataplex の `@bigquery` 等 **@ 始まり EntryGroup** = サービス連携の自動生成カタログ → P3。@ 無しは利用者作成で action。
  - Service Directory の `gk3-*` / `goog-*`（GKE control plane PSC / goog-psc-default）→ P3。
  - `SecretVersion` は Secret 本体の要対応に集約（P2）。**値の複製はツール対象外**（秘密情報を自動で写さない方針）。
  - Cloud DNS はゾーンを名前と数値 ID（v2 API 表現）で**二重に CAI へ出す** → 数値 short は P2（名前行に集約）。
  - gen2 Cloud Functions は実体が Cloud Run。**同名の run.Service が CAI にあれば P1 で Run 側へ集約**（`run_service_names` を analyze → classify に渡す）。
  - `networkconnectivity.googleapis.com/InternalRange` の gke-* は Pod range の自動表現 → `_GKE_DERIVED_ASSET_TYPES` で P3。
  - **`_ASSET_COVERAGE` の未登録は必ず埋める**（未登録 = 全部 action 落ちで最大のノイズ源）。export される型は `_CAI_TO_TF_RESOURCE` にも登録して covered 判定を効かせる。設定オブジェクト（SCC settings / GlobalTriggerSettings 等「自動存在・作成不可」）は None。
- **判定材料が無い場合は必ず action に倒す**（`bound_custom_roles=None`、Address の `state` 欠落等）。見落とすより過剰報告を選ぶ。
- カスタムロールの付与判定は `step_iam_sync` が取った src ポリシー（`self._src_iam_policies`）の再利用。iam_sync 無効時は取得されず `None` → 全カスタムロールが action になる（意図どおり）。
- 分類は純粋関数なのでテストは直接叩く（`TestClassifyMissingAsset` / `TestBoundCustomRoleIds`）。**新しい「実は対応不要」パターンを見つけたら reference 条件に足す**。逆に判断が付かないものを reference に入れてはいけない。

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
  - GitHub Flow に従う
  - branch は feat/branchname ではなく branchname で作る
- 変更点については、branch ごとにまとめて、ユーザーが利用時に意識すべきもののもののみを簡潔に RELEASE_NOTE.md に記載する
  - 変更した日付ごとまとめ、日付のヘッダをつける
  - 新しいものを上に書く


## ツール
以下のツールを積極的に使う
- GCP関連のコードは、Developer Knowledge APIを積極的に使う
- それ以外はContext7で最新のドキュメントを確認する

