"""Copy the shipped site data into contour/_bundled so a wheel carries it.

Runs automatically at build time from setup.py; can also be run by hand.

The MCP server (contour/mcp_server.py) reads configs/, fixtures/ and examples/ from a
source checkout when it can, and from contour/_bundled when installed from a wheel.
The bundle is a build artifact: it is git-ignored and rebuilt from the repo each time.
Rendered HTML reports are left out to keep the wheel small; report.md stays because the
server quotes both leakage rules from it.
"""
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / 'contour' / '_bundled'
SKIP = {'report.html', '__pycache__'}


def ignore(_dir, names):
    return [n for n in names if n in SKIP or n.endswith('.pyc')]


def main():
    if DEST.exists():
        shutil.rmtree(DEST)
    for sub in ('configs', 'fixtures', 'examples/landscapes'):
        shutil.copytree(ROOT / sub, DEST / sub, ignore=ignore)
    size = sum(p.stat().st_size for p in DEST.rglob('*') if p.is_file())
    print(f'bundled {DEST.relative_to(ROOT)}: {size / 1e6:.1f} MB')
    return 0


if __name__ == '__main__':
    sys.exit(main())
