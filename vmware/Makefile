.DEFAULT_GOAL := help
.PHONY: help vmware-setup vmware-setup-apply vmware-import vmware-import-apply \
        vmware-start vmware-start-apply vmware-all vmware-all-apply vmware-clean

CONFIG ?= config.yaml
PY     := uv run python3 scripts/vmdk_run.py --config $(CONFIG)

## help: ターゲット一覧を表示
help:
	@echo "使用方法: make [target] [CONFIG=config.yaml] [ARGS=\"...\"]"
	@echo ""
	@echo "ターゲット一覧:"
	@fgrep -h "##" $(MAKEFILE_LIST) | fgrep -v fgrep | sed -e 's/\\$$//' | sed -e 's/##//' | awk 'BEGIN {FS = ":"}; {printf "  \033[36m%-26s\033[0m %s\n", $$1, $$2}'

## vmware-setup: target project の準備 (API 有効化 / scratch bucket / 内部IP 予約) ※config の dry_run に従う
vmware-setup:
	$(PY) setup $(ARGS)

## vmware-setup-apply: 同上 (--apply で必ず実行)
vmware-setup-apply:
	$(PY) setup --apply $(ARGS)

## vmware-import: VMDK を gcloud compute images import でカスタムイメージ化
vmware-import:
	$(PY) import $(ARGS)

## vmware-import-apply: 同上 (--apply で必ず実行)
vmware-import-apply:
	$(PY) import --apply $(ARGS)

## vmware-start: カスタムイメージから GCE instance を作成して起動
vmware-start:
	$(PY) start $(ARGS)

## vmware-start-apply: 同上 (--apply で必ず実行)
vmware-start-apply:
	$(PY) start --apply $(ARGS)

## vmware-all: setup → import → start を順次 (dry_run は config に従う)
vmware-all: vmware-setup vmware-import vmware-start

## vmware-all-apply: setup → import → start を --apply で順次
vmware-all-apply: vmware-setup-apply vmware-import-apply vmware-start-apply

## vmware-clean: vmware/logs を削除
vmware-clean:
	@rm -rf logs
	@echo "  ✓ vmware/logs を削除しました"
