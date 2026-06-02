# Walkthrough - Implement 'make projects' and Mock Mode Simulation

This walkthrough documents the implementation and verification of:
1.  The `make projects` feature to provision destination projects.
2.  The **Mock Mode (`--mock`)** to safely simulate the entire migration run in mock environments.

---

## 1. Feature: `make projects` for Destination Provisioning

To automate the "Provisioning / Bootstrap" phase defined in `PROCEDURE.md`, we created `scripts/create_projects.py`.

### 1.1. Functionality
- Parses `bootstrap` (org/folder ID, billing account) and `project_mapping` (destination projects) from `dst/config.yaml`.
- Check: Verifies if the project already exists via `gcloud projects describe`.
- Create: Provisions the project under the specified organization or folder if it doesn't exist.
- Billing: Links the billing account.
- Services: Enables core required APIs (`compute.googleapis.com`, `dns.googleapis.com`).
- Support for Dry-run (`make projects-plan`) and Production (`make projects`).

### 1.2. Verification
- **Unit Tests**: Implemented in `tests/test_create_projects.py` and passed successfully.
- **Dry-run Simulation**: Successfully verified with templates.
  ```
  [Create Project dst-sharedhost] [DRY RUN] Planned: gcloud projects create dst-sharedhost --organization=123456789012
  [Link Billing dst-sharedhost] [DRY RUN] Planned: gcloud beta billing projects link dst-sharedhost --billing-account=012345-6789AB-CDEF01
  [Enable APIs dst-sharedhost] [DRY RUN] Planned: gcloud services enable compute.googleapis.com dns.googleapis.com --project=dst-sharedhost
  ```

---

## 2. Feature: Mock Mode for End-to-End Run Simulation

To bypass live GCP authentication restrictions during testing and verification, we implemented a robust Mock Mode.

### 2.1. Functionality
- Triggered by the `--mock` CLI flag or `global.mock: true` in `config.yaml`.
- Intercepts live GCP / Terraform CLI commands in `run_command` and returns simulated successful outputs:
  - `instances list`: Returns simulated VM list JSON containing targeting VMs (`org-svc1-...`).
  - `snapshots list`: Returns fresh snapshot metadata for the VM boot disks to pass age check validation.
  - `storage list` / `rsync`: Returns mock GCS bucket lists and simulates synchronization.
  - `bq ls` / `show` / `mk` / `cp`: Simulates BQ datasets/tables metadata querying and copying.
  - VM mutation steps: Simulates stop, detach, disk delete, disk creation from snapshot, attach, and start success.
  - Terraform CLI commands: Simulates `init` and `apply` success.
- **Skeleton HCL Generation**: Dynamically writes dummy `.tf` files into `terraform/raw/` inside mock run to give the HCL Customizer real files to process.

### 2.2. Verification (`make run ARGS="--mock"`)
Executed the full orchestrator in production mode simulation.

**Command**:
```bash
make run ARGS="--mock"
```

**Results**:
- All 6 steps completed without GCP credential errors.
- Mock resources list mapped correctly.
- Snapshot validation passed.
- Dummy HCL successfully customized (project IDs swapped, bucket renamed with suffix `-dst-0602`, boot disk source removed).
- All VM restoration steps and GCS / BQ data copies successfully simulated.

```
[2026-06-02 14:32:45] [INFO] --- [Step 6] Data Sync Completed ---
[2026-06-02 14:32:45] [INFO] === Migration Finished ===
```

---

## 3. Test Suite Summary
All unit tests are passing:
```bash
$ make test
PYTHONPATH=. uv run pytest
============================== 10 passed in 0.18s ==============================
```

## 4. Location of Files
- **Project Provisioner**: [scripts/create_projects.py](file:///usr/local/google/home/kawanos/repos/copy-all-env/scripts/create_projects.py)
- **Unit Tests**: [tests/test_create_projects.py](file:///usr/local/google/home/kawanos/repos/copy-all-env/tests/test_create_projects.py)
- **Orchestrator**: [scripts/sync_env.py](file:///usr/local/google/home/kawanos/repos/copy-all-env/scripts/sync_env.py)
- **Makefile**: [Makefile](file:///usr/local/google/home/kawanos/repos/copy-all-env/Makefile)
