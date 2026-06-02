.DEFAULT_GOAL := help
.PHONY: setup run plan mock test projects projects-plan help

## help: ターゲット一覧を表示します
help:
	@echo "使用方法: make [target] [ARGS=\"...\"]"
	@echo ""
	@echo "ターゲット一覧:"
	@fgrep -h "##" $(MAKEFILE_LIST) | fgrep -v fgrep | sed -e 's/\\$$//' | sed -e 's/##//' | awk 'BEGIN {FS = ":"}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

## setup: Python/uvの仮想環境を同期・セットアップします
setup:
	uv sync

## plan: ドライランで実行計画を表示します（ORG への書き込みは発生しません）
plan: setup
	uv run python3 scripts/sync_env.py --dry-run $(ARGS)

## mock: Mock モードで一連の処理をローカル試走（GCP 未接続でも動きます）
mock: setup
	uv run python3 scripts/sync_env.py --mock --no-dry-run $(ARGS)

## run: 本番実行（dst プロジェクトに対する書き込みを伴います）
run: setup
	uv run python3 scripts/sync_env.py --no-dry-run $(ARGS)

## test: 単体テスト（pytest）を実行
test:
	PYTHONPATH=. uv run pytest

## projects-plan: コピー先プロジェクト作成のドライラン
projects-plan: setup
	uv run python3 scripts/create_projects.py --dry-run $(ARGS)

## projects: コピー先プロジェクトを実際に作成（請求紐付け + API 有効化）
projects: setup
	uv run python3 scripts/create_projects.py --no-dry-run $(ARGS)

