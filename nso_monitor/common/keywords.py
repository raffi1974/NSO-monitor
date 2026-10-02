"""Decides which theme(s) a piece of text belongs to, using the keywords in config/themes.yaml.

Works the same for English, French and Arabic: text and keywords are both normalised first
(lower case, no accents, Arabic letter variants and diacritics folded, Arabic digits -> 0-9).
"""
from __future__ import annotations

import re
import unicodedata

from .config import Theme

_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_ARABIC_FOLD = str.maketrans({"ٱ": "ا", "ة": "ه", "ى": "ي", "ـ": ""})
_ARABIC = re.compile(r"[؀-ۿ]")
_SPACES = re.compile(r"\s+")


def normalize(text: str | None) -> str:
    """'Éducation' -> 'education';  'الإحصاءات الحيوية ٢٠٢٤' -> 'الاحصاءات الحيويه 2024'."""
    if not text:
        return ""
    t = unicodedata.normalize("NFKD", str(text).translate(_DIGITS))   # splits é -> e + accent, أ -> ا + hamza
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return _SPACES.sub(" ", t.translate(_ARABIC_FOLD).lower()).strip()


def _pattern(words: list[str]) -> re.Pattern | None:
    parts = []
    for w in words:
        k = normalize(w)
        if k:
            # Arabic: anywhere (prefixes attach to words). Latin: at the start of a word ("industr" -> industry).
            parts.append(re.escape(k) if _ARABIC.search(k) else r"(?<![a-z0-9])" + re.escape(k))
    return re.compile("|".join(sorted(set(parts), key=len, reverse=True))) if parts else None


def non_dataset_pattern(keywords: dict[str, list[str]]) -> re.Pattern | None:
    """Compile config/themes.yaml's 'not_a_dataset' keywords into one pattern, same rules as a theme's
    (normalized, Arabic matches anywhere, Latin matches at the start of a word)."""
    return _pattern([w for ws in keywords.values() for w in ws])


class ThemeMatcher:
    def __init__(self, themes: dict[str, Theme]):
        self.themes = themes
        self._include = {tid: _pattern([w for ws in t.keywords.values() for w in ws]) for tid, t in themes.items()}
        self._exclude = {tid: _pattern([w for ws in (t.exclude or {}).values() for w in (ws or [])])
                         for tid, t in themes.items()}

    def match(self, *texts: str | None) -> list[str]:
        """Ids of every theme whose keywords appear in the text (and none of its exclusions)."""
        t = normalize(" ".join(x for x in texts if x))
        if not t:
            return []
        hits = []
        for tid, pat in self._include.items():
            if pat is not None and pat.search(t):
                exc = self._exclude.get(tid)
                if exc is None or not exc.search(t):
                    hits.append(tid)
        return hits

    def matched_words(self, theme_id: str, *texts: str | None) -> list[str]:
        """Which keywords of one theme matched (useful to explain a classification in reports)."""
        pat = self._include.get(theme_id)
        t = normalize(" ".join(x for x in texts if x))
        return sorted({m.group(0) for m in pat.finditer(t)}) if pat else []
