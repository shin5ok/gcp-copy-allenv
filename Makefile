.DEFAULT_GOAL := help
.PHONY: setup run plan mock test projects projects-plan help clean clean-all clean-project \
        delete-projects delete-projects-plan \
        bootstrap bootstrap-plan \
        bootstrap-dst-sa bootstrap-dst-sa-plan \
        bootstrap-cross-project bootstrap-cross-project-plan \
        bootstrap-shared-vpc bootstrap-shared-vpc-plan \
        org org-plan \
        vmware-setup vmware-setup-apply \
        vmware-import vmware-import-apply \
        vmware-start vmware-start-apply \
        vmware-all vmware-all-apply vmware-clean

# vmware/ 配下の Makefile に委譲するためのパラメータ
#   make vmware-setup VMWARE_CONFIG=vmware/other.yaml
VMWARE_CONFIG ?= config.yaml
VMWARE_MAKE   := $(MAKE) -C vmware CONFIG=$(VMWARE_CONFIG)

# 続行確認 ([y/N]) を自動承認する（非対話 / CI 用）
#   make plan YES=1 / make run YES=1
# `?=` ではなく `:=` にして環境変数 YES を無視する（コマンドラインでの明示指定のみ有効）
YES      :=
YES_FLAG := $(if $(YES),--yes,)

## help: ターゲット一覧を表示します
help:
	@echo "使用方法: make [target] [ARGS=\"...\"]"
	@echo ""
	@echo "ターゲット一覧:"
	@fgrep -h "##" $(MAKEFILE_LIST) | fgrep -v fgrep | sed -e 's/\\$$//' | sed -e 's/##//' | awk 'BEGIN {FS = ":"}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

## setup: Python/uvの仮想環境を同期・セットアップします
setup:
	uv sync

## plan: ドライランで実行計画を表示します（ORG への書き込みは発生しません / state・生成物は保持）※YES=1 で続行確認を自動承認
plan: setup
	uv run python3 scripts/sync_env.py --dry-run $(YES_FLAG) $(ARGS)

## mock: Mock モードで一連の処理をローカル試走（GCP 未接続でも動きます）
mock: setup
	uv run python3 scripts/sync_env.py --mock --no-dry-run $(ARGS)

## run: 本番実行（dst プロジェクトに対する書き込みを伴います）※YES=1 で続行確認を自動承認
run: setup
	uv run python3 scripts/sync_env.py --no-dry-run $(YES_FLAG) $(ARGS)

## test: 単体テスト（pytest）を実行
test:
	PYTHONPATH=. uv run pytest

## clean: terraform 生成物と state を全プロジェクト分削除して初期化（特定のみは clean-project）
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

## clean-project: 指定プロジェクトの terraform 生成物と state のみ削除 要 P=<src または dst の project id>
clean-project: setup
	@if [ -z "$(P)" ]; then \
		echo "ERROR: P を指定してください。例: make clean-project P=my-dst-project" >&2; \
		exit 1; \
	fi
	uv run python3 scripts/sync_env.py --clean-state "$(P)" $(ARGS)

## projects-plan: コピー先プロジェクト作成のドライラン
projects-plan: setup
	uv run python3 scripts/create_projects.py --dry-run $(ARGS)

## projects: コピー先プロジェクトを実際に作成（請求紐付け + API 有効化）
projects: setup
	uv run python3 scripts/create_projects.py --no-dry-run $(ARGS)

## delete-projects-plan: project_id に PATTERN を含むプロジェクトを一覧表示（削除なし） 要 PATTERN=...
delete-projects-plan: setup
	@if [ -z "$(PATTERN)" ]; then \
		echo "ERROR: PATTERN を指定してください。例: make delete-projects-plan PATTERN=foo-dst" >&2; \
		exit 1; \
	fi
	uv run python3 scripts/delete_projects.py --pattern "$(PATTERN)" --dry-run $(ARGS)

## delete-projects: 同上を削除（6 桁ランダムコード入力で安全確認・lien も自動解除） 要 PATTERN=...
delete-projects: setup
	@if [ -z "$(PATTERN)" ]; then \
		echo "ERROR: PATTERN を指定してください。例: make delete-projects PATTERN=foo-dst" >&2; \
		exit 1; \
	fi
	uv run python3 scripts/delete_projects.py --pattern "$(PATTERN)" --no-dry-run $(ARGS)

## bootstrap-plan: dst SA / src 読取権限 / Shared VPC を順に dry-run（コマンド表示のみ）
bootstrap-plan:
	@echo "===== [1/3] bootstrap_dst_sa.sh (dry-run) ====="
	bash scripts/bootstrap_dst_sa.sh
	@echo "===== [2/3] bootstrap_cross_project.sh (dry-run) ====="
	bash scripts/bootstrap_cross_project.sh
	@echo "===== [3/3] bootstrap_shared_vpc.sh (dry-run) ====="
	bash scripts/bootstrap_shared_vpc.sh

## bootstrap: 上記 3 つを --apply で実行（dst プロジェクトと src(ORG) に IAM/構成を書き込みます）
bootstrap:
	@echo "===== [1/3] bootstrap_dst_sa.sh --apply ====="
	bash scripts/bootstrap_dst_sa.sh --apply
	@echo "===== [2/3] bootstrap_cross_project.sh --apply ====="
	bash scripts/bootstrap_cross_project.sh --apply
	@echo "===== [3/3] bootstrap_shared_vpc.sh --apply ====="
	bash scripts/bootstrap_shared_vpc.sh --apply

## bootstrap-dst-sa-plan: dst SA 作成 + ロール付与（dry-run）
bootstrap-dst-sa-plan:
	bash scripts/bootstrap_dst_sa.sh
## bootstrap-dst-sa: 同上を --apply で実行
bootstrap-dst-sa:
	bash scripts/bootstrap_dst_sa.sh --apply

## bootstrap-cross-project-plan: dst SA に src 読取権限を付与（dry-run）
bootstrap-cross-project-plan:
	bash scripts/bootstrap_cross_project.sh
## bootstrap-cross-project: 同上を --apply で実行
bootstrap-cross-project:
	bash scripts/bootstrap_cross_project.sh --apply

## bootstrap-shared-vpc-plan: host を Shared VPC 化し svc をアタッチ（dry-run）
bootstrap-shared-vpc-plan:
	bash scripts/bootstrap_shared_vpc.sh
## bootstrap-shared-vpc: 同上を --apply で実行
bootstrap-shared-vpc:
	bash scripts/bootstrap_shared_vpc.sh --apply

## org-plan: org/ORG.md に定義された元環境（VPC/Subnet/NAT/VM）を dry-run
org-plan:
	bash scripts/setup_org.sh

## org: 同上を --apply で実行（host/svc プロジェクトに書き込みます）
org:
	bash scripts/setup_org.sh --apply

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

