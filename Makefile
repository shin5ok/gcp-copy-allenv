.PHONY: plan deploy destroy test setup snapshot-all

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

test:
	PYTHONPATH=. uv run pytest
