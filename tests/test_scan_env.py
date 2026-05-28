import os
import pytest
from scripts.scan_env import EnvConfig, SubnetConfig, VMConfig, MarkdownRenderer

def test_markdown_renderer():
    # Assemble mock scanned configuration data
    config = EnvConfig(host_project="mock-host-proj")
    config.service_projects = ["mock-svc-1", "mock-svc-3"]
    
    # Mock subnets
    config.subnets.append(SubnetConfig(
        name="subnet-svc1",
        ip_range="10.100.1.0/24",
        project="mock-svc-1"
    ))
    config.subnets.append(SubnetConfig(
        name="subnet-svc3",
        ip_range="10.100.3.0/24",
        project="mock-svc-3"
    ))
    
    # Mock VMs
    config.vms["mock-svc-1"] = [
        VMConfig(
            name="org-svc1-deb-e2-std4-01",
            machine_type="e2-standard-4",
            image="debian-12",
            zone="asia-northeast1-a",
            subnet="subnet-svc1",
            ip_address="10.100.1.11"
        )
    ]
    config.vms["mock-svc-3"] = [
        VMConfig(
            name="org-svc3-ub-e2-med-01",
            machine_type="e2-medium",
            image="ubuntu-2204-lts",
            zone="asia-northeast1-a",
            subnet="subnet-svc3",
            ip_address="10.100.3.11"
        )
    ]
    
    renderer = MarkdownRenderer(config, network_name="mock-vpc", region="asia-northeast1")
    rendered_md = renderer.render()
    
    # Verify basic structural headers
    assert "# コピー元環境実機スキャン構成定義 (DST)" in rendered_md
    assert "## 1. プロジェクト構造" in rendered_md
    assert "## 2. ネットワーク構成 (共有VPC)" in rendered_md
    assert "### 2.1. サブネット定義" in rendered_md
    assert "### 2.2. インターネット接続ゲートウェイ (Cloud NAT)" in rendered_md
    assert "## 3. VMインスタンス構成 (固定IP割り当て)" in rendered_md
    
    # Verify Host Project and Service Projects are rendered in table
    assert "| **Host Project** | `mock-host-proj` |" in rendered_md
    assert "| **Service Project 1** | `mock-svc-1` |" in rendered_md
    assert "| **Service Project 2** | `mock-svc-3` |" in rendered_md
    
    # Verify VPC network and region scoping
    assert "- **共有VPCネットワーク名**: `mock-vpc`" in rendered_md
    assert "- **リージョン**: `asia-northeast1`" in rendered_md
    
    # Verify subnets rendering
    assert "| `subnet-svc1` | `10.100.1.0/24` | `mock-svc-1` |" in rendered_md
    assert "| `subnet-svc3` | `10.100.3.0/24` | `mock-svc-3` |" in rendered_md
    
    # Verify VM configs under correct subheaders
    assert "### 3.1. Service Project 1 (`mock-svc-1`)" in rendered_md
    assert "OSはすべて **Debian 12** (`debian-12`) を使用します。" in rendered_md
    assert "| `org-svc1-deb-e2-std4-01` | `e2-standard-4` | `debian-12` | `asia-northeast1-a` | `subnet-svc1` | `10.100.1.11` |" in rendered_md
    
    assert "### 3.2. Service Project 2 (`mock-svc-3`)" in rendered_md
    assert "OSはすべて **Ubuntu 22.04 LTS** (`ubuntu-2204-lts`) を使用します。" in rendered_md
    assert "| `org-svc3-ub-e2-med-01` | `e2-medium` | `ubuntu-2204-lts` | `asia-northeast1-a` | `subnet-svc3` | `10.100.3.11` |" in rendered_md
