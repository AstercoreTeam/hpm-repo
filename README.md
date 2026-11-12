# hpm-repo

HPM 官方包仓库。存放芯片驱动、设备驱动和工具链描述文件。

> **当前处于测试状态。**

## 仓库结构

```
hpm-repo/
├── README.md               本文件（仓库根目录唯一文档）
├── docs/                   全部文档
│   ├── repo.md                  仓库维护（索引、信任链、发布流程、镜像、排障）
│   ├── certificates.md          证书颁发与吊销机制（含 ROOT_revoke.json）
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
| 登录 | 仅私域管理员需要；委托 `hpm-dev login`，不写 `dev.json` |
| 生成签名请求 | 未登录、登录页、主页均可使用；生成 domain/developer 请求到 `repo-tool/requests/` |
| 包管理 | 未索引包可见；普通开发者可仅签名入库；管理员在详情页加入索引；删除版本；重建/签名索引 |
| 开发者管理 | 查看证书、读取 `requests/` 请求列表、签发/吊销/删除开发者、导出证书与回执 |
| 私域管理 | 官方仓库主界面的「私域管理」：私域列表、签发证书、登记证书、吊销/删除域 |
| 信任根管理 | 官方仓库主界面：签发官方证书、吊销官方证书、更新 ROOT 吊销列表日期；每次操作重新输入根私钥路径，不缓存 |
| 更新吊销列表 | 主页主菜单：仅刷新 `revoke.json` 的 `updatedAt` 并重签，不改条目 |
| 吊销证书 | 生成并签名根目录 `revoke.json` |
| 初始化私域仓库 | 建 Git 仓库、生成加密的 official 密钥、生成官方签名请求、配置远程 |
| 仓库校验 | 索引签名、包哈希/签名、证书链、私钥忽略规则 |
| Git | 查看状态、按操作提交、推送（禁用交互式凭据提示，永不 force push） |

### 身份与权限

身份**只检测私钥文件是否存在，不验证私钥内容**；即使绕过界面，没有正确私钥也无法完成签名。

| 身份 | 检测依据 | 登录开发者中心 | 可用功能 |
|------|----------|----------------|----------|
| 未注册 | 没有可用私钥 | 否 | 生成签名请求（申请私域/开发者证书） |
| 普通开发者 | `~/.hpm/keys/dev/<name>/<name>.key` | 否 | 发布包（仅签名入库），不改索引 |
| 私域管理员 | `~/.hpm/keys/domain/<domain>/official.key` | **是** | 管理包、管理开发者、签名索引、更新/吊销列表、维护仓库；同步数据到开发者中心 |
| HPM 官方管理员 | `~/.hpm/keys/official.key` | 否 | 私域管理员的全部仓库功能 + 管理私域 + 信任根管理（需根私钥路径） |

- 优先级：官方管理员 > 私域管理员 > 普通开发者 > 未注册。
- 官方仓库只认 `keys/official.key`；域私钥不会获得官方仓库管理权。
- 私域管理员未登录时主页只显示登录与生成签名请求；Token 来自 `~/.hpm/dev.json`，默认 1 天有效。
- 官方管理员无需登录，也不参与开发者中心同步。
- 普通开发者发布包时只复制 tar.gz/.sig 到 `<developer>/`，由管理员在包详情页加入索引。

### 开发者中心同步（仅私域管理员）

私域管理员登录后，每次增改开发者或包会同步一次：

- 新增/重签开发者：`update_developer`（`<name>.pub`、`<name>.pub.sig`）；
- 更新包：`update_package`（URL、签名 URL、sha256、size、license、requires）；
- 删除开发者：`delete_developer`；
- 删除包：`delete_package`（开发者中心按包名删除全部版本；本地仍保留其他版本时不远程删除）；
- 目标在开发者中心不存在（404）按已删除处理；
- 同步失败不会影响本地操作，会以提示形式显示原因；
- 可用 `HPM_DISABLE_CENTER_SYNC=1` 临时关闭同步。

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
- 已有证书但未登记时，用「私域管理 → 登记证书」补登记；
- 删除开发者/域时会移除对应目录、`outgoing` 文件与索引，可在删除前选择是否同时吊销证书。
- 普通开发者：管理包 →「发布包（仅签名入库）」，只复制并签名，不改索引；`publish/` 或 `repo-tool/publish/` 有文件时列出全部包，确认后批量发布，并从 publish 删除源文件；都为空才要求输入路径；
- 仓库密钥持有者：包列表会显示「未加入索引」的包，Enter 进详情后按 `a` 加入索引。

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
python3 -m pytest -q          # 33 项单元/集成测试
python3 tests/pty_smoke.py    # 17 个 pty 端到端场景
```

### 文档

- [证书颁发与吊销机制](docs/certificates.md)
- [hrm 使用说明](docs/repo-tool.md)
- [hrm 设计与实现](docs/repo-tool-design.md)
- [hrm 开发与扩展指南](docs/repo-tool-development.md)
- [仓库维护文档](docs/repo.md)

## 信任链

```
HPM Root CA（内置在 hpm 客户端）
    ├─ 签发 → official.crt（官方仓库证书）
    │            ├─ 验签 → index.json.sig → index.json
    │            ├─ 验签 → revoke.json.sig → revoke.json
    │            └─ 签发 → <domain>/official.crt（域仓库证书）
    │                        ├─ 验签 → 域 index.json.sig
    │                        └─ 签发 → developer.crt
    │                                    └─ 验签 → <package>.tar.gz.sig
    └─ 签发 → ROOT_revoke.json.sig → ROOT_revoke.json
                  （先检查 official.crt 是否被根吊销）
```

## 镜像

镜像需要同步：

- `index.json` + `index.json.sig`
- `revoke.json` + `revoke.json.sig`（存在时）
- `ROOT_revoke.json` + `ROOT_revoke.json.sig`（根维护者生成，原样同步）
- `official.crt`
- 所有 `<developer>/` 目录
- 域仓库根目录的 `official.crt`（由域仓库自己维护）

旧客户端使用 `official.pub` / `*.pub.sig` 的镜像不再兼容。

## 证书与吊销

完整的证书层次、签发流程、仓库级 `revoke.json` 与根级 `ROOT_revoke.json` 规范见
[证书颁发与吊销机制](docs/certificates.md)。

- `revoke.json` 由 hrm 管理，用仓库 official 私钥签名；
- `ROOT_revoke.json` 由 HPM Root CA 签名，用于吊销官方证书；
- 官方管理员可在「信任根管理」中签发/吊销官方证书、刷新根吊销列表；
- 根私钥每次操作重新输入路径和密码，不进入密钥缓存；
- 两个文件都需要镜像同步。

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
