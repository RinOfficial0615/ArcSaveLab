from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, DataTable, Input, Label, Select, Static

from arcsavelab.errors import ReloadRequired
from arcsavelab.i18n import Translator
from arcsavelab.interface import CommitPreview, CommitRequest, OpenRequest, SourceSpec
from arcsavelab.sources import SOURCE_FILES, classify_source

from .common import ResponsiveModal, error_text, local, value_text
from .files import FilePicker
from .help import identity_help


class OpenScreen(ResponsiveModal[OpenRequest | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, request: OpenRequest, tr: Translator) -> None:
        super().__init__()
        self.request = request
        self.tr = tr

    def compose(self) -> ComposeResult:
        paths = {source.kind: str(source.path) for source in self.request.sources}
        with Vertical(classes="dialog", id="open-dialog"):
            yield Label(
                local(self.tr, "Open a save workspace", "打开存档工作区"), classes="dialog-title"
            )
            with VerticalScroll(id="open-fields"):
                yield Static(
                    local(
                        self.tr,
                        "Choose a folder to detect standard filenames, "
                        "or enter individual paths below.",
                        "输入文件夹以识别标准文件名，或分别填写下方文件路径。",
                    ),
                    classes="muted",
                )
                with Horizontal(id="folder-row"):
                    yield Input(
                        placeholder=local(self.tr, "Save folder", "存档文件夹"), id="folder"
                    )
                    yield Button(local(self.tr, "Detect", "识别"), id="detect")
                    yield Button(local(self.tr, "Browse", "浏览"), id="browse")
                for kind, filename in SOURCE_FILES.items():
                    yield Label(f"{self.tr('source.' + kind.value)} · {filename}", markup=False)
                    yield Input(
                        paths.get(kind, ""),
                        placeholder=local(self.tr, "Optional", "可选"),
                        id=f"source-{kind.value}",
                    )
            yield Static("", id="open-error", classes="error", markup=False)
            with Horizontal(classes="dialog-actions"):
                yield Button(self.tr("action.cancel"), id="cancel")
                yield Button(self.tr("action.open"), variant="primary", id="open-files")

    def action_cancel(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#cancel")
    def cancel_button(self) -> None:
        self.action_cancel()

    @on(Button.Pressed, "#browse")
    def browse(self) -> None:
        raw = self.query_one("#folder", Input).value.strip().strip('"')
        root = Path(raw).expanduser() if raw else Path.cwd()
        self.app.push_screen(FilePicker(root, self.tr), self._picked)

    def _picked(self, path: Path | None) -> None:
        if path is None:
            return
        try:
            if path.is_dir():
                self.query_one("#folder", Input).value = str(path)
                self.detect()
            else:
                kind = classify_source(path)
                self.query_one(f"#source-{kind.value}", Input).value = str(path)
        except (OSError, ValueError) as exc:
            self.query_one("#open-error", Static).update(str(exc))

    @on(Input.Submitted, "#folder")
    @on(Button.Pressed, "#detect")
    def detect(self) -> None:
        root = Path(self.query_one("#folder", Input).value.strip().strip('"')).expanduser()
        if not root.is_dir():
            self.query_one("#open-error", Static).update(
                local(self.tr, "Folder does not exist.", "文件夹不存在。")
            )
            return
        count = 0
        for kind, filename in SOURCE_FILES.items():
            path = root / filename
            present = path.is_file()
            count += present
            self.query_one(f"#source-{kind.value}", Input).value = str(path) if present else ""
        self.query_one("#open-error", Static).update(
            local(self.tr, f"Detected {count} files.", f"识别到 {count} 个文件。")
        )

    @on(Button.Pressed, "#open-files")
    def open_files(self) -> None:
        try:
            sources: list[SourceSpec] = []
            for kind in SOURCE_FILES:
                raw = self.query_one(f"#source-{kind.value}", Input).value.strip().strip('"')
                if raw:
                    path = Path(raw).expanduser().resolve()
                    if not path.is_file():
                        raise ValueError(self.tr("ui.file_not_found", path=path))
                    sources.append(SourceSpec(kind, path))
            if not sources:
                raise ValueError(self.tr("ui.select_one_file"))
            self.dismiss(replace(self.request, sources=tuple(sources)))
        except (ValueError, OSError) as exc:
            self.query_one("#open-error", Static).update(str(exc))


class ConfirmScreen(ResponsiveModal[bool]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, title: str, body: str, tr: Translator, *, information: bool = False) -> None:
        super().__init__()
        self.heading, self.body, self.tr = title, body, tr
        self.information = information

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog", id="confirm-dialog"):
            yield Label(self.heading, classes="dialog-title", markup=False)
            with VerticalScroll(id="confirm-body"):
                yield Static(self.body, markup=False)
            with Horizontal(classes="dialog-actions"):
                if not self.information:
                    yield Button(self.tr("action.cancel"), id="cancel")
                yield Button(
                    local(self.tr, "Close", "知道了")
                    if self.information
                    else local(self.tr, "Confirm", "确认"),
                    variant="primary" if self.information else "warning",
                    id="confirm",
                )

    def action_cancel(self) -> None:
        self.dismiss(False)

    @on(Button.Pressed)
    def clicked(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm")


class IdentityScreen(ResponsiveModal[tuple[str, int | None] | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(
        self,
        tr: Translator,
        device_id: str | None = None,
        user_id: int | None = None,
        apply: Callable[[tuple[str, int | None]], object] | None = None,
        identity_mismatch: bool = False,
    ) -> None:
        super().__init__()
        self.tr = tr
        self.device_id, self.user_id, self.apply_operation = device_id, user_id, apply
        self.identity_mismatch = identity_mismatch

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog", id="identity-dialog"):
            yield Label(self.tr("ui.device_identity"), classes="dialog-title")
            with VerticalScroll(id="identity-fields"):
                yield Label(self.tr("prompt.device_id"))
                yield Input(self.device_id or "", id="device-id")
                yield Label(self.tr("prompt.user_id") + " · " + self.tr("ui.optional"))
                yield Input(str(self.user_id) if self.user_id is not None else "", id="user-id")
                yield Static(
                    identity_help(self.tr), id="identity-guide", classes="explanation", markup=False
                )
            if self.identity_mismatch:
                yield Static(
                    local(
                        self.tr,
                        "identity_mismatch: device identity mismatch; apply is disabled.",
                        "identity_mismatch：设备身份与存档不匹配，已禁止应用。",
                    ),
                    id="identity-mismatch",
                    classes="error",
                    markup=False,
                )
            yield Static("", id="identity-error", classes="error", markup=False)
            with Horizontal(classes="dialog-actions"):
                yield Button(self.tr("action.cancel"), id="cancel")
                yield Button(
                    self.tr("action.apply"),
                    variant="primary",
                    id="identity-apply",
                    disabled=self.identity_mismatch,
                )

    def action_cancel(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#cancel")
    def cancel_button(self) -> None:
        self.action_cancel()

    @on(Button.Pressed, "#identity-apply")
    def apply_identity(self) -> None:
        try:
            device = self.query_one("#device-id", Input).value.strip()
            raw = self.query_one("#user-id", Input).value.strip()
            user = int(raw) if raw else None
            if not device:
                raise ValueError(self.tr("ui.device_id_required"))
            if user is not None and user < 0:
                raise ValueError(
                    local(self.tr, "User ID must be non-negative.", "用户 ID 应为非负整数。")
                )
            if self.apply_operation:
                self.apply_operation((device, user))
            self.dismiss((device, user))
        except Exception as exc:
            self.query_one("#identity-error", Static).update(error_text(exc, self.tr))


class ReviewScreen(ResponsiveModal[bool]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(
        self, preview: CommitPreview, tr: Translator, commit: Callable[[CommitRequest], object]
    ) -> None:
        super().__init__()
        self.preview, self.tr, self.commit_operation = preview, tr, commit

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog", id="review-dialog"):
            yield Label(local(self.tr, "Review & save", "审阅与保存"), classes="dialog-title")
            yield Static(
                local(
                    self.tr,
                    f"{len(self.preview.semantic_changes)} changes "
                    f"· revision {self.preview.revision}",
                    f"{len(self.preview.semantic_changes)} 项变更 · 修订 {self.preview.revision}",
                ),
                classes="muted",
            )
            yield DataTable(id="diff-table", cursor_type="row", zebra_stripes=True)
            with VerticalScroll(id="review-options"):
                for artifact in self.preview.artifacts:
                    state = "●" if artifact.changed else "○"
                    yield Static(
                        f"{state} {artifact.source.name}  "
                        f"{artifact.before_sha256[:10]} → {artifact.after_sha256[:10]}",
                        markup=False,
                        classes="muted",
                    )
                for diagnostic in self.preview.diagnostics:
                    yield Static(
                        f"{diagnostic.code} · {dict(diagnostic.arguments)}",
                        markup=False,
                        classes="error",
                    )
                yield Select(
                    [
                        (
                            local(
                                self.tr,
                                "Export to a new folder (originals untouched)",
                                "导出到新文件夹（保留原文件）",
                            ),
                            "export",
                        ),
                        (
                            local(
                                self.tr, "Replace source files + create backup", "备份后覆盖源文件"
                            ),
                            "replace",
                        ),
                    ],
                    value="export",
                    allow_blank=False,
                    id="save-mode",
                )
                yield Input(
                    placeholder=local(
                        self.tr,
                        "New output folder · blank = timestamped folder next to source",
                        "新输出文件夹 · 留空则在源文件旁创建时间戳文件夹",
                    ),
                    id="output-path",
                )
            yield Static("", id="review-error", classes="error", markup=False)
            with Horizontal(classes="dialog-actions"):
                yield Button(self.tr("action.cancel"), id="cancel")
                yield Button(
                    local(self.tr, "Save verified files", "保存并验证"),
                    variant="primary",
                    id="save",
                    disabled=not self.preview.can_commit,
                )

    def on_mount(self) -> None:
        table = self.query_one("#diff-table", DataTable)
        table.add_columns(
            local(self.tr, "Item / field", "项目 / 字段"),
            local(self.tr, "Before", "修改前"),
            local(self.tr, "After", "修改后"),
        )
        for change in self.preview.semantic_changes:
            table.add_row(
                Text(f"{change.item_label} · {self.tr(change.field_label_id)}"),
                Text(value_text(change.before, self.tr)),
                Text(value_text(change.after, self.tr)),
            )

    @on(Select.Changed, "#save-mode")
    def mode_changed(self, event: Select.Changed) -> None:
        self.query_one("#output-path", Input).disabled = event.value == "replace"

    def action_cancel(self) -> None:
        self.dismiss(False)

    @on(Button.Pressed, "#cancel")
    def cancel_button(self) -> None:
        self.action_cancel()

    @on(Button.Pressed, "#save")
    def save(self) -> None:
        if self.query_one("#save-mode", Select).value == "replace":
            self.app.push_screen(
                ConfirmScreen(
                    local(self.tr, "Replace original files?", "覆盖原文件？"),
                    local(
                        self.tr,
                        "A verified backup will be kept next to the originals.",
                        "将在源文件旁保留备份，然后覆盖原文件。",
                    ),
                    self.tr,
                ),
                self._confirmed,
            )
        else:
            self._commit(False)

    def _confirmed(self, approved: bool | None) -> None:
        if approved:
            self._commit(True)

    def _commit(self, overwrite: bool) -> None:
        try:
            raw = self.query_one("#output-path", Input).value.strip().strip('"')
            destination = Path(raw).expanduser().resolve() if raw and not overwrite else None
            self.commit_operation(
                CommitRequest(
                    destination=destination,
                    overwrite_sources=overwrite,
                    create_backup=True,
                    preview_token=self.preview.token,
                )
            )
            self.dismiss(True)
        except Exception as exc:
            self.query_one("#review-error", Static).update(error_text(exc, self.tr))
            if isinstance(exc, ReloadRequired):
                self.query_one("#save", Button).disabled = True
