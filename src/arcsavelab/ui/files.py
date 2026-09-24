from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DirectoryTree, Input, Label, Static

from arcsavelab.i18n import Translator

from .common import ResponsiveModal, local


class SaveTree(DirectoryTree):
    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
        return (
            path
            for path in paths
            if path.name not in {".git", ".venv", "__pycache__", "node_modules", ".old_ver"}
        )


class FilePicker(ResponsiveModal[Path | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, root: Path, tr: Translator) -> None:
        super().__init__()
        self.current = root.resolve() if root.is_dir() else Path.cwd()
        self.tr = tr

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog", id="file-dialog"):
            yield Label(
                local(self.tr, "Select a folder or file", "选择文件夹或文件"),
                classes="dialog-title",
            )
            with Horizontal(classes="path-row"):
                yield Input(str(self.current), id="tree-path")
                yield Button("↑", id="parent")
                yield Button(local(self.tr, "Go", "跳转"), id="go")
            yield SaveTree(self.current, id="file-tree")
            yield Static(str(self.current), id="tree-selection", markup=False, classes="muted")
            yield Static("", id="tree-error", markup=False, classes="error")
            with Horizontal(classes="dialog-actions"):
                yield Button(self.tr("action.cancel"), id="cancel")
                yield Button(
                    local(self.tr, "Use folder", "使用文件夹"), id="use-folder", variant="primary"
                )

    def action_cancel(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#cancel")
    def cancel_button(self) -> None:
        self.action_cancel()

    @on(Input.Submitted, "#tree-path")
    @on(Button.Pressed, "#go")
    def go(self) -> None:
        raw = self.query_one("#tree-path", Input).value.strip().strip('"')
        path = Path(raw).expanduser()
        if path.is_dir():
            self.current = path.resolve()
            self.query_one("#file-tree", SaveTree).path = self.current
            self.query_one("#tree-selection", Static).update(str(self.current))
            self.query_one("#tree-error", Static).update("")
        else:
            self.query_one("#tree-error", Static).update(
                local(self.tr, "Folder does not exist.", "文件夹不存在。")
            )

    @on(Button.Pressed, "#parent")
    def parent_folder(self) -> None:
        self.query_one("#tree-path", Input).value = str(self.current.parent)
        self.go()

    @on(DirectoryTree.DirectorySelected)
    def select_directory(self, event: DirectoryTree.DirectorySelected) -> None:
        self.current = event.path.resolve()
        self.query_one("#tree-selection", Static).update(str(self.current))

    @on(DirectoryTree.FileSelected)
    def select_file(self, event: DirectoryTree.FileSelected) -> None:
        self.dismiss(event.path.resolve())

    @on(Button.Pressed, "#use-folder")
    def use_folder(self) -> None:
        self.dismiss(self.current)
