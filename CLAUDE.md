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

## Git コミット

- コミットメッセージに Claude の名前や署名を付けないこと
  - `Co-Authored-By: Claude ...` 行を付けない
  - `🤖 Generated with Claude Code` などの行も付けない
  - 勝手にコミットやプッシュしない
