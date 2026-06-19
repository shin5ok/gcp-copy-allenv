# オリジナル環境構成定義 (ORG)

このファイルは、コピー元となるオリジナル環境の構成情報を定義します。
この情報を元に、`gcloud` コマンド等を用いてマシンの作成を行います。

## 1. プロジェクト構造

| ロール | プロジェクトID | 備考 |
| :--- | :--- | :--- |
| **Host Project** | `<SRC_HOST_PROJECT_ID>` | 共有VPCホスト |
| **Service Project 1** | `<SRC_SERVICE_PROJECT_ID_1>` | リソース配置先（Debian環境） |
| **Service Project 3** | `<SRC_SERVICE_PROJECT_ID_3>` | リソース配置先（Ubuntu環境） |

---

## 2. ネットワーク構成 (共有VPC)

ホストプロジェクトで管理され、サービスプロジェクトに共有されるネットワークリソースの定義。

- **共有VPCネットワーク名**: `shared-vpc`
- **リージョン**: `asia-northeast1` (東京)

### 2.1. サブネット定義

| サブネット名 | IP範囲 | 共有先プロジェクト | 備考 |
| :--- | :--- | :--- | :--- |
| `subnet-svc1` | `10.100.1.0/24` | `<SRC_SERVICE_PROJECT_ID_1>` | Debian VM用 |
| `subnet-svc3` | `10.100.3.0/24` | `<SRC_SERVICE_PROJECT_ID_3>` | Ubuntu VM用 |

### 2.2. インターネット接続ゲートウェイ (Cloud NAT)

プライベートVMが外部インターネット（アップデートサーバー等）へ発信通信を行えるように配置するゲートウェイ。

| ゲートウェイタイプ | リソース名 | 紐付け先ネットワーク/ルーター | 設定詳細 |
| :--- | :--- | :--- | :--- |
| **Cloud Router** | `shared-router` | `shared-vpc` | リージョン: `asia-northeast1` |
| **Cloud NAT** | `shared-nat` | `shared-router` | すべてのサブネットの全IP範囲を対象、外部IP自動割り当て |

---

## 3. VMインスタンス構成 (固定IP割り当て)

全てのインスタンスは、デフォルトで以下の設定を共有します（個別指定がある場合を除く）。
- **ゾーン (Zone)**: `asia-northeast1-a`
- **ネットワークカード設定**: 外部IPなし（プライベートIPのみ、上記 Cloud NAT 経由でインターネット接続）

### 3.1. Service Project 1 (`<SRC_SERVICE_PROJECT_ID_1>`)
# 
OSはすべて **Debian 12** (`projects/debian-cloud/global/images/family/debian-12`) を使用します。
# 
| インスタンス名 | マシンタイプ | OSイメージ | ゾーン | サブネット | 内部固定IPアドレス |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `org-svc1-deb-e2-mic-101` | `e2-micro` | `debian-12` | `asia-northeast1-a` | `subnet-svc1` | `10.100.1.110` |
# | `org-svc1-deb-e2-mic-102` | `e2-micro` | `debian-12` | `asia-northeast1-a` | `subnet-svc1` | `10.100.1.120` |
# | `org-svc1-deb-e2-mic-103` | `e2-micro` | `debian-12` | `asia-northeast1-a` | `subnet-svc1` | `10.100.1.130` |
# | `org-svc1-deb-n2-std2-101` | `e2-micro` | `debian-12` | `asia-northeast1-a` | `subnet-svc1` | `10.100.1.140` |
# | `org-svc1-deb-n2-std2-102` | `e2-micro` | `debian-12` | `asia-northeast1-a` | `subnet-svc1` | `10.100.1.150` |

### 3.2. Service Project 3 (`<SRC_SERVICE_PROJECT_ID_3>`)

OSはすべて **Ubuntu 22.04 LTS** (`projects/ubuntu-os-cloud/global/images/family/ubuntu-2204-lts`) を使用します。

| インスタンス名 | マシンタイプ | OSイメージ | ゾーン | サブネット | 内部固定IPアドレス |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `org-svc3-ub-e2-med-301` | `e2-medium` | `ubuntu-2204-lts` | `asia-northeast1-a` | `subnet-svc3` | `10.100.3.111` |
| `org-svc3-ub-e2-med-302` | `e2-medium` | `ubuntu-2204-lts` | `asia-northeast1-a` | `subnet-svc3` | `10.100.3.121` |
| `org-svc3-ub-e2-med-303` | `e2-medium` | `ubuntu-2204-lts` | `asia-northeast1-a` | `subnet-svc3` | `10.100.3.131` |
| `org-svc3-ub-e2-mic-301` | `e2-micro` | `ubuntu-2204-lts` | `asia-northeast1-a` | `subnet-svc3` | `10.100.3.141` |
| `org-svc3-ub-e2-mic-302` | `e2-micro` | `ubuntu-2204-lts` | `asia-northeast1-a` | `subnet-svc3` | `10.100.3.151` |
| `org-svc3-ub-c2-std4-301` | `e2-micro` | `ubuntu-2204-lts` | `asia-northeast1-a` | `subnet-svc3` | `10.100.3.161` |
