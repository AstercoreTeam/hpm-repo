# -*- coding: utf-8 -*-
"""初始化私域仓库，以及收到证书后的收尾。"""

from __future__ import annotations

import re

from ..core import crypto, developers as dev_mod, gitops, index as index_mod, revoke
from ..repo import Role, detect
from ..tui.screen import Screen, ellipsize, st
from ..tui.widgets import draw_footer, draw_title
from ..util import log
from ..util.fs import atomic_write, ensure_private
from ..util.jsonc import dumps
from .base import View
from .common import commit_ui, official_key_ui

DOMAIN_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

GITIGNORE = """# HPM repo
*.key
publish/
incoming/
__pycache__/
*.pyc
.hpm/
"""

ABOUT = """# HPM 私域仓库

本仓库由 hrm（HPM Repo Manager）初始化。

- `index.json` / `index.json.sig`：仓库索引与签名
- `official.crt`：仓库证书（由官方仓库签发）
- `revoke.json` / `revoke.json.sig`：证书吊销列表
- `<developer>/`：开发者证书与包
- `incoming/`：待处理的签名请求
- `requests/`：已处理请求归档

私钥（`*.key`）不入库。
"""


class InitRepoView(View):
    def __init__(self, app) -> None:
        super().__init__(app)
        self.done = False

    def render(self, screen: Screen) -> None:
        draw_title(screen, "初始化私域仓库", "生成密钥与签名请求")
        ctx = self.app.ctx
        lines = [
            f"仓库目录: {ctx.root}",
            "",
            "将执行：",
            "  1. 生成 Git 仓库（main 分支）与 .gitignore",
            "  2. 生成 official Ed25519 密钥对（私钥用密码加密）",
            "  3. 生成 official.request.json / .txt（发送给官方签发）",
            "  4. 生成空 index.json 与 revoke.json",
            "  5. 首次提交，并可选配置远程地址",
            "",
            "按 Enter 开始，Esc 返回。",
        ]
        if ctx.role is not Role.UNINITIALIZED:
            lines.insert(0, f"当前仓库角色为「{ctx.role.label}」，无需初始化。")
            lines.insert(1, "")
        y = 3
        for line in lines:
            screen.put(4, y, ellipsize(line, screen.w - 8), st(fg="text"))
            y += 1
        draw_footer(screen, "Enter 开始 · Esc 返回")

    def handle_key(self, key) -> None:
        if key.name == "esc":
            self.app.pop()
        elif key.name == "enter":
            if self.app.ctx.role is not Role.UNINITIALIZED:
                self.app.info("当前目录已经初始化过了。")
                return
            self._run()

    def _run(self) -> None:
        app = self.app
        ctx = app.ctx
        if ctx.index_path.exists() or ctx.official_cert_path.exists():
            app.error(
                "目录中已存在 index.json 或 official.crt",
                "为避免覆盖已有仓库，初始化已中止。",
            )
            return

        dev = app.dev
        default_domain = (dev.developer if dev else "") or ""
        domain = app.input(
            "初始化私域仓库",
            "域名（小写字母、数字、点、下划线、短横线）",
            default=default_domain,
            hint="会写入证书 SAN: hpm://domain/<name>",
        )
        if domain is None:
            return
        domain = domain.strip()
        if not DOMAIN_RE.match(domain):
            app.error("域名不合法", "只能使用小写字母、数字、点、下划线、短横线。")
            return

        email = app.input(
            "初始化私域仓库",
            "联系邮箱（可留空）",
            default="",
            hint="仅用于签名请求，不会写入证书",
        )
        if email is None:
            return

        remote_default = (dev.repo_url if dev else "") or ""
        remote = app.input(
            "初始化私域仓库",
            "Git 远程地址（可留空，稍后再加）",
            default=remote_default,
            hint="支持 https 或 ssh",
        )
        if remote is None:
            return

        password = self._ask_password()
        if password is None:
            return

        summary = [
            f"目录: {ctx.root}",
            f"域名: {domain}",
            f"邮箱: {email or '-'}",
            f"远程: {remote or '暂不配置'}",
            f"私钥密码: {'已设置' if password else '空密码（不推荐）'}",
            "",
            "确认开始初始化？",
        ]
        if not app.confirm("初始化私域仓库", summary):
            return

        self._execute(domain, email, remote, password)
        self.done = True
        app.home()

    def _ask_password(self) -> str | None:
        for attempt in range(3):
            first = self.app.input(
                "私钥密码",
                "为 official.key 设置密码（至少 8 位，可留空）",
                mask=True,
                hint="密码只保存在内存，不写入磁盘 · Esc 取消",
            )
            if first is None:
                return None
            if len(first) < 8:
                if not self.app.confirm(
                    "密码过短",
                    [
                        "密码少于 8 位，私钥保护强度不足。",
                        "仍然使用这个密码吗？",
                    ],
                    danger=True,
                ):
                    continue
            second = self.app.input(
                "确认密码", "请再次输入密码", mask=True, hint="Esc 取消"
            )
            if second is None:
                return None
            if first != second:
                self.app.error("两次输入的密码不一致")
                continue
            return first
        return None

    def _execute(self, domain: str, email: str, remote: str, password: str) -> None:
        app = self.app
        ctx = app.ctx
        root = ctx.root
        app.busy("初始化", "正在生成密钥与仓库骨架…")

        # 1. Git 骨架（保留已有 .gitignore 规则，只补充缺失项）
        ignore_path = root / ".gitignore"
        if ignore_path.is_file():
            existing = ignore_path.read_text(encoding="utf-8", errors="ignore")
            have = {line.strip() for line in existing.splitlines()}
            extra = [
                line for line in GITIGNORE.splitlines()
                if line.strip() and not line.startswith("#") and line.strip() not in have
            ]
            if extra:
                atomic_write(
                    ignore_path,
                    existing.rstrip() + "\n" + "\n".join(extra) + "\n",
                    mode=0o644,
                )
        else:
            atomic_write(ignore_path, GITIGNORE, mode=0o644)
        if not (root / "README.md").exists():
            atomic_write(root / "README.md", ABOUT, mode=0o644)

        # 2. 密钥
        private_pem, public_pem = crypto.generate_ed25519_keypair(password or None)
        atomic_write(ctx.official_key_path, private_pem, mode=0o600)
        ensure_private(ctx.official_key_path)
        atomic_write(root / "official.pub", public_pem, mode=0o644)

        # 3. 签名请求
        key = crypto.load_private_key(ctx.official_key_path, password or None)
        req = dev_mod.build_request(
            key,
            kind="domain",
            name=domain,
            email=email,
            github_url=(app.dev.github_url if app.dev else ""),
        )
        json_path, txt_path = dev_mod.write_request(root, req, stem="official")

        # 4. 空索引与吊销列表
        index_mod.write_empty_index(root)
        revoke.save(root, revoke.empty_doc())
        (root / "incoming").mkdir(exist_ok=True)
        (root / "requests").mkdir(exist_ok=True)
        (root / "outgoing").mkdir(exist_ok=True)

        # 5. Git 提交
        app.busy("初始化", "正在创建 Git 提交…")
        if not gitops.available():
            app.toast("未安装 git，已跳过 Git 初始化", "warn")
        else:
            if not gitops.is_repo(root):
                gitops.init(root)
            paths = [".gitignore", "README.md", "index.json", "revoke.json",
                     "official.pub", json_path.name, txt_path.name]
            if (root / "repo-tool").is_dir():
                paths.append("repo-tool")
            try:
                gitops.add(root, [p for p in paths if (root / p).exists()])
                result = gitops.commit(root, "Initialize private HPM repository")
                if not result.ok:
                    app.toast(
                        "Git 提交失败（请检查 user.name / user.email）",
                        "warn",
                        5,
                    )
            except Exception as exc:  # noqa: BLE001
                app.toast(f"Git 提交失败: {exc}", "warn", 5)

        if remote and gitops.available():
            try:
                gitops.add_remote(root, "origin", remote)
                if app.confirm(
                    "推送远程",
                    [f"远程: {remote}", "现在把初始提交推送到远程仓库吗？"],
                ):
                    pushed = gitops.push(root, "origin", "main")
                    if pushed.ok:
                        app.toast("已推送到远程", "ok")
                    else:
                        app.error("推送失败", pushed.summary())
            except Exception as exc:  # noqa: BLE001
                app.toast(f"设置远程失败: {exc}", "warn", 5)

        log.log_action("repo_init", f"{root} domain={domain}")
        app.toast("初始化完成", "ok", 4)
        app.info(
            "初始化完成",
            [
                f"签名请求: {json_path}",
                f"人读版本: {txt_path}",
                "",
                "下一步：",
                "1. 把 official.request.json 发给官方仓库维护者；",
                "2. 收到 official.crt 后放到仓库根目录；",
                "3. 在主页选择「完成初始化」，签名索引与吊销列表。",
                "",
                "完成后可运行 git push 同步到远程仓库。",
            ],
        )


class FinishInitView(View):
    def __init__(self, app) -> None:
        super().__init__(app)
        self.info = self._inspect()

    def _inspect(self) -> list[str]:
        ctx = self.app.ctx
        lines = [f"仓库: {ctx.root}", f"角色: {ctx.role.label}"]
        if ctx.cert:
            lines.append(f"证书: {ctx.cert.san[0] if ctx.cert.san else '-'} · 剩余 {ctx.cert.days_left} 天")
            lines.append(f"序列号: {ctx.cert.serial}")
        lines.append(f"index.json: {'存在' if ctx.index_path.is_file() else '缺失'}")
        lines.append(f"index.json.sig: {'存在' if ctx.index_sig_path.is_file() else '缺失'}")
        lines.append(f"revoke.json: {'存在' if ctx.revoke_path.is_file() else '缺失'}")
        lines.append(f"revoke.json.sig: {'存在' if ctx.revoke_sig_path.is_file() else '缺失'}")
        lines.append(f"official.key: {'存在' if ctx.official_key_path.is_file() else '缺失'}")
        return lines

    def render(self, screen: Screen) -> None:
        draw_title(screen, "完成初始化", "导入证书后签名索引")
        y = 3
        for line in self.info:
            screen.put(4, y, ellipsize(line, screen.w - 8), st(fg="text"))
            y += 1
        y += 1
        hint = [
            "将使用 official.key 重新签名 index.json 与 revoke.json。",
            "如果 official.crt 还没有放到仓库根目录，请先放好。",
            "",
            "按 Enter 开始，Esc 返回。",
        ]
        for line in hint:
            screen.put(4, y, ellipsize(line, screen.w - 8), st(fg="muted"))
            y += 1
        draw_footer(screen, "Enter 签名 · Esc 返回")

    def handle_key(self, key) -> None:
        if key.name == "esc":
            self.app.pop()
        elif key.name == "enter":
            self._finish()

    def _finish(self) -> None:
        app = self.app
        ctx = app.ctx
        if not ctx.official_cert_path.is_file():
            app.error("缺少 official.crt", "请把官方签发回来的证书放到仓库根目录。")
            return
        role_ctx = detect(ctx.root)
        if role_ctx.role is Role.INVALID_CERT:
            app.error("official.crt 校验失败", role_ctx.cert_error or "无法解析")
            return
        key = official_key_ui(app, ctx)
        if key is None:
            return
        app.busy("签名", "正在签名索引与吊销列表…")
        if not ctx.index_path.is_file():
            index_mod.rebuild(ctx.root)
        if not ctx.revoke_path.is_file():
            revoke.save(ctx.root, revoke.empty_doc(role_ctx.cert))
        else:
            doc = revoke.load(ctx.root)
            doc["issuer"] = {
                "san": role_ctx.cert.san[0] if role_ctx.cert and role_ctx.cert.san else "",
                "certSha256": role_ctx.cert.fingerprint if role_ctx.cert else "",
            }
            revoke.save(ctx.root, doc)
        index_mod.sign_index(ctx.root, key)
        revoke.sign(ctx.root, key)
        ok_index, detail_index = index_mod.verify_index(ctx.root)
        ok_revoke, detail_revoke = revoke.verify(ctx.root)
        if not (ok_index and ok_revoke):
            app.error(
                "签名校验失败",
                f"index: {detail_index}；revoke: {detail_revoke}",
            )
            return
        log.log_action("repo_finish", str(ctx.root))
        app.toast("初始化完成，索引与吊销列表已签名", "ok", 4)
        commit_ui(app, ctx, "Sign index and revocation list")
        app.home()
