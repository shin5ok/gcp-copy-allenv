# Implementation Plan - Add 'make projects' and Mock Mode Simulation

This plan outlines the steps to implement:
1.  The `make projects` command to provision destination GCP projects.
2.  A comprehensive **Mock Mode** to simulate the migration run end-to-end in mock environments.

## 1. Objectives
- Implement `make projects` and `make projects-plan` targets in `Makefile`.
- Create `scripts/create_projects.py` to handle project creation, billing linkage, and initial API enablement.
- Implement `--mock` / `--no-mock` CLI argument and `global.mock` config support in `scripts/sync_env.py`.
- Enable complete end-to-end execution of all 6 migration steps in `make run --mock` without needing valid GCP service accounts.
- Intercept `run_command` calls in mock mode to return simulated success responses (VM lists, snapshot metadata, GCS buckets, BQ tables).
- Dynamically generate skeleton HCL during mock bulk-export to allow the HCL Customizer to run normally.
- Ensure robustness: 100% passing unit tests.

## 2. Detailed Changes

### 2.1. New Script: `scripts/create_projects.py`
- Reads `bootstrap` and `project_mapping` from `dst/config.yaml`.
- Checks existence via `gcloud projects describe`.
- Creates projects using `gcloud projects create` under organization or folder.
- Links billing via `gcloud beta billing projects link`.
- Enables APIs via `gcloud services enable` (`compute` and `dns`).
- Safe dry-run integration.

### 2.2. Mock Mode in `scripts/sync_env.py`
- **Flag Support**: Add `--mock` CLI flag and `global.mock` config check.
- **Simulation Interceptor (`_simulate_command`)**: Intercept command strings inside `run_command` and return mock payloads.
- **TF Skeleton Generation**: Mock `step_bulk_export` to write skeleton `.tf` files to `./terraform/raw/` during mock execution, enabling HCL customization step to run successfully.

### 2.3. `Makefile` Alignment
```makefile
## projects: dst/config.yaml に基づいてコピー先（Destination）プロジェクト群を新規作成・初期化します
projects: setup
	uv run python3 scripts/create_projects.py --no-dry-run $(ARGS)

## projects-plan: プロジェクト作成のドライラン（シミュレーション）を実行します
projects-plan: setup
	uv run python3 scripts/create_projects.py --dry-run $(ARGS)
```

## 3. Verification Plan
- **Unit Tests**: Implement `tests/test_create_projects.py` to mock subprocess and verify command-building logic. Run all tests via `make test`.
- **Mock Run**: Execute `make run ARGS="--mock"` and verify that all 6 migration steps complete successfully without GCP credentials.
