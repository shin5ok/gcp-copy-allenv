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

## 配下のルール

作業対象に応じて配下の `CLAUDE.md` も読む（Claude Code は作業ディレクトリ配下の CLAUDE.md を自動で参照する）:

- **`scripts/CLAUDE.md`** — `sync_env.py` の並列化方針・Step ごとのハマりどころ（既知の regression 知識）・`config.yaml` の実行前検証
  - GKE / GCE 復元 / Artifact Registry / IAM 複製 / Network Firewall Policy / DIFF 分類 / VPC-SC quota project / mock 分離 / terraform customize / bulk-export timeout など

`AGENTS.md` と `GEMINI.md` は本ファイルへのシンボリックリンク。編集時は必ず `CLAUDE.md` を対象にすること。
