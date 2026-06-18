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

- src プロジェクトへの書き込みは禁止（コード上も強制）
- SA impersonation は `CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT` 経由のみ
- `.env` / `*.key` / `*.json`（サービスアカウントキー）は絶対に編集・コミットしない

## 禁止事項

- 勝手に変更を加える・デプロイしない（テストであっても）
- 変更を行う場合は、ユーザーにコマンド操作を促してログを貼るよう促す

## 並列化方針（パフォーマンス）

各ステップの直列処理が長時間化するため `_parallel_for_each` で並列化済み。`config global.parallel_jobs` (推奨 8) を上限としたスレッドプールで実行。

| ステップ | 並列単位 | 備考 |
|---|---|---|
| 1 cai_scan | プロジェクト | src read-only |
| 2 gce_snapshot | プロジェクト | 既存 |
| 3 bulk_export | プロジェクト | 既存 |
| 4.5 network_firewall (classic rules) | ルール | `_sync_classic_firewall_rules` 内 |
| 4.5 network_firewall (policy rules) | ルール | `_sync_fw_policy_rules` 内 |
| 5 gce_restore (listing) | プロジェクト | VM/snap 一覧並列取得 |
| 5 gce_restore (restore) | **VM** | flat (project, vm) units で並列 |
| 5 secondary disks (create) | ディスク | attach は同一 VM 内で直列（409 回避） |
| 6 data_sync (GCS/BQ) | バケット / テーブル | 既存 |

実装ルール:
- 共有可変状態（dict など）に書き込む場合は `threading.Lock()` で保護。`StageStats` は組み込み Lock 済み。
- 並列 worker から `sys.exit(1)` しない（他 worker を巻き添えで止める）。代わりに `stats.add_failure()` + `stats.incr("failed")` で記録し return。`main()` が最終的に exit 1 で抜ける。
- 同一リソースへの並列操作は API レベルで競合する場合があるので注意（例: 同一 VM への並列 attach-disk は 409 になる → attach は直列）。
- worker 内では `dst_logger` / `org_logger` をそのまま使ってよい（Python logging はスレッドセーフ）。

## ハマりどころ（既知の落とし穴）

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

## Git コミット

- コミットメッセージに Claude の名前や署名を付けないこと
  - `Co-Authored-By: Claude ...` 行を付けない
  - `🤖 Generated with Claude Code` などの行も付けない
  - 勝手にコミットやプッシュしない

## ツール
以下のツールを積極的に使う
- GCP関連のコードは、Developer Knowledge APIを積極的に使う
- それ以外はContext7で最新のドキュメントを確認する

