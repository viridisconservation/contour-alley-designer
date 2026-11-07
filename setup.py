"""Build hook: bundle the shipped site data into the package so the MCP server works
from any install (wheel, sdist or `pip install git+...`). Metadata lives in pyproject.toml."""
import importlib.util
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py

ROOT = Path(__file__).resolve().parent


class BuildPyWithSiteData(build_py):
    def run(self):
        script = ROOT / 'tools' / 'bundle_mcp_data.py'
        if script.is_file() and (ROOT / 'configs').is_dir():
            spec = importlib.util.spec_from_file_location('bundle_mcp_data', script)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.main()
        super().run()


setup(cmdclass={'build_py': BuildPyWithSiteData})
