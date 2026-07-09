# dst 環境の outbound 遮断（検疫）設計 — egress deny + Private Google Access

> 目的: クローンした dst 環境（VPC 上の GCE / マネージドサービス）からの outbound を遮断し、
> src ORG や他プロジェクトへネットワーク的な影響を与えない。かつ GCP 自体は正常動作させる。
>
> 裏取り: Developer Knowledge API (`developerknowledge.googleapis.com` Managed MCP) 経由で
> 公式ドキュメントを照会・確認済み（末尾の Sources 参照）。2026-07-07 作成。

---

## TL;DR（結論）

| 問い | 答え |
|---|---|
| outbound を reject しつつ GCP を正常動作させられるか | **可能**。deny-all egress + Google API VIP への allow の組み合わせが公式レシピ |
| Private Google Access (PGA) は有効にすべきか | **Yes（必須）**。ただし PGA 単体では不十分で、**FW allow + Cloud DNS 上書き + route** とセットで初めて機能する |
| マネージドサービス（GCS/BQ）の outbound は | firewall では止まらない。**VPC-SC ペリメタ（既存 Step 7）+ restricted VIP** が担当 |
| どのタイミングで適用するか | 「最後」ではなく **VM が起動する前（Step 4.5 冒頭）** に適用すべき（後述の注意点） |

構成 4 点セット:

1. **deny-all egress**（priority 1）+ **VIP / 内部 / KMS への allow**（priority 0）
2. **PGA** を全 subnet で有効化
3. **Cloud DNS private zone** で `*.googleapis.com` → VIP に上書き
4. **default route は残す**（VIP も KMS も default-internet-gateway 経由が公式要件）

---

## 1. Firewall ルールセット（dst host VPC に適用）

dst は Shared VPC 構成なので、**host プロジェクトの VPC に張れば全 service プロジェクトの VM に効く**。

egress ルール（priority の小さい順に評価。同点は deny 勝ち）:

| priority | action | 対象 | 用途 |
|---|---|---|---|
| 0 | allow | `tcp:443` → `199.36.153.4/30` | restricted.googleapis.com（VPC-SC 併用時の推奨）。VPC-SC を使わないなら `199.36.153.8/30`（private.googleapis.com） |
| 0 | allow | `all` → dst VPC の subnet CIDR 群 | クローン内部の VM 間通信を維持する場合のみ |
| 0 | allow | `tcp:1688` → `35.190.247.13/32` | Windows VM の KMS ライセンス認証（公式要件） |
| 1 | **deny** | `all` → `0.0.0.0/0` | 検疫本体。IPv6 subnet があれば `::/0` の deny と VIP v6（`2600:2d00:0002:1000::/56` restricted / `2600:2d00:0002:2000::/56` private）+ KMS v6（`2001:4860:4802:32::86/128`）の allow も追加 |

gcloud 例:

```bash
HOST=<dst-host-project>
NET=shared-vpc   # dst host VPC 名

# Google API VIP (restricted) への allow
gcloud compute firewall-rules create quarantine-allow-google-apis \
  --project=$HOST --network=$NET --direction=EGRESS --action=ALLOW \
  --rules=tcp:443 --destination-ranges=199.36.153.4/30 --priority=0

# 内部通信の維持（必要な場合のみ。CIDR は dst VPC の実 subnet に合わせる）
gcloud compute firewall-rules create quarantine-allow-internal \
  --project=$HOST --network=$NET --direction=EGRESS --action=ALLOW \
  --rules=all --destination-ranges=10.0.0.0/8 --priority=0

# Windows KMS（Windows VM がある場合のみ）
gcloud compute firewall-rules create quarantine-allow-win-kms \
  --project=$HOST --network=$NET --direction=EGRESS --action=ALLOW \
  --rules=tcp:1688 --destination-ranges=35.190.247.13/32 --priority=0

# deny-all（検疫本体）
gcloud compute firewall-rules create quarantine-deny-all-egress \
  --project=$HOST --network=$NET --direction=EGRESS --action=DENY \
  --rules=all --destination-ranges=0.0.0.0/0 --priority=1
```

### 複製ルールとの優先度競合（要注意）

- Step 4.5 が複製する src 由来ルールは通常 priority 1000 前後 → deny(1) が必ず勝つ。
- ただし **src に priority 0 の allow egress があると同点で突破される**ため、
  複製時に「priority 0/1 のルールが無いか」を検査して WARNING を出すのが安全。
- より堅い代替案: VPC を
  `gcloud compute networks update $NET --network-firewall-policy-enforcement-order=BEFORE_CLASSIC_FIREWALL`
  にして、検疫ルールを **global network firewall policy** 側に置く。
  classic（複製ルール）より常に先に評価されるため、優先度の数値競合自体が消える。
  Step 4.5 の既存機構（`fw_rule_scope_flag()` 等）で扱える形式。
- さらに強くするなら dst folder への **hierarchical firewall policy**（全 dst プロジェクトに
  強制・プロジェクト内から上書き不可）もあるが、org レベル権限が必要なのでオプション扱い。

---

## 2. Private Google Access + DNS + route

**PGA は「有効にすべき」**。外部 IP なしの VM が Google API（VIP 含む）へ到達する唯一の経路。
ただし以下 3 点とセットでないと機能しない:

```bash
# (1) 全 subnet で PGA 有効化（dst host プロジェクト）
gcloud compute networks subnets update <subnet> \
  --project=$HOST --region=<region> --enable-private-google-access

# (2) Cloud DNS private zone で *.googleapis.com を VIP に収束させる
gcloud dns managed-zones create quarantine-googleapis \
  --project=$HOST --dns-name=googleapis.com. \
  --visibility=private --networks=$NET
gcloud dns record-sets create restricted.googleapis.com. --zone=quarantine-googleapis \
  --project=$HOST --type=A --ttl=300 \
  --rrdatas=199.36.153.4,199.36.153.5,199.36.153.6,199.36.153.7
gcloud dns record-sets create "*.googleapis.com." --zone=quarantine-googleapis \
  --project=$HOST --type=CNAME --ttl=300 --rrdatas=restricted.googleapis.com.
```

- **(3) route**: default route（`0.0.0.0/0` → default-internet-gateway）は**消さない**。
  VIP（199.36.153.x）も KMS（35.190.247.13）もこの next hop 経由が公式要件。
  route まで消して多層防御にしたい場合は、`199.36.153.4/30` と `35.190.247.13/32` への
  static route（next-hop-gateway=default-internet-gateway）を個別に張ること。
- **FW は PGA トラフィックにも適用される**。だから priority 0 の VIP allow が必須。
- Cloud NAT は複製されたままで無害（FW deny が NAT より先に効く）。

### 壊れない根拠（公式ドキュメント確認済み）

- **metadata server（169.254.169.254 / fd20:ce::254）は firewall ルールの適用対象外で常に到達可**。
  DHCP / DNS 解決 / インスタンスメタデータ / NTP は deny-all でも無傷（公式明記:
  "the instance can access it regardless of any firewall rules that you configure"）。
- ops agent（Logging/Monitoring）、OS Login、snapshot 連携などの guest 発 API 通信は
  DNS 上書き経由で VIP に乗り生存する。
- 死ぬもの（隔離の意図通り）: 外部インターネット全般、apt/yum の外部リポジトリ
  （`packages.cloud.google.com` は VIP 対象外）、src ORG の本番エンドポイントへの通信。

---

## 3. マネージドサービスからの outbound

- GCS / BigQuery のような「VM を介さない outbound」（API 経由のデータ移動・エクスポート）は
  VPC firewall では止まらない。ここは **VPC-SC ペリメタが担当**:
  - dst プロジェクトをペリメタに入れる（既存 **Step 7 `vpc_sc`** そのまま）。
  - ペリメタ外（src ORG・他プロジェクト）への API アクセスを遮断 = クローン VM 内に残った
    本番向け認証情報による横断アクセスもここで封じられる。
  - VIP に **restricted.googleapis.com** を選ぶ理由がこれ（VPC-SC 対応 API のみ提供 + 境界検査）。
- 将来 Cloud Run / Cloud Functions を対象に含める場合: egress を VPC 経由に固定
  （Direct VPC egress / connector + `--vpc-egress=all-traffic`）すれば同じ FW に乗る。
  その際 deny の priority は connector 内部通信用 allow より後にする（公式注意事項）。

---

## 4. 適用タイミング — 「最後のプロセス」への注意（重要）

- Step 5（gce_restore）の復元 VM は**一旦 RUNNING で起動する**仕様
  （電源状態の反映は Step 5.5 でまとめて実施）。
- 検疫 FW を「最後」に張ると、**復元直後〜隔離完了の窓**でクローン VM が boot し、
  ゲスト内に残った設定で src 本番エンドポイントへ通信し得る。
- 検疫ルールは operator 主導の後続ステップを一切妨げない
  （restore / data_sync は手元マシン → GCP API の通信で、VM の egress を使わない）。
- → **推奨: Step 4.5 冒頭（`_replicate_host_networks()` 直後）で適用**。
  VM が 1 台も起動していない時点で VPC を検疫状態にしてから復元に進む。
- 一方 **VPC-SC は現行どおり最後（Step 7）が正解**
  （先に張ると operator の API 操作が境界で弾かれる。既存設計と整合）。
- どうしても最後に張る要件なら、Step 5.5 まで全 VM を TERMINATED で保持する運用変更が必要。

---

## 5. 実装する場合の config 案

```yaml
steps:
  network_firewall:
    quarantine:
      enabled: true
      vip: restricted        # restricted | private（vpc_sc.enabled=true なら restricted 推奨）
      allow_internal: true   # dst VPC subnet CIDR への egress を維持
      windows_kms: true      # Windows VM が対象に含まれる場合
```

- 実処理: subnet PGA 有効化 → Cloud DNS zone 作成 → FW ルール作成（すべて dst のみ書込・冪等）。
  ORG 保護（src read-only）と整合する。
- `validate_steps_config()` に「`quarantine.vip` が `restricted|private` 以外はエラー」
  「`vpc_sc.enabled=true` かつ `vip=private` は WARNING」等の検査を追加。
- 複製ルールの priority 0/1 検出 → WARNING も Step 4.5 に追加。

## 6. 検証チェックリスト（適用後）

```bash
# VM 内から（期待: 成功）
curl -sS https://storage.googleapis.com/           # VIP 経由で 4xx が返れば到達性 OK
dig +short storage.googleapis.com                  # 199.36.153.4-7 が返ること
# VM 内から（期待: タイムアウト = 遮断）
curl -m 5 https://example.com/ ; echo "exit=$?"
# 到達性テスト（手元から）
gcloud network-management connectivity-tests create quarantine-test \
  --source-instance=<vm> --destination-ip-address=8.8.8.8 --destination-port=443 --protocol=TCP
```

---

## Sources（Developer Knowledge API 経由で確認）

- [Configure Private Google Access](https://cloud.google.com/vpc/docs/configure-private-google-access) — VIP レンジ（restricted `199.36.153.4/30` / private `199.36.153.8/30`、IPv6 `/56`）、DNS・route 要件
- [VPC firewall rules — Always allowed traffic](https://cloud.google.com/firewall/docs/firewalls) — metadata server は firewall 適用対象外
- [Windows インスタンスの作成と管理 — KMS 要件](https://cloud.google.com/compute/docs/instances/windows/creating-managing-windows-instances) — `kms.windows.googlecloud.com` = `35.190.247.13:1688`、default-internet-gateway 経由必須、egress allow 例
- [VPC-SC: private connectivity のセットアップ](https://cloud.google.com/vpc-service-controls/docs/set-up-private-connectivity) — deny-all + restricted VIP + DNS 上書きの隔離レシピ
- [Cloud Run/Functions と VPC-SC](https://cloud.google.com/run/docs/securing/using-vpc-service-controls) — serverless egress を VPC 経由に固定する構成、deny priority の注意
