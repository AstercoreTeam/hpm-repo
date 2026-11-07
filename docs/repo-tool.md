# hrm — HPM Repo Manager

HPM 仓库维护工具：终端全屏界面，用于管理官方仓库与私域仓库。

相关文档：[仓库 README](../README.md) · [仓库维护](repo.md) · [设计与实现](repo-tool-design.md)

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
| 私域仓库 | 只显示登录与退出 | 登录、管理包、管理开发者、设置、退出 |
| 未初始化 | 只显示登录与退出 | 初始化私域仓库、设置、退出 |

登录委托给 `hpm-dev login`（也兼容 `hpm login`，可用 `HPM_DEV_BIN` 指定）。本工具不实现 OAuth、不接触 AppKey、不写 `dev.json`。

## 功能

### 管理包

- 包列表：索引与磁盘对账，标出缺失、哈希不符、签名无效、证书已吊销
- 发布 / 签名包：选择 `publish/*.tar.gz` → 开发者私钥签名 → 入库 → 重建索引 → 签名索引 → 校验 → 可选 Git 提交/推送
- 重建索引、签名索引、签名吊销列表
- 校验仓库：索引签名、包完整性、证书链、私钥忽略规则
- Git 同步：查看状态、提交指定变更、推送（永不 force push）

### 管理开发者

- 开发者列表与证书详情
- 添加开发者：读取 `incoming/*.request.json` → 校验 CSR 自签名、请求自签名、SAN、nonce 防重放 → 用 official 私钥签发 → 写 `<developer>/<developer>.crt` → 导出 `outgoing/` 回执 → 重建并签名索引
- 签发域证书（仅官方仓库）：写 `outgoing/<domain>.crt`
- 吊销证书：写根目录 `revoke.json` 并签名

### 初始化私域仓库

未初始化且 Token 有效时可用：

1. 生成 `.gitignore`、`README.md`、空 `index.json`、空 `revoke.json`
2. 生成加密的 Ed25519 `official.key` + `official.pub`
3. 生成 `official.request.json` / `official.request.txt`（SAN `hpm://domain/<name>`）
4. `git init -b main`，首次提交，可配置远程
5. 收到官方签发的 `official.crt` 后，主页会出现「完成初始化」，用 official 私钥签名 `index.json` 与 `revoke.json`

## 密钥与密码

签名时按以下顺序查找私钥：

1. 仓库根 `official.key`
2. `<developer>/<developer>.key`
3. `~/.hpm/keys/<developer>/<developer>.key`
4. 都没有 → 要求输入路径

私钥如果加密，会弹出密码输入框（最多 3 次）。密码只保存在内存，可在「设置 → 清除密钥缓存」清除；自动化场景可设置 `HPM_KEY_PASSWORD` 环境变量。

## 签名请求格式

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
  "selfSignature": "base64(sign(canonical_json(payload)))"
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
