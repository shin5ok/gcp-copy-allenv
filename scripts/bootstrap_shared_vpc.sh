#!/usr/bin/env bash
# =============================================================================
# bootstrap_shared_vpc.sh
# -----------------------------------------------------------------------------
# config.yaml の dst マッピングに従い、コピー先で共有 VPC を構成する。
#   - host_project.dst を「共有 VPC ホスト」に有効化
#   - service_projects[].dst を「サービスプロジェクト」としてホストに関連付け
#   - サービスプロジェクトが使う各 SA にホストプロジェクトの networkUser を付与
#
# bulk-export には共有 VPC の共有設定が含まれないため、terraform(Step 4) で VM を
# 共有サブネットに作成する前提として本スクリプトで構成しておく必要がある。
# dst プロジェクトへの書き込みを伴う手動セットアップ用。デフォルトは dry-run。
#
# 使い方:
#   scripts/bootstrap_shared_vpc.sh                 # dry-run（表示のみ）
#   scripts/bootstrap_shared_vpc.sh --apply         # 実際に構成
#   scripts/bootstrap_shared_vpc.sh --config dst/config.yaml
# =============================================================================
set -euo pipefail

CONFIG="dst/config.yaml"
APPLY=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)  APPLY=true; shift ;;
    --config) CONFIG="$2"; shift 2 ;;
    -h|--help) sed -n '2,24p' "$0"; exit 0 ;;
    *) echo "不明な引数: $1" >&2; exit 1 ;;
  esac
done

if [[ ! -f "${CONFIG}" ]]; then
  echo "エラー: 設定ファイルが見つかりません: ${CONFIG}" >&2
  exit 1
fi

# host_dst をタブ区切りで取得
HOST_DST="$(uv run python3 - "${CONFIG}" <<'PY'
import sys, yaml
cfg = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
h = (cfg.get("project_mapping") or {}).get("host_project") or {}
print(h.get("dst", ""))
PY
)"

# 各 service_project の (dst, dst_sa) を取得
SVC_PAIRS="$(uv run python3 - "${CONFIG}" <<'PY'
import sys, yaml
cfg = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
m = cfg.get("project_mapping") or {}
for e in (m.get("service_projects") or []):
    if isinstance(e, dict) and e.get("dst"):
        print(f"{e['dst']}\t{e.get('dst_impersonate_service_account','')}")
PY
)"

if [[ -z "${HOST_DST}" ]]; then
  echo "エラー: host_project.dst を取得できませんでした。" >&2
  exit 1
fi

echo "============================================================"
echo " copy-all-env  共有 VPC ブートストラップ"
echo "============================================================"
echo "  config    = ${CONFIG}"
echo "  host(dst) = ${HOST_DST}"
echo "  mode      = $([[ "${APPLY}" == true ]] && echo APPLY || echo 'DRY-RUN（表示のみ。実行は --apply）')"
echo "------------------------------------------------------------"

run() {
  echo "+ $*"
  if [[ "${APPLY}" == true ]]; then "$@"; fi
}

# 1) host を共有 VPC ホストに有効化
#    ホスト判定は associated-projects list が成功するか（=既にホスト）で行う。
#    （get-host-project はサービスプロジェクト用で、ホスト自身に使うと空成功になり誤判定）
echo
echo "===== host を共有 VPC ホスト化: ${HOST_DST} ====="
if [[ "${APPLY}" == true ]] && \
   gcloud compute shared-vpc associated-projects list "${HOST_DST}" >/dev/null 2>&1; then
  echo "  ✓ ${HOST_DST} は既に共有 VPC ホスト。スキップ"
else
  run gcloud compute shared-vpc enable "${HOST_DST}"
fi

# 2) 各 service_project を関連付け + networkUser 付与
HOST_NUM=""
if [[ "${APPLY}" == true ]]; then
  HOST_NUM="$(gcloud projects describe "${HOST_DST}" --format='value(projectNumber)' 2>/dev/null || true)"
fi

while IFS=$'\t' read -r SVC SA; do
  [[ -z "${SVC}" ]] && continue
  echo
  echo "===== service project: ${SVC} ====="

  # 関連付け（既存ならスキップ）
  if [[ "${APPLY}" == true ]] && \
     gcloud compute shared-vpc associated-projects list "${HOST_DST}" \
       --format='value(id)' 2>/dev/null | grep -qx "${SVC}"; then
    echo "  ✓ ${SVC} は既に関連付け済み。スキップ"
  else
    run gcloud compute shared-vpc associated-projects add "${SVC}" \
      --host-project "${HOST_DST}"
  fi

  # networkUser を付与する member を構築:
  #  - サービスプロジェクトの dst 借用 SA
  #  - サービスプロジェクトの Google API サービスエージェント / 既定 compute SA
  SVC_NUM=""
  if [[ "${APPLY}" == true ]]; then
    SVC_NUM="$(gcloud projects describe "${SVC}" --format='value(projectNumber)' 2>/dev/null || true)"
  fi
  MEMBERS=()
  [[ -n "${SA}" ]] && MEMBERS+=("serviceAccount:${SA}")
  if [[ -n "${SVC_NUM}" ]]; then
    MEMBERS+=("serviceAccount:${SVC_NUM}@cloudservices.gserviceaccount.com")
    MEMBERS+=("serviceAccount:${SVC_NUM}-compute@developer.gserviceaccount.com")
  fi

  for M in "${MEMBERS[@]}"; do
    run gcloud projects add-iam-policy-binding "${HOST_DST}" \
      --member="${M}" --role="roles/compute.networkUser" --condition=None
  done
done <<< "${SVC_PAIRS}"

echo
echo "------------------------------------------------------------"
if [[ "${APPLY}" == true ]]; then
  echo " 完了。IAM/共有 VPC の反映に少し時間がかかることがあります。"
else
  echo " DRY-RUN でした。実際に構成するには --apply を付けて再実行:"
  echo "   scripts/bootstrap_shared_vpc.sh --apply"
fi
echo "------------------------------------------------------------"
