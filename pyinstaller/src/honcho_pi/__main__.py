"""Entry point for honcho-pi package.

When run with `python -m honcho_pi`, this module executes the CLI.
PyApp uses this entry point when PYAPP_EXEC_MODULE is set.
"""

import sys
from honcho_pi.cli import main

if __name__ == "__main__":
    sys.exit(main())
