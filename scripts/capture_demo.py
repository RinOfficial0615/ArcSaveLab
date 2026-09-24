"""Regenerate the README screenshot using synthetic data only."""

import argparse
import asyncio
import os
import tempfile
from pathlib import Path

from demo_data import create_demo
from textual.widgets import OptionList

from arcsavelab.interface import OpenRequest, SourceSpec
from arcsavelab.sources import SOURCE_FILES
from arcsavelab.ui.app import ArcSaveLabApp


async def capture(output: Path) -> None:
    # A screenshot is deliberately full-color even in CI's no-color shell.
    os.environ.pop("NO_COLOR", None)
    os.environ["FORCE_COLOR"] = "1"
    os.environ["TERM"] = "xterm-256color"
    os.environ["COLORTERM"] = "truecolor"
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="arcsavelab-demo-") as raw:
        root = Path(raw)
        create_demo(root)
        request = OpenRequest(
            tuple(SourceSpec(kind, root / name) for kind, name in SOURCE_FILES.items()),
            locale="zh-Hans",
            device_id="demo-device",
            user_id=123,
        )
        app = ArcSaveLabApp(request)
        async with app.run_test(size=(132, 38)) as pilot:
            nav = app.query_one("#navigation", OptionList)
            nav.focus()
            nav.highlighted = 11
            await pilot.press("enter")
            await pilot.pause()
            await pilot.pause(0.3)
            app.query_one("#items").focus()
            await pilot.pause()
            app.save_screenshot(output.name, path=str(output.parent))
    print(f"Synthetic screenshot: {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    asyncio.run(capture(parser.parse_args().output))
