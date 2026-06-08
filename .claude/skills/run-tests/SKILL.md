---
name: run-tests
description: Run the pytest suite with uv in PYTHONPATH=. mode and show pass/fail summary
---

Run `PYTHONPATH=. uv run pytest -v --tb=short 2>&1 | tail -20` in the project root and report only the summary line (PASSED / FAILED counts). If failures exist, show the failing test names and error snippets.
