from __future__ import annotations

from pathlib import Path

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Label, Static

from arcsavelab.backups import Backup
from arcsavelab.i18n import Translator

from .common import ResponsiveModal, local


class BackupScreen(ResponsiveModal[Path | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, backups: tuple[Backup, ...], tr: Translator) -> None:
        super().__init__()
        self.backups, self.tr = backups, tr

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog", id="backups-dialog"):
            yield Label(local(self.tr, "Backup history", "备份历史"), classes="dialog-title")
            yield Static(
                local(
                    self.tr,
                    "Restore validates every file and creates a new backup of the current state.",
                    "恢复前逐个验证备份文件，并为当前状态创建一份新备份。",
                ),
                classes="muted",
            )
            yield DataTable(id="backups-table", cursor_type="row", zebra_stripes=True)
            if not self.backups:
                yield Static(
                    local(
                        self.tr,
                        "No in-place backups in this save directory.",
                        "此存档目录中暂无覆盖保存产生的备份。",
                    )
                )
            with Horizontal(classes="dialog-actions"):
                yield Button(self.tr("action.cancel"), id="cancel")
                yield Button(
                    local(self.tr, "Restore…", "恢复…"),
                    variant="warning",
                    id="restore",
                    disabled=not self.backups,
                )

    def on_mount(self) -> None:
        table = self.query_one("#backups-table", DataTable)
        table.add_columns(local(self.tr, "Backup", "备份"), local(self.tr, "Files", "文件"))
        for backup in self.backups:
            table.add_row(Text(backup.directory.name), Text(", ".join(backup.files)))

    def action_cancel(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#cancel")
    def cancel_button(self) -> None:
        self.action_cancel()

    @on(Button.Pressed, "#restore")
    @on(DataTable.RowSelected, "#backups-table")
    def restore(self) -> None:
        index = self.query_one("#backups-table", DataTable).cursor_row
        if 0 <= index < len(self.backups):
            self.dismiss(self.backups[index].directory)
