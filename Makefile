.DEFAULT_GOAL := help
.PHONY: setup run plan mock test projects projects-plan help clean clean-all \
        bootstrap bootstrap-apply \
        bootstrap-dst-sa bootstrap-dst-sa-apply \
        bootstrap-cross-project bootstrap-cross-project-apply \
        bootstrap-shared-vpc bootstrap-shared-vpc-apply \
        vmware-setup vmware-setup-apply \
        vmware-import vmware-import-apply \
        vmware-start vmware-start-apply \
        vmware-all vmware-all-apply vmware-clean

# vmware/ 配下の Makefile に委譲するためのパラメータ
#   make vmware-setup VMWARE_CONFIG=vmware/other.yaml
VMWARE_CONFIG ?= config.yaml
VMWARE_MAKE   := $(MAKE) -C vmware CONFIG=$(VMWARE_CONFIG)

## help: ターゲット一覧を表示します
help:
	@echo "使用方法: make [target] [ARGS=\"...\"]"
	@echo ""
	@echo "ターゲット一覧:"
	@fgrep -h "##" $(MAKEFILE_LIST) | fgrep -v fgrep | sed -e 's/\\$$//' | sed -e 's/##//' | awk 'BEGIN {FS = ":"}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

## setup: Python/uvの仮想環境を同期・セットアップします
setup:
	uv sync

## plan: ドライランで実行計画を表示します（ORG への書き込みは発生しません / 先に clean 実施）
plan: setup clean
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

## clean: 前回 run のゴミ（terraform active/raw/state、GCS rename 値）を削除して初期化
clean:
	@echo "===== terraform/ 配下の生成物を削除 ====="
	@rm -rf terraform/active terraform/raw
	@rm -f  terraform/.gcs_rename_value
	@find terraform -name '.terraform' -type d -prune -exec rm -rf {} + 2>/dev/null || true
	@find terraform -name '.terraform.lock.hcl' -type f -delete 2>/dev/null || true
	@find terraform -name 'terraform.tfstate*' -type f -delete 2>/dev/null || true
	@find terraform -name 'tfplan' -type f -delete 2>/dev/null || true
	@find terraform -name '.dst_project' -type f -delete 2>/dev/null || true
	@echo "  ✓ terraform/active, raw, state, lock, tfplan を削除しました"
	@echo "  ※ logs/ と cai_export/ は保持（消したい場合は make clean-all）"

## clean-all: clean に加えて logs/ と cai_export/ も削除（完全初期化）
clean-all: clean
	@echo "===== logs/ と cai_export/ も削除 ====="
	@rm -rf logs cai_export
	@echo "  ✓ logs と cai_export を削除しました"

## projects-plan: コピー先プロジェクト作成のドライラン
projects-plan: setup
	uv run python3 scripts/create_projects.py --dry-run $(ARGS)

## projects: コピー先プロジェクトを実際に作成（請求紐付け + API 有効化）
projects: setup
	uv run python3 scripts/create_projects.py --no-dry-run $(ARGS)

## bootstrap: dst SA / src 読取権限 / Shared VPC を順に dry-run（コマンド表示のみ）
bootstrap:
	@echo "===== [1/3] bootstrap_dst_sa.sh (dry-run) ====="
	bash scripts/bootstrap_dst_sa.sh
	@echo "===== [2/3] bootstrap_cross_project.sh (dry-run) ====="
	bash scripts/bootstrap_cross_project.sh
	@echo "===== [3/3] bootstrap_shared_vpc.sh (dry-run) ====="
	bash scripts/bootstrap_shared_vpc.sh

## bootstrap-apply: 上記 3 つを --apply で実行（dst プロジェクトと src(ORG) に IAM/構成を書き込みます）
bootstrap-apply:
	@echo "===== [1/3] bootstrap_dst_sa.sh --apply ====="
	bash scripts/bootstrap_dst_sa.sh --apply
	@echo "===== [2/3] bootstrap_cross_project.sh --apply ====="
	bash scripts/bootstrap_cross_project.sh --apply
	@echo "===== [3/3] bootstrap_shared_vpc.sh --apply ====="
	bash scripts/bootstrap_shared_vpc.sh --apply

## bootstrap-dst-sa: dst SA 作成 + ロール付与（dry-run）
bootstrap-dst-sa:
	bash scripts/bootstrap_dst_sa.sh
bootstrap-dst-sa-apply:
	bash scripts/bootstrap_dst_sa.sh --apply

## bootstrap-cross-project: dst SA に src 読取権限を付与（dry-run）
bootstrap-cross-project:
	bash scripts/bootstrap_cross_project.sh
bootstrap-cross-project-apply:
	bash scripts/bootstrap_cross_project.sh --apply

## bootstrap-shared-vpc: host を Shared VPC 化し svc をアタッチ（dry-run）
bootstrap-shared-vpc:
	bash scripts/bootstrap_shared_vpc.sh
bootstrap-shared-vpc-apply:
	bash scripts/bootstrap_shared_vpc.sh --apply

# ==============================================================================
# VMware VMDK → GCE 化 (vmware/Makefile への委譲)
#   設定: vmware/config.yaml (VMWARE_CONFIG=... で切替可)
# ==============================================================================

## vmware-setup: vmware/ ターゲット project の準備 (API 有効化 / scratch bucket / 内部IP 予約) ※dry-run
vmware-setup:
	$(VMWARE_MAKE) vmware-setup
## vmware-setup-apply: 同上 (--apply で実行)
vmware-setup-apply:
	$(VMWARE_MAKE) vmware-setup-apply

## vmware-import: VMDK を gcloud compute images import でイメージ化 (dry-run)
vmware-import:
	$(VMWARE_MAKE) vmware-import
## vmware-import-apply: 同上 (--apply で実行)
vmware-import-apply:
	$(VMWARE_MAKE) vmware-import-apply

## vmware-start: カスタムイメージから GCE instance を作成・起動 (dry-run)
vmware-start:
	$(VMWARE_MAKE) vmware-start
## vmware-start-apply: 同上 (--apply で実行)
vmware-start-apply:
	$(VMWARE_MAKE) vmware-start-apply

## vmware-all: vmware-setup → vmware-import → vmware-start を一気通貫 (dry-run)
vmware-all:
	$(VMWARE_MAKE) vmware-all
## vmware-all-apply: 同上 (--apply で実行)
vmware-all-apply:
	$(VMWARE_MAKE) vmware-all-apply

## vmware-clean: vmware/logs を削除
vmware-clean:
	$(VMWARE_MAKE) vmware-clean

