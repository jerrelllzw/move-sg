"""Force UTF-8 on stdout/stderr so non-ASCII output never crashes on Windows.

Windows consoles default to the cp1252 code page, which can't encode some of the
characters the data scripts print — the ⚠ warning glyph, curly apostrophes in
place names — raising UnicodeEncodeError mid-run. Importing this module
reconfigures the streams to UTF-8 (a harmless no-op on platforms that already
use it).
"""

import sys

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")
