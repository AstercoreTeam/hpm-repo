# hrm 开发与扩展指南

本文面向后续维护 `repo-tool/` 的开发者，说明代码结构、内部接口、常见扩展方式与测试方法。

相关文档：

- [仓库 README](../README.md)
- [hrm 使用说明](repo-tool.md)
- [hrm 设计与实现](repo-tool-design.md)
- [证书颁发与吊销机制](certificates.md)
- [仓库维护文档](repo.md)

---

## 1. 快速开始

```bash
cd repo-tool
pip3 install -r requirements.txt

./hrm --no-animation        # 从源码直接运行
python3 -m pytest -q        # 单元/集成测试
python3 tests/pty_smoke.py  # pty 端到端冒烟
```

要求 Python ≥ 3.10。开发时建议同时打开 `--debug`，内部异常会显示堆栈而不是只写日志。

### 1.1 目录职责

```
repo-tool/
├── hrm                     入口：把 repo-tool 加入 sys.path，调用 hpmrepo.app.main
├── hpmrepo/
│   ├── app.py              主循环、导航栈、弹窗、终端挂起、key 缓存
│   ├── config.py           dev.json 只读加载、Token 三态
│   ├── repo.py             仓库根定位、Role 判定、RepoContext
│   ├── tui/                与业务无关的终端层
│   │   ├── terminal.py     原始模式、备用屏、按键解码（唯一直接接触 termios/tty 的模块）
│   │   ├── screen.py       字符画布、宽字符处理、ANSI 差分刷新
│   │   ├── theme.py        256 色表、NO_COLOR
│   │   └── widgets.py      菜单、表格、标题、页脚、弹窗
│   ├── views/              界面与流程；只允许依赖 app + core + tui
│   │   ├── request.py      生成签名请求（无需登录）
│   │   ├── domains.py      私域管理（列表/签发/吊销）
│   │   └── ...
│   ├── core/
│   │   ├── domains.py      domain.yaml / outgoing 域列表
│   │   └── ...             纯业务；禁止 import tui/views/app
│   └── requests/           待处理签名请求（运行时写入，默认 gitignore）
│   └── util/               fs/json/shell/log/errors
└── tests/                  pytest + pty 冒烟
```

依赖方向必须保持：

```
hrm → app → views → core → util
                 ↘ tui（views 可直接用）
```

`core` 不依赖终端，因此可以在脚本、测试、未来无头模式中单独调用。

---

## 2. 运行机制

### 2.1 主循环

`App.run()` 的每一轮：

1. 检查终端尺寸并同步画布；
2. 调用当前视图的 `update(now)`（用于动画/定时刷新）；
3. `view.render(screen)` 绘制；
4. `screen.flush()` 只输出变化的行；
5. 读一个按键，交给 `view.handle_key(key)`。

导航是一个视图栈：

```python
app.push(view)      # 进入下一级
app.pop()           # 返回上一级（栈底不弹）
app.replace(view)   # 替换当前视图
app.home()          # 重新加载状态并回到主页
```

`HomeView`、`PackagesView`、`DevelopersView`、`SettingsView` 继承 `MenuView`，统一处理
上下键、Enter、Esc、`?`、`r`。

### 2.2 应用状态

`App` 上只保存跨视图状态：

| 属性 | 含义 |
|------|------|
| `app.ctx` | `RepoContext`：仓库根、角色、证书信息、域 |
| `app.dev` | `DevConfig`：`dev.json` 的只读快照 |
| `app.token` | `TokenStatus`：`missing` / `expired` / `valid` |
| `app.screen` | 当前画布 |
| `app.stack` | 视图栈 |
| `app._key_cache` | 会话内已解密的私钥（`str(path) -> key`） |

`app.reload()` 重新读取 `dev.json` 并重新判定仓库角色。任何会改变 `dev.json`、`official.crt`
或 Git 状态的操作结束后都应调用 `app.reload()` 或 `app.home()`。

### 2.3 角色与门禁

`repo.detect()` 只看仓库根 `official.crt`：

| Role | 条件 | 行为 |
|------|------|------|
| `OFFICIAL` | SAN `hpm://official` + Root CA 签发 | 免登录 |
| `PRIVATE` | SAN `hpm://domain/<n>` + 官方证书验签通过 | 需要 Token |
| `PRIVATE_UNVERIFIED` | SAN 正确但无法验签 | 同私域，顶栏告警 |
| `UNINITIALIZED` | 无 `official.crt` | 可初始化 |
| `INVALID_CERT` | 证书损坏 / SAN 不符 / 官方证书非根签发 | 只允许诊断 |

主页菜单由 `HomeView._build_items()` 根据 `ctx.role` + `token.ok` 生成。
新增菜单项时，务必同时考虑这五种状态。

---

## 3. 常用接口速查

### 3.1 App 弹窗与交互

```python
app.message(title, lines, level="info")        # 信息框，Enter 关闭
app.info(title, lines=None)                    # info 的便捷包装
app.error(message, hint="")                    # 错误框（自动附日志路径）
app.confirm(title, lines, danger=False) -> bool # 默认焦点在「取消」
app.choose(title, options, hint="") -> int | None
app.input(title, prompt, default="", mask=False, hint="") -> str | None
app.busy(title, message)                       # 只画一次「正在…」，不阻塞
app.toast(message, level="info", seconds=4)    # 底部提示
app.suspend() / app.resume()                   # 交给外部交互式命令
```

约定：

- `input`/`choose`/`confirm` 返回 `None` 或 `False` 表示用户取消，**不要**把取消当错误；
- `input` 的 `mask=True` 用于密码；
- `app.busy()` 只是画面提示，后续同步操作仍会阻塞，因此不要用它包裹需要数秒且无法取消的操作而不加说明。

### 3.2 视图

```python
class View:
    def __init__(self, app): ...
    def update(self, now): ...
    def render(self, screen): ...
    def handle_key(self, key): ...
```

按键名：`up/down/left/right/enter/esc/tab/backspace/delete/home/end/pgup/pgdn/space/char/ctrl_c/ctrl_u/ctrl_w/ctrl_l`。
普通字符放在 `key.char`。

`MenuView` 子类通常只需：

```python
class FooView(MenuView):
    title = "Foo"
    subtitle = "说明"

    def build_items(self):
        return [
            MenuItem("做点什么", "提示", action="do"),
            MenuItem("← 返回", "", action="back"),
        ]

    def on_select(self, item):
        if item.action == "do":
            ...
        elif item.action == "back":
            self.app.pop()

    def header_lines(self):
        return [f"仓库: {self.app.ctx.root}"]
```

修改状态后调用 `self.refresh_items()`，否则菜单项不会更新。

### 3.3 画布与控件

```python
screen.put(x, y, text, style)
screen.fill(x, y, w, h, style)
screen.box(x, y, w, h, title="", style, border_style)
screen.cursor = (x, y)

st(fg="cyan", bg="select", bold=True)   # 颜色名见 tui/theme.py
ellipsize(text, width)                  # 按显示宽度截断（兼容中文）
wrap_text(text, width) -> list[str]
```

`draw_title`、`draw_footer`、`draw_menu`、`draw_table`、`draw_modal` 在 `tui/widgets.py`。
所有宽度按终端列计算，中文占 2 列；不要用 `len(str)` 排版。

### 3.4 核心函数

| 模块 | 主要函数 |
|------|----------|
| `core/crypto.py` | `load_private_key`、`sign_bytes`、`verify_bytes`、`create_csr`、`issue_certificate_from_csr`、`cert_info`、`verify_cert_signed_by` |
| `core/index.py` | `build_index(root)`、`rebuild(root)`、`sign_index`、`verify_index`、`load_index` |
| `core/packages.py` | `verify_all`、`sign_file`、`install_package`、`remove_version` |
| `core/developers.py` | `build_request`、`validate_request_data`、`issue_request`、`list_developers` |
| `core/revoke.py` | `issue`、`sign`、`verify`、`is_revoked` |
| `core/gitops.py` | `status`、`add`、`commit`、`add_remote`、`push`、`is_ignored` |
| `core/verify.py` | `run_all(ctx)`、`publish_gate(ctx)` |
| `core/keys.py` | `find_official(domain)`、`find_developer(name)`、`official_key_path`、`developer_key_path`、`key_is_encrypted` |
| `core/auth.py` | `detect()`：`HPM_DEV_BIN` → `hpm-dev login` → `hpm login` |

所有路径参数都是显式的 `Path`，不依赖当前工作目录。

---

## 4. 扩展手册

### 4.1 新增一个顶级菜单页

以「管理域（domain.yaml）」为例。

1. 新建 `hpmrepo/views/domains.py`：

```python
from ..tui.screen import Screen, ellipsize, st
from ..tui.widgets import draw_footer, draw_title
from .base import View

class DomainsView(View):
    def render(self, screen: Screen) -> None:
        draw_title(screen, "管理域", "domain.yaml")
        screen.put(3, 2, "这里是域列表", st(fg="text"))
        draw_footer(screen, "Esc 返回")

    def handle_key(self, key) -> None:
        if key.name == "esc":
            self.app.pop()
```

2. 在 `views/home.py` 的 `_build_items()` 里按角色加入 `MenuItem(..., action="domains")`；
3. 在 `HomeView._activate()` 中路由：

```python
elif action == "domains":
    from .domains import DomainsView
    self.app.push(DomainsView(self.app))
```

4. 在 `docs/repo-tool.md` 增加功能说明；
5. 在 `tests/pty_smoke.py` 增加一个场景（见 §6）。

如果页面是纯菜单，直接继承 `MenuView` 更省事。

### 4.2 给已有菜单加动作

以「管理包」为例：

1. `PackagesView.build_items()` 增加 `MenuItem`；
2. `PackagesView.on_select()` 中处理 `item.action`；
3. 需要用户输入时用 `app.input/confirm/choose`；
4. 完成后 `self.refresh_items()` 并视情况 `app.toast()`。

不要在 `build_items()` 里做耗时操作（每次进入页面/刷新都会调用）。

### 4.3 新增核心业务模块

适合放进 `core/` 的代码：

- 输入是路径/字节/数据类，输出是数据类或字典；
- 不 import `views`、`app`、`tui`；
- 失败抛 `ToolError` 子类（`RepoError`、`CryptoError`、`RequestInvalid`、`GitError`），
  由 `App.run()` 统一转成错误弹窗。

模板：

```python
# hpmrepo/core/foo.py
from pathlib import Path
from ..util.errors import RepoError
from ..util.fs import atomic_write
from ..util.jsonc import dumps

def do_foo(root: Path, value: str) -> dict:
    if not value:
        raise RepoError("value 不能为空", "由调用方先校验")
    data = {"value": value}
    atomic_write(Path(root) / "foo.json", dumps(data))
    return data
```

视图里只做「收集参数 → 调 core → 显示结果」：

```python
value = app.input("Foo", "请输入 value")
if value is None:
    return
try:
    do_foo(ctx.root, value)
except ToolError as exc:
    app.error(exc.message, exc.hint)
    return
app.toast("完成", "ok")
```

### 4.4 新增证书类型 / 签名请求种类

涉及位置（按顺序改）：

1. `core/developers.py`
   - `_san_for()`、`_common_name()`：SAN 与主题；
   - `validate_request_data()`：允许的 `kind`、名称白名单、SAN 校验；
2. `core/crypto.py`
   - `issue_certificate(profile=...)`：是否 CA、`path_length`、KeyUsage、有效期；
3. `core/developers.issue_request()`：写入目录、权限、归档；
4. `RepoContext` / `Role`（如带来新的仓库角色）；
5. `views/developers.py`：菜单入口与确认文案；
6. **`hpm` 客户端**：SAN 校验、证书链和用途必须能识别新类型，否则签出来也没法用；
7. 测试：请求校验、签发、链校验、吊销。

注意：开发者证书是 `CA:FALSE`，域证书是 `CA:TRUE pathlen:0`；改 profile 会直接影响客户端信任链。

### 4.5 给 `index.json` 增加字段

1. 在 `core/index.py` 的 `_parse_manifest()` 里解析 manifest 字段；
2. 在 `build_index()` 组装 `version_entry` 时写入；
3. 保持 `schema: 2`，新字段必须是**可选**的，旧客户端应能忽略；
4. 同步 `hpm` 客户端的索引解析；
5. 更新 `tests/test_index.py`（尤其 `test_builder_matches_existing_index` 会对比当前 `index.json`，
   字段变化后需要重新生成索引再更新断言/仓库索引）；
6. 文档更新 `docs/repo.md` 的索引字段表。

索引构建现在**只**在 `core/index.py`，没有 `gen_index.py`，不要再引入第二套扫描逻辑。

### 4.6 新增耗时/异步操作

当前所有操作都是同步的。短操作直接做；超过约 1 秒的操作：

- 先 `app.busy(...)` 画一帧提示；
- 执行；
- 结束后 `app.toast()`/`app.error()`。

如果确实要后台线程：

- 线程内**不要**碰 `Screen`/`App` 的 UI 方法；
- 结果写入 `queue.Queue`，在 `view.update()` 中轮询并更新状态；
- 注意异常也要回传，并在视图退出时取消/忽略迟到结果。

### 4.7 调用外部交互式命令

需要浏览器授权、SSH 密码、编辑器等交互时：

```python
app.suspend()
try:
    subprocess.call([...])
finally:
    app.resume()
app.reload()
```

参考 `views/login.py`。不要在备用屏（alternate screen）里直接 `print`/`input`。

### 4.8 新增命令行参数

`app.py` 的 `build_parser()` 增加参数，在 `main()` 中消费：

```python
parser.add_argument("--foo", action="store_true")
...
app = App(animate=not args.no_animation)
app.foo = args.foo
```

如果参数会改变仓库根，不要绕过 `repo.repo_root()`；测试用 `HPM_REPO_ROOT` 注入。

### 4.9 新增本地配置

`~/.hpm/dev.json` 属于 `hpm-dev`，本工具**只读**。如果需要保存工具自己的设置：

- 新建 `~/.hpm/repo-tool.json`（`0600`），用 `util.fs.atomic_write` 写入；
- 读取失败要降级到默认值，不要让工具起不来；
- 不要把 Token/密码写进去。

### 4.10 支持新平台终端

平台相关代码集中在 `tui/terminal.py`（`termios`/`tty`/`select`）。扩展 Windows 时：

- 保持 `read_key()` 返回相同的 `Key` 名称；
- 保持 `enter()/exit()/size()/write()` 语义；
- 不要在其它模块直接调用 `termios`。

### 4.11 签名请求收件与签发

- 收件目录：`repo.request_inbox()`（默认 `repo-tool/requests/`，`HPM_REQUESTS_DIR` 可覆盖）。
- 生成：`views/request.py` → `developers.build_request()` + `developers.write_request()`；
  新私钥固定保存到 `~/.hpm/keys/`，并通过 `ask_password_pair()` 强制设置密码。
- 列出：`developers.list_request_summaries(root)` 返回 `PendingRequest(path, kind, name, valid, error)`，
  损坏文件不会中断列表。
- 签发：`resolve_request_path(app, ctx, kind=...)` **只从列表选择**，不再回退到路径输入框；
  空目录时显示提示并返回。`issue_request()` 落盘证书并归档，同时从收件目录/旧 `incoming/` 删除请求。
- 新增请求类型时，记得同步 `validate_request_data()`、`_san_for()`、`_common_name()` 与 UI 菜单。

### 4.12 未索引包

- `packages.list_all(root)` = 已索引包 + `unindexed_packages(root)`；后者扫描 `<developer>/*.tar.gz`，按文件名/清单推导包名与版本，用开发者证书验签。
- 普通开发者发布：`publish_wizard(app, update_index=False)`，只签名并复制，不碰索引。
  文件选择优先 `packages.publish_candidates(root)`（扫描仓库内 `publish/` 与 `repo-tool/publish/`，按 mtime 倒序）；有文件时确认后批量发布并删除源文件，都为空才弹路径输入。
- 管理员加入索引：`PackageDetailView._add_to_index()` → `rebuild_and_sign()` → 校验该版本确实进入 `index.json`。
- 列表返回父视图时通过 `on_resume()` 重新加载（`App.pop()` 会调用父视图的该方法）。

### 4.13 私域管理与域证书

- 列表：`core/domains.py` 的 `list_domains(root)` 合并 `domain.yaml` 与 `outgoing/*.crt`，
  返回 `DomainEntry(name, index, cert_url, local_cert, cert, registered, revoked)`。
- 签发：`views/developers.py` 的 `sign_domain_wizard()`；只从请求列表选择。签发后如果有
  `indexUrl`/`certUrl`，调用 `domains.register_domain()` 写入 `domain.yaml`，再重建/签名索引。
- 登记：`views/domains.py` 的 `register_domain_wizard()`，用于补登记已签发证书。
- URL 推导：`domains.derive_urls(repo_url, github_url)`。
- 吊销：`views/domains.py` 的 `revoke_domain_wizard()`，写 `kind="domain"` 的 `revoke.json` 记录。
- 删除：`core/domains.remove_domain()` / `core/developers.delete_developer()`，删除后由视图重建并签名索引。
- 刷新日期：`core/revoke.touch()` 只改 `updatedAt`；主页调用 `views/common.update_revoke_list()` 重签。
- 新增域相关操作时放在 `DomainManagementView`，不要塞回「管理开发者」。

### 4.14 域证书签完后自动登记

目前 `sign_domain_wizard()` 只把证书写到 `outgoing/`。若要自动维护主仓库的 `domain.yaml`：

1. 读取 `domain.yaml`（`core/index._load_domains` 有读取逻辑可复用）；
2. 原子写回，保留其它字段与注释（PyYAML 会丢注释，必要时按行处理）；
3. 重建/签名索引；
4. 增加测试：重复登记、URL 校验。

---

## 5. 约定与安全红线

- **密钥**：私钥永不入库、永不打印、永不写日志。只从 `~/.hpm/keys/` 查找
  （`official.key`、`domain/<domain>/official.key`、`dev/<developer>/<developer>.key`）。
  新增功能如果读私钥，走 `views/common.py` 的 `official_key_ui` / `developer_key_ui`，
  以复用密码重试与缓存；不要重新从仓库目录找 key。
- **密码**：只用 `app.input(mask=True)` 或 `HPM_KEY_PASSWORD`；不要拼进命令行参数。
- **写文件**：统一用 `util.fs.atomic_write`；私钥 `0600`；证书/索引 `0644`。
- **路径**：开发者名/域名必须过 `NAME_RE` 与 `RESERVED_NAMES`；落盘前 `resolve()` 并确认在仓库根内。
- **Git**：只 `git add -- <明确路径>`；永不 `git add -A`、永不 force push；提交前检查 `*.key`。
- **取消**：用户 Esc/取消不应留下半成品；多步操作先校验再落盘。
- **错误**：可预期错误抛 `ToolError`（带 `hint`），不要 `print` 到终端；未知异常交给 `App` 记录日志。
- **终端**：进入备用屏后不要 `print`；退出/异常必须恢复终端（`App.run()` 的 `finally` 已处理）。
- **中文排版**：用 `text_width`/`ellipsize`，不要用字符数当列宽。

---

## 6. 测试扩展

### 6.1 pytest

`tests/conftest.py` 提供：

| fixture | 内容 |
|---------|------|
| `ca` | 一次性测试 CA：`(key, cert, pem)` |
| `repo` | 临时仓库根，设置 `HPM_REPO_ROOT`、`HPM_HOME` |
| `domain_repo` | 带域证书 `official.crt` 的私域仓库；域私钥写在临时 `HPM_HOME/keys/domain/test/official.key`，`HPM_OFFICIAL_CERT` 指向测试 CA |
| `developer_factory` | 生成并签发一个开发者证书，返回 `(dev_key, cert_path, cert_info)` |

新增 core 功能至少覆盖：

- 正常路径；
- 参数非法；
- 文件不存在/损坏；
- 签名/校验失败；
- 权限或路径越界（如适用）。

### 6.2 pty 端到端

`tests/pty_smoke.py` 用真实伪终端启动 `hrm`。新增场景模板：

```python
def scenario_foo() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "repo"; root.mkdir()
        home = base / "home"; home.mkdir()
        # 用 _make_domain_repo / _prepare_signed_repo / _make_request 准备状态
        session = Session(make_env(root, home), "--no-animation")
        session.wait_for("管理包")
        session.send("\r")
        session.wait_for("发布 / 签名包")
        ...
        session.send("q")
        session.proc.wait(timeout=3)
        session.drain(0.2)
        os.close(session.master)
```

注意：

- `wait_for()` 只匹配**本次等待期间新绘制**的内容；不要依赖历史屏幕；
- 不要用固定 `sleep` 猜状态，尽量 `wait_for` 文案；
- 完成后一定要 `wait` 子进程并关闭 pty；
- 把场景注册到 `main()` 的 `scenarios` 列表。

运行：

```bash
cd repo-tool
python3 -m pytest -q
python3 tests/pty_smoke.py
```

---

## 7. 调试与常用环境变量

| 变量 | 作用 |
|------|------|
| `HPM_HOME` | 覆盖 `~/.hpm`（`dev.json` 与 `keys/` 位置） |
| `HPM_REQUESTS_DIR` | 覆盖签名请求收件目录（默认 `repo-tool/requests/`） |
| `HPM_REPO_ROOT` | 覆盖仓库根（测试用） |
| `HPM_OFFICIAL_CERT` | 覆盖用于验证域证书的官方证书 |
| `HPM_ROOT_CA` | 覆盖内置根证书 PEM 文件 |
| `HPM_KEY_PASSWORD` | 非交互场景的私钥密码 |
| `HPM_ALLOW_UNENCRYPTED` | 测试用：跳过「未加密私钥」迁移提示 |
| `HPM_DISABLE_CENTER_SYNC` | 测试/离线：关闭开发者中心同步 |
| `HPM_APP_ID` / `HPM_APP_KEY` / `HPM_API_BASE` | 覆盖开发者中心应用凭据 |
| `HPM_DEV_BIN` | 指定登录程序 |
| `NO_COLOR` | 禁用颜色 |

日志：`~/.hpm/logs/repo-tool.log`（按天轮转，保留 7 天），只记操作、路径、错误，
不记 Token/密码/私钥。

常见问题：

- **改完界面出现残影/错位**：检查是否直接用了 `len()` 计算中文宽度，或绕过 `screen.put` 直接写 stdout；
- **退出后终端异常**：检查是否在 `app.suspend()` 后未 `resume()`，或自己调用了 `termios`；
- **循环导入**：`views` 之间不要互相 import，统一在函数内延迟导入（现有路由都是这样做的）；
- **初始化后没有「完成初始化」**：看 `HomeView._pending_init()` 与 `Role`，以及 `revoke.json.sig` 是否存在；
- **签名失败**：先用 `core/index.verify_index()` / `core/verify.run_all()` 定位，再检查私钥与证书是否配对。

---

## 8. 版本与兼容性

- 版本号在 `hpmrepo/__init__.py` 的 `__version__`；发版时同步 README/文档。
- `index.json` 保持 `schema: 2`；新增字段必须可选、可忽略。
- `revoke.json` 由 `hpm` 客户端按 §8.3（设计文档）验签；客户端未实现前，吊销只在服务端/仓库侧生效。
- 新增依赖必须写入 `requirements.txt`，并说明是否为可选。
- 保持 Python 3.10 兼容语法（不要用 3.11+ 独有的标准库行为；`tomllib` 已有回退逻辑）。

---

## 9. 提交前检查清单

- [ ] `python3 -m pytest -q` 全过
- [ ] `python3 tests/pty_smoke.py` 全过
- [ ] 新增/修改的路径没有泄露密钥、密码、Token
- [ ] 新功能在官方仓库、私域仓库、无 Token、未初始化四种状态下都不会崩
- [ ] 用户取消路径不产生半成品文件
- [ ] 文档（README / repo-tool.md / 本文件）已同步
- [ ] 如改索引/证书格式，已评估客户端兼容性
