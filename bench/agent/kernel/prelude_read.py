"""prelude_read: read(), grep()/search(), recall() — the read half of PRELUDE.
WHAT IT IS
  Fragment 4/5 of PRELUDE (prelude.py); concatenates its sub-fragments in dependency order into
  `_READ`, byte-identical to the old single constant and exec'd by the child as one PRELUDE string.
  prelude_read_helpers.py section/range helpers, _coerce_paths, _int_arg; prelude_read_file.py
  read() with range/ranges/section/repeat and paging; prelude_search_result.py _count_result,
  _grep_result; prelude_search.py grep() and search(); prelude_recall.py _recall_hits, recall,
  recall_tool, page; prelude_skill.py skill(). prelude.py imports `_READ` from this module.

ENTRY POINTS
  _READ   helpers + read + results + search + recall + skill.
  read/grep/search/recall/page/skill live in the fragments above, not as Python names here.

INVARIANTS
  fragment order = execution order: a later fragment calls an earlier one at cell time; do not
  reorder or add a separator, bytes must match the pre-split constant.
  read() = ledger, not firehose; results are dicts that also answer attributes.

WHERE TO CHANGE
  read .................. prelude_read_file.py;  fragment order ... this module
  search ................ prelude_search.py / prelude_search_result.py
  recall and page ....... prelude_recall.py;  skill ... prelude_skill.py
"""
from __future__ import annotations

from .prelude_read_file import _READ_FILE
from .prelude_read_helpers import _READ_HELPERS
from .prelude_recall import _RECALL
from .prelude_search import _SEARCH
from .prelude_search_result import _SEARCH_RESULT
from .prelude_skill import _SKILL

# One program fragment, order fixed by the kernel prelude. Do not join with a separator.
_READ = _READ_HELPERS + _READ_FILE + _SEARCH_RESULT + _SEARCH + _RECALL + _SKILL
