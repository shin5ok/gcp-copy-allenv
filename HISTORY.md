# 変更履歴 (HISTORY)

このファイルは `copy-all-env` プロジェクト（`copying` ブランチ）の主要な変更履歴を
**日付の新しい順** にリリースノート形式で記録します。
1 エントリは「日付 / 概要 / 変更内容 / 変更理由」をひとまとめにし、後から追跡できるようにします。

> 📌 **2026-06-02 以降の変更は [`RELEASE_NOTE.md`](./RELEASE_NOTE.md) に集約しています。**
> 利用者が把握すべき変更（新機能・仕様変更・要対応）は RELEASE_NOTE を参照してください。
> 実装上の判断理由・ハマりどころは [`CLAUDE.md`](./CLAUDE.md) の「ハマりどころ」に記録しています。

---

## 2026-06-02 (午後) — ORG 保護のコード強制 / dst コピーのバグ修正 / ログ刷新

### この変更で達成したいこと
- **ORG プロジェクトに対する書き込みを、コードレベルで物理的に不可能にする**。
- **dst コピーで発生していた致命バグを潰す**（HCL name 置換の暴走、project ID の部分一致誤置換、BQ クロスリージョン失敗 など）。
- **ログを日本語化・ステップ単位でグループ化・実行ごとに新規ディレクトリ保存** にして、終了後のレビューを楽にする。

### 🔐 ORG プロジェクト保護（最重要）
- **`run_command` に `side: "src" | "dst" | "local"` 引数を追加**。
  - `side="src"` のときは下記をコード側で強制:
    - `impersonate_sa is None` なら即時 `sys.exit(1)`（実行ユーザー権限で src を叩くのを禁止）。
    - コマンド本体に書き込み動詞が含まれていたら実行前に拒否。
      対象動詞: `create / delete / update / add / remove / set / enable / disable / attach / detach / stop / start / reset / apply / destroy / mk / cp / rm / rsync / mv / import / patch / replace`
    - 動詞検査は `--xxx=yyy` のフラグ値を除外（`--format='value(creationTimestamp)'` を誤検知しない）。
  - 理由: 旧実装は「Viewer SA を src に、Editor SA を dst に」を**運用ルール**で謳っていたが、コードでは何も検証していなかった。SA 設定漏れや実行ユーザー権限で簡単に ORG を壊せる構造だった。

- **`validate_config` を厳格化**。
  - 必須チェック: `project_mapping` 存在 / `host_project` / `service_projects` が空でないこと。
  - 各エントリで `src` / `dst` / `src_impersonate_service_account` / `dst_impersonate_service_account` がすべて埋まっていること。
  - `src == dst` 禁止（ORG への書き込みになる）。
  - dst 側 ID が他の src と一致するのを禁止（ORG を上書きするリスク）。
  - 同じ dst に複数の src がマップされる重複も禁止。
  - 違反があれば `load_config` で `sys.exit(1)`（処理は何もせずに止まる）。

- **Mock モードを fail-closed 化**。
  - `_MOCK_KNOWN_PATTERNS` のホワイトリストにないコマンドは「未対応」として即停止。
  - 理由: 旧実装はリストにないコマンドを `None` で返し、呼び出し側で**本物の subprocess 実行**に進んでいた。
    `--mock` をつけても一部が本物動作してしまう危険があった。

### 🛠 dst コピー作成のバグ修正
- **`customize_hcl` の `name = "..."` 置換を `resource "google_storage_bucket" { ... }` ブロック内に限定**。
  - 旧実装はファイル単位で全 `name = "..."` を suffix 置換していたため、**VM 名 / FW 名 / IAM 名まで `-dst-0602` が付く**致命バグ。
  - その結果、Step 4 Terraform Apply で作成される VM 名が変わり、Step 5 のディスク差し替えで src の VM 名と一致せず復元が機能しなかった。
- **プロジェクト ID 置換を境界付き正規表現に変更**。
  - パターン: `(?<![A-Za-z0-9_-]){src}(?![A-Za-z0-9_-])`
  - 長い ID から先に処理し、ある src ID が他 src ID の prefix だった場合の連鎖置換を防止。
  - 例: src=`proj` のときに `main-proj-2` を `main-dest-2` と誤置換しないようにする。
- **BigQuery dataset 作成時に src の `location` を継承**。
  - `bq show --format=json` で src の location を取り、`bq mk --location=` で dst にも同一の location を指定。
  - 旧実装は location 未指定でデフォルト US 作成 → src が asia-northeast1 だと `bq cp` がクロスリージョンエラーで失敗していた。
- **`terraform apply` を 2 段階に分離**: `terraform plan -out=tfplan` → `terraform apply tfplan`。
  - 差分レビューが可能になり、`dry_run` 時は plan のみで止まる。
- **`step_gce_snapshot` のエラー集約**: プロジェクトごとのエラーをロックで集めて一括判定。

### 📝 ログのレビュー性
- **実行ごとに `logs/<タイムスタンプ>/{org,dst}.log` の新規ディレクトリ**を作成（旧: `org.log` / `dst.log` への append で履歴が累積していた）。
- **メッセージを全面日本語化**。アクションは `✓ スキップ / + 作成 / − 削除 / ✗ 失敗` の記号で目視可能。
- **ステップ区切り**: `━━━━` バー + `ステップ N: タイトル (対象 X 件)`。
- **スレッドタグ自動付与**: ログレコードに現在のスレッド名を `[main] / [snap-check_0] / [cai-scan_1]` のタグで載せる（並列実行時に追跡可能）。
- **末尾サマリ**: 実行時間 / 読取成功 / 書込成功 / スキップ / 失敗 / Mock 実行 / ログパス。
- **`verbose_logging` 時に DEBUG ログをファイル出力**（コンソールは INFO のまま）。旧実装は logger.level=INFO で DEBUG ログが死にコードだった。

### 🚀 並列化
- **`parallel_jobs` を実装**。`ThreadPoolExecutor` で CAI scan / snapshot check / その他プロジェクトループを並列化。
- `_parallel_for_each` ヘルパで `parallel_jobs <= 1` のとき自動的に直列フォールバック。

### 🧹 dead code 整理（削除）
- `main.py` — hello-world のみで未使用。
- `scripts/main.py` — Step 3-6 が `TODO` placeholder のみで、どこからも呼ばれていなかった。
- `scripts/utils.py` / `scripts/cai_scan.py` / `scripts/gce_snapshot.py` — `scripts/main.py` 経由でしか呼ばれない孤立コード。

### Makefile の整理
- `plan` (dry-run) / `mock` (Mock モード) / `run` (本番) に整理。
- `projects-plan` / `projects` も同じ命名規則に統一。

### ✅ テスト
全 **32 件 PASS**（既存 10 件 + 新規 22 件）。主な追加:
| グループ | 件数 | 検証内容 |
|---|---|---|
| `TestSrcReadOnlyGuard` | 4 | 動詞判定 / bulk-export 許可 / フラグ値の誤検知防止 |
| `TestMockKnownCommand` | 2 | Mock 既知/未知判定 |
| `TestValidateConfig` | 6 | 各種バリデーション失敗パターン |
| `TestLoadConfigFailsFast` | 1 | 不正 config で `sys.exit` |
| `TestRunCommandSafety` | 5 | src 書き込み拒否 / SA 未指定拒否 / Mock 未知拒否 / dry-run 動作 |
| `TestCustomizeHcl` | 3 | VM name 不変 / bucket name 変換 / 境界付き ID 置換 / boot_disk.source 行削除 |
| `TestLogging` | 1 | per-run ディレクトリ生成 |
| `create_projects` 系 | 2 | src==dst 拒否 / dst が他 src と衝突拒否 |

### 影響範囲
| ファイル | 種別 | 内容 |
|---|---|---|
| `scripts/sync_env.py` | 全面リライト | ORG 保護 / Mock fail-closed / HCL バグ修正 / BQ location / TF plan / 並列 / 日本語ログ |
| `scripts/create_projects.py` | リライト | バリデーション強化 / per-run dir / 日本語サマリ |
| `Makefile` | 編集 | `plan / mock / run` に整理 |
| `tests/test_sync_env.py` | リライト | 19 件の新規テスト |
| `tests/test_create_projects.py` | 編集 | バリデーション失敗テスト追加 |
| `main.py` | 削除 | hello-world |
| `scripts/main.py` | 削除 | TODO placeholder |
| `scripts/utils.py` | 削除 | 孤立コード |
| `scripts/cai_scan.py` | 削除 | 孤立コード |
| `scripts/gce_snapshot.py` | 削除 | 孤立コード |
| `HISTORY.md` | 新規 | 本ファイル |

### 互換性メモ
- `run_command` のシグネチャに `side` 引数を追加し**必須化**。外部から呼ぶ場合は `side="src" / "dst" / "local"` のいずれかを必ず指定する必要があります。
- 旧 `logs/org.log` / `logs/dst.log` への append 出力は廃止。今後のログは `logs/<タイムスタンプ>/{org,dst}.log` に出力されます。
- `scripts/main.py` などの代替実装エントリーポイントは削除されたため、必ず `scripts/sync_env.py` を使用してください（Makefile の `make plan / mock / run` で呼ばれます）。

---

## 2026-06-02 (午前以前) — Terraform ベースへの全面移行 (参考: 過去 git ログ集約)

### 概要
旧来の bash/gcloud スクリプト方式から、`dst/config.yaml` ベースの Terraform/IaC 方式に全面リライト。
コピー先プロジェクトの自動プロビジョニング、Mock Mode、サービスアカウント借用 (Impersonation) によるキーレス運用などを導入。

### 主な変更
- `feat`: `make projects` でコピー先プロジェクト群の新規作成・billing 紐付け・API 有効化を実装。
- `feat`: Mock Mode を導入し、GCP 未接続でも end-to-end フローをローカル試走可能に。
- `feat`: CAI Scan、GCE Snapshot 検証、bulk-export、HCL カスタマイズの基盤実装。
- `docs`: SPEC.md / README.md を Terraform 移行方式向けに全面書き換え。
- `chore`: DST.md のマシンタイプを `e2-micro` / `n2-standard-2` に更新。
- `design`: verbose logging とコマンド可視化仕様を策定。

### 参考
詳細は `git log --oneline` および `dst/SPEC.md` / `dst/PROCEDURE.md` を参照。
