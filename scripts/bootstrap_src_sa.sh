#!/usr/bin/env bash
# =============================================================================
# bootstrap_src_sa.sh
# -----------------------------------------------------------------------------
# config.yaml の src_impersonate_service_account に指定された「借用SA」を、
# 各 src(ORG) プロジェクトに作成し、読み取りロールと借用権限を付与する。
#
# これは ORG(src) への書き込みを伴うため、sync_env.py の ORG 保護とは
# 意図的に分離した手動セットアップ用スクリプト。デフォルトは dry-run（表示のみ）。
# 実際に流すときだけ --apply を付ける。
#
# 付与内容（各 src プロジェクトごと）:
#   - viewer SA を作成
#   - roles/viewer           … compute / GCS / BigQuery の read を網羅
#   - roles/cloudasset.viewer … CAI スキャン・bulk-export 用
#   - 実行アカウントに roles/iam.serviceAccountTokenCreator（= SA 借用権限）
#
# 使い方:
#   scripts/bootstrap_src_sa.sh                 # dry-run（コマンド表示のみ）
#   scripts/bootstrap_src_sa.sh --apply         # 実際に作成・付与
#   scripts/bootstrap_src_sa.sh --apply --impersonator user:foo@example.com
#   scripts/bootstrap_src_sa.sh --config dst/config.yaml
# =============================================================================
set -euo pipefail

CONFIG="dst/config.yaml"
APPLY=false
IMPERSONATOR=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)        APPLY=true; shift ;;
    --config)       CONFIG="$2"; shift 2 ;;
    --impersonator) IMPERSONATOR="$2"; shift 2 ;;
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

# --- config.yaml から (src_project, sa_email) を抽出 --------------------------
# host_project / service_projects[] / standalone_projects[] の
# src / src_impersonate_service_account。
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
    src = e.get("src")
    sa = e.get("src_impersonate_service_account")
    if src and sa:
        print(f"{src}\t{sa}")
PY
)"

if [[ -z "${PAIRS}" ]]; then
  echo "情報: ${CONFIG} に src_impersonate_service_account が指定されたエントリがありません。"
  echo "       借用 SA は **オプション** で、未指定なら sync_env.py は gcloud アクティブアカウント / ADC に"
  echo "       フォールバックします (src は read-only / is_src_read_only ガードが常時適用)。"
  echo "       bootstrap で作る対象が無いためスキップします。"
  exit 0
fi

echo "============================================================"
echo " copy-all-env  借用SA ブートストラップ"
echo "============================================================"
echo "  config       = ${CONFIG}"
echo "  impersonator = ${IMPERSONATOR}"
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
    echo "  ! 注意: SA ドメイン(${SA_DOMAIN})が src プロジェクト(${PROJ})と一致しません。" \
         "config を確認してください（このまま続行します）。"
  fi

  # 1) viewer SA を作成（既存ならスキップ）
  if gcloud iam service-accounts describe "${EMAIL}" --project="${PROJ}" >/dev/null 2>&1; then
    echo "  ✓ ${EMAIL} は既存。作成をスキップ"
  else
    run gcloud iam service-accounts create "${SHORT}" \
      --project="${PROJ}" \
      --display-name="copy-all-env src read-only viewer"
    # 作成直後は伝播遅延で後続の binding が "does not exist" になるため、解決を待つ
    wait_for_sa "${EMAIL}" "${PROJ}"
  fi

  # 2) 読み取りロールを付与（伝播遅延に備えてリトライ）
  run_retry gcloud projects add-iam-policy-binding "${PROJ}" \
    --member="serviceAccount:${EMAIL}" \
    --role="roles/viewer" --condition=None

  run_retry gcloud projects add-iam-policy-binding "${PROJ}" \
    --member="serviceAccount:${EMAIL}" \
    --role="roles/cloudasset.viewer" --condition=None

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
  echo " 反映後、ORG に書き込まない 'make plan' で借用が通るか確認してください。"
else
  echo " DRY-RUN でした。実際に作成・付与するには --apply を付けて再実行:"
  echo "   scripts/bootstrap_src_sa.sh --apply"
fi
echo "------------------------------------------------------------"
