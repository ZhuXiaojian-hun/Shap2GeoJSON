# -*- coding: utf-8 -*-
"""用 PyInstaller 打包单文件 exe：dist\\Shap2GeoJSON.exe

直接运行：python build_exe.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

EXCLUDES = [
    "PySide6", "PySide2", "PyQt5", "PyQt6",
    "matplotlib", "pandas", "scipy",
    "IPython", "jupyter", "notebook", "pytest", "psycopg2",
]


def main() -> int:
    os.chdir(HERE)
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", "Shap2GeoJSON",
        "--collect-all", "pyproj",
        "--collect-all", "shapely",
    ]
    for mod in EXCLUDES:
        cmd += ["--exclude-module", mod]
    cmd.append("main.py")

    print("Running: %s" % " ".join(cmd))
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print("\n[FAILED] PyInstaller exited with code %d" % result.returncode)
        return result.returncode

    build_dir = os.path.join(HERE, "build")
    if os.path.isdir(build_dir):
        shutil.rmtree(build_dir, ignore_errors=True)

    exe = os.path.join(HERE, "dist", "Shap2GeoJSON.exe")
    if os.path.exists(exe):
        size = os.path.getsize(exe) / (1024 * 1024)
        print("\n[OK] Built: %s (%.1f MB)" % (exe, size))
    else:
        print("\n[OK] Build finished, but dist\\Shap2GeoJSON.exe was not found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
