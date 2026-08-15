"""Common schema definitions for the CodinGame API."""

from __future__ import annotations

CgSolutionLanguage = str
"""The programming language used for the reference solution, e.g. "Python3", "Java", "C++", etc.

   Code submitted/played (`TestSession/play`, `TestSession/submit`, contribution
   `updateContribution`/`createContribution`) runs server-side in a sandbox with a specific
   version and, for some languages, specific bundled libraries--relevant to know when writing a
   solution that assumes a particular language feature or library is available. Confirmed:

   - **Python3**: 3.11.5, with NumPy, pandas, and SciPy available.

   Other languages' exact versions/bundled libraries aren't catalogued here yet--see
   https://www.codingame.com/playgrounds/40701/help-center/languages-versions (a client-rendered
   page; fetching it programmatically only returns the loading shell, not the real content, so
   this couldn't be filled in automatically--add entries here as they're confirmed for other
   languages actually in use).

   File-extension mapping for a `CgSolutionLanguage` lives in `codingame_tools.language`
   (`get_language(cg_id).extension` / `get_language_by_extension(ext)`), not here--this module is
   wire-protocol schema only."""
