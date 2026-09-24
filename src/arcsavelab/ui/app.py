from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.events import Resize
from textual.widgets import Button, DataTable, Footer, Input, Label, OptionList, Static
from textual.widgets.option_list import Option

from arcsavelab.i18n import Translator
from arcsavelab.interface import (
    Availability,
    BrowseQuery,
    CommitReceipt,
    CommitRequest,
    ItemView,
    OpenRequest,
    Operation,
    Redo,
    RepairIntegrity,
    Section,
    SetDeviceIdentity,
    Undo,
    WorkspaceView,
)
from arcsavelab.session import SaveSession
from arcsavelab.workspace import WorkspaceLifecycle

from .backups import BackupScreen
from .common import (
    SECTION_NAMES,
    availability_rich,
    editable,
    error_text,
    field_rich,
    field_text,
    local,
)
from .dialogs import ConfirmScreen, IdentityScreen, OpenScreen, ReviewScreen
from .editor import EditScreen
from .table import ItemTable


class ArcSaveLabApp(App[int]):
    """An explicit workspace lifecycle and isolated edit/review dialogs.

    Selection is always an item ID, never a row number. Search and navigation
    reset batch selection; rerenders preserve the cursor if that ID still exists.
    """

    TITLE = "ArcSaveLab"
    CSS_PATH = "styles.tcss"
    BINDINGS = [
        Binding("ctrl+o", "open", "Open"),
        Binding("ctrl+s", "review", "Review"),
        Binding("ctrl+z", "undo", "Undo"),
        Binding("ctrl+y", "redo", "Redo", show=False),
        Binding("ctrl+f", "search", "Search"),
        Binding("space", "mark", "Mark", show=False),
        Binding("ctrl+a", "mark_all", "Mark all", show=False),
        Binding("ctrl+b", "batch", "Batch", show=False),
        Binding("ctrl+i", "identity", "Identity", show=False),
        Binding("ctrl+r", "repair", "Repair", show=False),
        Binding("ctrl+h", "backups", "Backups", show=False),
        Binding("f1", "help", "Help"),
        Binding("ctrl+q", "quit", "Quit"),
        Binding("ctrl+c", "quit", "Quit", show=False, priority=True),
    ]

    def __init__(self, request: OpenRequest | None = None) -> None:
        super().__init__()
        self.lifecycle = WorkspaceLifecycle(request or OpenRequest((), locale="zh-Hans"))
        self.tr = Translator(self.request.locale)
        self.workspace: WorkspaceView | None = None
        self.page = "overview"
        self.items: dict[str, ItemView] = {}
        self.selected_id: str | None = None
        self.marked: set[str] = set()
        self.section_editable = False
        self._rendering = False
        self.selection_anchor: str | None = None

    @property
    def request(self) -> OpenRequest:
        return self.lifecycle.request

    @property
    def session(self) -> SaveSession | None:
        return self.lifecycle.session

    @property
    def last_receipt(self) -> CommitReceipt | None:
        return self.lifecycle.last_receipt

    def compose(self) -> ComposeResult:
        with Horizontal(id="brand-bar"):
            yield Label("◈  ArcSaveLab", id="brand")
            yield Static("", id="tagline")
            yield Static(self.request.game_version, id="version-badge")
        with Horizontal(id="toolbar"):
            yield Button(id="open", variant="primary")
            yield Button(id="undo")
            yield Button(id="redo")
            yield Button(id="review", variant="success")
            yield Button("中 / EN", id="language")
        with Horizontal(id="workbench"):
            with Vertical(id="sidebar"):
                yield Static("WORKSPACE", id="nav-label", classes="eyebrow")
                yield OptionList(id="navigation")
            with Vertical(id="main"):
                yield Static("", id="page-title", markup=False)
                with VerticalScroll(id="overview"):
                    yield Static("", id="workspace-summary", markup=False)
                    with Horizontal(id="workspace-metrics"):
                        yield Static("", id="metric-files", classes="metric", markup=False)
                        yield Static("", id="metric-draft", classes="metric", markup=False)
                        yield Static("", id="metric-check", classes="metric", markup=False)
                    yield Static("", id="overview-content", markup=False)
                    with Horizontal(id="maintenance-actions"):
                        yield Button(id="identity")
                        yield Button(id="repair")
                        yield Button(id="backups")
                with Vertical(id="browser"):
                    with Horizontal(id="search-row"):
                        yield Input(id="search", placeholder="Search")
                        yield Button("×", id="clear-search")
                    yield Static("", id="list-meta", classes="muted", markup=False)
                    yield ItemTable(
                        id="items",
                        cursor_type="row",
                        zebra_stripes=True,
                        cursor_foreground_priority="renderable",
                    )
                    yield Static("", id="empty", classes="empty", markup=False)
                    yield Static("", id="item-detail", classes="muted", markup=False)
                    with Horizontal(id="list-actions"):
                        yield Button(id="edit", variant="primary")
                        yield Button(id="batch")
                        yield Static("", id="batch-status", markup=False)
        yield Static("", id="status", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self.theme = "textual-dark"
        self._labels()
        self.query_one("#items", DataTable).add_columns("", "Item", "Value", "State")
        if self.request.sources:
            self.open_workspace(self.request)
        else:
            self.refresh_workspace()

    def on_resize(self, event: Resize) -> None:
        self.screen.set_class(event.size.width < 100, "compact")

    def on_unmount(self) -> None:
        self.lifecycle.close(discard=True)

    def _labels(self) -> None:
        pairs = {
            "open": ("Open", "打开"),
            "undo": ("Undo", "撤销"),
            "redo": ("Redo", "重做"),
            "review": ("Review & save", "审阅保存"),
            "edit": ("Edit ↵", "编辑 ↵"),
            "batch": ("Batch edit", "批量编辑"),
            "identity": ("Device identity", "设备身份"),
            "repair": ("Repair integrity", "修复摘要"),
            "backups": ("Backups", "备份历史"),
        }
        for widget_id, labels in pairs.items():
            self.query_one(f"#{widget_id}", Button).label = local(self.tr, *labels)
        self.query_one("#search", Input).placeholder = local(
            self.tr, "Search names, IDs or difficulty…  Ctrl+F", "搜索名称、ID 或难度…  Ctrl+F"
        )
        self.query_one("#nav-label", Static).update(local(self.tr, "WORKSPACE", "工作区"))
        self.query_one("#tagline", Static).update(
            local(
                self.tr, "LOCAL FIRST  /  EDIT · REVIEW · SAVE", "本地优先  /  编辑 · 审阅 · 保存"
            )
        )
        self.query_one("#clear-search", Button).tooltip = local(self.tr, "Clear search", "清空搜索")
        self.query_one("#items").tooltip = None
        for key, message_id in {
            "ctrl+o": "action.open",
            "ctrl+s": "action.preview",
            "ctrl+z": "action.undo",
            "ctrl+f": "action.search",
            "ctrl+q": "action.quit",
        }.items():
            self._bindings.key_to_bindings[key] = [
                replace(binding, description=self.tr(message_id))
                for binding in self._bindings.get_bindings_for_key(key)
            ]
        self._bindings.key_to_bindings["f1"] = [
            Binding("f1", "help", local(self.tr, "Help", "帮助"))
        ]
        self.refresh_bindings()
        nav = self.query_one("#navigation", OptionList)
        nav.clear_options()
        nav.add_option(Option(local(self.tr, "◈  Overview", "◈  总览"), id="overview"))
        for number, (section, names) in enumerate(SECTION_NAMES.items(), 1):
            nav.add_option(Option(f"{number:02} {local(self.tr, *names)}", id=section.value))
        nav.highlighted = (
            0 if self.page == "overview" else list(SECTION_NAMES).index(Section(self.page)) + 1
        )

    def open_workspace(self, request: OpenRequest) -> None:
        try:
            self.lifecycle.open(request, discard=True)
        except Exception as exc:
            self.show_error(exc)
            return
        self.marked.clear()
        self.selected_id = None
        self.refresh_workspace()
        self.notify(local(self.tr, "Workspace opened", "工作区已打开"))

    def refresh_workspace(self) -> None:
        self.workspace = self.session.inspect() if self.session else None
        self.query_one("#version-badge", Static).update(self.request.game_version)
        self.query_one("#undo", Button).disabled = not self.workspace or not self.workspace.can_undo
        self.query_one("#redo", Button).disabled = not self.workspace or not self.workspace.can_redo
        self.query_one("#review", Button).disabled = self.session is None
        for widget_id in ("identity", "repair", "backups"):
            self.query_one(f"#{widget_id}", Button).disabled = self.session is None
        self.render_page()
        dirty = self.lifecycle.has_uncommitted_changes
        state = (
            local(self.tr, "● Unsaved changes", "● 有未保存修改")
            if dirty
            else local(self.tr, "○ No pending changes", "○ 无待保存修改")
        )
        if self.workspace:
            files = local(self.tr, "files", "个文件")
            state += f"  ·  {len(self.workspace.sources)} {files}  ·  r{self.workspace.revision}"
        if self.lifecycle.reload_error:
            state = error_text(self.lifecycle.reload_error, self.tr)
        self.query_one("#status", Static).update(state)

    def render_page(self) -> None:
        overview = self.page == "overview" or self.session is None
        self.query_one("#overview").display = overview
        self.query_one("#browser").display = not overview
        if overview:
            self.render_overview()
            return
        assert self.session is not None
        section = Section(self.page)
        self.query_one("#page-title", Static).update(local(self.tr, *SECTION_NAMES[section]))
        query = self.query_one("#search", Input).value
        # Ask the core for its actual section size; never silently truncate at 100 rows.
        summary = next(s for s in self.session.inspect().sections if s.section == section)
        view = self.session.inspect(
            BrowseQuery(section, search=query, limit=max(1, summary.total))
        ).sections[0]
        self.section_editable = view.availability is Availability.ENABLED
        self.items = {item.id: item for item in view.items}
        self.marked.intersection_update(self.items)
        if self.selected_id not in self.items:
            self.selected_id = next(iter(self.items), None)
        table = self.query_one("#items", ItemTable)
        self._rendering = True
        table.clear(columns=True)
        if section is Section.SCORES:
            table.add_columns(
                "",
                self.tr("ui.item"),
                self.tr("field.score"),
                local(self.tr, "PURE (+ shiny)", "PURE（大 P）"),
                "FAR",
                "LOST",
                self.tr("field.clear_type"),
            )
        else:
            table.add_columns("", self.tr("ui.item"), self.tr("field.value"), self.tr("ui.state"))
        for item in self.items.values():
            if section is Section.SCORES:
                fields = {field.id: field for field in item.fields}
                table.add_row(
                    "◆" if item.id in self.marked else "·",
                    Text(item.label),
                    f"{fields['score'].value:,}",
                    f"{fields['pure'].value} (+{fields['shiny_pure'].value})",
                    str(fields["far"].value),
                    str(fields["lost"].value),
                    Text(field_text(fields["clear_type"], self.tr)),
                    key=item.id,
                )
                continue
            availability = item.availability if self.section_editable else view.availability
            values = Text()
            if section is Section.ACCOUNT_DIAGNOSTICS:
                values.append(local(self.tr, "View in details", "详情中查看"), style="#a8b9d6")
            else:
                for field in [field for field in item.fields if not field.detail_only][:3]:
                    if values:
                        values.append(" · ")
                    values.append(f"{self.tr(field.label_id)}: ")
                    values.append_text(field_rich(field, self.tr))
            table.add_row(
                "◆" if item.id in self.marked else "·",
                Text(item.label),
                values[:160],
                availability_rich(availability, self.tr),
                key=item.id,
            )
        table.configure_widths("scores" if section is Section.SCORES else "general")
        table._resize_tooltip = local(
            self.tr,
            "Drag a header separator to resize; double-click to fit the longest value",
            "拖动表头分隔线调整列宽；双击分隔线自动适配最长内容",
        )
        if self.selected_id in self.items:
            table.move_cursor(
                row=list(self.items).index(self.selected_id), animate=False, scroll=False
            )
        self._rendering = False
        empty = not self.items
        table.display = not empty
        self.query_one("#empty").display = empty
        self.query_one("#empty", Static).update(
            local(
                self.tr,
                "No matching records. Try another search or open the required save file.",
                "没有匹配项目。请调整搜索词，或打开该分类所需的存档文件。",
            )
        )
        reason = self.tr(f"status.{view.availability.value}")
        if view.blocked_by:
            reason += " · " + ", ".join(view.blocked_by)
        self.query_one("#list-meta", Static).update(
            f"{len(self.items)} / {summary.total}  ·  {reason}  ·  "
            + local(self.tr, "Double-click / Enter for details", "双击 / Enter 查看详情")
        )
        self.render_selection()

    def render_overview(self) -> None:
        self.query_one("#page-title", Static).update(
            local(self.tr, "Your save, under control.", "让存档管理井然有序。")
        )
        sources = len(self.workspace.sources) if self.workspace else 0
        diagnostics = len(self.workspace.diagnostics) if self.workspace else 0
        dirty = self.lifecycle.has_uncommitted_changes
        self.query_one("#workspace-summary", Static).update(
            local(self.tr, "A quiet space for your next change.", "每一步修改，都有迹可循。")
        )
        self.query_one("#metric-files", Static).update(
            local(
                self.tr,
                f"{sources:02}  CONNECTED\nSave components",
                f"{sources:02}  已连接\n存档文件",
            )
        )
        self.query_one("#metric-draft", Static).update(
            local(self.tr, "●  DRAFT\nReview before saving", "●  有草稿\n审阅后再保存")
            if dirty
            else local(self.tr, "○  UNCHANGED\nNothing pending", "○  未修改\n没有待保存内容")
        )
        self.query_one("#metric-check", Static).update(
            local(
                self.tr,
                f"{diagnostics:02}  NOTICES\nDetails below",
                f"{diagnostics:02}  条提示\n在下方查看详情",
            )
            if self.workspace
            else local(self.tr, "—  WAITING\nOpen to inspect", "—  待检查\n打开后自动诊断")
        )
        if self.lifecycle.reload_error:
            body = error_text(self.lifecycle.reload_error, self.tr)
        elif not self.workspace:
            body = local(
                self.tr,
                "LOCAL FIRST\n\nOpen a save folder or select files individually.\n"
                "Settings, scores, unlocks and missions can be opened independently.\n\n"
                "01  Open       Ctrl+O\n02  Explore    Choose a section and search\n"
                "03  Edit       Enter opens an isolated draft\n"
                "04  Review     Ctrl+S, then export or replace\n\n"
                "Nothing is written until you explicitly save.\nPress F1 for keyboard shortcuts.",
                "本地优先\n\n打开存档文件夹，或分别选择文件。\n设置、成绩、解锁和任务支持独立打开。\n\n"
                "01  打开       Ctrl+O\n02  浏览       选择左侧分类，搜索目标项目\n"
                "03  编辑       Enter 打开独立草稿；可撤销、重做\n"
                "04  审阅       Ctrl+S，选择导出或备份后覆盖\n\n"
                "显式保存前，修改只留在内存中。\n按 F1 查看快捷键。",
            )
        else:
            lines = [local(self.tr, "CONNECTED FILES", "已连接的存档"), ""]
            for kind, path in self.workspace.sources.items():
                lines.extend([f"● {self.tr('source.' + kind.value)}", f"  {path}", ""])
            lines.extend([local(self.tr, "DIAGNOSTICS", "诊断"), ""])
            for issue in self.workspace.diagnostics:
                reason = issue.arguments.get("reason", issue.arguments.get("area", ""))
                lines.append(f"{issue.severity.upper()} · {issue.code} {reason}")
            if not self.workspace.diagnostics:
                lines.append(
                    local(
                        self.tr,
                        "No structural or integrity mismatch detected.",
                        "未检出结构或摘要不一致。",
                    )
                )
            lines.extend(
                [
                    "",
                    local(
                        self.tr,
                        "Device-bound fields need an identity. "
                        "Repair schedules digest regeneration; it is undoable.",
                        "设备绑定字段需要身份信息；修复摘要会暂存重算请求，支持撤销。",
                    ),
                ]
            )
            if self.last_receipt:
                lines.extend(["", local(self.tr, "LAST VERIFIED SAVE", "最近一次已验证保存")])
                lines.extend(str(a.destination) for a in self.last_receipt.artifacts)
                if self.last_receipt.backup_directory:
                    lines.append(
                        local(self.tr, "Backup: ", "备份：")
                        + str(self.last_receipt.backup_directory)
                    )
            body = "\n".join(lines)
        self.query_one("#overview-content", Static).update(body)

    def render_selection(self) -> None:
        item = self.items.get(self.selected_id or "")
        can_edit = bool(
            item
            and self.section_editable
            and item.availability is Availability.ENABLED
            and any(editable(f) for f in item.fields)
        )
        self.query_one("#edit", Button).disabled = not can_edit
        self.query_one("#batch", Button).disabled = not self.marked
        self.query_one("#item-detail", Static).update(
            f"{item.label}\n{item.id}  ·  "
            + local(self.tr, "Double-click / Enter for explanation", "双击 / Enter 查看说明")
            if item
            else ""
        )
        self.query_one("#batch-status", Static).update(
            local(
                self.tr,
                f"{len(self.marked)} marked · Space / Ctrl+A",
                f"已选 {len(self.marked)} 项 · Space / Ctrl+A",
            )
        )

    @on(OptionList.OptionSelected, "#navigation")
    def navigate(self, event: OptionList.OptionSelected) -> None:
        if event.option.id:
            self.page = event.option.id
            self.marked.clear()
            self.selected_id = None
            self.query_one("#search", Input).value = ""
            self.render_page()

    @on(Input.Changed, "#search")
    def search_changed(self) -> None:
        self.marked.clear()
        self.render_page()

    @on(DataTable.RowHighlighted, "#items")
    def highlight(self, event: DataTable.RowHighlighted) -> None:
        # Queued highlights can outlive their table during shutdown or a dialog.
        if self._rendering or not event.data_table.is_attached or not self.query("#edit"):
            return
        key = event.row_key.value
        if key in self.items:
            self.selected_id = key
            self.render_selection()

    @on(ItemTable.SelectionClick, "#items")
    def selection_click(self, event: ItemTable.SelectionClick) -> None:
        ids = list(self.items)
        if event.item_id not in self.items:
            return
        if event.shift:
            anchor = self.selection_anchor if self.selection_anchor in ids else event.item_id
            lo, hi = sorted((ids.index(anchor), ids.index(event.item_id)))
            selected = set(ids[lo : hi + 1])
            self.marked = (self.marked | selected) if event.ctrl else selected
        elif event.ctrl:
            self.marked.symmetric_difference_update({event.item_id})
        else:
            self.selection_anchor = event.item_id
            self.marked = {event.item_id}
        self.selected_id = event.item_id
        self.render_selection()

    @on(DataTable.RowSelected, "#items")
    def select_row(self, event: DataTable.RowSelected) -> None:
        if event.row_key.value in self.items:
            self.selected_id = event.row_key.value
            self.action_edit()

    @on(Button.Pressed)
    def button_pressed(self, event: Button.Pressed) -> None:
        actions: dict[str, Any] = {
            "open": self.action_open,
            "undo": self.action_undo,
            "redo": self.action_redo,
            "review": self.action_review,
            "language": self.action_language,
            "edit": self.action_edit,
            "batch": self.action_batch,
            "identity": self.action_identity,
            "repair": self.action_repair,
            "backups": self.action_backups,
            "clear-search": self.clear_search,
        }
        action = actions.get(event.button.id or "")
        if action:
            action()

    def action_open(self) -> None:
        if self.lifecycle.has_uncommitted_changes:
            self.push_screen(
                ConfirmScreen(
                    self.tr("ui.replace_workspace"),
                    self.tr("ui.discard_workspace_warning"),
                    self.tr,
                ),
                lambda approved: self._pick_files() if approved else None,
            )
        else:
            self._pick_files()

    def _pick_files(self) -> None:
        self.push_screen(OpenScreen(self.request, self.tr), self._picked_files)

    def _picked_files(self, request: OpenRequest | None) -> None:
        if request:
            self.open_workspace(request)

    def action_edit(self) -> None:
        if not self.session or self.page == "overview":
            return
        item = self.items.get(self.selected_id or "")
        if item:
            if self.page == Section.ACCOUNT_DIAGNOSTICS.value:
                item = self.session.inspect_account_detail(item.id)
            self.push_screen(
                EditScreen(
                    (item,),
                    self.session.revision,
                    self.tr,
                    self.session.perform,
                    read_only=not self.section_editable
                    or item.availability is not Availability.ENABLED,
                ),
                self._edited,
            )

    def action_batch(self) -> None:
        if (
            not self.session
            or not self.marked
            or self.page == "overview"
            or not self.section_editable
        ):
            return
        items = [item for key, item in self.items.items() if key in self.marked]
        if not items:
            return
        self.push_screen(
            EditScreen(items, self.session.revision, self.tr, self.session.perform), self._edited
        )

    def _edited(self, changed: bool | None) -> None:
        if changed:
            self.refresh_workspace()
            self.notify(
                local(self.tr, "Changes staged · Ctrl+S to review", "修改已暂存 · Ctrl+S 审阅")
            )

    def action_mark(self) -> None:
        if self.focused is not self.query_one("#items") or not self.section_editable:
            return
        item = self.items.get(self.selected_id or "")
        if (
            item
            and item.availability is Availability.ENABLED
            and any(editable(f) for f in item.fields)
        ):
            self.marked.symmetric_difference_update({item.id})
            table = self.query_one("#items", ItemTable)
            table.update_cell(
                item.id, table.ordered_columns[0].key, "◆" if item.id in self.marked else "·"
            )
            self.render_selection()

    def action_mark_all(self) -> None:
        if self.focused is not self.query_one("#items") or not self.section_editable:
            return
        available = {
            key
            for key, item in self.items.items()
            if item.availability is Availability.ENABLED and any(editable(f) for f in item.fields)
        }
        self.marked = set() if self.marked == available else available
        table = self.query_one("#items", ItemTable)
        for item_id in self.items:
            table.update_cell(
                item_id, table.ordered_columns[0].key, "◆" if item_id in self.marked else "·"
            )
        self.render_selection()

    def action_search(self) -> None:
        if self.session and self.page != "overview":
            self.query_one("#search", Input).focus()

    def clear_search(self) -> None:
        self.query_one("#search", Input).value = ""
        self.query_one("#search", Input).focus()

    def _perform(self, operation: Operation) -> None:
        if self.session:
            try:
                self.lifecycle.perform(operation)
                self.refresh_workspace()
            except Exception as exc:
                self.show_error(exc)

    def action_undo(self) -> None:
        self._perform(Undo())

    def action_redo(self) -> None:
        self._perform(Redo())

    def action_review(self) -> None:
        if not self.session:
            return
        try:
            self.push_screen(
                ReviewScreen(self.session.preview(), self.tr, self._commit), self._saved
            )
        except Exception as exc:
            self.show_error(exc)

    def _commit(self, request: CommitRequest) -> None:
        try:
            self.lifecycle.commit(request)
        except Exception:
            if self.lifecycle.reload_error:
                self.refresh_workspace()
            raise

    def _saved(self, saved: bool | None) -> None:
        if saved:
            self.refresh_workspace()
            assert self.last_receipt is not None
            destination = self.last_receipt.artifacts[0].destination.parent
            self.notify(
                local(self.tr, f"Verified save: {destination}", f"保存并验证完成：{destination}"),
                timeout=10,
            )

    def action_language(self) -> None:
        self.tr.switch("en" if self.tr.locale == "zh-Hans" else "zh-Hans")
        self.lifecycle.set_locale(self.tr.locale)
        self._labels()
        self.refresh_workspace()

    def action_identity(self) -> None:
        if self.session:
            identity_mismatch = bool(
                self.workspace
                and any(
                    item.code == "integrity.identity_mismatch"
                    for item in self.workspace.diagnostics
                )
            )
            self.push_screen(
                IdentityScreen(
                    self.tr,
                    self.request.device_id,
                    self.request.user_id,
                    self._apply_identity,
                    identity_mismatch=identity_mismatch,
                ),
                lambda result: self.refresh_workspace() if result else None,
            )

    def _apply_identity(self, value: tuple[str, int | None]) -> None:
        self.lifecycle.perform(SetDeviceIdentity(*value))

    def action_repair(self) -> None:
        if self.session:
            self.push_screen(
                ConfirmScreen(
                    local(self.tr, "Recalculate integrity?", "重算摘要？"),
                    local(
                        self.tr,
                        "Stage digest repair. Device-bound values need a matching device ID. "
                        "No file is written yet.",
                        "暂存摘要重算请求。设备绑定摘要需要匹配的设备 ID。此时不写入文件。",
                    ),
                    self.tr,
                ),
                lambda approved: self._perform(RepairIntegrity()) if approved else None,
            )

    def action_help(self) -> None:
        self.push_screen(
            ConfirmScreen(
                local(self.tr, "Keyboard guide", "快捷键"),
                "Ctrl+O   Open / 打开\nCtrl+F   Search / 搜索\n"
                "Double-click / Enter    Details / 详情\n"
                "Space    Mark row / 选择行\nCtrl+A   Mark filtered rows / 全选筛选结果\n"
                "Ctrl+B   Batch edit / 批量编辑\nCtrl+Z / Ctrl+Y   Undo / Redo\n"
                "Ctrl+S   Review & save / 审阅保存\nCtrl+I   Device identity / 设备身份\n"
                "Ctrl+R   Repair integrity / 修复摘要\nEsc      Cancel dialog / 取消弹窗\n"
                "Ctrl+H   Backup history / 备份历史\n"
                "Ctrl+Q   Quit / 退出\n"
                "Drag header │ to resize columns / 拖动表头 │ 调整列宽",
                self.tr,
                information=True,
            )
        )

    def action_backups(self) -> None:
        if not self.session:
            return
        try:
            self.push_screen(BackupScreen(self.lifecycle.backups(), self.tr), self._backup_selected)
        except Exception as exc:
            self.show_error(exc)

    def _backup_selected(self, directory: Path | None) -> None:
        if directory:
            self.push_screen(
                ConfirmScreen(
                    local(self.tr, "Restore this backup?", "恢复这份备份？"),
                    local(
                        self.tr,
                        f"{directory.name}\nPending edits will be discarded. "
                        "Current files will be backed up, then replaced.",
                        f"{directory.name}\n当前草稿将被放弃。现有文件会先备份，再被恢复内容覆盖。",
                    ),
                    self.tr,
                ),
                lambda approved: self._restore_backup(directory) if approved else None,
            )

    def _restore_backup(self, directory: Path) -> None:
        try:
            self.lifecycle.restore(directory, discard=True)
            self.refresh_workspace()
            self.notify(local(self.tr, "Backup restored and verified", "备份已恢复并验证"))
        except Exception as exc:
            self.refresh_workspace()
            self.show_error(exc)

    async def action_quit(self) -> None:
        if self.lifecycle.has_uncommitted_changes:
            self.push_screen(
                ConfirmScreen(
                    local(self.tr, "Leave without saving?", "放弃修改并退出？"),
                    local(
                        self.tr,
                        "Your original files have not been changed by these pending edits.",
                        "当前待保存修改尚未写入文件。",
                    ),
                    self.tr,
                ),
                lambda approved: self.exit(0) if approved else None,
            )
        else:
            self.exit(0)

    def show_error(self, exc: Exception) -> None:
        message = error_text(exc, self.tr)
        self.query_one("#status", Static).update(message)
        self.notify(message, severity="error", timeout=10, markup=False)
