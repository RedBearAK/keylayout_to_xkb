"""
keylayout_to_xkb/__init__.py

Package marker.

Bytecode caching is disabled for this package. The source tree commonly lives in
a synced folder (e.g. Dropbox), where __pycache__ .pyc files sync between
machines with mismatched timestamps -- Python then loads a stale .pyc compiled
from a DIFFERENT machine's copy of the source, so edits to the .py files appear
to have no effect. Setting sys.dont_write_bytecode here (the package's first
import, before any submodule loads) stops new .pyc files being written for every
submodule imported afterward. For a complete guarantee (including this file),
also set PYTHONDONTWRITEBYTECODE=1 or run python with -B.
"""

import sys as _sys

_sys.dont_write_bytecode = True


__version__ = '20260622'


# End of file #
