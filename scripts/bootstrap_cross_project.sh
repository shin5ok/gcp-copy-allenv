#!/usr/bin/env bash
# =============================================================================
# bootstrap_cross_project.sh
# -----------------------------------------------------------------------------
# データ移行（Step 5: スナップショット復元 / Step 6: GCS・BigQuery 同期）のため、
# 各 dst 借用 SA に対応する src(ORG) プロジェクトの「読み取り」権限を付与する。
#
# sync_env.py の ORG 保護は「src は src SA 経由で read-only」を強制するが、
# Step 5/6 は dst SA が src のスナップショット/バケット/テーブルを読む必要がある。
# これは src(ORG) への IAM 書き込みを伴うため、bootstrap_src_sa.sh と同様に
# sync_env の保護とは分離した手動セットアップとして用意する。デフォルト dry-run。
#
# 付与内容（各ペアの dst SA → 対応する src プロジェクト）:
#   - カスタムロール migrationSrcReader（read-only。基本/定義済みロールでは
#     storage.buckets.get や compute.snapshots.useReadOnly を過不足なく満たせない
#     ため、必要最小限の read 権限だけを束ねた専用ロールを作成して付与する）
#   - roles/bigquery.dataViewer … BigQuery データ読取（Step 6 BQ）
#
# 使い方:
#   scripts/bootstrap_cross_project.sh                 # dry-run
#   scripts/bootstrap_cross_project.sh --apply         # 実際に付与
#   scripts/bootstrap_cross_project.sh --config dst/config.yaml
# =============================================================================
set -euo pipefail

CONFIG="dst/config.yaml"
APPLY=false
CUSTOM_ROLE_ID="migrationSrcReader"
# read-only 権限のみ（ORG を変更しない）。
CUSTOM_PERMS="storage.buckets.get,storage.buckets.list,storage.objects.get,storage.objects.list,compute.snapshots.useReadOnly,compute.snapshots.get,compute.snapshots.list,compute.disks.get,compute.disks.list"
PREDEFINED_ROLES=("roles/bigquery.dataViewer")

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)  APPLY=true; shift ;;
    --config) CONFIG="$2"; shift 2 ;;
    -h|--help) sed -n '2,28p' "$0"; exit 0 ;;
    *) echo "不明な引数: $1" >&2; exit 1 ;;
  esac
done

if [[ ! -f "${CONFIG}" ]]; then
  echo "エラー: 設定ファイルが見つかりません: ${CONFIG}" >&2
  exit 1
fi

# (src_project, dst_sa) を取得
PAIRS="$(uv run python3 - "${CONFIG}" <<'PY'
import sys, yaml
cfg = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
m = cfg.get("project_mapping") or {}
ents = []
h = m.get("host_project")
if isinstance(h, dict):
    ents.append(h)
ents += [e for e in (m.get("service_projects") or []) if isinstance(e, dict)]
ents += [e for e in (m.get("standalone_projects") or []) if isinstance(e, dict)]
for e in ents:
    src = e.get("src"); sa = e.get("dst_impersonate_service_account")
    if src and sa:
        print(f"{src}\t{sa}")
PY
)"

if [[ -z "${PAIRS}" ]]; then
  echo "情報: ${CONFIG} に dst_impersonate_service_account が指定されたエントリがありません。"
  echo "       借用 SA は **オプション** で、未指定なら sync_env.py は ADC (実行ユーザー) で src を読みます。"
  echo "       その場合 dst SA への cross-project read 付与は不要 (実行ユーザー自身が src の Viewer 等を"
  echo "       持つ前提)。bootstrap で付与する対象が無いためスキップします。"
  exit 0
fi

echo "============================================================"
echo " copy-all-env  クロスプロジェクト読取権限 ブートストラップ"
echo "============================================================"
echo "  config = ${CONFIG}"
echo "  custom = ${CUSTOM_ROLE_ID} (${CUSTOM_PERMS})"
echo "  roles  = ${PREDEFINED_ROLES[*]}"
echo "  mode   = $([[ "${APPLY}" == true ]] && echo APPLY || echo 'DRY-RUN（表示のみ。実行は --apply）')"
echo "  注意: これは src(ORG) への read-only IAM 付与です。"
echo "------------------------------------------------------------"

run_retry() {
  echo "+ $*"
  [[ "${APPLY}" != true ]] && return 0
  local attempt=1 max=5
  until "$@"; do
    if (( attempt >= max )); then echo "  ! ${max} 回失敗: $*" >&2; return 1; fi
    echo "  … 失敗。${attempt}0 秒後に再試行 (${attempt}/${max})"; sleep $(( attempt * 10 )); (( attempt++ ))
  done
}

# src ごとにカスタムロールを作成/更新（冪等）
ensure_custom_role() {
  local src="$1"
  [[ "${APPLY}" != true ]] && { echo "+ (custom role ${CUSTOM_ROLE_ID} を ${src} に作成/更新)"; return 0; }
  if gcloud iam roles describe "${CUSTOM_ROLE_ID}" --project="${src}" >/dev/null 2>&1; then
    gcloud iam roles update "${CUSTOM_ROLE_ID}" --project="${src}" \
      --permissions="${CUSTOM_PERMS}" >/dev/null
    echo "  ✓ custom role 更新: ${CUSTOM_ROLE_ID} (${src})"
  else
    gcloud iam roles create "${CUSTOM_ROLE_ID}" --project="${src}" \
      --title="Migration Src Reader" --permissions="${CUSTOM_PERMS}" --stage=GA >/dev/null
    echo "  ✓ custom role 作成: ${CUSTOM_ROLE_ID} (${src})"
  fi
}

while IFS=$'\t' read -r SRC SA; do
  [[ -z "${SRC}" ]] && continue
  echo
  echo "===== src ${SRC} ← dst SA ${SA} ====="
  ensure_custom_role "${SRC}"
  run_retry gcloud projects add-iam-policy-binding "${SRC}" \
    --member="serviceAccount:${SA}" \
    --role="projects/${SRC}/roles/${CUSTOM_ROLE_ID}" --condition=None
  for ROLE in "${PREDEFINED_ROLES[@]}"; do
    run_retry gcloud projects add-iam-policy-binding "${SRC}" \
      --member="serviceAccount:${SA}" --role="${ROLE}" --condition=None
  done
done <<< "${PAIRS}"

echo
echo "------------------------------------------------------------"
if [[ "${APPLY}" == true ]]; then
  echo " 完了。IAM 反映に 1〜2 分かかることがあります。"
else
  echo " DRY-RUN でした。付与するには --apply を付けて再実行。"
fi
echo "------------------------------------------------------------"
