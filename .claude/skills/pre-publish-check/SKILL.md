---
name: pre-publish-check
description: GitHub の public リポジトリに公開する前に、機密情報・プライバシー上のリスクを Critical / High / Medium / Low にレベル分けして報告し、シンプルな対策を提示する。秘密鍵 / API キー / SA メール / GCP project・org・folder ID / 内部 IP / 個人メール / 認証ファイル などを git 追跡ファイル全体からスキャンする。ユーザーが「公開前チェック」「pre-publish」「リポジトリ公開して大丈夫か」等を要求したときに起動する。
---

# Pre-Publish Check

GitHub の public 公開前に、機密漏洩リスクを自動スキャン → レベル付け → 対策提示する。

## 起動条件

ユーザーから以下のような依頼があったときに使う:
- 「公開して大丈夫かチェックして」「public にする前に確認して」
- 「リポジトリに秘密情報残ってない？」
- `/pre-publish-check` を明示呼び出し

## 実行手順

1. `bash .claude/skills/pre-publish-check/scripts/scan.sh` を実行（リポジトリのルートから）
2. 出力をレベル別に整理して報告
3. 各ヒットに対し、下記「対策表」のシンプルな対処をユーザーに **提案**（勝手に実行しない）
4. git 履歴チェック（後述）を案内

## チェック対象

`git ls-files` の出力（= 追跡されているファイル）のみが対象。`.gitignore` 済みのものは安全とみなしてスキップ。

| Level | 何を | 例 |
|---|---|---|
| **Critical** | 秘密鍵 / API キー / 認証ファイル / 実在 SA メール | `-----BEGIN`, `AIza...`, `sk-...`, `AKIA...`, `*.key`, `.env`, `*@*.iam.gserviceaccount.com` |
| **High** | GCP 数値 ID（org / folder / billing） / ハードコードされた project_id | `organizations/123456789012`, `project_id: real-proj-xxx` |
| **Medium** | private IP / 内部ホスト / 個人メール | `10.x.x.x`, `192.168.x.x`, `*@gmail.com` |
| **Low** | 社内固有名詞（人名・社名・社内システム名） | スクリプトでは検出困難 → README/docs を Claude が目視 |

## 対策（シンプル版）

### Critical — 公開ブロック相当
1. **値を即時ローテーション**（API キー / SA キー / パスワード を再発行）。コミットから消すだけでは漏洩リスクは残る。
2. ファイルを削除し `.gitignore` に追加。必要なら `.example` 化（プレースホルダだけ残す）。
3. 過去コミットに残っているなら `git filter-repo` で履歴ごと除去 → force push。
   - **これは破壊的操作。ユーザーが自身のローカルで実行する**。Claude は提案だけ。
4. 共有 SA メールも `<SERVICE_ACCOUNT_EMAIL>` 等に置換するのが望ましい（露出するとフィッシング標的）。

### High — 公開前に置換推奨
- 実プロジェクト ID / 組織 ID / フォルダ ID は `YOUR_PROJECT_ID` `<ORG_ID>` `<FOLDER_ID>` 等に。
- 設定 YAML は `config.example.yaml` を残し、実体は `.gitignore` + ローカル symlink パターン（このリポジトリの `dst/config.yaml` 方式）。

### Medium — 状況に応じて
- private IP は CIDR (`10.0.0.0/8`) に丸めるか、文書用 IP (`198.51.100.x`, `203.0.113.x`) に置換。
- 個人メールは `user@example.com` に。
- 「内部ネットワークの構造そのものを推測されたくない」場合は IP 体系・サブネット幅も伏せる。

### Low — 余裕があれば
- 「弊社」「○○部」「内製ツール △△」等を一般的な表現に書き換え。
- README/docs を Claude が読み、固有名詞をリスト化して確認するのが効率的。

## git 履歴のチェック

スキャンスクリプトは **作業ツリーのみ**。過去コミットに機密が残っていないか、特に怪しいファイルは明示確認する:

```bash
git log --all --full-history -p -- <file>
```

このリポジトリの場合、最低でも次は要チェック:
- `dst/config.yaml` / `src/config.yaml` / `vmware/config.yaml`
- `.env` 全種
- `*.key` / `*-key.json` / `credentials.json` / `service-account*.json`

過去にコミットされていた場合は **値ローテーション + `git filter-repo` で履歴除去** が必須。

## レポート形式

スキャン結果を以下の順で報告する:

1. **総括** — Critical N 件 / High N 件 / Medium N 件。公開可否の判断（Critical 0 件なら公開可能、それ以外は要対処）。
2. **Critical 詳細** — 1 件ずつ `file:line` と該当値（最初の 8 文字 + `…` で伏字）+ 即時対策 1 行。
3. **High 詳細** — 1 件ずつ `file:line` と該当値（伏字なし）+ 推奨対策 1 行。
4. **Medium / Low サマリー** — 件数だけ。ユーザーが「詳細見たい」と言ったら展開。
5. **次のアクション** — Claude が自動で実行できる修正（プレースホルダ置換など）と、ユーザー手動が必要な操作（キーローテーション、force push）を明確に分けて提示。

## やってはいけないこと

- Claude が **勝手にキーをローテーションしたり履歴を書き換えたりしない**。提案のみ。
- スキャン結果を memory に保存しない（機密情報を含むため）。
- false positive をユーザーに無断で削除しない。「これは例示なので無視してよいですか？」と確認する。
