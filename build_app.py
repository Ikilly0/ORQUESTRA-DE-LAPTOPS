"""Builds the program with PyInstaller (no Python needed to run the result).

    pip install pyinstaller -r requirements.txt
    python build_app.py

Result in dist/:
  Windows  KeyboardInstruments/KeyboardInstruments.exe   (zip the whole folder to share it)
  macOS    KeyboardInstruments.app
  Linux    KeyboardInstruments/KeyboardInstruments
The songs/ folder is bundled inside, and also copied next to the program so you can add your own .mid.
A PyInstaller build only runs on the system it was built on: build on Windows for Windows, etc.
(.github/workflows/build.yml does the three on GitHub.)
"""
import os
import shutil
import sys

import PyInstaller.__main__

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = "KeyboardInstruments"


def main():
    os.chdir(HERE)
    sep = os.pathsep
    args = [
        "app.py", "--name", NAME, "--noconfirm", "--clean", "--windowed",
        "--add-data", f"songs{sep}songs",
        "--copy-metadata", "mido",
        "--hidden-import", "mido.backends",
        "--collect-data", "sounddevice",   # the PortAudio library that ships with sounddevice
    ]
    if sys.platform == "darwin":
        args += ["--osx-bundle-identifier", "cl.uc.orquesta-laptops.keyboard-instruments"]
    PyInstaller.__main__.run(args)

    if sys.platform != "darwin":  # Windows / Linux: user folders next to the program
        out = os.path.join("dist", NAME)
        for folder in ("songs", "samples"):
            if os.path.isdir(folder):
                shutil.copytree(folder, os.path.join(out, folder), dirs_exist_ok=True)
        shutil.copy("README.md", os.path.join(out, "LEEME-app.md"))
    print("listo:", os.path.abspath("dist"))


if __name__ == "__main__":
    main()
