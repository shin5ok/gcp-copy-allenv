.DEFAULT_GOAL := help
.PHONY: plan deploy destroy test setup snapshot-all scan-org sync-to-dst prepare-dst help

## help: 引数なしmake時のヘルプメッセージと全コマンドの使い方を自動パースして表示します
help:
	@echo "使用方法: make [target] [ARGS=\"...\"]"
	@echo ""
	@echo "ターゲット一覧:"
	@fgrep -h "##" $(MAKEFILE_LIST) | fgrep -v fgrep | sed -e 's/\\$$//' | sed -e 's/##//' | awk 'BEGIN {FS = ":"}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

## setup: Python/uvの仮想環境を同期・セットアップします
setup:
	uv sync

## plan: 静的定義に基づき、構築予定のgcloudコマンド一覧（ドライラン）を表示します
plan:
	uv run scripts/build_env.py --config org/ORG.md --dry-run $(ARGS)

## deploy: 静的定義に基づき、VPC/NAT/IAP FW/VM 11台を一括並列で高速デプロイします
deploy:
	uv run scripts/build_env.py --config org/ORG.md $(ARGS)

## destroy: state.jsonに保存された構築実績に基づき、自分が作成したリソースのみを安全に一撃で逆順撤去します
destroy:
	uv run scripts/build_env.py --config org/ORG.md --destroy $(ARGS)

## snapshot-all: 稼働中の全VMのブートディスクスナップショット（バックアップ）をマルチスレッド並列で一括作成します
snapshot-all: setup
	uv run scripts/build_env.py --config org/ORG.md --snapshot $(ARGS)

## scan-org: 実機共有VPCを動的スキャンし、OSイメージ判別や定義外VMフィルタリングを行ってdst/DST.mdに書き出します
scan-org: setup
	uv run scripts/scan_env.py --project shingo-ar-sharedhost0926 --network shared-vpc --output dst/DST.md $(ARGS)

## prepare-dst: コピー先プロジェクトにインフラデプロイを行う前に、必要なAPIを並列一括で事前有効化します
prepare-dst: setup
	uv run scripts/sync_env.py --config dst/DST.md --prepare $(ARGS)

## sync-to-dst: dst/DST.mdに基づき、プロジェクトID置換を行い、スナップショットからディスク復元してVMを完全クローン構築します
sync-to-dst: setup
	uv run scripts/sync_env.py --config dst/DST.md $(ARGS)

## test: ツール全体の単体テスト（API事前準備、スナップショット、複製マッピング等のpytest）を実行します
test:
	PYTHONPATH=. uv run pytest
