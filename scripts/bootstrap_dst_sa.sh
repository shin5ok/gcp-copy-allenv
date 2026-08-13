#!/usr/bin/env bash
# =============================================================================
# bootstrap_dst_sa.sh
# -----------------------------------------------------------------------------
# config.yaml の dst_impersonate_service_account に指定された「借用SA」を、
# 各 dst(コピー先) プロジェクトに作成し、編集ロールと借用権限を付与する。
#
# src 版 (bootstrap_src_sa.sh) の dst 対応版。dst プロジェクトへの書き込みを
# 伴う手動セットアップ用スクリプト。デフォルトは dry-run（表示のみ）。
# 実際に流すときだけ --apply を付ける。
#
# 付与内容（各 dst プロジェクトごと）:
#   - owner/editor SA を作成
#   - roles/editor … VM/ディスク/GCS/BigQuery などの作成・編集を網羅
#   - roles/resourcemanager.projectIamAdmin … Step 5.7 の IAM ロール複製に必須
#   - 実行アカウントに roles/iam.serviceAccountTokenCreator（= SA 借用権限）
#
# 使い方:
#   scripts/bootstrap_dst_sa.sh                 # dry-run（コマンド表示のみ）
#   scripts/bootstrap_dst_sa.sh --apply         # 実際に作成・付与
#   scripts/bootstrap_dst_sa.sh --apply --impersonator user:foo@example.com
#   scripts/bootstrap_dst_sa.sh --config dst/config.yaml
#   scripts/bootstrap_dst_sa.sh --apply --role roles/owner   # ロールを変更
# =============================================================================
set -euo pipefail

CONFIG="dst/config.yaml"
APPLY=false
IMPERSONATOR=""
# 付与ロール。roles/editor は data-plane 権限（storage.objects.* / bigquery.tables.* 等）を
# 含まないため、Step 6（GCS/BQ 同期）用に storage.admin / bigquery.admin も付与する。
# iam.roleAdmin は Terraform で google_project_iam_custom_role を作成するために必須
# （editor には iam.roles.create が含まれない）。
# resourcemanager.projectIamAdmin は Step 5.7（IAM ロール複製）で src SA の
# ロールを dst SA へ付与するために必須（editor には setIamPolicy が含まれない）。
# この SA が「任意の principal に任意のロールを配れる」= 実質 owner 相当の力を
# 持つことになる点は認識した上で付与すること。IAM 複製が不要なら
# config の steps.iam_sync.enabled=false にしてこのロールを外してよい。
ROLES=(
  "roles/editor"
  "roles/storage.admin"
  "roles/bigquery.admin"
  "roles/iam.roleAdmin"
  "roles/resourcemanager.projectIamAdmin"
)

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)        APPLY=true; shift ;;
    --config)       CONFIG="$2"; shift 2 ;;
    --impersonator) IMPERSONATOR="$2"; shift 2 ;;
    --role)         ROLES=("$2"); shift 2 ;;
    -h|--help)
      sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "不明な引数: $1" >&2; exit 1 ;;
  esac
done

# --- 実行アカウント（借用する側）を決定 ---------------------------------------
if [[ -z "${IMPERSONATOR}" ]]; then
  ACTIVE="$(gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null | head -1)"
  if [[ -z "${ACTIVE}" ]]; then
    echo "エラー: gcloud のアクティブアカウントが取得できません。--impersonator で明示してください。" >&2
    exit 1
  fi
  IMPERSONATOR="user:${ACTIVE}"
fi

if [[ ! -f "${CONFIG}" ]]; then
  echo "エラー: 設定ファイルが見つかりません: ${CONFIG}" >&2
  exit 1
fi

# --- config.yaml から (dst_project, sa_email) を抽出 --------------------------
# host_project / service_projects[] / standalone_projects[] の
# dst / dst_impersonate_service_account。
PAIRS="$(uv run python3 - "${CONFIG}" <<'PY'
import sys, yaml
with open(sys.argv[1], encoding="utf-8") as f:
    cfg = yaml.safe_load(f)
m = cfg.get("project_mapping", {}) or {}
ents = []
h = m.get("host_project")
if isinstance(h, dict):
    ents.append(h)
ents += [e for e in (m.get("service_projects") or []) if isinstance(e, dict)]
ents += [e for e in (m.get("standalone_projects") or []) if isinstance(e, dict)]
for e in ents:
    dst = e.get("dst")
    sa = e.get("dst_impersonate_service_account")
    if dst and sa:
        print(f"{dst}\t{sa}")
PY
)"

if [[ -z "${PAIRS}" ]]; then
  echo "情報: ${CONFIG} に dst_impersonate_service_account が指定されたエントリがありません。"
  echo "       借用 SA は **オプション** で、未指定なら sync_env.py は gcloud アクティブアカウント / ADC に"
  echo "       フォールバックします (ADC 主体に編集権限が必要)。bootstrap で作る対象が無いためスキップします。"
  exit 0
fi

echo "============================================================"
echo " copy-all-env  dst 借用SA ブートストラップ"
echo "============================================================"
echo "  config       = ${CONFIG}"
echo "  impersonator = ${IMPERSONATOR}"
echo "  roles        = ${ROLES[*]}"
echo "  mode         = $([[ "${APPLY}" == true ]] && echo APPLY || echo 'DRY-RUN（表示のみ。実行は --apply）')"
echo "------------------------------------------------------------"

# コマンドを表示し、--apply のときだけ実行する。
run() {
  echo "+ $*"
  if [[ "${APPLY}" == true ]]; then
    "$@"
  fi
}

# コマンドを表示し、--apply のときだけ実行。失敗したら数回リトライする。
# 新規作成した SA の IAM 伝播遅延（"does not exist" / "Policy modification failed"）対策。
run_retry() {
  echo "+ $*"
  if [[ "${APPLY}" != true ]]; then
    return 0
  fi
  local attempt=1 max=6
  until "$@"; do
    if (( attempt >= max )); then
      echo "  ! ${max} 回試行しても失敗しました: $*" >&2
      return 1
    fi
    echo "  … 失敗。IAM 伝播待ちで ${attempt}0 秒後に再試行 (${attempt}/${max})"
    sleep $(( attempt * 10 ))
    (( attempt++ ))
  done
}

# 新規作成した SA が API から解決できる（describe が通る）まで待つ。
wait_for_sa() {
  local email="$1" proj="$2"
  [[ "${APPLY}" != true ]] && return 0
  local attempt=1 max=12
  until gcloud iam service-accounts describe "${email}" --project="${proj}" >/dev/null 2>&1; do
    if (( attempt >= max )); then
      echo "  ! SA がまだ解決できません（伝播遅延の可能性）: ${email}" >&2
      return 1
    fi
    echo "  … SA 伝播待ち 5 秒 (${attempt}/${max})"
    sleep 5
    (( attempt++ ))
  done
}

while IFS=$'\t' read -r PROJ EMAIL; do
  [[ -z "${PROJ}" ]] && continue
  SHORT="${EMAIL%%@*}"           # @ より前 = SA 短名
  SA_DOMAIN="${EMAIL#*@}"        # 念のため確認用
  echo
  echo "===== ${PROJ} / ${EMAIL} ====="

  # SA のメールは <short>@<project>.iam.gserviceaccount.com である前提を軽く検証
  if [[ "${SA_DOMAIN}" != "${PROJ}.iam.gserviceaccount.com" ]]; then
    echo "  ! 注意: SA ドメイン(${SA_DOMAIN})が dst プロジェクト(${PROJ})と一致しません。" \
         "config を確認してください（このまま続行します）。"
  fi

  # 1) editor SA を作成（既存ならスキップ）
  if gcloud iam service-accounts describe "${EMAIL}" --project="${PROJ}" >/dev/null 2>&1; then
    echo "  ✓ ${EMAIL} は既存。作成をスキップ"
  else
    run gcloud iam service-accounts create "${SHORT}" \
      --project="${PROJ}" \
      --display-name="copy-all-env dst editor"
    # 作成直後は伝播遅延で後続の binding が "does not exist" になるため、解決を待つ
    wait_for_sa "${EMAIL}" "${PROJ}"
  fi

  # 2) 各ロールを付与（伝播遅延に備えてリトライ）
  for ROLE in "${ROLES[@]}"; do
    run_retry gcloud projects add-iam-policy-binding "${PROJ}" \
      --member="serviceAccount:${EMAIL}" \
      --role="${ROLE}" --condition=None
  done

  # 3) 実行アカウントにこの SA の借用権限を付与
  run_retry gcloud iam service-accounts add-iam-policy-binding "${EMAIL}" \
    --project="${PROJ}" \
    --member="${IMPERSONATOR}" \
    --role="roles/iam.serviceAccountTokenCreator"

done <<< "${PAIRS}"

echo
echo "------------------------------------------------------------"
if [[ "${APPLY}" == true ]]; then
  echo " 完了。IAM 反映に 1〜2 分かかることがあります。"
  echo " 反映後、'make plan' で dst SA の借用と権限チェックが通るか確認してください。"
else
  echo " DRY-RUN でした。実際に作成・付与するには --apply を付けて再実行:"
  echo "   scripts/bootstrap_dst_sa.sh --apply"
fi
echo "------------------------------------------------------------"
