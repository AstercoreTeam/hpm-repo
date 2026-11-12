# HPM 证书颁发与吊销机制

本文说明 HPM 的证书体系、签发流程、仓库级吊销列表。


---

## 1. 信任链

```
HPM Root CA（内置在 hpm 客户端，私钥离线）
    │
    ├── 签发 → official.crt（官方仓库证书，SAN hpm://official）
    │              │
    │              ├── 签发 → <domain>/official.crt（域仓库证书，SAN hpm://domain/<name>）
    │              │              │
    │              │              └── 签发 → <developer>/<developer>.crt（SAN hpm://developer/<name>）
    │              │
    │              └── 签发 → <developer>/<developer>.crt（官方仓库直属开发者）
    │
    └── 签名 → ROOT_revoke.json（根吊销列表，仅根私钥可签）
```

| 层级 | 证书 | 签发者 | 用途 |
|------|------|--------|------|
| 根 | Root CA | 自签 | 信任锚，内置客户端 |
| 官方 | `official.crt` | Root CA | 官方仓库索引、域证书 |
| 域 | `<domain>/official.crt` | 官方证书 | 域仓库索引、开发者证书 |
| 开发者 | `<developer>/<developer>.crt` | 官方/域证书 | 签名包 |

---

## 2. 证书档案（profile）

| 类型 | Subject | SAN | 有效期 | 扩展 |
|------|---------|-----|--------|------|
| 开发者 | `CN=HPM Developer <name>, O=HPM` | `URI:hpm://developer/<name>` | 3 年 | `CA:FALSE`、`KU=digitalSignature`、`EKU=codeSigning`、SKI |
| 域 | `CN=HPM Domain <name>, O=HPM` | `URI:hpm://domain/<name>` | 5 年 | `CA:TRUE pathlen:0`、`KU=digitalSignature+certSign`、EKU=codeSigning、SKI |
| 官方 | `CN=HPM Official Registry, O=HPM` | `URI:hpm://official` | 由根决定 | `CA:TRUE pathlen:1`、`KU=digitalSignature+certSign`、EKU=codeSigning、SKI |
| 根 | `CN=HPM Root CA, O=HPM` | 无 | 20 年 | `CA:TRUE`、`KU=certSign` |

- 序列号：16 字节随机正整数，大写十六进制展示；
- 证书算法：默认 Ed25519，读取兼容 RSA/ECDSA；
- 客户端校验 SAN、签发者、有效期和 KeyUsage。

根证书指纹固定在内置客户端中（见 [README](../README.md#证书指纹)），不做首次使用确认。

---

## 3. 签发流程

### 3.1 根证书 → 官方证书

1. 官方仓库生成 official CSR（SAN `hpm://official`）；
2. 离线环境用根证书签发；
3. 把签好的 `official.crt` 放回官方仓库根目录；
4. 用 official 私钥签 `index.json` / `revoke.json`。

### 3.2 官方证书 → 域证书

1. 域管理员通过 hrm「生成签名请求」生成 `*.request.json`（`kind=domain`）；
2. 请求交给官方仓库管理员；
3. 官方管理员在 hrm「私域管理 → 签发证书」中签发；
4. 输出 `outgoing/<domain>.crt` 与回执；
5. 域管理员把证书保存为域仓库根目录的 `official.crt`；
6. 域管理员用该私钥签域索引与域吊销列表。

### 3.3 官方/域证书 → 开发者证书

1. 开发者生成开发者签名请求（`kind=developer`）；
2. 仓库管理员在 hrm「管理开发者 → 添加开发者」中签发；
3. 证书写 `<developer>/<developer>.crt`；
4. 开发者用私钥签名包，生成 `<package>.tar.gz.sig`。

### 3.4 签名请求格式

```json
{
  "schema": 1,
  "kind": "developer",
  "name": "alice",
  "san": "hpm://developer/alice",
  "publicKey": "-----BEGIN PUBLIC KEY-----…",
  "csr": "-----BEGIN CERTIFICATE REQUEST-----…",
  "email": "alice@example.com",
  "githubUrl": "https://github.com/alice",
  "createdAt": "2026-11-07T03:20:00Z",
  "nonce": "base64(16B)",
  "selfSignature": "base64(Ed25519(canonical_json_without_this_field))"
}
```

域请求还可以带 `repoUrl` / `indexUrl` / `certUrl`，用于签发后自动登记 `domain.yaml`。

校验：schema、名称白名单、SAN 严格匹配、CSR 自签名、公钥一致、请求自签名、30 天窗口、nonce 未重复。

---

## 4. 吊销机制总览

HPM 有两个层级的吊销列表：

| 列表 | 文件 | 签名者 | 吊销对象 | 维护方 |
|------|------|--------|----------|--------|
| 仓库级 | `revoke.json` + `revoke.json.sig` | 该仓库的 `official.crt` 私钥 | 域证书、开发者证书 | hrm |
| 根级 | `ROOT_revoke.json` + `ROOT_revoke.json.sig` | HPM Root CA 私钥 | 官方证书 | 官方管理员在 hrm「信任根管理」中操作 |

两级列表分开的原因：

- 官方证书是信任链的第一级，客户端必须先确认官方证书可信，才能信任仓库级 `revoke.json`；
- 根私钥不随仓库保存，hrm 每次操作都提示路径和密码，不缓存；
- 根吊销列表更换频率低，可以独立分发、缓存。

---

## 5. 仓库级 `revoke.json`

### 5.1 位置与签名

- 仓库根目录；
- `revoke.json.sig` 是裸签名（Ed25519 为 64 字节），对 `revoke.json` 原始字节签名；
- 签名私钥必须与仓库根 `official.crt` 配对；
- hrm 每次吊销后立即签名并自检。

### 5.2 格式

```json
{
  "schema": 1,
  "updatedAt": "2026-11-12T13:00:00Z",
  "issuer": {
    "san": "hpm://domain/qincnd",
    "certSha256": "AA:BB:…"
  },
  "entries": [
    {
      "serial": "3F2A9C0D8E7B6A5C4D3E2F1A0B9C8D7E",
      "kind": "developer",
      "name": "alice",
      "san": "hpm://developer/alice",
      "subject": "O=HPM,CN=HPM Developer alice",
      "certSha256": "11:22:…",
      "revokedAt": "2026-11-12T13:00:00Z",
      "reason": "keyCompromise",
      "revokedBy": "admin",
      "comment": ""
    }
  ]
}
```

- 条目按 `serial` 去重，只追加；
- `reason`：`keyCompromise` / `superseded` / `ceased` / `unspecified`；
- 命中依据：序列号 **或** 证书 SHA-256 指纹；
- `updatedAt` 可用 hrm「更新吊销列表」单独刷新，不改变条目。

### 5.3 哪些操作会写 `revoke.json`

- 管理开发者 → 吊销证书；
- 私域管理 → 吊销证书；
- 删除开发者/域时选择「先吊销再删除」；
- 重新签发同名开发者/域时建议吊销旧证书（旧序列号记录保留）。

---

## 6. 根级 `ROOT_revoke.json`

### 6.1 用途

用于吊销官方证书


### 6.2 位置

- 文件名固定：`ROOT_revoke.json`；
- 签名文件：`ROOT_revoke.json.sig`；
- 放在官方仓库根目录，并由官方镜像同步；

### 6.3 格式

```json
{
  "schema": 1,
  "updatedAt": "2026-11-12T13:00:00Z",
  "issuer": {
    "subject": "CN=HPM Root CA, O=HPM",
    "certSha256": "20:4A:E5:10:20:1C:FC:80:29:36:00:72:F0:2C:CE:EE:92:CC:03:16:0C:76:B1:23:EB:66:93:22:F2:9D:67:AB"
  },
  "entries": [
    {
      "serial": "7A1B2C3D4E5F60718293A4B5C6D7E8F9",
      "san": "hpm://official",
      "subject": "O=HPM,CN=HPM Official Registry",
      "certSha256": "34:A8:75:44:20:FB:A0:8D:EE:67:BA:5D:11:25:6C:5C:44:5F:BC:60:92:D4:BE:53:45:55:B9:CC:F3:A9:61:CF",
      "revokedAt": "2026-11-12T13:00:00Z",
      "reason": "keyCompromise",
      "comment": "official key rotated",
      "replacementSha256": "可选：新官方证书指纹"
    }
  ]
}
```

- `entries` 只追加；同一序列号/指纹重复写入视为同一条；
- `reason` 语义与仓库级一致；

### 6.4 签名与验证（根私钥）

根私钥建议离线保存。hrm 的「信任根管理」会在每次操作时重新询问根私钥路径；如果私钥加密，还会询问密码，且不写入密钥缓存。也可以手工在离线环境执行：

```bash
# RSA / ECDSA
openssl dgst -sha256 -sign root.key \
  -out ROOT_revoke.json.sig ROOT_revoke.json

# Ed25519
openssl pkeyutl -sign -inkey root.key -rawin \
  -in ROOT_revoke.json -out ROOT_revoke.json.sig
```

验证：

```bash
openssl x509 -in root.crt -pubkey -noout > /tmp/root.pub
openssl pkeyutl -verify -pubin -inkey /tmp/root.pub -rawin \
  -in ROOT_revoke.json -sigfile ROOT_revoke.json.sig
```

签名前请确认：

- `issuer.certSha256` 等于内置根证书指纹；
- 条目中的 `certSha256` / `serial` 与实际官方证书一致；
- 文件字节没有被再次编辑（必须对最终文件签名）。

---

### 6.5 hrm 的「信任根管理」

仅 HPM 官方管理员 + 官方仓库显示：

```
管理包
管理开发者
私域管理
信任根管理
生成签名请求
更新吊销列表
设置 / 诊断
退出
```

子菜单：

| 操作 | 说明 |
|------|------|
| 签发官方证书 | 读取官方私钥生成 CSR，用根私钥签发 `official.crt`（CA:TRUE pathlen:1），旧证书备份为 `official.crt.bak` |
| 吊销官方证书 | 把当前 `official.crt` 的 serial/指纹写入 `ROOT_revoke.json`，用根私钥签名 |
| 更新 ROOT 吊销列表日期 | 只刷新 `updatedAt`，不改条目，用根私钥重签 |

- 每次操作都重新输入根私钥路径；
- 加密根私钥每次重新输入密码；
- 根私钥不进入 `App` 的密钥缓存，也不会写日志；
- 私钥路径错误/密码错误时操作直接失败，不影响现有文件。

## 7. 客户端校验顺序

建议所有 hpm 客户端按以下顺序执行：

```
1. 用内置 Root CA 验证 official.crt 的签名、SAN、有效期、KeyUsage
2. 拉取并验证 ROOT_revoke.json.sig（根公钥）
3. 如果 official.crt 的 serial / fingerprint 命中根吊销列表
       → 拒绝该官方证书，并拒绝其签发的域证书
4. 拉取并验证仓库级 revoke.json.sig（用该仓库 official.crt）
5. 校验域/开发者证书：SAN、链、有效期
6. 查询仓库级吊销列表（域 + 开发者）
7. 验证包签名与 sha256
```

策略建议：

- `ROOT_revoke.json` 存在但**签名无效** → 拒绝整个官方仓库；
- `ROOT_revoke.json` 不存在 → 兼容旧仓库可继续，但应告警；新客户端建议固定 URL，避免被静默移除；
- 仓库级 `revoke.json` 存在但签名无效 → 拒绝该仓库；
- 仓库级 `revoke.json` 不存在 → 兼容旧仓库，可告警；
- 客户端应缓存吊销列表并定期刷新；离线时使用最近一次有效列表。

---

## 8. 镜像同步

镜像需要额外同步：

```
index.json
index.json.sig
revoke.json
revoke.json.sig
ROOT_revoke.json          # 根负责生成
ROOT_revoke.json.sig
official.crt
<developer>/...
```

- 根吊销列表由根维护者分发，镜像只做原样同步；
- 镜像不能修改 `ROOT_revoke.json`，否则签名失效；
- 域仓库同理同步自己的 `revoke.json` 与 `official.crt`。

---

## 9. 轮换与泄露处置

### 9.1 开发者密钥泄露

1. 管理开发者 → 吊销证书，`reason=keyCompromise`；
2. 重新签发新证书（新序列号）；
3. 开发者重新签名并发布包；
4. 推送 `revoke.json` / `revoke.json.sig` / 索引。

### 9.2 域密钥泄露

1. 私域管理 → 吊销域证书；
2. 官方管理员重新为域签发新证书；
3. 域管理员更新 `official.crt`、重签域索引与域吊销列表；
4. 更新主索引 `domains` 与镜像。

### 9.3 官方密钥泄露

1. 在「信任根管理」中吊销官方证书（或离线生成 `ROOT_revoke.json`），加入旧官方证书的 serial / fingerprint；
2. 用根私钥签名 `ROOT_revoke.json.sig`；
3. 把两个文件分发到官方仓库、镜像和固定 URL；
4. 用根私钥重新签发新的 `official.crt`（新序列号）；
5. 新官方证书签发的 `index.json`、`revoke.json`、域证书重新发布；
6. 客户端更新根吊销列表后，拒绝旧官方证书及其签发的域证书。

### 9.4 根密钥泄露

根吊销列表无法自证安全，必须：

- 发布新的 Root CA；
- 更新 hpm 客户端内置的 `HPM_ROOT_CA_PEM` 和根指纹；
- 重新签发官方证书和根吊销列表；
- 旧客户端需要升级。

---

## 10. 工具职责边界

| 事项 | hrm | 根维护者 |
|------|-----|----------|
| 生成/签名 `revoke.json` | ✅ | — |
| 更新 `revoke.json` 日期 | ✅ | — |
| 签发域/开发者证书 | ✅ | — |
| 生成/签名 `ROOT_revoke.json` | ✅（信任根管理） | ✅（可离线复核） |
| 分发 `ROOT_revoke.json` | ❌ | ✅ |
| 客户端消费两种吊销列表 | ❌（由 hpm 客户端实现） | — |

> hrm 的「信任根管理」会写入和重签 `ROOT_revoke.json`，但普通主页不会因为根吊销列表改变角色判定；根吊销最终由客户端执行。

---

## 11. 当前状态与待办

已实现：

- 仓库级 `revoke.json` 的写入、签名、验签、日期刷新；
- hrm 内部的包/开发者/域吊销判断；
- 官方管理员「信任根管理」：签发/吊销官方证书、刷新根吊销列表；
- 根私钥每次输入路径/密码，不使用密钥缓存。

尚未实现：

- hpm 客户端读取并执行 `ROOT_revoke.json`；
- hpm 客户端读取并执行仓库级 `revoke.json`；
- CRL 编号 / `nextUpdate` / TTL；
- 吊销列表的自动在线刷新策略。
