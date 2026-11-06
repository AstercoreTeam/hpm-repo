# hpm-repo

HPM 官方包仓库。存放芯片驱动、设备驱动和工具链描述文件。
# !!!当前处于测试状态

## 仓库结构

```
hpm-repo/
├── index.json              仓库索引（schema 2）
├── index.json.sig          索引签名（官方证书私钥签）
├── official.crt            官方仓库证书（Root CA 签发）
├── <developer>/            每个开发者一个文件夹
│   ├── <developer>.crt         开发者证书（官方/域证书签发）
│   ├── <package>-<version>.tar.gz
│   └── <package>-<version>.tar.gz.sig
└── toolchains/
    └── <toolchain>-<version>.json
```

## 信任链

```
HPM Root CA（内置在 hpm 客户端）
    ↓ 签发
official.crt（官方仓库证书）
    ↓ 验证
index.json.sig → index.json
    ↓ 主索引 domains.<name>.cert
<domain>/official.crt（域仓库证书）
    ↓ 验证
域 index.json.sig
    ↓ 域索引 developers.<name>.cert
developer.crt（开发者证书）
    ↓ 验证
<package>.tar.gz.sig → <package>.tar.gz
```

## 镜像

镜像需要同步：

- `index.json` + `index.json.sig`
- `official.crt`
- 所有 `<developer>/` 目录
- 域仓库根目录的 `official.crt`（由域仓库自己维护）

旧客户端使用 `official.pub` / `*.pub.sig` 的镜像不再兼容。

## 相关链接

- [HPM 主仓库](https://github.com/xzc842/hpm)
- [驱动包格式规范](https://github.com/xzc842/hpm/blob/main/docs/device_driver.md)
- [PKI 维护文档](https://github.com/xzc842/hpm/blob/main/docs/pki.md)

## 证书指纹

根证书：

```
SHA256: 20:4A:E5:10:20:1C:FC:80:29:36:00:72:F0:2C:CE:EE:92:CC:03:16:0C:76:B1:23:EB:66:93:22:F2:9D:67:AB
```

官方证书：

```
SHA256: 34:A8:75:44:20:FB:A0:8D:EE:67:BA:5D:11:25:6C:5C:44:5F:BC:60:92:D4:BE:53:45:55:B9:CC:F3:A9:61:CF
```

根证书已固定在 hpm 的 `core/include/hpm/config.h`，不再使用首次使用指纹确认。
