# hpm-repo

HPM 官方包仓库。存放芯片驱动、设备驱动和工具链描述文件。

> **当前处于测试状态。**

## 仓库结构

```
hpm-repo/
├── README.md               本文件（仓库根目录唯一文档）
├── docs/                   全部文档
│   ├── repo.md                  仓库维护（索引、信任链、发布流程、镜像、排障）
│   ├── repo-tool.md             hrm 使用说明
│   ├── repo-tool-design.md      hrm 设计与实现
│   └── repo-tool-development.md hrm 开发与扩展指南
├── index.json              仓库索引（schema 2）
├── index.json.sig          索引签名（官方证书私钥签）
├── revoke.json             证书吊销列表（可选，官方私钥签）
├── revoke.json.sig         吊销列表签名
├── official.crt            官方仓库证书（Root CA 签发）
├── repo-tool/              仓库维护工具 hrm
│   ├── hrm                     可执行入口
│   ├── hpmrepo/                工具源码
│   ├── requests/               待处理签名请求（未登录也可提交）
│   ├── tests/                  单元测试与 pty 冒烟
│   └── requirements.txt        依赖
├── <developer>/            每个开发者一个文件夹
│   ├── <developer>.crt         开发者证书（官方/域证书签发）
│   ├── <package>-<version>.tar.gz
│   └── <package>-<version>.tar.gz.sig
└── toolchains/
    └── <toolchain>-<version>.json
```

## 维护工具 hrm

`repo-tool/` 下是仓库维护工具 **hrm（HPM Repo Manager）**，用终端全屏界面完成官方仓库和私域仓库的日常维护，不依赖 Web 后台。

```bash
./repo-tool/hrm                 # 启动（字符画动画 → 主页）
./repo-tool/hrm --no-animation  # 跳过动画
./repo-tool/hrm --no-color      # 纯文本
./repo-tool/hrm --debug         # 显示内部错误堆栈
```

### 安装依赖

需要 Python ≥ 3.10，以及：

```bash
pip3 install -r repo-tool/requirements.txt
```

- `cryptography`：Ed25519 / CSR / X.509 / 签名
- `PyYAML`：仓库包含 `domain.yaml` 时读取域配置

可选安装 `git`（提交与推送）和 `hpm-dev`（登录、打包）。

### 主要功能

| 功能 | 说明 |
|------|------|
| 仓库识别 | 读取根目录 `official.crt`，判定官方仓库 / 私域仓库 / 未初始化 |
| 登录 | 委托 `hpm-dev login`，不接触 AppKey，不写 `dev.json` |
| 生成签名请求 | 未登录、登录页、主页均可使用；生成 domain/developer 请求到 `repo-tool/requests/` |
| 包管理 | 列表与磁盘对账、签名 tar.gz、入库、删除版本、重建/签名索引、发布后校验 |
| 开发者管理 | 查看证书、读取 `requests/` 请求列表、签发开发者证书、导出证书与回执 |
| 私域管理 | 官方仓库主界面的「私域管理」：私域列表、签发证书、登记证书、吊销证书 |
| 吊销证书 | 生成并签名根目录 `revoke.json` |
| 初始化私域仓库 | 建 Git 仓库、生成加密的 official 密钥、生成官方签名请求、配置远程 |
| 仓库校验 | 索引签名、包哈希/签名、证书链、私钥忽略规则 |
| Git | 查看状态、按操作提交、推送（禁用交互式凭据提示，永不 force push） |

### 角色与登录规则

- **官方仓库**：`official.crt` 的 SAN 为 `hpm://official`，且由内置根证书签发。完全免登录，远程地址来自 `git remote -v`。
- **私域仓库**：`official.crt` 的 SAN 为 `hpm://domain/<name>`。Token 来自 `~/.hpm/dev.json`（只读）；无 Token 时主页显示登录与「生成签名请求」。
- **未初始化**：没有 `official.crt`，可先「生成签名请求」提交给官方，有 Token 后也可「初始化私域仓库」。
- Token 由 `hpm-dev login` 写入，默认 1 天有效；过期后重新登录。

### 签名请求与签发

未登录也可以注册：

- 主页 →「生成签名请求」，或登录页按 `g`；
- 选择「私域仓库请求」或「开发者请求」，填写名称、邮箱 / GitHub；
- 生成新私钥（必须设置至少 8 位密码）或使用已有私钥；新密钥自动保存到 `~/.hpm/keys/`；
- 请求写入 `repo-tool/requests/<kind>-<name>.request.json`；
- 把该文件交给官方仓库管理员。

管理员在工具中签发：

- 开发者请求：主页 →「管理开发者」→「添加开发者」；
- 域请求：主页 →「私域管理」→「签发证书」；同页还有「私域列表」「登记证书」「吊销证书」；
- 两个入口直接列出 `repo-tool/requests/` 中的请求，不再要求输入 JSON 路径；
- 请求目录为空时只显示提示；签发后归档到仓库根 `requests/`；
- 域请求带索引/证书 URL，签发成功后自动写入 `domain.yaml` 并重建签名索引；
- 已有证书但未登记时，用「私域管理 → 登记证书」补登记。

### 密钥与密码

私钥只从用户目录 `~/.hpm` 查找，不再从仓库目录读取：

1. `~/.hpm/keys/official.key` — 官方仓库私钥
2. `~/.hpm/keys/domain/<domain>/official.key` — 域私钥
3. `~/.hpm/keys/dev/<developer>/<developer>.key` — 开发者私钥
4. 都没有 → 提示输入路径（默认仍是上述位置）

初始化私域仓库时，`official.key` 保存在 `~/.hpm/keys/domain/<domain>/`，仓库里只保留 `official.pub`。旧布局 `~/.hpm/keys/<name>/...` 仍会作为兼容回退读取。
旧版本放在仓库根或开发者目录的私钥不再被读取，请手动移到上述位置。
新私钥必须设置至少 8 位密码；已有未加密私钥在首次签名时会提示设置密码并加密保存。
支持 PEM（PKCS#8/传统 OpenSSL）与 OpenSSH 私钥格式；加密私钥会弹出密码输入框（最多 3 次）。密码只保存在内存，可在「设置」中清除缓存，自动化场景可用 `HPM_KEY_PASSWORD`。

### 测试

```bash
cd repo-tool
python3 -m pytest -q          # 22 项单元/集成测试
python3 tests/pty_smoke.py    # 12 个 pty 端到端场景
```

### 文档

- [hrm 使用说明](docs/repo-tool.md)
- [hrm 设计与实现](docs/repo-tool-design.md)
- [hrm 开发与扩展指南](docs/repo-tool-development.md)
- [仓库维护文档](docs/repo.md)

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
- `revoke.json` + `revoke.json.sig`（存在时）
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
