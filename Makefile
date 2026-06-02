.DEFAULT_GOAL := help
.PHONY: setup run plan test help

## help: 引数なしmake時のヘルプメッセージと全コマンドの使い方を表示します
help:
	@echo "使用方法: make [target] [ARGS=\"...\"]"
	@echo ""
	@echo "ターゲット一覧:"
	@fgrep -h "##" $(MAKEFILE_LIST) | fgrep -v fgrep | sed -e 's/\\$$//' | sed -e 's/##//' | awk 'BEGIN {FS = ":"}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

## setup: Python/uvの仮想環境を同期・セットアップします
setup:
	uv sync

## run: dst/config.yaml に基づいて移行処理（スキャンからクローン同期まで）を本番実行します
run: setup
	uv run python3 scripts/main.py --no-dry-run $(ARGS)

## plan: ドライランモードで実行計画（予定されるgcloud/terraformコマンドと日本語補足）を表示します
plan: setup
	uv run python3 scripts/main.py --dry-run $(ARGS)

## test: ツール全体の単体テスト（pytest）を実行します
test:
	PYTHONPATH=. uv run pytest

