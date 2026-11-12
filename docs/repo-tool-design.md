# hpm-repo 维护工具设计

> 状态：v0.1 已实现（`repo-tool/hrm`）
> 目标读者：仓库维护者、HPM 客户端维护者
> 关联文档：[README](../README.md)、[仓库维护](repo.md)、[hrm 使用](repo-tool.md)、[开发与扩展](repo-tool-development.md)、`hpm_dev_center.md`

---

## 1. 目标与边界

### 1.1 目标

一个放在仓库 `repo-tool/` 下运行的 Python 程序（入口 `hrm`），用终端全屏界面（CLI 模拟 GUI）完成 HPM 仓库的全部日常维护：

- 启动即检查 Token 与仓库证书，据此决定主页可见功能；
- 展示当前开发者、私域仓库、Token、证书、密钥、Git 状态；
- 登录 HPM 开发者中心：**委托给 `hpm-dev login`**，本工具不实现 OAuth、不接触 AppKey；
- 按 `official.crt` 判定当前目录是「官方仓库」「私域仓库」还是「未初始化」；
- 初始化私域仓库：建 Git 仓库、生成 official Ed25519 密钥对（带密码）、生成官方签名请求、配置远程地址；
- 管理开发者：查看、签发开发者证书（消费签名请求）、吊销；
- 管理包：查看、签名、入库、删版本、重建索引、签索引；
- 维护根目录 `revoke.json`（证书吊销列表）及其签名；
- 所有需要私钥的操作，自动定位密钥；找不到就让用户输入路径；密钥有密码就弹密码输入。

### 1.2 非目标（本期不做）

- 不做新的编译/打包器：tar.gz 由 `hpm-dev publish` 或构建流水线产出，本工具负责接收、签名、入库；
- 不做 PKI 根私钥管理（根私钥离线，本工具不碰）；
- 不替代 `hpm` 客户端（安装/构建/烧录）；
- 不做 Web 服务端：开发者中心仍由现有后端提供；
- 不实现证书链的客户端校验逻辑，只按既定规则生成数据（客户端侧需同步支持 `revoke.json`，见 §9.4）。

### 1.3 关键约束

- TUI 与 Git 调用只依赖 Python 标准库；
- X.509 使用 `cryptography`（v0.1 的硬依赖，`repo-tool/requirements.txt` 已声明）；
- 索引格式保持 schema 2 不变；原 `gen_index.py` 的扫描规则已并入 `core/index.py`，仓库不再保留该脚本；
- `~/.hpm/dev.json` 是用户级配置，与具体仓库无关；本工具**只读**，唯一写者是 `hpm-dev login`；
- 私钥永不入库（`.gitignore`），永不打印、永不写日志。

---

## 2. 总体架构

```
┌──────────────────────────────────────────────────────┐
│                    views（界面/流程）                │
│  splash  home  login  packages  developers  init     │
│  certs   revoke  settings                             │
├──────────────────────────────────────────────────────┤
│            app（状态机、路由、导航栈、任务）          │
│   AppState: dev.json 数据 / 仓库角色 / 会话密码缓存     │
├──────────────────────────────────────────────────────┤
│ tui（终端抽象）        │ widgets（可复用控件）        │
│ terminal/screen/keys   │ menu/table/input/modal/toast │
├──────────────────────────────────────────────────────┤
│                    core（业务）                      │
│ config  repo  auth  index  packages  developers      │
│ revoke  crypto  gitops  verify                       │
├──────────────────────────────────────────────────────┤
│                    util                              │
│ shell  fs(atomic/perm)  json(canonical)  log  errors │
└──────────────────────────────────────────────────────┘
```

- **单向依赖**：views → app → core → util；core 不 import tui，保证可单测、可无头运行。
- **无头模式**：core 可独立导入用于脚本/测试；当前入口只提供 TUI，非 TTY 时返回码 2。
- **长任务不阻塞界面**：Git push、子进程登录、索引重建放到工作线程，界面显示 spinner + 可取消；结果通过队列回到 UI 线程。登录子进程特殊：必须独占终端，见 §6.2。

---

## 3. 目录与入口

工具放在 `repo-tool/` 子目录，索引扫描时会忽略该目录。

```
hpm-repo/
├── README.md                     # 根目录唯一文档
├── docs/                         # 全部文档
│   ├── repo.md
│   ├── repo-tool.md
│   ├── repo-tool-design.md
│   └── repo-tool-development.md
├── repo-tool/
│   ├── hrm                       # 可执行入口（#!/usr/bin/env python3）
│   ├── requirements.txt
│   ├── hpmrepo/
│   │   ├── app.py                # 主循环、导航栈、弹窗、子进程挂起
│   │   ├── config.py             # dev.json 只读加载、Token 状态
│   │   ├── repo.py               # 仓库根定位、角色判定
│   │   ├── tui/
│   │   │   ├── terminal.py       # 原始模式、备用屏、按键解码
│   │   │   ├── screen.py         # 双缓冲画布、ANSI 差分刷新
│   │   │   ├── theme.py          # 颜色，NO_COLOR 降级
│   │   │   └── widgets.py        # 菜单/表格/弹窗/页脚
│   │   ├── views/
│   │   │   ├── base.py  splash.py  home.py  login.py
│   │   │   ├── packages.py  developers.py  domains.py
│   │   │   ├── request.py
│   │   │   ├── init_repo.py  settings.py  help.py  common.py
│   │   ├── core/
│   │   │   ├── crypto.py         # 密钥/CSR/证书/签名
│   │   │   ├── index.py          # 索引构建/签名/校验
│   │   │   ├── packages.py       # 包扫描/签名/入库/删除
│   │   │   ├── developers.py     # 请求校验、证书签发
│   │   │   ├── revoke.py         # revoke.json
│   │   │   ├── domains.py        # domain.yaml / outgoing 域列表
│   │   │   ├── identity.py       # 四类身份（按私钥文件）
│   │   │   ├── center.py         # 开发者中心同步 API
│   │   │   ├── keys.py  auth.py  gitops.py  verify.py
│   │   └── util/
│   │       ├── shell.py  fs.py  jsonc.py  log.py  errors.py
│   └── tests/
│       ├── conftest.py  test_crypto.py  test_index.py  test_init.py
│       └── pty_smoke.py          # pty 端到端冒烟（非 pytest）
└── index.json / official.crt / <developer>/ ...
```

入口参数：

```
./repo-tool/hrm [--no-animation] [--no-color] [--debug] [--version]
```

**仓库根 = `repo-tool` 的上一级目录**，不做目录选择；`HPM_REPO_ROOT` 仅作为测试注入点。私域仓库把整个 `repo-tool/` 目录放在自己根目录下运行，因此初始化检查必须容忍工具自身文件已存在。

**索引脚本的归属**：

1. 原 `gen_index.py` 已删除，索引构建只在 `core/index.py` 的 `build_index()` 中实现；
2. 扫描时忽略 `repo-tool`、`incoming`、`requests`、`outgoing`、`docs` 等目录；
3. 已用当前仓库验证 `build_index()` 输出与历史 `index.json` 完全一致。

---

## 4. 终端界面设计

### 4.1 渲染基座

- 进入备用屏（`\x1b[?1049h`）、隐藏光标、`cbreak`/raw 模式，退出时完整恢复（`atexit` + 信号处理）。
- 自绘双缓冲：记录每个单元格字符与样式，只把变化的片段写到 stdout，避免整屏闪烁。
- 尺寸：最小 80×24；窗口变化（`SIGWINCH`）时重排；过窄时折叠表格列。
- 颜色：256 色；`NO_COLOR`/`--no-color` 时退化为纯文本。
- stdin 不是 TTY 时直接退出（返回码 2），避免在管道里挂起。

### 4.2 启动字符画

- 内容：`hpm-repo` 的 FIGlet 风格字符画（预置多套字模，按终端宽度选）。
- 动效：按列从左到右逐帧显示（每帧 1~2 列，约 25 fps，总时长 500~700ms），底部显示版本与「按任意键跳过」。
- 跳过：任意键立即完成；`--no-animation` 或 `NO_COLOR` 直接显示完整 logo 后进入主页。
- 收尾：logo 上移淡出，进入主页布局；动画期间不读取仓库（避免等待感）。

### 4.3 主页布局

顶栏随仓库角色变化：官方仓库不显示登录/私域仓库，改显示 Git 远程；私域仓库显示开发者中心信息。

**官方仓库（无需 Token）**

```
┌──────────────────────────────────────────────────────────────────────┐
│ 证书: HPM Official Registry (hpm://official)   有效期: 1764 天       │
│ 本地: /run/media/xzc/Data/hpm-repo   角色: 官方仓库                  │
│ 远程: github git@github.com:xzc842/hpm-repo.git                      │
│ 索引: 签名有效   包: 7   开发者: 1   Git: 干净 ↑0 ↓0                 │
├──────────────────────────────────────────────────────────────────────┤
│    ▸ 管理包                                                          │
│      管理开发者                                                      │
│      私域管理                                                        │
│      生成签名请求                                                    │
│      更新吊销列表                                                    │
│      设置 / 诊断                                                     │
│      退出                                                            │
├──────────────────────────────────────────────────────────────────────┤
│ ↑↓ 移动   Enter 确认   Esc 返回   r 刷新   ? 帮助                    │
└──────────────────────────────────────────────────────────────────────┘
```

**私域仓库（需要 Token）**

```
┌──────────────────────────────────────────────────────────────────────┐
│ 开发者: xzc        GitHub: github.com/xzc842                         │
│ 私域仓库: https://raw.githubusercontent.com/xzc842/hpm-repo/main     │
│ 本地: /run/media/xzc/Data/hpm-repo   角色: 私域仓库 (qincnd)         │
│ Token: 有效 (剩 23h)   证书: 有效(1824天)   Git: 干净 ↑0 ↓0          │
├──────────────────────────────────────────────────────────────────────┤
│    ▸ 登录开发者中心（重新登录 / 切换账号）                            │
│      生成签名请求                                                    │
│      管理包                                                          │
│      管理开发者                                                      │
│      设置 / 诊断                                                     │
│      退出                                                            │
└──────────────────────────────────────────────────────────────────────┘
```

**无 Token 且需要登录时**（私域/未初始化仓库）：仍保留「生成签名请求」，管理员之外的
用户无需登录即可提交请求：

```
│    ▸ 登录开发者中心                                                  │
│      生成签名请求                                                    │
│      退出                                                            │
```

顶栏数据来源：

| 字段 | 来源 |
|------|------|
| 开发者 / GitHub | `dev.json.developer` / `dev.json.githubUrl`（私域仓库显示） |
| 私域仓库 URL | `dev.json.repoUrl`（由 `hpm-dev login` 写入；官方仓库忽略） |
| 本地路径 | 程序所在目录 |
| 角色 | 仓库根 `official.crt` 的 SAN 与签发者（见 §5.3.1） |
| 证书 | 解析 `official.crt` 的 SAN/有效期/签发者 |
| Token | `dev.json.accessToken` + `expiresAt`，过期标红 |
| Git | `git status --porcelain` 计数、`rev-list --left-right --count` |
| 远程 | `git remote -v`（官方仓库用它代替私域仓库 URL） |

数据缺失时显示 `未登录` / `未初始化`；官方仓库不要求登录，菜单里不出现登录项，顶栏直接省略 Token 行。私域仓库即使 Token 有效也保留登录项，用于重新登录或切换账号。

### 4.4 导航与按键

- 菜单：`↑/↓` 或 `j/k` 移动，`Enter` 确认，`Esc` 返回上一级，`q` 仅在主页退出。
- 二级菜单末尾固定 `← 返回` 项，与 `Esc` 等价。
- 列表/表格：`PgUp/PgDn`、`Home/End`，`/` 过滤，`r` 刷新。
- 输入框：行编辑（左右、Home/End、Backspace/Delete、Ctrl-U/Ctrl-W），`Esc` 取消，`Enter` 提交。
- 密码框：掩码显示，无回显；支持从环境变量/文件读取（见 §7.4）。
- 弹窗：选择列表纵向排列（↑↓ 移动）；确认框默认焦点在「取消」；危险操作需二次确认。
- `?`：帮助页列出所有快捷键；底部状态栏始终显示当前可用键。

### 4.5 界面清单

| 编号 | 视图 | 说明 |
|------|------|------|
| S0 | Splash | 字符画动画 |
| S1 | Home | 按角色/Token 动态显示菜单 |
| S2 | Login | 说明 + 执行 `hpm-dev login`（暂停 TUI，原生命令接管） |
| S3 | Packages | 包列表（名称/版本数/kind/chip/开发者/状态），二级菜单 |
| S4 | PackageDetail | 某包各版本的 sha256、size、签名状态、URL、删除入口 |
| S5 | PublishWizard | 选择 tar/sig → 开发者密钥 → 签名 → 入库 → 重建索引 → 签索引 → 提交推送 |
| S6 | Developers | 开发者列表（证书 SAN/有效期/吊销状态/包数） |
| S7 | AddDeveloper | 选择签名请求 → 校验 → 预览 → 输入 official 密钥/密码 → 签发 → 放置证书 |
| S8 | CertDetail | 证书主题、序列号、指纹、有效期、SAN、签发者 |
| S9 | RevokeWizard | 选择证书/手填序列号 → 原因 → 确认 → 写 revoke.json + 签名 |
| S10 | InitRepo | 多步向导：域信息 → 密钥密码 → 远程地址 → 生成请求 |
| S11 | Settings | 全部路径、密钥缓存策略、重新登录、诊断（依赖/签名/证书链） |
| S12 | Help | 快捷键与流程说明 |

每个界面都实现三个回调：`render()`、`handle_key()`、`refresh()`；异步动作通过 `task(name, fn)` 注册。

---

## 5. 数据模型与文件格式

### 5.1 `~/.hpm/dev.json`（只读）

与 `hpm-dev` 共用，路径规则与 `dev/src/dev_config.cpp` 一致：

- `HPM_HOME` 存在 → `$HPM_HOME/dev.json`
- 否则 → `$HOME/.hpm/dev.json`（Windows 用 `%USERPROFILE%\.hpm\dev.json`）

```json
{
  "accessToken": "…",
  "developer": "xzc",
  "expiresAt": "2026-11-02T14:38:47Z",
  "githubUrl": "https://github.com/xzc842",
  "repoUrl": "https://raw.githubusercontent.com/AstercoreTeam/hpm-repo-test/main"
}
```

本工具**只读取，不写入**；登录、续期、修改仓库地址统一由 `hpm-dev login` 完成。因此不存在字段扩展，也不需要担心与客户端互相覆盖。文件缺失、损坏、字段缺失都只影响顶栏显示与主页菜单，不阻塞官方仓库的维护操作。

### 5.2 Token 状态（登录门禁依据）

```python
def token_state(cfg) -> Literal["missing", "expired", "valid"]:
    if not cfg or not cfg.accessToken:
        return "missing"
    if not cfg.expiresAt or parse_iso(cfg.expiresAt) <= now_utc():
        return "expired"
    return "valid"
```

| 状态 | 顶栏 | 主页行为 |
|------|------|----------|
| `missing` | `Token: 未登录` | 私域/未初始化仓库：登录、生成签名请求、退出；官方仓库：不显示 Token 行 |
| `expired` | `Token: 已过期 (日期)` | 同上，登录项文案改为「重新登录」 |
| `valid` | `Token: 有效 (剩 n 小时)` | 显示完整菜单 |

只做本地判断，不联网校验，保证离线可用；`accessToken` 的真实有效性由 `hpm-dev` 调用 API 时报错。

登录命令探测顺序：

1. `HPM_DEV_BIN` 环境变量指定；
2. `hpm-dev login`（当前 `dev/` 子工程提供的命令，功能最全）；
3. `hpm login`（若发行版把 dev 子命令合并进主程序）。

### 5.3 仓库布局与角色

```
<repo>/                                     # = 本程序所在目录
├── repo-tool/                              # 工具自身（私域仓库也一起提交）
├── index.json / index.json.sig
├── revoke.json / revoke.json.sig           # 新增，见 §8
├── official.crt                            # 角色判定的唯一依据
├── domain.yaml                             # 仅官方仓库维护域列表
├── <developer>/
│   ├── <developer>.crt
│   └── <pkg>-<chip>-<ver>.tar.gz(+.sig)
├── requests/                               # 已处理请求归档
└── publish/                                # hpm-dev 产物，gitignore

~/.hpm/keys/
├── official.key                            # 官方仓库私钥（0600）
├── domain/<domain>/official.key            # 域私钥（0600）
└── dev/<developer>/<developer>.key         # 开发者私钥（0600）

repo-tool/requests/                         # 待处理签名请求（未登录可提交）
├── domain-<name>.request.json
└── developer-<name>.request.json
```

#### 5.3.1 角色判定（证书）

判定输入只有仓库根的 `official.crt`（不存在则未初始化），**不看 `dev.json`、不看 `domain.yaml`**：

```python
def detect_role(repo) -> Role:
    cert_path = repo / "official.crt"
    if not cert_path.exists():
        return Role.UNINITIALIZED
    cert = load_cert(cert_path)
    sans = cert.san_uris

    if sans == ["hpm://official"]:
        # 官方仓库：必须由内置 Root CA 签发
        if verify_signed_by(cert, ROOT_CA):
            return Role.OFFICIAL
        return Role.INVALID_CERT

    if len(sans) == 1 and sans[0].startswith("hpm://domain/"):
        # 私域仓库：先看 SAN；再用本机缓存的官方证书验证签发者
        issuer = load_optional(OFFICIAL_CERT_CACHE)   # ~/.hpm/official.crt
        if issuer and verify_signed_by(cert, issuer):
            return Role.PRIVATE
        return Role.PRIVATE_UNVERIFIED                # 离线/官方证书轮换/验签失败，顶栏提示
    return Role.INVALID_CERT
```

| 角色 | 判定条件 | 状态 |
|------|----------|------|
| `OFFICIAL` | SAN=`hpm://official`，且由 HPM Root CA 签发 | 官方仓库 |
| `PRIVATE` | SAN=`hpm://domain/<name>`，且由本机官方证书（`~/.hpm/official.crt`）签名可验证 | 私域仓库 |
| `PRIVATE_UNVERIFIED` | SAN 正确，但本机没有官方证书、或验签失败（离线/首次/官方证书轮换） | 私域仓库，顶栏标黄并提示「未验证签发者」 |
| `UNINITIALIZED` | 无 `official.crt` | 可初始化 |
| `INVALID_CERT` | 证书无法解析、SAN 不符、官方证书不是根签发 | 只读诊断 + 修复入口 |

根证书：内置 `HPM_ROOT_CA_PEM`（与 `core/include/hpm/config.h` 相同），支持 `HPM_ROOT_CA` 指向 PEM 文件覆盖。签发者证书：优先 `HPM_OFFICIAL_CERT`，其次 `~/.hpm/official.crt`（`hpm-dev login` 会更新它）。

角色对功能的影响：

| 功能 | 官方仓库 | 私域仓库 | 未初始化 |
|------|----------|----------|----------|
| 登录/Token 检查 | 不需要 | 需要 | 需要（用于取得 repoUrl） |
| 管理包 / 重建索引 / 签索引 | 是 | 是 | 否 |
| 发布包（仅签名入库，不需 official 私钥） | 是 | 是 | 否 |
| 未索引包列表 / 详情页加入索引 | 是 | 是 | 否 |
| 添加开发者（签开发者证书） | 是 | 是 | 否 |
| 私域管理（列表/签发/登记/吊销/删除域） | 是 | 否 | 否 |
| 删除开发者 | 是 | 是 | 否 |
| 更新吊销列表（仅 updatedAt） | 是 | 是 | 否 |
| 生成签名请求（登录/未登录/登录页均可） | 是 | 是 | 是 |
| 初始化私域仓库 | 否 | 否 | 是（有 Token 后） |
| 远程地址来源 | `git remote -v` | `dev.json.repoUrl`（默认）/ `git remote` | `dev.json.repoUrl`（默认）/ 手输 |

#### 5.3.2 人员身份（只检测私钥文件）

| 身份 | 文件 | 登录 | 能力 |
|------|------|------|------|
| 未注册 | 无 | 否 | 生成签名请求 |
| 普通开发者 | `keys/dev/<name>/<name>.key` | 否 | 发布包（仅签名入库） |
| 私域管理员 | `keys/domain/<domain>/official.key` | 是 | 管理包/开发者、签索引、吊销、同步开发者中心 |
| HPM 官方管理员 | `keys/official.key` | 否 | 私域管理员能力 + 私域管理 |

优先级：官方 > 域 > 开发者 > 未注册。只判断文件存在，不验证私钥内容；真正签名失败由密钥/证书不匹配兜底。
官方仓库只认 `keys/official.key`，域私钥不授予官方仓库管理权。

### 5.4 签名请求格式（新增，双向复用）

统一用 JSON 包装，便于邮件/IM 传输，同时保留 PEM 供 openssl 校验：

```json
{
  "schema": 1,
  "kind": "developer",                  // developer | domain
  "name": "xzc",                        // 开发者名或域名
  "san": "hpm://developer/xzc",
  "publicKey": "-----BEGIN PUBLIC KEY-----\n…",
  "csr": "-----BEGIN CERTIFICATE REQUEST-----\n…",
  "email": "xzc@example.com",
  "githubUrl": "https://github.com/xzc842",
  "createdAt": "2026-11-07T03:20:00Z",
  "nonce": "base64(16B)",
  "selfSignature": "base64(Ed25519(canonical_json_without_this_field))",

  "repoUrl": "https://github.com/alice/hpm-repo",              // domain 可选
  "indexUrl": "https://raw.githubusercontent.com/alice/hpm-repo/main/index.json",
  "certUrl": "https://raw.githubusercontent.com/alice/hpm-repo/main/official.crt"
}
```

生成方式：主页「生成签名请求」或登录页按 `g`（均不要求登录），选择 domain/developer，
填写名称/邮箱/GitHub，生成或选择私钥后写入 `repo-tool/requests/<kind>-<name>.request.json`
（同目录 `.txt` 为人读版本）。生成的私钥自动保存到 `~/.hpm/keys/`，且必须设置密码。
管理员签发时**直接显示该目录的请求列表**，目录为空只显示提示，不弹出路径输入框；
也兼容旧的仓库根 `incoming/`。

校验项：

1. `schema == 1`，`kind` 合法，`name` 匹配 `^[a-z0-9][a-z0-9._-]{0,63}$`（防路径穿越）；
2. `san` 必须严格等于 `hpm://developer/<name>`（或 `hpm://domain/<name>`）；
3. CSR 自签名可验证，且 CSR 公钥 == `publicKey`；
4. `selfSignature` 用该公钥可验证，内容为去掉 `selfSignature` 后的规范化 JSON；
5. 非重放：`nonce` 未出现在 `requests/` 归档中；`createdAt` 在 30 天内；
6. 该开发者未被吊销，公钥未与现有证书重复。

同时导出人读版本 `<name>.request.txt`，含指纹、公钥摘要和发送说明。

### 5.5 密钥

- 算法：Ed25519（与现有 `official.crt`、`xzc.crt` 一致）；读取时自动识别 RSA/ECDSA 并走 SHA-256 分支。
- 容器：优先 PKCS#8 PEM；读取兼容传统 OpenSSL 加密 PEM 与 OpenSSH 私钥（含 bcrypt 加密），初始化时用用户密码加密（AES-256-CBC + PBKDF2）。
- 位置：只从 `~/.hpm` 查找，仓库目录不再保存私钥：
  - `~/.hpm/keys/official.key`（官方仓库）
  - `~/.hpm/keys/domain/<domain>/official.key`（域私钥）
  - `~/.hpm/keys/dev/<developer>/<developer>.key`（开发者）
  - 旧布局 `~/.hpm/keys/<name>/...` 作为兼容回退
  - 找不到时要求用户输入路径（默认仍为上述位置）。
- 权限：生成后立即 `chmod 600`；发现权限过宽时警告并可一键修正。

---

## 6. 核心流程

### 6.1 启动流程

```
解析参数
  → 初始化终端/主题（非 TTY 直接退出）
  → 播 splash（可跳过）
  → 仓库根 = 本程序所在目录；读 official.crt 判定角色（证书缺失/损坏不阻塞启动）
  → 读 dev.json，计算 Token 状态（官方仓库仅展示，不作门禁）
  → 由「角色 + Token 状态」生成主页菜单（§5.3.1、§5.2）
  → 并行加载：证书信息、密钥存在性、Git 状态、索引健康度
  → 渲染主页；后台校验 index.sig 与各包签名（失败只在顶栏标黄，不阻塞）
```

异常一律进「诊断」页，不把 traceback 打到终端（`--debug` 除外）。

### 6.2 登录开发者中心（S2，委托 `hpm-dev login`）

登录、授权、写 `dev.json` 全部交给现有工具，本工具不实现 OAuth、不保存 AppKey：

1. 触发登录时暂停 TUI：退出备用屏、恢复光标与终端模式；
2. 按 §5.2 的顺序探测登录命令（`HPM_DEV_BIN` → `hpm-dev login` → `hpm login`），都不可用时展示安装/升级指引并返回；
3. 以 `subprocess.call` 继承 stdin/stdout/stderr 运行；浏览器授权、确认仓库地址与开发者名都在 `hpm-dev login` 原生命令里完成；
4. 子进程退出后提示「按任意键返回」，重新初始化终端并进入备用屏；
5. 重新加载 `~/.hpm/dev.json` 与 Token 状态；
6. 结果处理：
   - 退出码非 0：错误弹窗展示 `hpm-dev` 输出末尾几行；
   - `dev.json` 仍缺失 / `accessToken` 为空：提示「登录未完成」，保留登录入口；
   - 成功：刷新顶栏，回到主页；若此前是私域/未初始化仓库，菜单随之展开。

因为 `dev.json` 由 `hpm-dev login` 写入，本工具不需要、也不应该修改它；这样两个程序的字段所有权清晰，不会互相覆盖。

### 6.3 初始化私域仓库（S10，角色为 `UNINITIALIZED` 且 Token 有效时）

仓库根固定为本程序所在目录，因此**不存在「选目录」步骤**；向导只处理域信息、密钥、远程。

1. **前置检查**：`official.crt` 与 `index.json` 均不存在；已有 `.git`、`repo-tool/`、`README.md`、`LICENSE`、设计文档直接沿用，其他文件不会阻止初始化。
2. **域信息**：域名 `name`（默认取 `dev.json.developer` 或 GitHub 用户名，允许修改）、显示名、邮箱；远程地址默认 `dev.json.repoUrl`（登录时已确认），可编辑或跳过。
3. **生成密钥**：`~/.hpm/keys/domain/<domain>/official.key`（Ed25519，加密）+ 仓库内 `official.pub`；必须设置密码（两次输入，至少 8 位）；生成后 `0600`。若该位置已有密钥（例如先用「生成签名请求」创建过），提示复用，避免请求与密钥不匹配。
4. **写骨架**：`git init -b main`、`.gitignore`（`*.key`、`publish/`、`incoming/`、`__pycache__/`、`.hpm/`）、`README.md`、空 `revoke.json`（暂不签，见第 7 步）、空 `index.json`；把工具自身文件一并纳入首次提交。
5. **生成签名请求**：`official.request.json` + `official.request.txt`，`kind=domain`，`san=hpm://domain/<name>`；同时复制到 `repo-tool/requests/`，方便管理员直接读取。
6. **配置远程并提交**：`git remote add origin <url>`（可跳过）→ 首次提交（不含私钥）→ 是否立即 push 由用户决定。
7. **等待签发**：主页出现「完成初始化」入口；索引签名功能在证书到位前不可用。此时不写 `dev.json`，不假装初始化完成。
8. **收到证书后**（用户把官方发回的 `official.crt` 放到根目录，或走「导入证书」）：校验 SAN/签发者/有效期 → `git add` → 用 official 私钥签 `index.json` 与 `revoke.json` → 再提交推送；角色变为 `PRIVATE`，完整菜单展开。

> 为什么第 8 步才签索引：`index.json.sig` 必须能由 `official.crt` 验证，而 `official.crt` 在第 8 步才存在。`pending_cert` 状态下禁用索引签名，避免产出无法验证的签名。
>
> 为什么初始化需要 Token：远程地址与开发者身份来自 `dev.json`（由 `hpm-dev login` 写入）。没有 Token 时主页仍保留登录与「生成签名请求」入口。

### 6.4 管理开发者（S6/S7）

列表字段：开发者、证书 SAN、序列号、有效期、吊销状态、包数量、密钥是否存在。

- 官方仓库：可签发开发者证书；域证书相关操作统一放到**主页主菜单**「私域管理」；
- 私域仓库：只可签发开发者证书；
- 未初始化/证书异常：该菜单不渲染。

**添加开发者（核心流程）**

1. 选择请求：直接显示 `repo-tool/requests/*.request.json`（兼容旧 `incoming/`）的 kind/name/时间列表；目录为空时只提示，不提供路径输入弹窗。
2. 严格校验 §5.4 的 6 项；任一失败 → 展示具体原因与「查看原始请求」，不允许继续。
3. 预览：姓名、SAN、公钥指纹、CSR 指纹、请求时间、邮箱；重复公钥/已存在证书时提示（可选择覆盖＝重签并吊销旧证书）。
4. 读取 official 密钥：按 §5.5 定位；缺失 → 输入路径（Tab 补全）；加密 → 密码输入（3 次机会）。
5. 签发证书（§7.3）：随机 128-bit 序列号、有效期 3 年、`CN=HPM Developer <name>`、`O=HPM`、SAN URI、`basicConstraints=CA:false`、`keyUsage=digitalSignature`、`subjectKeyIdentifier`。
6. 落盘：`<developer>/<developer>.crt`（0644），请求归档到仓库根 `requests/<name>-<serial>.request.json`，从 `repo-tool/requests/` 或 `incoming/` 移除。
7. 重建索引并签名（可选，默认做）：调用 `index.rebuild()` + `index.sign()`。
8. 导出回执：`<name>.crt` 与 `cert-receipt.json`（含序列号、指纹、有效期），提示通过原渠道发回；界面展示「证书已生成，可发送给开发者」。
9. 提交：`git add` 证书/索引/吊销文件，提交信息模板 `Add developer <name> (serial <hex>)`；推送需确认。

**吊销开发者证书（S9）**：选择开发者或证书文件 → 显示序列号/指纹 → 原因（keyCompromise/superseded/ceased/unspecified）→ 二次确认 → 写 `revoke.json`、签 `revoke.json.sig`、可选删除证书文件（保留更安全，默认保留）、重建/签索引、提交。拒绝吊销自己正在使用的 official 证书。

**私域管理**（仅官方仓库，主页主菜单）包含三个入口：

- **私域列表**：读取 `domain.yaml` 与 `outgoing/*.crt`，显示域名、登记状态、序列号、剩余天数、本地证书；Enter 查看索引/证书 URL、指纹等详情。
- **签发证书**：读取 `repo-tool/requests/` 中的 domain 请求（无请求只提示），签发后写 `outgoing/<domain>.crt` 与回执；请求带 `indexUrl`/`certUrl` 时自动写入 `domain.yaml`、重建并签名主索引，否则提示未登记。
- **登记证书**：对“已签发，未登记”的域证书补填索引/证书 URL，写 `domain.yaml` 并重签索引。
- **吊销证书**：列出 `outgoing/` 中的域证书，写 `revoke.json` 并重新签名。
- **删除域**：删除 `domain.yaml` 条目与 `outgoing/` 证书/回执，重建索引；可选择先吊销。

主页「更新吊销列表」只调用 `revoke.touch()` 刷新 `updatedAt`，再用官方私钥重签，不修改条目、不重建索引。

### 6.5 管理包（S3/S4/S5）

- **列表**：以 `index.json.packages` 为骨架，与磁盘 `*.tar.gz` 对账；磁盘上存在但未进索引的包也会显示，状态为「未加入索引」。
- **发布向导（仅签名入库）**：普通开发者可用，不需要 official 私钥：
  1. `publish/` 或 `repo-tool/publish/` 有 tar.gz 时列出全部包，确认后批量发布；都为空才输入路径；
  2. 用开发者私钥签名（已有有效 `.sig` 可复用）；
  3. 复制到 `<developer>/`；
  4. 成功后删除 publish 中的源 tar.gz/.sig；
  5. **不重建索引**，由管理员后续加入。
- **发布向导（并更新索引）**：需要 official 私钥，签名入库后重建索引、签索引并全量校验。
- **详情页**：未索引包按 `a` 用 official 私钥重建/签名索引，将其加入 `index.json`；按 `d` 删除未索引文件或已索引版本。
- **查看/删除版本**：删除前确认；删除后自动重建并重签索引；`git rm` 交给 Git。
- **重建索引 / 签索引**：独立入口，供「只改了证书」「只手工放了包」时使用。
- **包元数据**：直接读 tar 内 `manifest.json|chip.json`（`core/index.py` 的 `_read_package_meta`），界面展示 requires/provides/config 的结构化摘要。

### 6.6 Git 同步

- 只调用系统 `git`（不实现协议），检测不到时禁用 Git 功能并提示安装。
- 状态：`git status --porcelain=v1 -b`、ahead/behind、最近提交、remote -v。
- 提交：`git add -- <具体路径>`（避免 `git add -A` 误提交私钥）；提交信息可编辑，模板由操作类型生成。
- 推送：先 `fetch`，落后则提示 `pull --rebase`（需确认）；**永不 force push**；冲突时中止并显示冲突文件，保留现场。
- 远程管理：初始化时可加，设置页可改；不处理凭据（交给系统 credential helper / SSH agent）。

---

### 6.7 开发者中心同步（仅私域管理员）

登录后的私域管理员在本地变更后调用 `/api/webhooks/apps`：

| 本地操作 | action | payload |
|----------|--------|---------|
| 新增/重签开发者 | `update_developer` | `name`、`pubUrl`、`pubSigUrl` |
| 更新包（发布/加入索引） | `update_package` | `name`、`kind`、`requiresJson`、`version`、`url`、`sigUrl`、`sha256`、`size`、`license` |
| 删除开发者 | `delete_developer` | `name` |
| 删除包 | `delete_package` | `name`（服务端删除该包全部版本） |

- 删除接口返回 404 时视为“已不存在”，不算同步失败；
- 包在本地仍保留其他版本时**不调用** `delete_package`，避免误删远程全部版本；
- 开发者中心按包名删除全部版本，无法只删单个版本；
- 同步失败只提示，不回滚本地操作。

## 7. 密码学设计

### 7.1 算法与格式

| 项 | 取值 |
|----|------|
| 密钥算法 | Ed25519（默认）；读取兼容 RSA-2048+/ECDSA P-256+ |
| 私钥格式 | PKCS#8 PEM，带密码时 `EncryptedPrivateKeyInfo`（PBKDF2 + AES-256-CBC） |
| 证书签名 | Ed25519（`algorithm=None`）/ RSA·ECDSA 用 SHA-256 |
| 包签名 | 对 tar.gz 原始字节签名；Ed25519 产出 64 字节裸签名，即现有 `.sig` |
| 索引签名 | 对 `index.json` 原始字节签名，写 `index.json.sig`（裸二进制，与现状一致） |
| 规范化 JSON | `json.dumps(obj, sort_keys=True, separators=(",",":"), ensure_ascii=False)` UTF-8 |

### 7.2 后端策略

`crypto.py` 直接使用 `cryptography`（v0.1 不提供 OpenSSL 回退）：

```
generate_ed25519_keypair(password) -> (private_pem, public_pem)
load_private_key(path, password=None)
sign_bytes(key, data) / verify_bytes(pub, data, sig)
create_csr(key, common_name, san)
load_csr(...) / issue_certificate(...) / issue_certificate_from_csr(...)
load_cert(path) / cert_info(cert) / verify_cert_signed_by(cert, issuer)
```

等价的 OpenSSL 命令（仅作参考，工具内部不调用）：

```
openssl genpkey -algorithm ed25519 -aes-256-cbc -out official.key
openssl req -new -key official.key -subj "/CN=..." -addext "subjectAltName=URI:hpm://developer/xzc" -out x.csr
openssl x509 -req -in x.csr -CA official.crt -CAkey official.key -set_serial 0x<hex> \
  -days 1095 -extfile <(printf "subjectAltName=URI:hpm://developer/xzc\nbasicConstraints=CA:FALSE\nkeyUsage=digitalSignature") -out x.crt
openssl pkeyutl -sign -inkey official.key -rawin -in index.json -out index.json.sig
openssl pkeyutl -verify -pubin -inkey official.pub -rawin -in index.json -sigfile index.json.sig
```

`cryptography` 路径对 Ed25519 使用 `sign(None, data)`、`CertificateBuilder().sign(key, None)`；对 RSA/ECDSA 用 `hashes.SHA256()`。

### 7.3 证书档案（profile）

| 类型 | Subject | SAN | 有效期 | 扩展 |
|------|---------|-----|--------|------|
| 开发者 | `CN=HPM Developer <name>, O=HPM` | `URI:hpm://developer/<name>` | 3 年 | CA:FALSE、KU=digitalSignature、EKU=codeSigning、SKI |
| 域 | `CN=HPM Domain <name>, O=HPM` | `URI:hpm://domain/<name>` | 5 年 | CA:TRUE pathlen:0（需要签发开发者证书）、KU=digitalSignature+certSign、EKU=codeSigning、SKI |
| 官方（根自签，工具不生成） | `CN=HPM Official Registry` | `URI:hpm://official` | — | 由离线根签 |

序列号：16 字节随机正整数（最高位清零），大写十六进制展示，同时写入 `revoke.json` 与回执，保证唯一且可用于吊销。
扩展字段与现有证书保持兼容；新增字段不破坏客户端解析。

### 7.4 密钥定位与密码输入（所有签名操作的统一入口）

```
resolve_key(purpose: "official" | "developer:<name>") -> Path
  ├─ 只从 ~/.hpm/keys 的约定路径查找（§5.5）
  ├─ 都不存在 → 输入框：请输入私钥路径（Esc 取消）
  ├─ 文件存在但格式非法 → 明确报错，不重试覆盖
  └─ 返回路径

load_key_with_password(path) -> Key
  ├─ 先尝试无密码加载
  ├─ 若未加密 → 询问是否设置密码并加密保存（可跳过，继续使用未加密私钥）
  ├─ 若加密 → 密码框（掩码，3 次机会），支持：
  │    · 会话内缓存（默认开启，退出即清空；设置页可关）
  │    · 环境变量 HPM_KEY_PASSWORD（CI 用）
  ├─ 密码错误 → 剩余次数提示；3 次失败返回上层菜单，不清空文件
  └─ 成功后 chmod 600（若权限过宽则提示）
```

密码只存在于内存（`bytearray`，用完清零）与系统提示，不落盘、不进日志、不进 Git 提交信息；进程退出注销。

### 7.5 校验与发布门禁

每次「签名」和「推送」前执行门禁，任一失败即阻止并定位到具体文件：

1. `index.json` 能被 `official.crt` 验证；
2. `revoke.json` 能被 `official.crt` 验证（存在时）；
3. 索引中每个包：文件存在、`sha256`/`size` 匹配、`.sig` 能用对应开发者证书验证；
4. 开发者证书：SAN 匹配目录名、未过期、序列号未在 `revoke.json`；
5. 私钥对与证书公钥一致（签出的 `.sig` 能验过）；
6. 工作区没有未跟踪的 `*.key`（防误提交，`git check-ignore` 自检）。

---

## 8. `revoke.json` 设计

### 8.1 位置与签名

- 仓库根 `revoke.json`，配套 `revoke.json.sig`；
- 由该仓库的 official 私钥签名（官方仓库用官方密钥，域仓库用域密钥）；
- 客户端应「主索引所在仓库 + 各域仓库」的吊销列表取并集；本工具只负责生成与校验。

### 8.2 格式

```json
{
  "schema": 1,
  "updatedAt": "2026-11-07T03:20:00Z",
  "issuer": {
    "san": "hpm://domain/qincnd",
    "certSha256": "AA:BB:…"
  },
  "entries": [
    {
      "serial": "3F2A9C0D8E7B6A5C4D3E2F1A0B9C8D7E",
      "kind": "developer",
      "name": "xzc",
      "san": "hpm://developer/xzc",
      "subject": "CN=HPM Developer xzc, O=HPM",
      "certSha256": "11:22:…",
      "revokedAt": "2026-11-07T03:20:00Z",
      "reason": "superseded",
      "revokedBy": "xzc",
      "comment": "reissued after key rotation"
    }
  ]
}
```

- `entries` 按 `serial` 去重，追加式写入（已有记录不改，保证审计性）；
- `reason` 取 RFC 5280 枚举：`keyCompromise`、`superseded`、`ceased`、`unspecified`；
- 空列表也生成文件（明确表示「无吊销」），并在初始化时生成空文件 + 签名；
- 手工编辑后支持「重新签名」，签名前校验结构；
- 吊销不删除证书文件（默认），客户端命中指纹/序列号即拒绝；删除文件由用户在高级选项显式选择。

### 8.3 客户端配合（需在 `hpm` 侧实现）

1. 用内置根证书验证 official.crt；
2. 下载并验证 `ROOT_revoke.json` + `.sig`（根公钥），检查 official.crt 是否被根吊销；
3. 下载 `index.json` 同目录的 `revoke.json` + `.sig`，用同一 official 证书验签；
4. `revoke.json` 缺失：兼容旧仓库，视为「无吊销」，但给出一次警告；
5. `revoke.json` / `ROOT_revoke.json` 存在但签名无效：**拒绝该仓库/官方证书**；
6. 校验顺序：先根吊销 → 链与 SAN → 仓库吊销（序列号或证书 SHA-256）→ 包签名；
7. 离线缓存需带 TTL，过期后重新拉取。

### 8.4 根级 `ROOT_revoke.json` 与“信任根管理”

- 文件名固定 `ROOT_revoke.json`，签名 `ROOT_revoke.json.sig`；
- 由 HPM Root CA 私钥签名，用于吊销官方证书（以及根直接签发的证书）；
- 官方仓库主页为 HPM 官方管理员显示「信任根管理」：
  - 签发官方证书；
  - 吊销官方证书；
  - 更新 `ROOT_revoke.json` 的 `updatedAt` 并重签；
- 每次操作都重新输入根私钥路径；加密私钥再输入密码；**不进入 App 密钥缓存**；
- 根私钥不写日志、不复制到仓库；
- 建议放在官方仓库根目录并由镜像原样同步，或由客户端从固定的 `HPM_ROOT_REVOKE_URL` 拉取；
- 格式、签名命令、客户端策略和轮换流程见 [证书颁发与吊销机制](certificates.md)。

实现：`core/root_revoke.py`、`views/root_ca.py`。

---

## 9. 登录委托与 Token 生命周期

本工具**不直接访问开发者中心**，OAuth、AppKey、用户信息、仓库地址全部由 `hpm-dev login` 负责。

| 事项 | 负责方 |
|------|--------|
| 浏览器授权、`code` 换 `accessToken` | `hpm-dev login` |
| 调用 `get_user`，确认开发者名与仓库地址 | `hpm-dev login` |
| 写 `~/.hpm/dev.json` | `hpm-dev login` |
| 读 `dev.json`、判断 Token 是否过期、决定菜单 | 本工具 |
| 提示重新登录、在 TUI 与原生命令之间切换 | 本工具 |

`hpm-dev login` 的既有行为（`dev/src/commands/login_cmd.cpp`）可直接复用：

- 随机端口 `30000-39999` 启动本地回调服务，浏览器授权；
- 用 `HPM_DEV_CENTER_APP_ID/APP_KEY`（编译期常量）换 Token，AppKey 不落到本工具；
- 拉取 `name/email/githubUrl/repoUrl`；
- 依次确认仓库地址（`repoUrl` 为空时默认 `<githubUrl>/hpm-repo`）与开发者名；
- 保存 `accessToken/expiresAt/repoUrl/githubUrl/developer`。

本工具只读取这些结果。Token 默认 1 天有效，过期后主页的「重新登录」可用；顶栏显示剩余时间，不做后台自动续期。

> 若将来登录命令升级（设备码、PKCE、refresh token），本工具无需改动，只要 `dev.json` 字段不变。
>
> 如果运行环境没有 `hpm-dev`：登录页给出安装路径提示，并允许「手动粘贴 dev.json 内容」的降级入口（仅解析，不联网）。

---

## 10. 安全设计

| 风险 | 对策 |
|------|------|
| AppKey 泄露 | 本工具完全不接触；授权与 AppKey 都在 `hpm-dev` 内，`dev.json` 只含短期 Token |
| Token 泄露 | 只读展示，不写日志；`dev.json` 权限交给创建者 `hpm-dev`，发现过宽时仅提示 |
| 私钥泄露 | `.gitignore` + 权限自检 + 推前扫描未跟踪 `*.key` + 禁止 `git add -A` |
| 密码泄露 | 掩码输入、内存清零、不落盘、不进命令行参数 |
| 恶意请求 | CSR 自签名 + 自签字段校验 + 名称白名单 + nonce 防重放 + 二次确认 |
| 路径穿越 | 开发者名/域名严格正则，落盘路径 `resolve()` 后必须在仓库根内 |
| TOCTOU | 读取文件后校验哈希再签名；签名后复验 |
| 索引被篡改 | 每次操作前验签；发布门禁强制校验 |
| 吊销被删除 | 客户端强制验 `revoke.json.sig`，缺失/无效按策略拒绝 |
| 误操作 | 危险操作二次确认、默认焦点在取消、可回退的导航栈、写前备份 |
| 终端注入 | 输出前过滤控制字符（文件名/描述来自包元数据） |

日志：`~/.hpm/logs/repo-tool.log`（按天轮转，保留 7 天），只记操作、路径、结果、错误码，**不记** token/密码/私钥/完整证书。

---

## 11. 错误处理与可用性

- 统一异常层级：`ConfigError`、`RepoError`、`RoleError`、`AuthError`、`CryptoError`、`KeyNotFound`、`PasswordError`、`RequestInvalid`、`GitError`、`UserCancel`；
- 界面错误以弹窗展示「做什么失败了 + 为什么 + 怎么修 + 日志路径」，例如：
  `无法签名 index.json：official.key 不存在。请把私钥放到仓库根目录，或按 Enter 输入路径。`
- 所有写操作「先校验、后写临时文件、再原子替换」；
- 用户取消（Esc）不产生副作用，不留下半成品文件；
- 崩溃处理：恢复终端、写日志、以非零码退出；`--debug` 打印 traceback；
- 无 TTY 时直接退出并提示；自动化测试用 pty（`tests/pty_smoke.py`）。

---

## 12. 测试与验收

### 12.1 单元测试

- `crypto`：密钥生成/密码加载/签名验签/CSR 生成解析/证书签发与链校验（全部在 `tmp_path` 内用一次性密钥）；
- `jsonc`：规范化 JSON 稳定性（键序、Unicode、浮点）；
- `index`：构造若干 tar.gz，验证 `build_index` 输出与现有 `index.json` 一致；
- `revoke`：追加、去重、签名、篡改检测；
- `config/auth`：`HPM_HOME` 路径、缺失/损坏 dev.json、`expiresAt` 解析、Token 三态；
- `repo`：角色判定矩阵（无证书/官方/私域/链断裂/SAN 不符）、菜单可见性矩阵；
- `login`：用临时脚本冒充 `hpm-dev`（写入 dev.json）验证子进程调用与返回后的状态刷新。

### 12.2 端到端（临时目录）

1. 工具所在目录（含工具自身文件）→ 初始化 → 断言 Git/密钥/请求/空索引齐全，且工具文件已提交；
2. 用同一工具的「官方」角色给该请求签域证书 → 断言 SAN/有效期/序列号；
3. 导入证书 → 角色变为私域 → 生成开发者请求 → 签开发者证书 → 包入库 → 签索引 → 全量门禁通过；
4. 吊销开发者证书 → 断言门禁拒绝该开发者的包；
5. 篡改请求/索引/包 → 断言每一步都被拒绝且给出定位信息；
6. 无 Token / Token 过期 / 官方仓库三种状态下，主页菜单项与预期完全一致。

### 12.3 TUI 测试

- pty 驱动：启动 → 跳过动画 → 方向键移动 → 二级菜单返回 → 退出；
- 80×24 与 120×40 渲染快照（ANSI 文本）；
- `NO_COLOR`、非 TTY、`SIGWINCH` 不发散。

### 12.4 验收标准

- 在无 `dev.json` 的新机器上：官方仓库仍可直接启动并完整操作；私域仓库显示登录与生成签名请求入口；
- 登录一次后（`hpm-dev login` 成功）无需重启程序即可展开私域菜单；
- 初始化全过程无需手工执行 openssl 命令；
- 从「收到请求」到「证书出现在开发者目录并索引可验证」不超过 3 个界面操作；
- 所有签名操作均能在密钥缺失/有密码时按 §7.4 正确提示；
- 键盘可完成全部功能，无鼠标依赖；Esc 在任何二级菜单可返回。

---

## 13. 实施里程碑

| 阶段 | 内容 | 状态 |
|------|------|------|
| M1 | TUI 基座 + splash + 主页 + 顶栏 + 角色判定 + 菜单门禁 | 完成 |
| M2 | 登录委托（`hpm-dev login`）+ Token 状态刷新 | 完成 |
| M3 | 初始化向导 + 密钥生成 + 签名请求 | 完成 |
| M4 | 证书签发 + 开发者管理 + revoke.json | 完成 |
| M5 | 包管理 + 索引重建/签名 + Git 发布向导 | 完成 |
| M6 | 校验门禁、错误处理、测试与文档 | 完成 |

测试：`python3 -m pytest -q`（33 项）与 `python3 tests/pty_smoke.py`（17 个 pty 场景）。

---

## 14. 已确认决策与后续工作

已确认：

1. **登录**：`hpm-dev login` 优先，`hpm login` 作为兼容回退；`HPM_DEV_BIN` 可覆盖。
2. **官方仓库**：完全免登录，不读取 `dev.json.repoUrl`，远程地址来自 `git remote -v`。
3. **私域仓库**：就是 `repo-tool/` 所在目录；仓库根 = `repo-tool` 的上一级。
4. **仓库根目录**：固定，不做目录选择；`HPM_REPO_ROOT` 仅用于测试。
5. **请求格式**：`*.request.json`（CSR + 公钥 + 自签名 + nonce），工具内已实现校验与签发。
6. **包来源**：tar.gz 由 `hpm-dev publish` 或构建流水线产出，本工具负责签名、入库、索引。

后续可做：

- `hpm` 客户端实现 `revoke.json` 验签与吊销检查（§8.3）；当前客户端还没有相关逻辑。
- 域证书签发后自动登记已实现；仍可增加“从域名反查仓库 URL”的默认值增强。
- Windows 终端（ConPTY/msvcrt）适配；当前主要面向 Linux/macOS。
- 纯 OpenSSL 后端（无 `cryptography` 环境）。
- 包删除/发布的操作审计与更细的权限控制。
