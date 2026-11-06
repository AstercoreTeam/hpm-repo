#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自动生成 index.json

包名规则：
    驱动包  <driver>-<chip>-<version>.tar.gz   → key <driver>-<chip>
    芯片包  <chip>-<version>.tar.gz            → key <chip>
    chip = "*" 表示通用

用法：
    cd <repo-root>
    python3 gen_index.py

扫描：
    <developer>/<driver>-<chip>-<version>.tar.gz
    <developer>/<driver>-<chip>-<version>.tar.gz.sig
    <developer>/<chip>-<version>.tar.gz
    <developer>/<chip>-<version>.tar.gz.sig
    <developer>/<developer>.pub
    <developer>/<developer>.pub.sig

生成：
    index.json（不签名）
"""

import hashlib
import json
import sys
import tarfile
from pathlib import Path

try:
    import tomllib  # type: ignore
    HAS_TOML = True
except ImportError:
    try:
        import tomli as tomllib  # type: ignore
        HAS_TOML = True
    except ImportError:
        HAS_TOML = False

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

REPO_DIR = Path(__file__).resolve().parent

IGNORE_DIRS = {".git", "publish", "__pycache__", ".github", "toolchains"}
IGNORE_FILES = {"index.json", "index.json.sig", "gen_index.py",
                "README.md", "LICENSE", "domain.yaml"}
IGNORE_SUFFIXES = {".key"}

MANIFEST_NAMES = ("manifest.json", "manifest.toml",
                  "chip.json", "chip.toml")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_manifest(data, is_chip, fallback_chip=""):
    chip = data.get("chip", "") or ""
    if is_chip and not chip:
        chip = data.get("name", "") or ""
    if not chip:
        chip = fallback_chip
    return {
        "kind": "chip" if is_chip else "driver",
        "name": data.get("name", "") or "",
        "chip": chip,
        "requires":    data.get("requires", {}) or {},
        "description": data.get("description", "") or "",
        "config":      data.get("config", {}) or {},
        "provides":    data.get("provides", {}) or {},
        "license":     data.get("license", "MIT") or "MIT",
        "pins":        (data.get("pins", {}) or {}) if is_chip else None,
    }


def read_package_meta(tar_path, fallback_chip=""):
    try:
        with tarfile.open(tar_path, "r:gz") as tar:
            for member in tar.getmembers():
                if not member.isfile():
                    continue
                parts = member.name.split("/")
                if len(parts) < 2:
                    continue
                fname = parts[-1]
                if fname not in MANIFEST_NAMES:
                    continue

                f = tar.extractfile(member)
                if not f:
                    continue
                raw = f.read()

                is_chip = fname.startswith("chip")
                if fname.endswith(".toml"):
                    if not HAS_TOML:
                        print(f"  warn: 需要 tomllib/tomli 才能读 {fname}，跳过")
                        return None
                    data = tomllib.loads(raw.decode("utf-8"))
                else:
                    data = json.loads(raw.decode("utf-8"))

                return parse_manifest(data, is_chip, fallback_chip)
    except Exception as e:
        print(f"  warn: 读 {tar_path} 失败: {e}")
    return None


def parse_filename(fname):
    """
    <driver>-<chip>-<version>.tar.gz  → (driver, chip, version)
    <chip>-<version>.tar.gz          → (chip, "", version)
    其他 → (None, None, None)
    """
    if not fname.endswith(".tar.gz"):
        return None, None, None

    base = fname[:-len(".tar.gz")]
    parts = base.rsplit("-", 2)
    if len(parts) == 3:
        driver, chip, ver = parts
    elif len(parts) == 2:
        driver, ver = parts
        chip = ""
    else:
        return None, None, None

    vparts = ver.split(".")
    if len(vparts) < 2:
        return None, None, None
    for p in vparts:
        if not p.isdigit():
            return None, None, None

    return driver, chip, ver


def load_domain_config():
    path = REPO_DIR / "domain.yaml"
    if not path.exists():
        print("  domain.yaml 不存在，跳过")
        return {}

    if not HAS_YAML:
        print("  warn: 没装 PyYAML，跳过 domain.yaml")
        print("        安装：pip3 install pyyaml")
        return {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:
        print(f"  warn: 读 domain.yaml 失败: {e}")
        return {}

    domains = data.get("domains", {})
    if not isinstance(domains, dict):
        print("  warn: domain.yaml 的 domains 字段必须是对象")
        return {}

    result = {}
    for name, info in domains.items():
        if not isinstance(info, dict):
            print(f"  warn: 域 {name} 配置必须是对象")
            continue

        entry = {
            "index": info.get("index", ""),
            "cert": info.get("cert", ""),
        }

        if not entry["index"]:
            print(f"  warn: 域 {name} 缺少 index 字段")
            continue
        if not entry["cert"]:
            print(f"  warn: 域 {name} 缺少 cert 字段")
            continue

        result[name] = entry

    return result


def main():
    repo = REPO_DIR
    print(f"仓库目录: {repo}")

    if not HAS_TOML:
        print("  warn: 未检测到 tomllib/tomli，tar 内的 .toml 清单将无法读取")

    # 1. domain.yaml
    print(f"\n读 domain.yaml")
    domains = load_domain_config()
    if domains:
        for name in domains:
            print(f"  ok: {name}")
    else:
        print(f"  （无域配置）")

    # 2. 扫描
    developers = {}
    packages = {}

    for entry in sorted(repo.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name in IGNORE_DIRS:
            continue

        dev_name = entry.name
        print(f"\n开发者: {dev_name}")

        cert = entry / f"{dev_name}.crt"

        if not cert.exists():
            print(f"  warn: 缺少开发者证书 {cert}")
            continue

        developers[dev_name] = {
            "cert": f"{{baseurl}}/{dev_name}/{dev_name}.crt",
        }
        print(f"  ok: 开发者证书")

        for tar in sorted(entry.glob("*.tar.gz")):
            if tar.name.endswith(".sig"):
                continue

            driver, chip, ver = parse_filename(tar.name)
            if not driver or not ver:
                print(f"  warn: 跳过（文件名不符合）: {tar.name}")
                continue

            sig = entry / (tar.name + ".sig")
            if not sig.exists():
                print(f"  warn: 缺少签名: {sig.name}")
                continue

            meta = read_package_meta(tar, fallback_chip=chip)
            if not meta:
                print(f"  warn: 跳过（无法识别包类型）: {tar.name}")
                continue

            sha = sha256_file(tar)
            size = tar.stat().st_size

            if meta["kind"] == "chip":
                pkg_key = driver
                chip_final = driver
            else:
                if not chip:
                    print(f"  warn: 跳过（驱动包文件名缺 chip 段）: {tar.name}")
                    continue
                pkg_key = f"{driver}-{chip}"
                chip_final = chip

            if pkg_key not in packages:
                packages[pkg_key] = {"versions": {}}

            ver_entry = {
                "kind": meta["kind"],
                "chip": chip_final,
                "developer": dev_name,
                "url": f"{{baseurl}}/{dev_name}/{tar.name}",
                "sig_url": f"{{baseurl}}/{dev_name}/{tar.name}.sig",
                "sha256": sha,
                "size": size,
                "license": meta["license"],
                "description": meta["description"],
                "config": meta["config"],
                "provides": meta["provides"],
                "requires": meta["requires"],
            }
            if meta["kind"] == "chip" and meta.get("pins") is not None:
                ver_entry["pins"] = meta["pins"]

            packages[pkg_key]["versions"][ver] = ver_entry
            print(f"  ok: {pkg_key}@{ver} ({meta['kind']}, {size} bytes)")

    # 3. 写 index.json
    index = {
        "schema": 2,
        "official_cert": "{baseurl}/official.crt",
        "developers": developers,
        "domains": domains,
        "packages": packages,
    }

    index_path = repo / "index.json"
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"\n=== 完成 ===")
    print(f"开发者: {len(developers)}")
    print(f"域: {len(domains)}")
    print(f"包: {len(packages)}")
    print(f"索引: {index_path}")

    print(f"\n下一步：")
    print(f"  # RSA")
    print(f"  openssl dgst -sha256 -sign official.key -out index.json.sig index.json")
    print(f"  # Ed25519")
    print(f"  openssl pkeyutl -sign -inkey official.key -rawin -in index.json -out index.json.sig")


if __name__ == "__main__":
    main()
