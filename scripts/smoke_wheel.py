"""Install the wheel outside the checkout and test packaged catalog, CSS and UI."""

import argparse
import os
import subprocess
import tempfile
from pathlib import Path

SMOKE = """
import asyncio
from arcsavelab.catalog import verify_catalog
from arcsavelab.ui.app import ArcSaveLabApp
from arcsavelab.ui.dialogs import OpenScreen
assert verify_catalog().valid
async def run():
    app = ArcSaveLabApp()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.click('#open')
        await pilot.pause()
        assert isinstance(app.screen, OpenScreen)
        await pilot.press('escape')
asyncio.run(run())
print('PASS isolated wheel: catalog + compact UI + open dialog')
"""


def smoke(directory: Path) -> None:
    wheels = sorted(directory.resolve().glob("*.whl"))
    if len(wheels) != 1:
        raise SystemExit("provide a directory containing exactly one wheel")
    with tempfile.TemporaryDirectory(prefix="arcsavelab-wheel-") as raw:
        root = Path(raw)
        env = root / "env"
        subprocess.run(["uv", "venv", str(env), "--python", "3.12"], check=True, cwd=root)
        python = env / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        subprocess.run(
            ["uv", "pip", "install", "--python", str(python), str(wheels[0])], check=True, cwd=root
        )
        subprocess.run([str(python), "-I", "-c", SMOKE], check=True, cwd=root)
        subprocess.run([str(python), "-I", "-m", "arcsavelab", "--version"], check=True, cwd=root)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    smoke(parser.parse_args().directory)
