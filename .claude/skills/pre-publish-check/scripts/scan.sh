#!/usr/bin/env bash
# pre-publish-check: GitHub public 公開前に機密情報を検出してレベル分け表示する。
#
# 使い方:  bash .claude/skills/pre-publish-check/scripts/scan.sh
# 出力:    Critical / High / Medium に分けてヒットを列挙。最後に件数サマリー。
#
# 対象:    git ls-files (= 追跡中のファイル) のみ。.gitignore 済みは無視。
# 注意:    検出は正規表現ベースで false positive はあり得る。最終判断はユーザー。

set -u

ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || {
  echo "ERROR: not a git repository" >&2
  exit 1
}
cd "$ROOT" || exit 1

# 追跡ファイル一覧（NUL 区切り → スペース/特殊文字を含むファイル名も安全）
mapfile -d '' FILES < <(git ls-files -z)

if [ "${#FILES[@]}" -eq 0 ]; then
  echo "ERROR: no tracked files" >&2
  exit 1
fi

# スキャン対象から外す: バイナリ拡張子と巨大ファイル
EXCLUDE_RE='\.(png|jpg|jpeg|gif|webp|ico|pdf|zip|tar|gz|xz|7z|bz2|lock|min\.js|min\.css|woff2?|ttf|eot|svg)$'
SCAN_FILES=()
for f in "${FILES[@]}"; do
  [[ "$f" =~ $EXCLUDE_RE ]] && continue
  [ -f "$f" ] || continue
  SCAN_FILES+=("$f")
done

# 出力ヘルパ
hr() { printf '\n========== %s ==========\n' "$1"; }
sec() { printf '\n--- %s ---\n' "$1"; }

# パターン検索ヘルパ
#   $1: 表示名  $2: grep -E パターン  $3: 除外パターン (空可)
scan() {
  local label=$1 pat=$2 excl=${3:-}
  sec "$label"
  local out
  if [ -n "$excl" ]; then
    out=$(grep -nHEI -- "$pat" "${SCAN_FILES[@]}" 2>/dev/null | grep -vE "$excl" || true)
  else
    out=$(grep -nHEI -- "$pat" "${SCAN_FILES[@]}" 2>/dev/null || true)
  fi
  if [ -z "$out" ]; then
    echo "(none)"
    return 0
  fi
  echo "$out"
  printf '%s\n' "$out" | wc -l | awk '{print "  -> hits:", $1}'
}

# 共通の除外（このスキャンスクリプト自身、サンプル類、example/template、伏字 prefix）
SELF_EXCLUDE='(\.claude/skills/pre-publish-check/|\.example|placeholder|YOUR_|<[A-Z_]+>|REDACTED|XXXXX)'

C_HITS=0
H_HITS=0
M_HITS=0

count_hits() {
  local name=$1 out
  out=$(grep -nHEI -- "$2" "${SCAN_FILES[@]}" 2>/dev/null | grep -vE "${3:-$SELF_EXCLUDE}" || true)
  [ -z "$out" ] && echo 0 || printf '%s\n' "$out" | wc -l
}

hr "CRITICAL — keys, credentials, real SA emails"

scan "Private key headers (-----BEGIN ...)" \
  '-----BEGIN [A-Z ]*PRIVATE KEY-----' "$SELF_EXCLUDE"

scan "API key shaped strings (Google / OpenAI / AWS)" \
  '(AIza[0-9A-Za-z_-]{30,}|sk-[a-zA-Z0-9_-]{20,}|AKIA[A-Z0-9]{16}|ghp_[A-Za-z0-9]{30,}|xox[abp]-[A-Za-z0-9-]{20,})' \
  "$SELF_EXCLUDE"

scan "Authorization / Bearer tokens" \
  '(Bearer [A-Za-z0-9._-]{20,}|Authorization:\s*Bearer)' \
  "$SELF_EXCLUDE"

sec "Suspicious tracked file paths (.env / *.key / SA JSON)"
SUSP=$(printf '%s\n' "${FILES[@]}" | grep -E '(^|/)(\.env$|\.env\.[^.]+$|.*\.key$|.*-key\.json$|credentials\.json$|service[_-]?account.*\.json$)' | grep -vE "$SELF_EXCLUDE" || true)
if [ -z "$SUSP" ]; then echo "(none)"; else echo "$SUSP"; fi

scan "Real Service Account emails (*@*.iam.gserviceaccount.com)" \
  '[a-z0-9._-]+@[a-z0-9-]+\.iam\.gserviceaccount\.com' \
  "$SELF_EXCLUDE"

C_HITS=$(( \
  $(count_hits k1 '-----BEGIN [A-Z ]*PRIVATE KEY-----') + \
  $(count_hits k2 '(AIza[0-9A-Za-z_-]{30,}|sk-[a-zA-Z0-9_-]{20,}|AKIA[A-Z0-9]{16}|ghp_[A-Za-z0-9]{30,}|xox[abp]-[A-Za-z0-9-]{20,})') + \
  $(count_hits k3 '(Bearer [A-Za-z0-9._-]{20,}|Authorization:\s*Bearer)') + \
  $(printf '%s\n' "${FILES[@]}" | grep -cE '(^|/)(\.env$|\.env\.[^.]+$|.*\.key$|.*-key\.json$|credentials\.json$|service[_-]?account.*\.json$)' || true) + \
  $(count_hits k5 '[a-z0-9._-]+@[a-z0-9-]+\.iam\.gserviceaccount\.com') \
))

hr "HIGH — GCP numeric IDs, hardcoded project_id"

scan "GCP organization / folder numeric IDs (8+ digits)" \
  '(organizations|folders|billingAccounts)/[0-9]{8,}' \
  "$SELF_EXCLUDE|012345|123456"

scan "Hardcoded project_id (yaml / cli / code)" \
  '(project[_-]?id|--project=|project:)\s*["'"'"']?[a-z][a-z0-9-]{4,}' \
  "$SELF_EXCLUDE|project_id:\s*$|project_id:\s*null|YOUR-PROJECT|example-"

H_HITS=$(( \
  $(count_hits h1 '(organizations|folders|billingAccounts)/[0-9]{8,}' "$SELF_EXCLUDE|012345|123456") + \
  $(count_hits h2 '(project[_-]?id|--project=|project:)\s*["'"'"']?[a-z][a-z0-9-]{4,}' "$SELF_EXCLUDE|project_id:\s*$|project_id:\s*null|YOUR-PROJECT|example-") \
))

hr "MEDIUM — private IPs, personal email"

scan "Private / RFC1918 IPv4 addresses" \
  '\b(10\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}|192\.168\.[0-9]{1,3}\.[0-9]{1,3}|172\.(1[6-9]|2[0-9]|3[01])\.[0-9]{1,3}\.[0-9]{1,3})\b' \
  "$SELF_EXCLUDE|10\.0\.0\.0|192\.168\.0\.0|172\.16\.0\.0"

scan "Personal-domain email addresses" \
  '\b[a-zA-Z0-9._%+-]+@(gmail|yahoo|hotmail|outlook|icloud|me|protonmail)\.(com|co\.jp|jp)\b' \
  "$SELF_EXCLUDE|user@example|noreply@"

scan "Internal hostnames (*.internal / *.corp / *.local)" \
  '[a-zA-Z0-9-]+\.(internal|corp|local|lan)\b' \
  "$SELF_EXCLUDE"

M_HITS=$(( \
  $(count_hits m1 '\b(10\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}|192\.168\.[0-9]{1,3}\.[0-9]{1,3}|172\.(1[6-9]|2[0-9]|3[01])\.[0-9]{1,3}\.[0-9]{1,3})\b' "$SELF_EXCLUDE|10\.0\.0\.0|192\.168\.0\.0|172\.16\.0\.0") + \
  $(count_hits m2 '\b[a-zA-Z0-9._%+-]+@(gmail|yahoo|hotmail|outlook|icloud|me|protonmail)\.(com|co\.jp|jp)\b' "$SELF_EXCLUDE|user@example|noreply@") + \
  $(count_hits m3 '[a-zA-Z0-9-]+\.(internal|corp|local|lan)\b') \
))

hr "GIT HISTORY HINT (作業ツリーには無くても履歴に残っている可能性)"
cat <<'EOF'
作業ツリーのみのスキャンです。過去コミットに機密が残っていないか、
特に下記のような怪しいファイルは履歴チェックを推奨:

  git log --all --full-history -p -- dst/config.yaml src/config.yaml vmware/config.yaml
  git log --all --full-history -- '*.env' '*.key' '*-key.json' 'credentials.json'

過去コミットにあった場合: 値ローテーション + git filter-repo で除去 + force push。
EOF

hr "SUMMARY"
printf "Critical : %d\n" "$C_HITS"
printf "High     : %d\n" "$H_HITS"
printf "Medium   : %d\n" "$M_HITS"
echo
if [ "$C_HITS" -gt 0 ]; then
  echo "=> CRITICAL あり: 公開ブロック。値ローテーション + 除去が必要。"
  exit 2
elif [ "$H_HITS" -gt 0 ]; then
  echo "=> HIGH あり: 公開前にプレースホルダ化を強く推奨。"
  exit 1
elif [ "$M_HITS" -gt 0 ]; then
  echo "=> Medium のみ: 内容を確認の上で公開判断。"
  exit 0
else
  echo "=> 検出なし。ただし git 履歴と Low (固有名詞) は別途要確認。"
  exit 0
fi
