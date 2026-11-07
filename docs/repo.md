# HPM Repo 维护文档

## 一、仓库概述

`hpm-repo` 存放：

- 索引 `index.json` + `index.json.sig`
- 官方证书 `official.crt`
- 域仓库证书（域仓库根目录 `official.crt`）
- 开发者证书与包（每个开发者一个目录）
- 工具链描述 `toolchains/`

**仓库地址**：https://github.com/AstercoreTeam/hpm-repo

**客户端配置**：

```bash
export HPM_REGISTRY="https://raw.githubusercontent.com/AstercoreTeam/hpm-repo/main/index.json"
```

官方证书地址由 hpm 的 `HPM_OFFICIAL_CERT_URL` 固定，根证书固定在
`core/include/hpm/config.h` 的 `HPM_ROOT_CA_PEM`。

## 二、目录结构

```
hpm-repo/
├── README.md                   仓库说明（根目录唯一文档）
├── docs/                       全部文档
│   ├── repo.md                 本文件
│   ├── repo-tool.md             hrm 使用说明
│   ├── repo-tool-design.md      hrm 设计与实现
│   └── repo-tool-development.md hrm 开发与扩展指南
├── index.json                  索引（schema 2）
├── index.json.sig              官方证书私钥签名
├── revoke.json                 证书吊销列表（官方私钥签，可选）
├── revoke.json.sig             吊销列表签名
├── official.crt                官方仓库证书（Root CA 签发）
├── xzc/
│   ├── xzc.crt                 开发者证书（官方/域证书签发）
│   ├── ch552g-1.0.0.tar.gz
│   └── ch552g-1.0.0.tar.gz.sig
├── domain.yaml                 域配置
├── repo-tool/                  维护工具 hrm（./repo-tool/hrm）
└── toolchains/
```

私钥（`*.key`）不进入仓库。

## 三、信任链

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

## 四、索引格式

```json
{
  "schema": 2,
  "official_cert": "{baseurl}/official.crt",
  "developers": {
    "xzc": {
      "cert": "{baseurl}/xzc/xzc.crt"
    }
  },
  "domains": {
    "qincnd": {
      "index": "https://raw.githubusercontent.com/qincnd/hpm-repo/main/index.json",
      "cert": "https://raw.githubusercontent.com/qincnd/hpm-repo/main/official.crt"
    }
  },
  "packages": {}
}
```

字段：

| 字段 | 说明 |
|------|------|
| `schema` | 必须为 `2` |
| `official_cert` | 官方证书 URL |
| `developers.<name>.cert` | 开发者证书 URL |
| `domains.<name>.index` | 域索引 URL |
| `domains.<name>.cert` | 域证书 URL |
| `packages` | 包列表 |
| `kind` | `chip` / `driver` |
| `developer` | 开发者名（对应目录） |
| `url` / `sig_url` | tarball / 签名 URL |
| `sha256` / `size` / `license` | 包元数据 |

`{baseurl}` 会替换成索引 URL 的目录。

## 五、维护工具 hrm

仓库根目录运行：

```bash
./repo-tool/hrm
```

- 私域仓库：Token 来自 `~/.hpm/dev.json`；没有 Token 时主页只显示登录。
- 登录：调用 `hpm-dev login`，本工具不接触 AppKey、不写 `dev.json`。
- 初始化私域仓库：生成 Git 仓库、加密的 official Ed25519 密钥、`official.request.json`。
- 管理开发者：校验 `incoming/*.request.json` 后签发证书，写 `<developer>/<developer>.crt`。
- 吊销：写根目录 `revoke.json` 与 `revoke.json.sig`。
- 重建索引：索引扫描逻辑已并入 hrm，仓库不再需要 `gen_index.py`。

签名时按 `official.key` → `<developer>/<developer>.key` → `~/.hpm/keys/<developer>/` 查找私钥；
找不到会要求输入路径，加密私钥会提示密码。

## 六、发布新包

### 1. 开发者签名包

开发者在自己的包目录运行：

```bash
hpm-dev publish <package-dir> -k <developer.key>
```

它只生成：

```text
publish/<name>-<version>.tar.gz
publish/<name>-<version>.tar.gz.sig
```

**不会更新或签名 `index.json`。**

### 2. 放入仓库

把 tar + sig 放入：

```text
<developer>/
```

### 3. 重新生成索引

使用 hrm：

```bash
./repo-tool/hrm
# 主页 → 管理包 → 重建索引
```

hrm 扫描 `*.tar.gz`、`*.tar.gz.sig`、`<developer>/<developer>.crt`，
生成 schema 2 索引。

### 4. 签索引

官方私钥：

```bash
openssl pkeyutl -sign -inkey official.key -rawin \
  -in index.json -out index.json.sig
```

RSA/ECDSA：

```bash
openssl dgst -sha256 -sign official.key \
  -out index.json.sig index.json
```

### 5. 提交

```bash
git add index.json index.json.sig <developer>/
git commit -m "Update <package>"
git push origin main
```

## 七、证书维护

证书生成、签发和 profile 见 HPM 主仓库的
`docs/pki.md`。

- 根私钥离线保存；
- 官方私钥只用于签主索引和域证书；
- 域私钥用于签域索引和开发者证书；
- 开发者私钥只签自己的包；
- 证书过期前重新签发并更新索引 URL。

## 八、镜像

镜像需要同步：

- `index.json` + `index.json.sig`
- `official.crt`
- 所有开发者目录
- 域仓库根目录的 `official.crt`（由域仓库自己维护）

客户端的 `HPM_OFFICIAL_CERT_URL` 仍指向官方，不从镜像取官方证书。

## 九、故障排查

### `索引签名无效`

- `index.json` 被修改但未重新签；
- 签名私钥不是 `official.crt` 对应私钥；
- 检查：

```bash
openssl x509 -in official.crt -pubkey -noout > /tmp/off.pub
openssl pkeyutl -verify -pubin -inkey /tmp/off.pub -rawin \
  -in index.json -sigfile index.json.sig
```

### `证书链验证失败`

- 官方证书不是内置根签发；
- 域证书不是官方证书签发；
- 开发者证书不是域/官方证书签发；
- SAN 不匹配（`hpm://official`、`hpm://domain/<name>`、
  `hpm://developer/<name>`）。

### `包签名无效`

- tarball 被改；
- `sig` 不是开发者证书对应私钥签的；
- 重新运行 `hpm-dev publish`。

## 十、发布检查清单

- [ ] 开发者证书已放入 `<developer>/<developer>.crt`
- [ ] tar.gz 和 sig 已放入对应目录
- [ ] 在 hrm 中执行「重建索引」
- [ ] 用官方私钥签 `index.json.sig`
- [ ] 验证索引签名
- [ ] 验证包签名
- [ ] `git push`
