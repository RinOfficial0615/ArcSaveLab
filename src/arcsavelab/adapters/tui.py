"""CLI-to-Textual entry point; the UI implementation lives in arcsavelab.ui."""

from arcsavelab.interface import OpenRequest
from arcsavelab.ui.app import ArcSaveLabApp


def run_tui(request: OpenRequest) -> int:
    return ArcSaveLabApp(request).run() or 0
