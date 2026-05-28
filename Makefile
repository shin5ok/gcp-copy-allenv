.PHONY: plan deploy destroy test setup

setup:
	uv sync

plan:
	uv run scripts/build_env.py --config org/ORG.md --dry-run $(ARGS)

deploy:
	uv run scripts/build_env.py --config org/ORG.md $(ARGS)

destroy:
	uv run scripts/build_env.py --config org/ORG.md --destroy $(ARGS)

test:
	PYTHONPATH=. uv run pytest
