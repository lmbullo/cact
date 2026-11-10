"""prelude_index: file listing and structure: map() and outline().

WHAT IT IS
  Fragment 3/5 of PRELUDE (see prelude.py), split into source modules this file concatenates in
  dependency order; the assembled _INDEX is byte-identical to the old single constant and the child
  still execs one PRELUDE string.
    prelude_scan.py           _walk (gitignore-aware traversal) + map()
    prelude_outline_parse.py  _outline_doc / _outline_html (language parsers)
    prelude_outline.py        outline() with paging and rendering
  Only this module names the order; prelude.py imports _INDEX from it.

ENTRY POINTS
  _INDEX   the complete fragment: _SCAN + _OUTLINE_PARSE + _OUTLINE
  map(...) / outline(...) live in the fragments above; importing this module does not define them.

INVARIANTS
  fragment order = execution order: outline() uses the parsers, both use helpers from earlier
  fragments; do not reorder or add a separator (bytes must match the pre-split constant).
  read-only; never opens a file through the journalled open().

WHERE TO CHANGE
  listed files .. prelude_scan.py (_walk, map);  outline grammar .. prelude_outline_parse.py;
  outline paging .. prelude_outline.py;  fragment order .. this module
"""
from __future__ import annotations

from .prelude_outline import _OUTLINE
from .prelude_outline_parse import _OUTLINE_PARSE
from .prelude_scan import _SCAN

# One program fragment, order fixed by the kernel prelude. Do not join with a separator.
_INDEX = _SCAN + _OUTLINE_PARSE + _OUTLINE
