# hrm — HPM Repo Manager

HPM 仓库维护工具：终端全屏界面，用于管理官方仓库与私域仓库。

相关文档：[仓库 README](../README.md) · [仓库维护](repo.md) · [设计与实现](repo-tool-design.md) · [开发与扩展](repo-tool-development.md)

## 运行要求

- Python ≥ 3.10（推荐 3.11+）
- `cryptography`（X.509 / Ed25519）
- `PyYAML`（仅在仓库有 `domain.yaml` 时需要）
- 可选：`git`、`hpm-dev`（登录与打包）

```bash
pip3 install -r repo-tool/requirements.txt
chmod +x repo-tool/hrm
./repo-tool/hrm
```

仓库根目录 = `repo-tool/` 的上一级目录。私域仓库把整个 `repo-tool/` 目录放在自己根目录下运行即可。

## 启动

```bash
./repo-tool/hrm                 # 启动动画 + 主页
./repo-tool/hrm --no-animation  # 跳过字符画
./repo-tool/hrm --no-color      # 纯文本（NO_COLOR）
./repo-tool/hrm --debug         # 显示内部错误堆栈
./repo-tool/hrm --version
```

非交互终端（stdin 不是 TTY）会直接退出并返回码 2。

## 主页与门禁

启动时读取：

- 仓库根 `official.crt` → 判定角色
  - `hpm://official` 且由内置根证书签发 → **官方仓库**（完全免登录）
  - `hpm://domain/<name>` → **私域仓库**
  - 无证书 → **未初始化**
- `~/.hpm/dev.json`（或 `$HPM_HOME/dev.json`，只读）→ Token 状态

| 角色 | 无 Token / 过期 | Token 有效 |
|------|-----------------|------------|
| 官方仓库 | 正常显示管理功能 | 正常显示 |
| 私域仓库 | 登录、生成签名请求、退出 | 登录、生成签名请求、管理包、管理开发者、设置、退出 |
| 未初始化 | 登录、生成签名请求、退出 | 初始化私域仓库、生成签名请求、设置、退出 |

官方仓库主页额外提供「私域管理」：私域列表、签发证书、吊销证书；域证书签发不再放在「管理开发者」子菜单。

登录委托给 `hpm-dev login`（也兼容 `hpm login`，可用 `HPM_DEV_BIN` 指定）。本工具不实现 OAuth、不接触 AppKey、不写 `dev.json`。

## 功能

### 管理包

- 包列表：索引与磁盘对账，标出缺失、哈希不符、签名无效、证书已吊销
- 发布 / 签名包：选择 `publish/*.tar.gz` → 开发者私钥签名 → 入库 → 重建索引 → 签名索引 → 校验 → 可选 Git 提交/推送
- 重建索引、签名索引、签名吊销列表
- 校验仓库：索引签名、包完整性、证书链、私钥忽略规则
- Git 同步：查看状态、提交指定变更、推送（永不 force push）

### 生成签名请求（无需登录）

主页 →「生成签名请求」，登录页也可以按 `g`：

- 「私域仓库请求」：申请 `hpm://domain/<name>`；
- 「开发者请求」：申请 `hpm://developer/<name>`；
- 可生成新的 Ed25519 私钥（必须设置至少 8 位密码，自动保存到 `~/.hpm/keys/`）或使用已有私钥；
- 请求写入 `repo-tool/requests/<kind>-<name>.request.json`（同目录还有 `.txt` 人读版本）；
- 私钥不会写入请求文件；生成时不再询问保存路径。

请求文件可以发给官方仓库管理员，也可以直接放到管理员的 `repo-tool/requests/`。

### 管理开发者

- 开发者列表与证书详情
- 添加开发者：直接显示 `repo-tool/requests/*.request.json`（兼容旧 `incoming/`）的请求列表；目录为空时只提示，不再要求输入 JSON 路径 → 校验 CSR 自签名、请求自签名、SAN、nonce 防重放 → 用 official 私钥签发 → 写 `<developer>/<developer>.crt` → 导出 `outgoing/` 回执 → 重建并签名索引
- 吊销证书：写根目录 `revoke.json` 并签名
- 删除开发者：先选择是否吊销，然后删除 `<developer>/` 目录并重建/签名索引；本地私钥保留在 `~/.hpm/keys/dev/`

### 私域管理（官方仓库）

主页主菜单的「私域管理」包含：

- **私域列表**：读取 `domain.yaml` 与 `outgoing/*.crt`（只收域证书，开发者证书不会混入），显示域名、状态、序列号、剩余天数、本地证书；Enter 查看索引 URL、证书 URL、指纹等详情。
- **签发证书**：直接显示 `repo-tool/requests/` 中的 domain 请求列表；没有请求时只提示；签发后写 `outgoing/<domain>.crt` 与回执；请求带 `indexUrl`/`certUrl` 时自动登记到 `domain.yaml` 并重建签名索引。
- **登记证书**：用于补登记“已签发但未登记”的域证书，手动填写索引 URL 与证书 URL。
- **吊销证书**：列出 `outgoing/` 中的域证书，选择原因后写入 `revoke.json` 并重新签名。
- **删除域**：删除 `domain.yaml` 条目与 `outgoing/` 中的证书/回执，重建索引；可先吊销再删除。

「管理开发者」只处理开发者证书，不再包含域证书签发。

### 初始化私域仓库

未初始化且 Token 有效时可用：

1. 生成 `.gitignore`、`README.md`、空 `index.json`、空 `revoke.json`
2. 生成加密的 Ed25519 `official.key`（必须设置至少 8 位密码），保存在 `~/.hpm/keys/domain/<domain>/official.key`；仓库里只写 `official.pub`
3. 生成 `official.request.json` / `official.request.txt`（SAN `hpm://domain/<name>`），并复制到 `repo-tool/requests/`
4. `git init -b main`，首次提交，可配置远程
5. 收到官方签发的 `official.crt` 后，主页会出现「完成初始化」，用 `~/.hpm` 中的 official 私钥签名 `index.json` 与 `revoke.json`

如果之前用「生成签名请求」创建过该域的 `official.key`，初始化时会提示复用，避免密钥与请求不匹配。

## 更新吊销列表

主页主菜单提供「更新吊销列表」：只刷新 `revoke.json` 的 `updatedAt` 并重新签名，
不修改任何吊销条目，也不重建索引。适合让客户端重新拉取/刷新吊销列表。

## 密钥与密码

私钥只从用户目录 `~/.hpm` 查找，不再读取仓库目录：

1. `~/.hpm/keys/official.key` — 官方仓库
2. `~/.hpm/keys/domain/<domain>/official.key` — 域私钥
3. `~/.hpm/keys/dev/<developer>/<developer>.key` — 开发者私钥
4. 还会搜索 `~/.hpm/keys/domain/*/official.key`、`~/.hpm/keys/dev/*/*.key`，以及旧布局 `~/.hpm/keys/*/official.key`、`~/.hpm/keys/*/<name>.key`；
5. 都没有 → 要求输入路径（默认仍是上述位置）

旧版本放在仓库根 `official.key` 或 `<developer>/<developer>.key` 的私钥不再读取，请移到
`~/.hpm/keys/domain/<domain>/` 或 `~/.hpm/keys/dev/<developer>/` 下。旧目录布局仍可兼容读取。

如果同时存在多个 official 私钥，工具会优先选择与 `official.crt` 公钥匹配的那一个
（通过同名 `.pub`）；支持 PEM（PKCS#8/传统 OpenSSL）与 `BEGIN OPENSSH PRIVATE KEY`
格式，加密判断兼容 `Proc-Type: 4,ENCRYPTED` 与 OpenSSH bcrypt 加密。

私钥如果未加密，首次使用时会询问是否设置密码并加密保存；加密私钥会弹出密码输入框（最多 3 次）。密码只保存在内存，可在「设置 → 清除密钥缓存」清除；自动化场景可设置 `HPM_KEY_PASSWORD` 环境变量。

## 签名请求格式

通常由「生成签名请求」自动生成；格式如下：

```json
{
  "schema": 1,
  "kind": "developer",              // developer | domain
  "name": "alice",
  "san": "hpm://developer/alice",
  "publicKey": "-----BEGIN PUBLIC KEY-----…",
  "csr": "-----BEGIN CERTIFICATE REQUEST-----…",
  "email": "alice@example.com",
  "githubUrl": "https://github.com/alice",
  "createdAt": "2026-11-07T03:20:00Z",
  "nonce": "base64(16B)",
  "selfSignature": "base64(sign(canonical_json(payload)))",

  // domain 请求可选，用于签发后自动登记
  "repoUrl": "https://github.com/alice/hpm-repo",
  "indexUrl": "https://raw.githubusercontent.com/alice/hpm-repo/main/index.json",
  "certUrl": "https://raw.githubusercontent.com/alice/hpm-repo/main/official.crt"
}
```

校验：schema、名称白名单、SAN 严格匹配、CSR 自签名、公钥一致、请求自签名、时间窗口（30 天）、nonce 未重复。

## 快捷键

- `↑↓` / `j k` 移动，`Enter` 确认，`Esc` 返回
- `r` 刷新，`?` 帮助，`q` 主页退出
- 输入框：`←→`、`Home/End`、`Backspace/Delete`、`Ctrl-U`、`Ctrl-W`
- 弹窗默认焦点在「取消」

## 测试

```bash
cd repo-tool
python3 -m pytest -q          # 单元/集成测试
python3 tests/pty_smoke.py    # pty 端到端冒烟
```

## 退出码

| 码 | 含义 |
|----|------|
| 0 | 正常退出 |
| 1 | 内部错误（见 `~/.hpm/logs/repo-tool.log`） |
| 2 | 非交互终端 |
| 130 | Ctrl-C 中断 |

## 安全

- 私钥不入库：`.gitignore` 排除 `*.key`，提交前检查未忽略的私钥
- 密码不落盘、不进日志、不进命令行参数
- 索引、`revoke.json`、包签名在发布前强制校验
- 吊销列表缺失或签名无效时，客户端应拒绝该仓库（需 hpm 客户端配合，见设计文档）
