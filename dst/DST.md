# コピー元環境実機スキャン構成定義 (DST)

このファイルは、実機環境の自動スキャン結果に基づいて自動生成されました。
この構成情報を複製同期先 (Destination) 環境の構築に適用します。

## 1. プロジェクト構造

| ロール | プロジェクトID | 備考 |
| :--- | :--- | :--- |
| **Host Project** | `shingo-ar-sharedhost0926` | 共有VPCホスト |
| **Service Project 1** | `shingo-ar-sharedservice0926-1` | リソース配置先 |
| **Service Project 2** | `shingo-ar-sharedservice0926-3` | リソース配置先 |

---

## 2. ネットワーク構成 (共有VPC)

ホストプロジェクトで管理され、サービスプロジェクトに共有されるネットワークリソースの定義。

- **共有VPCネットワーク名**: `shared-vpc`
- **リージョン**: `asia-northeast1` (東京)

### 2.1. サブネット定義

| サブネット名 | IP範囲 | 共有先プロジェクト | 備考 |
| :--- | :--- | :--- | :--- |
| `subnet-svc1` | `10.100.1.0/24` | `shingo-ar-sharedservice0926-1` | 自動スキャンによる検出 |
| `subnet-svc3` | `10.100.3.0/24` | `shingo-ar-sharedservice0926-3` | 自動スキャンによる検出 |
| `tokyo` | `10.0.0.0/16` | `shingo-ar-sharedhost0926` | 自動スキャンによる検出 |
| `tokyo-2` | `10.1.0.0/16` | `shingo-ar-sharedhost0926` | 自動スキャンによる検出 |

### 2.2. インターネット接続ゲートウェイ (Cloud NAT)

プライベートVMが外部インターネットへ発信通信を行えるように配置するゲートウェイ。

| ゲートウェイタイプ | リソース名 | 紐付け先ネットワーク/ルーター | 設定詳細 |
| :--- | :--- | :--- | :--- |
| **Cloud Router** | `shared-router` | `shared-vpc` | リージョン: `asia-northeast1` |
| **Cloud NAT** | `shared-nat` | `shared-router` | すべてのサブネットの全IP範囲を対象、外部IP自動割り当て |

---

## 3. VMインスタンス構成 (固定IP割り当て)

全てのインスタンスは、デフォルトで以下の設定を共有します。
- **ゾーン (Zone)**: `asia-northeast1-a`
- **ネットワークカード設定**: 外部IPなし（プライベートIPのみ、上記 Cloud NAT 経由でインターネット接続）

### 3.1. Service Project 1 (`shingo-ar-sharedservice0926-1`)

OSはすべて **Debian 12** (`debian-12`) を使用します。

| インスタンス名 | マシンタイプ | OSイメージ | ゾーン | サブネット | 内部固定IPアドレス |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `org-svc1-deb-e2-mic-01` | `e2-micro` | `debian-12` | `asia-northeast1-a` | `subnet-svc1` | `10.100.1.11` |
| `org-svc1-deb-e2-mic-02` | `e2-micro` | `debian-12` | `asia-northeast1-a` | `subnet-svc1` | `10.100.1.12` |
| `org-svc1-deb-e2-mic-03` | `e2-micro` | `debian-12` | `asia-northeast1-a` | `subnet-svc1` | `10.100.1.13` |
| `org-svc1-deb-n2-std2-01` | `n2-standard-2` | `debian-12` | `asia-northeast1-a` | `subnet-svc1` | `10.100.1.14` |
| `org-svc1-deb-n2-std2-02` | `n2-standard-2` | `debian-12` | `asia-northeast1-a` | `subnet-svc1` | `10.100.1.15` |

### 3.2. Service Project 2 (`shingo-ar-sharedservice0926-3`)

OSはすべて **Ubuntu 22.04 LTS** (`ubuntu-2204-lts`) を使用します。

| インスタンス名 | マシンタイプ | OSイメージ | ゾーン | サブネット | 内部固定IPアドレス |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `org-svc3-ub-c2-std4-01` | `c2-standard-4` | `ubuntu-2204-lts` | `asia-northeast1-a` | `subnet-svc3` | `10.100.3.16` |
| `org-svc3-ub-e2-med-01` | `e2-medium` | `ubuntu-2204-lts` | `asia-northeast1-a` | `subnet-svc3` | `10.100.3.11` |
| `org-svc3-ub-e2-med-02` | `e2-medium` | `ubuntu-2204-lts` | `asia-northeast1-a` | `subnet-svc3` | `10.100.3.12` |
| `org-svc3-ub-e2-med-03` | `e2-medium` | `ubuntu-2204-lts` | `asia-northeast1-a` | `subnet-svc3` | `10.100.3.13` |
| `org-svc3-ub-e2-mic-01` | `e2-micro` | `ubuntu-2204-lts` | `asia-northeast1-a` | `subnet-svc3` | `10.100.3.14` |
| `org-svc3-ub-e2-mic-02` | `e2-micro` | `ubuntu-2204-lts` | `asia-northeast1-a` | `subnet-svc3` | `10.100.3.15` |
