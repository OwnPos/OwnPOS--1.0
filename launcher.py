"""Entry point for the packaged Windows program (PyInstaller) and for `python launcher.py`."""
import sys

from ownpos.ui.app import main

if __name__ == "__main__":
    sys.exit(main(sys.argv))
