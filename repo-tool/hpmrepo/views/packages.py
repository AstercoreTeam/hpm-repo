# -*- coding: utf-8 -*-
"""包管理界面与发布流程。"""

from __future__ import annotations

from pathlib import Path

from ..core import crypto, index as index_mod, packages
from ..repo import Role
from ..tui.screen import Screen, ellipsize, st, text_width
from ..tui.widgets import MenuItem, draw_footer, draw_table, draw_title
from ..util import log
from .base import MenuView, View
from .common import (
    commit_ui,
    developer_key_ui,
    official_key_ui,
    rebuild_and_sign,
    sign_existing,
    sync_delete_package_result,
    sync_package_result,
)


class PackagesView(MenuView):
    title = "管理包"
    subtitle = "签名 / 索引 / 发布"

    def __init__(self, app) -> None:
        super().__init__(app)
        self.refresh_items()

    def build_items(self) -> list[MenuItem]:
        return [
            MenuItem("包列表", "索引与未索引包", action="list"),
            MenuItem("发布包（仅签名入库）", "普通开发者可用，不更新索引", action="publish_only"),
            MenuItem("发布包并更新索引", "需要仓库 official 私钥", action="publish"),
            MenuItem("重建索引", "扫描仓库并生成", action="rebuild"),
            MenuItem("签名索引", "官方私钥", action="sign"),
            MenuItem("签名吊销列表", "官方私钥", action="sign_revoke"),
            MenuItem("校验仓库", "索引 / 包 / 证书", action="verify"),
            MenuItem("Git 同步", "提交与推送", action="git"),
            MenuItem("← 返回", "", action="back"),
        ]

    def header_lines(self) -> list[str]:
        ctx = self.app.ctx
        try:
            data = index_mod.load_index(ctx.root)
            count = index_mod.package_count(data)
            unindexed = len(packages.unindexed_packages(ctx.root))
        except Exception:  # noqa: BLE001
            count = unindexed = 0
        ok, detail = index_mod.verify_index(ctx.root)
        lines = [
            f"仓库: {ctx.root}",
            f"索引: {detail} · 已索引包 {count} · 未索引 {unindexed}",
        ]
        if self.app.dev and self.app.dev.developer:
            lines.append(f"当前开发者: {self.app.dev.developer}")
        return lines

    def on_select(self, item: MenuItem) -> None:
        ctx = self.app.ctx
        if item.action == "list":
            self.app.push(PackageListView(self.app))
        elif item.action == "publish_only":
            publish_wizard(self.app, update_index=False)
        elif item.action == "publish":
            publish_wizard(self.app, update_index=True)
        elif item.action == "rebuild":
            key = official_key_ui(self.app, ctx)
            if key is None:
                return
            data = rebuild_and_sign(self.app, ctx, key)
            self.app.toast(f"索引已重建并签名（{index_mod.package_count(data)} 个包）", "ok")
            commit_ui(self.app, ctx, "Rebuild index")
        elif item.action == "sign":
            key = official_key_ui(self.app, ctx)
            if key is None:
                return
            sign_existing(self.app, ctx, key)
            ok, detail = index_mod.verify_index(ctx.root)
            self.app.toast(detail, "ok" if ok else "error")
        elif item.action == "sign_revoke":
            key = official_key_ui(self.app, ctx)
            if key is None:
                return
            if not ctx.revoke_path.is_file():
                self.app.error("revoke.json 不存在", "请先在「管理开发者」中吊销证书。")
                return
            from ..core import revoke

            revoke.sign(ctx.root, key)
            self.app.toast("吊销列表已签名", "ok")
        elif item.action == "verify":
            self.app.push(VerifyView(self.app))
        elif item.action == "git":
            git_sync(self.app)
        elif item.action == "back":
            self.app.pop()
        self.refresh_items()


class PackageListView(View):
    def __init__(self, app) -> None:
        super().__init__(app)
        self.cursor = 0
        self.offset = 0
        self.rows: list[packages.PackageVersion] = []
        self.reload()

    def reload(self) -> None:
        try:
            self.rows = packages.list_all(self.app.ctx.root)
        except Exception:  # noqa: BLE001
            self.rows = []
        if self.cursor >= len(self.rows):
            self.cursor = max(0, len(self.rows) - 1)

    def _status(self, row: packages.PackageVersion) -> str:
        if not row.indexed:
            if row.revoked:
                return "未索引 / 证书已吊销"
            if not row.sig_ok:
                return "未索引 / " + (row.issues[0] if row.issues else "签名无效")
            if row.kind == "?":
                return "未索引 / 清单无效"
            return "未加入索引"
        if row.revoked:
            return "证书已吊销"
        if not row.installed:
            return "文件缺失"
        if not row.hash_ok:
            return "哈希不符"
        if not row.sig_ok:
            return "签名无效"
        return "OK"

    def render(self, screen: Screen) -> None:
        draw_title(screen, "包列表", f"{len(self.rows)} 个版本")
        headers = ["包名", "版本", "类型", "开发者", "状态"]
        name_w = max(14, min(28, max((text_width(r.package) for r in self.rows), default=14)))
        dev_w = max(8, min(16, max((text_width(r.developer) for r in self.rows), default=8)))
        ver_w = 10
        kind_w = 7
        status_w = max(10, screen.w - (name_w + dev_w + ver_w + kind_w + 4 + 8))
        widths = [name_w, ver_w, kind_w, dev_w, status_w]
        body = screen.h - 5
        if self.cursor < self.offset:
            self.offset = self.cursor
        elif self.cursor >= self.offset + body:
            self.offset = self.cursor - body + 1
        table_rows = [
            [
                r.package,
                r.version,
                r.kind,
                r.developer,
                self._status(r),
            ]
            for r in self.rows
        ]
        if not table_rows:
            screen.put(4, 3, "还没有包。可先使用「发布 / 签名包」。", st(fg="muted"))
        else:
            draw_table(
                screen,
                3,
                3,
                screen.w - 6,
                headers,
                table_rows,
                widths,
                cursor=self.cursor,
                offset=self.offset,
                max_rows=body,
            )
        tip = "Enter 详情 · ↑↓ 移动 · PgUp/PgDn · r 刷新 · Esc 返回"
        if self.rows:
            issues = sum(1 for r in self.rows if not r.ok)
            unindexed = sum(1 for r in self.rows if not r.indexed)
            prefix = []
            if issues:
                prefix.append(f"{issues} 个版本异常")
            if unindexed:
                prefix.append(f"{unindexed} 个未索引")
            if prefix:
                tip = " · ".join(prefix) + " · " + tip
        draw_footer(screen, tip)

    def handle_key(self, key) -> None:
        if key.name == "esc" or (key.name == "char" and key.char == "q"):
            self.app.pop()
            return
        if key.name == "char" and key.char == "r":
            self.reload()
            self.app.toast("已刷新", "ok", 1.2)
            return
        if not self.rows:
            return
        if key.name in ("up", "k"):
            self.cursor = max(0, self.cursor - 1)
        elif key.name in ("down", "j"):
            self.cursor = min(len(self.rows) - 1, self.cursor + 1)
        elif key.name == "pgup":
            self.cursor = max(0, self.cursor - 10)
        elif key.name == "pgdn":
            self.cursor = min(len(self.rows) - 1, self.cursor + 10)
        elif key.name == "home":
            self.cursor = 0
        elif key.name == "end":
            self.cursor = len(self.rows) - 1
        elif key.name in ("enter", "right"):
            self.app.push(PackageDetailView(self.app, self.rows[self.cursor]))

    def on_resume(self) -> None:
        self.reload()


class PackageDetailView(View):
    def __init__(self, app, row: packages.PackageVersion) -> None:
        super().__init__(app)
        self.row = row

    def render(self, screen: Screen) -> None:
        row = self.row
        draw_title(screen, f"{row.package} @ {row.version}", row.kind)
        lines = [
            f"包名:     {row.package}",
            f"版本:     {row.version}",
            f"类型:     {row.kind}   芯片: {row.chip}",
            f"开发者:   {row.developer}",
            f"描述:     {row.description or '-'}",
            f"许可:     {row.license or '-'}",
            f"大小:     {row.size} 字节",
            f"sha256:   {row.sha256}",
            f"文件:     {row.tar_path or '缺失'}",
            f"签名:     {row.sig_path or '缺失'}",
            "",
            f"状态:     {'正常' if row.ok else '异常'}",
            f"索引:     {'已加入' if row.indexed else '未加入'}",
        ]
        for issue in row.issues or []:
            lines.append(f"  - {issue}")
        y = 3
        for line in lines:
            screen.put(4, y, ellipsize(line, screen.w - 8), st(fg="text"))
            y += 1
        draw_footer(screen, "a 加入索引 · d 删除 · Esc 返回" if not row.indexed else "d 删除该版本 · Esc 返回")

    def handle_key(self, key) -> None:
        if key.name == "esc":
            self.app.pop()
        elif key.name == "char" and key.char == "a" and not self.row.indexed:
            self._add_to_index()
        elif key.name == "char" and key.char == "d":
            self._delete()

    def _delete(self) -> None:
        row = self.row
        if not row.indexed:
            if not self.app.confirm(
                "删除未索引包",
                [f"确定删除 {row.package}@{row.version} 的文件？", "不会修改索引。"],
                danger=True,
            ):
                return
            removed = packages.remove_package_files(row)
            self.app.toast(f"已删除 {len(removed)} 个文件", "ok")
            self.app.pop()
            return

        if not self.app.confirm(
            "删除版本",
            [
                f"确定删除 {row.package}@{row.version}？",
                "将删除 tar.gz 与 .sig，并重建索引。",
            ],
            danger=True,
        ):
            return
        ctx = self.app.ctx
        packages.remove_version(ctx.root, row.package, row.version)
        key = official_key_ui(self.app, ctx)
        if key is None:
            self.app.error("文件已删除，但索引未更新", "请稍后重新运行「重建索引」。")
            return
        rebuild_and_sign(self.app, ctx, key)
        log.log_action("package_delete", f"{row.package}@{row.version}")
        data = index_mod.load_index(ctx.root)
        remaining = (
            ((data.get("packages") or {}).get(row.package) or {}).get("versions") or {}
        )
        if remaining:
            note = "包仍有其他版本，未远程删除"
        else:
            note = sync_delete_package_result(self.app, row.package)
        self.app.toast(
            "已删除并重建索引" + (f" · {note}" if note else ""), "ok", 5
        )
        commit_ui(self.app, ctx, f"Remove {row.package} {row.version}")
        self.app.pop()

    def _add_to_index(self) -> None:
        row = self.row
        ctx = self.app.ctx
        if row.revoked:
            self.app.error("开发者证书已吊销，不能加入索引")
            return
        if not row.sig_ok:
            self.app.error("包签名无效", "请让开发者重新签名后再加入索引。")
            return
        if row.kind == "?":
            self.app.error("无法识别包清单", "tar.gz 内需要有 manifest.json / chip.json。")
            return
        key = official_key_ui(self.app, ctx)
        if key is None:
            return
        self.app.busy("加入索引", f"正在将 {row.package}@{row.version} 写入索引…")
        rebuild_and_sign(self.app, ctx, key)
        data = index_mod.load_index(ctx.root)
        versions = ((data.get("packages") or {}).get(row.package) or {}).get(
            "versions", {}
        )
        if row.version not in versions:
            self.app.error(
                "加入索引失败",
                "重建索引后仍未找到该包，请检查文件名与清单。",
            )
            return
        log.log_action("package_index", f"{row.package}@{row.version}")
        sync_note = sync_package_result(self.app, row.package, row.version)
        self.app.toast(
            f"已加入索引: {row.package}@{row.version}"
            + (f" · {sync_note}" if sync_note else ""),
            "ok",
            5,
        )
        commit_ui(self.app, ctx, f"Add {row.package} {row.version} to index")
        self.app.pop()


class VerifyView(View):
    def __init__(self, app) -> None:
        super().__init__(app)
        from ..core import verify

        self.checks = verify.run_all(app.ctx)
        self.offset = 0

    def render(self, screen: Screen) -> None:
        draw_title(screen, "仓库校验", "索引 / 包 / 证书 / Git")
        y = 2
        visible = screen.h - 5
        for check in self.checks[self.offset : self.offset + visible]:
            if check.name:
                mark = check.mark
                color = "green" if check.ok else ("yellow" if check.warn else "red")
                screen.put(3, y, f"[{mark}]", st(fg=color, bold=True))
                screen.put(9, y, check.name, st(fg="text", bold=True))
                screen.put(
                    9 + max(10, text_width(check.name) + 2),
                    y,
                    ellipsize(check.detail, screen.w - 14 - text_width(check.name)),
                    st(fg="muted"),
                )
                y += 1
            else:
                screen.put(9, y, ellipsize(check.detail, screen.w - 14), st(fg="red"))
                y += 1
        draw_footer(screen, "↑↓ 滚动 · r 重新校验 · Esc 返回")

    def handle_key(self, key) -> None:
        if key.name == "esc":
            self.app.pop()
        elif key.name == "char" and key.char == "r":
            from ..core import verify

            self.checks = verify.run_all(self.app.ctx)
        elif key.name in ("down", "j"):
            self.offset = min(max(0, len(self.checks) - 1), self.offset + 1)
        elif key.name in ("up", "k"):
            self.offset = max(0, self.offset - 1)


def publish_wizard(app, *, update_index: bool = True) -> None:
    ctx = app.ctx
    if ctx.role is Role.UNINITIALIZED:
        app.error("仓库未初始化", "请先完成私域仓库初始化。")
        return

    default_tar = packages.default_tarball(ctx.root)
    text = app.input(
        "发布包",
        "请输入 tar.gz 路径",
        default=str(default_tar) if default_tar else "publish/",
        hint="通常由 hpm-dev publish 产出 · Esc 取消",
    )
    if text is None:
        return
    tar = Path(text).expanduser()
    if tar.is_dir():
        candidates = sorted(tar.glob("*.tar.gz"))
        if not candidates:
            app.error(f"目录中没有 tar.gz: {tar}")
            return
        index = app.choose("选择包", [p.name for p in candidates])
        if index is None:
            return
        tar = candidates[index]
    if not tar.is_file():
        app.error(f"文件不存在: {tar}")
        return

    driver, chip, version = packages.parse_filename(tar.name)
    if not driver or not version:
        app.error("文件名不符合包命名规则", "应为 <driver>-<chip>-<version>.tar.gz 或 <chip>-<version>.tar.gz")
        return

    developer = (app.dev.developer if app.dev else "") or ""
    developer = app.input(
        "发布包", "开发者名（对应仓库子目录）", default=developer, hint="Esc 取消"
    )
    if developer is None:
        return
    developer = developer.strip()
    if not developer:
        app.error("开发者名不能为空")
        return
    from ..core import developers as dev_mod

    if not dev_mod.NAME_RE.match(developer) or developer in dev_mod.RESERVED_NAMES:
        app.error("开发者名不合法", "只能使用小写字母、数字、点、下划线、短横线。")
        return
    cert_path = packages.resolve_cert(ctx.root, developer)
    if cert_path is None:
        app.error(
            f"开发者 {developer} 没有证书",
            f"请先把 {developer}.crt 放到 {developer}/ 目录，或在「管理开发者」中签发。",
        )
        return

    dest_dir = ctx.root / developer
    dest_tar = dest_dir / tar.name
    if dest_tar.exists() and dest_tar.resolve() != tar.resolve():
        if not app.confirm("覆盖包", f"{dest_tar} 已存在，覆盖吗？", danger=True):
            return

    sig_path = tar.with_name(tar.name + ".sig")
    cert = crypto.load_cert(cert_path)
    need_signing = True
    if sig_path.is_file():
        valid = crypto.verify_signature_with_cert(cert, tar.read_bytes(), sig_path.read_bytes())
        if valid:
            need_signing = False
            app.toast("已有有效签名，跳过签名", "ok", 2)
        else:
            if not app.confirm("重新签名", "现有 .sig 无效，是否重新签名？"):
                return

    if need_signing:
        key = developer_key_ui(app, ctx, developer)
        if key is None:
            return
        app.busy("签名", f"正在签名 {tar.name} …")
        packages.sign_file(key, tar, sig_path)
        if not crypto.verify_signature_with_cert(cert, tar.read_bytes(), sig_path.read_bytes()):
            app.error("签名自检失败", "私钥与开发者证书不匹配。")
            return

    packages.install_package(ctx.root, tar, sig_path, developer)
    if not update_index:
        log.log_action("package_install", f"{tar.name} by {developer}")
        app.toast(f"已签名入库 {tar.name}，等待管理员加入索引", "ok", 5)
        app.info(
            "包已入库",
            [
                f"开发者: {developer}",
                f"包: {dest_dir / tar.name}",
                f"签名: {dest_dir / (tar.name + '.sig')}",
                "",
                "本操作没有更新索引。",
                "仓库管理员会在包列表中看到「未加入索引」，进入详情按 a 加入索引。",
            ],
        )
        return
    official = official_key_ui(app, ctx)
    if official is None:
        app.toast("包已入库，但索引未更新", "warn")
        return
    data = rebuild_and_sign(app, ctx, official)
    present = any(
        str(entry.get("url", "")).endswith(tar.name)
        for pkg in (data.get("packages") or {}).values()
        for entry in (pkg.get("versions") or {}).values()
    )
    if not present:
        app.error(
            "包没有进入索引",
            "请检查 tar.gz 内是否有 manifest.json / chip.json，以及文件名是否符合 <driver>-<chip>-<version>。",
        )
        return
    ok, detail = index_mod.verify_index(ctx.root)
    if not ok:
        app.error("发布后索引校验失败", detail)
        return

    matched_package = None
    for pkg_name, pkg in (data.get("packages") or {}).items():
        for ver, entry in (pkg.get("versions") or {}).items():
            if str(entry.get("url", "")).endswith(tar.name):
                matched_package = (pkg_name, ver)
                break
        if matched_package:
            break
    sync_note = ""
    if matched_package:
        sync_note = sync_package_result(app, matched_package[0], matched_package[1])

    log.log_action("package_publish", f"{tar.name} by {developer}")
    app.toast(
        f"已发布 {tar.name}" + (f" · {sync_note}" if sync_note else ""),
        "ok",
        5,
    )
    commit_ui(app, ctx, f"Publish {tar.name}")


def git_sync(app) -> None:
    from ..core import gitops

    ctx = app.ctx
    if not gitops.available():
        app.error("未安装 git")
        return
    if not gitops.is_repo(ctx.root):
        app.error("当前目录不是 Git 仓库", "私域仓库初始化时会自动创建。")
        return
    status = gitops.status(ctx.root)
    lines = [
        f"分支: {status.branch or '-'}",
        f"变更: {status.dirty} 项  领先: {status.ahead}  落后: {status.behind}",
        f"远程: {gitops.remote_url(ctx.root) or '未配置'}",
    ]
    changed = gitops.changed_paths(ctx.root)
    if changed:
        lines.append("")
        lines.extend(f"  {p}" for p in changed[:10])
        if len(changed) > 10:
            lines.append("  …")
        message = app.input("提交", "提交信息", default="Update repository")
        if message is None:
            return
        commit_ui(app, ctx, message, paths=changed)
    else:
        app.message("Git 状态", lines)
