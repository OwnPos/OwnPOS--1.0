"""`python -m ownpos` starts the program (also used by the server setup's administrator step)."""
import sys

from ownpos.ui.app import main

if __name__ == "__main__":
    sys.exit(main(sys.argv))
