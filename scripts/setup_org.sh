#!/usr/bin/env bash
# =============================================================================
# setup_org.sh
# -----------------------------------------------------------------------------
# org/ORG.md に定義された「オリジナル環境」を gcloud で構築する。
#   - host project に VPC / Subnet / Cloud Router / Cloud NAT を作成
#   - host を Shared VPC 化し、service project 1 / 3 を関連付け
#   - 各 service project に内部固定 IP を予約し、Debian / Ubuntu VM を作成
#
# パラメータは下記 ORG.md の表をそのまま埋め込んでいる。値を変更する場合は
# ORG.md 側も同時に更新すること（Single source of truth は ORG.md）。
#
# 冪等性: 各リソースは describe で存在確認し、既存ならスキップする。
# デフォルトは dry-run（表示のみ）。実際に流すときは --apply を付ける。
#
# 使い方:
#   scripts/setup_org.sh                 # dry-run（コマンド表示のみ）
#   scripts/setup_org.sh --apply         # 実際に作成
# =============================================================================
set -euo pipefail

APPLY=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)   APPLY=true; shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "不明な引数: $1" >&2; exit 1 ;;
  esac
done

# ----- ORG.md 由来のパラメータ (SoT は org/ORG.md) -----------------------------
# 値はすべて scripts/parse_org_md.py 経由で ORG.md から読み取る。
# 取得変数: HOST_PROJECT / SVC1_PROJECT / SVC3_PROJECT / NETWORK / REGION / ZONE
#           SUBNET_SVC{1,3} / SUBNET_SVC{1,3}_CIDR / ROUTER / NAT
#           DEBIAN_IMAGE_FAMILY / DEBIAN_IMAGE_PROJECT
#           UBUNTU_IMAGE_FAMILY / UBUNTU_IMAGE_PROJECT
#           VMS_SVC1=( "name|mt|ip" ... ) / VMS_SVC3=( ... )
ORG_MD="${ORG_MD:-org/ORG.md}"
if ! _org_env="$(python3 scripts/parse_org_md.py "${ORG_MD}")"; then
  echo "ERROR: ORG.md パースに失敗 (${ORG_MD})" >&2
  exit 1
fi
eval "${_org_env}"
unset _org_env

# startup-script ディレクトリ。OS 別に配置:
#   org/startup-scripts/linux/*   (実行可能ファイル) → Linux VM の startup-script
#   org/startup-scripts/windows/* (実行可能ファイル) → Windows VM の startup-script (.ps1)
# 複数ファイルがある場合は lexicographic 順に連結して 1 つの startup-script として登録。
# 該当 OS のディレクトリが無い / 実行可能ファイルが 0 件 の場合は登録をスキップする。
STARTUP_DIR="org/startup-scripts"

# OS 別 startup-script bundle 構築。
# 該当 OS の実行可能ファイルを lexicographic 順に連結し tmp に書き出す。
# 該当ファイルが無ければ空文字を残し、create_vm 側で metadata 付与をスキップする。
LINUX_BUNDLE=""
WINDOWS_BUNDLE=""
LINUX_TMP="$(mktemp -t setup_org_linux_startup.XXXXXX.sh)"
WINDOWS_TMP="$(mktemp -t setup_org_windows_startup.XXXXXX.ps1)"
trap 'rm -f "${LINUX_TMP}" "${WINDOWS_TMP}"' EXIT

build_bundle() {
  local os="$1" outfile="$2" header="$3"
  local dir="${STARTUP_DIR}/${os}"
  [[ -d "${dir}" ]] || return 1
  local files=()
  while IFS= read -r -d '' f; do
    files+=("$f")
  done < <(find "${dir}" -mindepth 1 -maxdepth 1 -type f -executable -print0 \
           | sort -z)
  (( ${#files[@]} == 0 )) && return 1
  {
    echo "${header}"
    echo "# auto-bundled by scripts/setup_org.sh from ${dir}/"
    for f in "${files[@]}"; do
      echo
      echo "# ===== $(basename "$f") ====="
      cat "$f"
    done
  } > "${outfile}"
  printf '%s' "${outfile}"
  return 0
}

if LINUX_BUNDLE="$(build_bundle linux "${LINUX_TMP}" '#!/bin/bash')"; then
  echo "  startup(linux)   = $(find "${STARTUP_DIR}/linux" -mindepth 1 -maxdepth 1 -type f -executable | wc -l) ファイル連結 → ${LINUX_BUNDLE}"
else
  LINUX_BUNDLE=""
  echo "  startup(linux)   = (なし - 登録スキップ)"
fi
if WINDOWS_BUNDLE="$(build_bundle windows "${WINDOWS_TMP}" '# PowerShell startup-script')"; then
  echo "  startup(windows) = $(find "${STARTUP_DIR}/windows" -mindepth 1 -maxdepth 1 -type f -executable | wc -l) ファイル連結 → ${WINDOWS_BUNDLE}"
else
  WINDOWS_BUNDLE=""
  echo "  startup(windows) = (なし - 登録スキップ)"
fi

echo "============================================================"
echo " ORG 環境セットアップ (org/ORG.md)"
echo "============================================================"
echo "  host         = ${HOST_PROJECT}"
echo "  service1     = ${SVC1_PROJECT}"
echo "  service3     = ${SVC3_PROJECT}"
echo "  network      = ${NETWORK} (${REGION})"
echo "  zone (VM)    = ${ZONE}"
echo "  mode         = $([[ "${APPLY}" == true ]] && echo APPLY || echo 'DRY-RUN（表示のみ。実行は --apply）')"
echo "------------------------------------------------------------"

# コマンドを表示し、--apply のときだけ実行する。
run() {
  echo "+ $*"
  if [[ "${APPLY}" == true ]]; then "$@"; fi
}

# 存在確認ヘルパ。dry-run でも実行する（API へは read-only）。
exists() { "$@" >/dev/null 2>&1; }

# ---------- 並列実行ヘルパ -------------------------------------------------
# PARALLEL_JOBS で上限を制御 (env override 可)。
# Quota 観点 (Compute Engine API):
#   - regional concurrent ops (instances.insert / addresses.insert): default 500/project
#   - global concurrent ops (snapshots.insert): 専用枠 / 8 並列で問題なし
#   - snapshot per-disk: 6/60min (各 VM 別ディスクなので無関係)
# ⇒ 8 並列はマージン十分。
PARALLEL_JOBS="${PARALLEL_JOBS:-8}"

# parallel_for_each FN ITEM1 ITEM2 ...
#   - 各 ITEM について FN を最大 PARALLEL_JOBS 並列で実行
#   - 出力は item 順に flush（行混在防止のため tmpfile にバッファ）
#   - いずれかが非 0 で終わったら return 1（他 worker は完走させる: set -e の即時中断とは挙動が変わる）
#
# 注意: 末尾は per-pid wait のみで child を順に reap する。`wait` (no args)
# を先に呼ぶと child が job table から消えて以降の `wait "$pid"` が 127 を
# 返してしまうため絶対に呼ばない。
parallel_for_each() {
  local fn="$1"; shift
  local n=$#
  (( n == 0 )) && return 0
  if (( PARALLEL_JOBS <= 1 )) || (( n == 1 )); then
    local item rc=0
    for item in "$@"; do "$fn" "$item" || rc=$?; done
    return $rc
  fi
  local tmpdir; tmpdir="$(mktemp -d -t setup_org_par.XXXXXX)"
  local -a pids=() files=()
  local idx=0 running=0 item out
  for item in "$@"; do
    while (( running >= PARALLEL_JOBS )); do
      wait -n 2>/dev/null || true
      running=$((running - 1))
    done
    out="${tmpdir}/${idx}.out"
    ( "$fn" "$item" ) >"$out" 2>&1 &
    pids+=("$!"); files+=("$out")
    idx=$((idx + 1)); running=$((running + 1))
  done
  local pid rc=0
  for pid in "${pids[@]}"; do wait "$pid" || rc=1; done
  local f
  for f in "${files[@]}"; do cat "$f"; done
  rm -rf "$tmpdir"
  return $rc
}

# ---------- 0. Compute Engine API を有効化 ---------------------------------
echo
echo "===== [0/6] Compute Engine API 有効化 (parallel=${PARALLEL_JOBS}) ====="
enable_compute_api_one() {
  run gcloud services enable compute.googleapis.com --project="$1"
}
parallel_for_each enable_compute_api_one \
  "${HOST_PROJECT}" "${SVC1_PROJECT}" "${SVC3_PROJECT}"

# ---------- 1. Host project: Network / Subnet / Router / NAT ----------------
echo
echo "===== [1/6] host=${HOST_PROJECT}: VPC / Subnet / Router / NAT ====="

# 1.1 VPC
if exists gcloud compute networks describe "${NETWORK}" --project="${HOST_PROJECT}"; then
  echo "  ✓ network ${NETWORK} は既存。スキップ"
else
  run gcloud compute networks create "${NETWORK}" \
    --project="${HOST_PROJECT}" \
    --subnet-mode=custom \
    --bgp-routing-mode=regional
fi

# 1.2 Subnets
create_subnet() {
  local name="$1" cidr="$2"
  if exists gcloud compute networks subnets describe "${name}" \
       --region="${REGION}" --project="${HOST_PROJECT}"; then
    echo "  ✓ subnet ${name} は既存。スキップ"
  else
    run gcloud compute networks subnets create "${name}" \
      --project="${HOST_PROJECT}" \
      --network="${NETWORK}" \
      --region="${REGION}" \
      --range="${cidr}"
  fi
}
create_subnet "${SUBNET_SVC1}" "${SUBNET_SVC1_CIDR}"
create_subnet "${SUBNET_SVC3}" "${SUBNET_SVC3_CIDR}"

# 1.3 Cloud Router
if exists gcloud compute routers describe "${ROUTER}" \
     --region="${REGION}" --project="${HOST_PROJECT}"; then
  echo "  ✓ router ${ROUTER} は既存。スキップ"
else
  run gcloud compute routers create "${ROUTER}" \
    --project="${HOST_PROJECT}" \
    --region="${REGION}" \
    --network="${NETWORK}"
fi

# 1.4 Cloud NAT
if exists gcloud compute routers nats describe "${NAT}" \
     --router="${ROUTER}" --router-region="${REGION}" --project="${HOST_PROJECT}"; then
  echo "  ✓ nat ${NAT} は既存。スキップ"
else
  run gcloud compute routers nats create "${NAT}" \
    --project="${HOST_PROJECT}" \
    --router="${ROUTER}" --router-region="${REGION}" \
    --nat-all-subnet-ip-ranges \
    --auto-allocate-nat-external-ips
fi

# ---------- 2. Shared VPC enable + service projects 関連付け -----------------
echo
echo "===== [2/6] Shared VPC enable + service project 関連付け ====="

# host を Shared VPC ホスト化（associated-projects list が通れば既にホスト）
if [[ "${APPLY}" == true ]] && \
   gcloud compute shared-vpc associated-projects list "${HOST_PROJECT}" >/dev/null 2>&1; then
  echo "  ✓ ${HOST_PROJECT} は既に Shared VPC ホスト。スキップ"
else
  run gcloud compute shared-vpc enable "${HOST_PROJECT}"
fi

associate_project() {
  local svc="$1"
  if [[ "${APPLY}" == true ]] && \
     gcloud compute shared-vpc associated-projects list "${HOST_PROJECT}" \
       --format='value(id)' 2>/dev/null | grep -qx "${svc}"; then
    echo "  ✓ ${svc} は既に関連付け済み。スキップ"
  else
    run gcloud compute shared-vpc associated-projects add "${svc}" \
      --host-project="${HOST_PROJECT}"
  fi
}
associate_project "${SVC1_PROJECT}"
associate_project "${SVC3_PROJECT}"

# ---------- 3. 内部固定 IP の予約 -----------------------------------------
echo
echo "===== [3/6] 内部固定 IP の予約 (parallel=${PARALLEL_JOBS}) ====="

reserve_ip() {
  local proj="$1" name="$2" ip="$3" subnet="$4"
  if exists gcloud compute addresses describe "${name}" \
       --region="${REGION}" --project="${proj}"; then
    echo "  ✓ ${proj}/${name} (${ip}) は既存。スキップ"
  else
    run gcloud compute addresses create "${name}" \
      --project="${proj}" \
      --region="${REGION}" \
      --subnet="projects/${HOST_PROJECT}/regions/${REGION}/subnetworks/${subnet}" \
      --addresses="${ip}"
  fi
}

# wrapper: "proj|name|ip|subnet" を展開して reserve_ip 呼出
# 注意: `local IFS=|` は dynamic scope で呼び先の `echo "+ $*"` まで漏れて
# gcloud 行が "|" 区切りで表示されるので、read builtin への一過性代入だけに限定する。
reserve_ip_one() {
  local proj name ip subnet
  IFS='|' read -r proj name ip subnet <<< "$1"
  reserve_ip "${proj}" "${name}" "${ip}" "${subnet}"
}

ip_items_svc1=()
for entry in "${VMS_SVC1[@]}"; do
  IFS='|' read -r name mt ip <<< "${entry}"
  ip_items_svc1+=("${SVC1_PROJECT}|${name}-ip|${ip}|${SUBNET_SVC1}")
done
parallel_for_each reserve_ip_one "${ip_items_svc1[@]}"

ip_items_svc3=()
for entry in "${VMS_SVC3[@]}"; do
  IFS='|' read -r name mt ip <<< "${entry}"
  ip_items_svc3+=("${SVC3_PROJECT}|${name}-ip|${ip}|${SUBNET_SVC3}")
done
parallel_for_each reserve_ip_one "${ip_items_svc3[@]}"

# ---------- 4. VM 作成 (svc1: Debian) -------------------------------------
create_vm() {
  local proj="$1" name="$2" mt="$3" ip="$4" subnet="$5" \
        image_family="$6" image_project="$7" os="$8"
  if exists gcloud compute instances describe "${name}" \
       --zone="${ZONE}" --project="${proj}"; then
    echo "  ✓ ${proj}/${name} は既存。スキップ"
    return
  fi
  # OS 別の startup-script bundle を metadata に付与（bundle 無しなら付けない）。
  local startup_flag=""
  if [[ "${os}" == "linux"   && -n "${LINUX_BUNDLE}"   ]]; then
    startup_flag="--metadata-from-file=startup-script=${LINUX_BUNDLE}"
  elif [[ "${os}" == "windows" && -n "${WINDOWS_BUNDLE}" ]]; then
    startup_flag="--metadata-from-file=windows-startup-script-ps1=${WINDOWS_BUNDLE}"
  fi
  if [[ -n "${startup_flag}" ]]; then
    run gcloud compute instances create "${name}" \
      --project="${proj}" \
      --zone="${ZONE}" \
      --machine-type="${mt}" \
      --image-family="${image_family}" \
      --image-project="${image_project}" \
      --subnet="projects/${HOST_PROJECT}/regions/${REGION}/subnetworks/${subnet}" \
      --private-network-ip="${ip}" \
      --no-address \
      "${startup_flag}"
  else
    run gcloud compute instances create "${name}" \
      --project="${proj}" \
      --zone="${ZONE}" \
      --machine-type="${mt}" \
      --image-family="${image_family}" \
      --image-project="${image_project}" \
      --subnet="projects/${HOST_PROJECT}/regions/${REGION}/subnetworks/${subnet}" \
      --private-network-ip="${ip}" \
      --no-address
  fi
}

# wrapper: "proj|name|mt|ip|subnet|imgFamily|imgProject|os" を展開して create_vm
create_vm_one() {
  local proj name mt ip subnet img_family img_project os
  IFS='|' read -r proj name mt ip subnet img_family img_project os <<< "$1"
  create_vm "${proj}" "${name}" "${mt}" "${ip}" "${subnet}" \
            "${img_family}" "${img_project}" "${os}"
}

echo
echo "===== [4/6] VM 作成: ${SVC1_PROJECT} (Debian) (parallel=${PARALLEL_JOBS}) ====="
vm_items_svc1=()
for entry in "${VMS_SVC1[@]}"; do
  IFS='|' read -r name mt ip <<< "${entry}"
  vm_items_svc1+=("${SVC1_PROJECT}|${name}|${mt}|${ip}|${SUBNET_SVC1}|${DEBIAN_IMAGE_FAMILY}|${DEBIAN_IMAGE_PROJECT}|linux")
done
parallel_for_each create_vm_one "${vm_items_svc1[@]}"

# ---------- 5. VM 作成 (svc3: Ubuntu) -------------------------------------
echo
echo "===== [5/6] VM 作成: ${SVC3_PROJECT} (Ubuntu) (parallel=${PARALLEL_JOBS}) ====="
vm_items_svc3=()
for entry in "${VMS_SVC3[@]}"; do
  IFS='|' read -r name mt ip <<< "${entry}"
  vm_items_svc3+=("${SVC3_PROJECT}|${name}|${mt}|${ip}|${SUBNET_SVC3}|${UBUNTU_IMAGE_FAMILY}|${UBUNTU_IMAGE_PROJECT}|linux")
done
parallel_for_each create_vm_one "${vm_items_svc3[@]}"

# ---------- 6. 初期 snapshot 作成 -----------------------------------------
# 各 VM 作成後の状態を一度だけスナップショット化する（disk = boot disk = VM 同名）。
# snapshot 名は <vm>-init-snap。snapshot は global リソースなのでプロジェクト内一意。
echo
echo "===== [6/6] 初期 snapshot 作成 (parallel=${PARALLEL_JOBS}) ====="

snapshot_vm() {
  local proj="$1" vm="$2"
  local snap="${vm}-init-snap"
  if exists gcloud compute snapshots describe "${snap}" --project="${proj}"; then
    echo "  ✓ ${proj}/${snap} は既存。スキップ"
  else
    run gcloud compute snapshots create "${snap}" \
      --project="${proj}" \
      --source-disk="${vm}" \
      --source-disk-zone="${ZONE}" \
      --storage-location="${REGION}"
  fi
}

snapshot_vm_one() {
  local proj vm
  IFS='|' read -r proj vm <<< "$1"
  snapshot_vm "${proj}" "${vm}"
}

snap_items_svc1=()
for entry in "${VMS_SVC1[@]}"; do
  IFS='|' read -r name mt ip <<< "${entry}"
  snap_items_svc1+=("${SVC1_PROJECT}|${name}")
done
parallel_for_each snapshot_vm_one "${snap_items_svc1[@]}"

snap_items_svc3=()
for entry in "${VMS_SVC3[@]}"; do
  IFS='|' read -r name mt ip <<< "${entry}"
  snap_items_svc3+=("${SVC3_PROJECT}|${name}")
done
parallel_for_each snapshot_vm_one "${snap_items_svc3[@]}"

echo
echo "------------------------------------------------------------"
if [[ "${APPLY}" == true ]]; then
  echo " 完了。"
else
  echo " DRY-RUN でした。実際に構築するには --apply を付けて再実行:"
  echo "   scripts/setup_org.sh --apply"
fi
echo "------------------------------------------------------------"
