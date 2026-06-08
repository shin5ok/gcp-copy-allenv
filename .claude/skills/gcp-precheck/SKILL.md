---
name: gcp-precheck
description: Pre-flight checklist before any GCP write operation (run, vmware-import, terraform apply)
user-invocable: false
---

Before suggesting or running any GCP write operation, verify:

1. **Target is dst, not src**: Confirm the operation targets the dst project, never org/src.
2. **Impersonation set**: `CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT` is configured for dst SA.
3. **Dry-run first**: Recommend `make plan` or `--dry-run` before `make run`.
4. **No destructive terraform**: Refuse `terraform destroy` unless user explicitly typed it.
5. **VMware**: For vmware-import, confirm VMDK path exists and GCS bucket is in dst project.

If any check fails, stop and ask the user to resolve before proceeding.
