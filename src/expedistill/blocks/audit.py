"""D8 leak audit (layer 4): pure, deterministic, no model, no disk.

A skill that literally contains the exam's protected entities has memorized
this question's answer instead of the general ability, so every skill version
must pass this audit before acceptance (stage 4 / K4). Two functions:

* ``extract_specifics`` -- what counts as a protected entity;
* ``leak_audit`` -- how much of it may appear in a skill before it is rejected.

Both are deterministic on purpose: the same inputs always give the same audit
result, so a rejected version can be re-audited after a rewrite without the
rule itself shifting under it.
"""

from __future__ import annotations

import re
from typing import Set, Tuple

__all__ = ["extract_specifics", "leak_audit"]

# Quoted spans ("..." / '...'). The (?<!\w) / (?!\w) guards keep an apostrophe
# inside a word ("don't") from being read as an opening or closing quote.
_QUOTED_RE = re.compile(r'(?<!\w)"([^"\n]+)"(?!\w)|(?<!\w)\'([^\'\n]+)\'(?!\w)')

# Integers, decimals, and version-style numbers like 1.2.3. Word boundaries keep
# digits glued to letters (v2, A4) from being read as bare numbers.
_NUMBER_RE = re.compile(r"\b\d+(?:\.\d+)*\b")

# ALL-CAPS acronyms of at least two letters (API, GPU, AUTH).
_ACRONYM_RE = re.compile(r"\b[A-Z]{2,}\b")

# Edge punctuation stripped from a whitespace token before the file-name-like
# check; '/' and '_' are NOT stripped because they are part of the name.
_TOKEN_EDGE = '.,;:!?()[]{}<>"\''

# A token is file-name-like when it is at least 3 chars long and contains a
# path separator, a dot, or an underscore.
_FILELIKE_CHARS = ("/", ".", "_")


def extract_specifics(question_text: str, reference_answer: str) -> Set[str]:
    """Collect the "specifics" of an exam worth protecting against leaks.

    The rule, applied to the question and the reference answer together:

    1. quoted spans: the text inside "..." or '...' (apostrophes inside words
       do not count as quotes);
    2. numbers: integers, decimals, and version-style chains like 1.2.3;
    3. file-name-like tokens: whitespace tokens of length >= 3 that contain
       '/', '.', or '_' (paths, config files, snake_case names), with edge
       punctuation stripped;
    4. ALL-CAPS acronyms of >= 2 letters.

    The returned set is case-normalized (lowercase), so the later substring
    match is case-insensitive by construction.
    """
    text = f"{question_text}\n{reference_answer}"
    specifics: Set[str] = set()
    for match in _QUOTED_RE.finditer(text):
        span = (match.group(1) or match.group(2)).strip()
        if span:
            specifics.add(span.lower())
    specifics.update(m.group(0).lower() for m in _NUMBER_RE.finditer(text))
    specifics.update(m.group(0).lower() for m in _ACRONYM_RE.finditer(text))
    for token in text.split():
        token = token.strip(_TOKEN_EDGE)
        if len(token) >= 3 and any(c in token for c in _FILELIKE_CHARS):
            specifics.add(token.lower())
    return specifics


def leak_audit(
    skill_content: str,
    question_text: str,
    reference_answer: str,
    threshold: float = 0.5,
) -> Tuple[bool, str]:
    """Audit one skill version against one exam; return ``(ok, report)``.

    A specific "hits" when it appears as a case-insensitive substring of the
    skill content. ``ok`` is True when the hit ratio (hits / specifics) is at
    most ``threshold``; a ratio exactly at the threshold passes, and
    ``threshold=0.0`` means any single hit fails. An exam with no specifics
    always passes. The report is "" when nothing leaked; otherwise it states
    the ratio, the threshold, and every hit, for the audit log.
    """
    specifics = extract_specifics(question_text, reference_answer)
    if not specifics:
        return True, ""
    skill_lower = skill_content.lower()
    hits = sorted(s for s in specifics if s in skill_lower)
    if not hits:
        return True, ""
    ratio = len(hits) / len(specifics)
    ok = ratio <= threshold
    status = "passed" if ok else "failed"
    report = (
        f"leak audit {status}: {len(hits)}/{len(specifics)} specifics appear in "
        f"the skill content (ratio {ratio:.3f}, threshold {threshold:.3f}); "
        f"hits: {', '.join(repr(h) for h in hits)}"
    )
    return ok, report
