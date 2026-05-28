.PHONY: plan deploy destroy test setup snapshot-all scan-org sync-to-dst

setup:
	uv sync

plan:
	uv run scripts/build_env.py --config org/ORG.md --dry-run $(ARGS)

deploy:
	uv run scripts/build_env.py --config org/ORG.md $(ARGS)

destroy:
	uv run scripts/build_env.py --config org/ORG.md --destroy $(ARGS)

snapshot-all: setup
	uv run scripts/build_env.py --config org/ORG.md --snapshot $(ARGS)

scan-org: setup
	uv run scripts/scan_env.py --project <SRC_HOST_PROJECT_ID> --network shared-vpc --output dst/DST.md $(ARGS)

sync-to-dst: setup
	uv run scripts/sync_env.py --config dst/DST.md $(ARGS)

test:
	PYTHONPATH=. uv run pytest
