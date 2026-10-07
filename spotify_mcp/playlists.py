"""Find the playlist a user means from a typed or spoken name.

Tiers, tried in order; the first tier with any match decides:

1. exact: the same name, ignoring case;
2. normalized: the same after removing accents, punctuation, extra spaces and filler words
   ("my", "playlist", ...);
3. partial: every word of the query starts a word of the name ("run" -> "Running Mix"), or every
   word of the name is in the query ("add to my schranz mix please" -> "Schranz Mix");
4. fuzzy: difflib's similarity ratio, on the text and on a rough sound-alike spelling, against
   the whole name and against runs of its words, so speech-to-text errors still land
   ("shrance" -> "Schranz", "tecno" -> "Techno", "dark wave" -> "Darkwave").

A tier that finds several playlists is ambiguous; the caller asks the user which one.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from typing import Any

EXACT = "exact"
NORMALIZED = "normalized"
PARTIAL = "partial"
FUZZY = "fuzzy"
NONE = "none"

FUZZY_MIN = 0.8  # a fuzzy match must score at least this (difflib ratio, 0..1)
FUZZY_MARGIN = 0.05  # candidates this close to the best one make the fuzzy tier ambiguous
CLOSEST_MIN = 0.7  # below FUZZY_MIN but at least this: offered as "closest" when nothing matches

FILLER = frozenset({"my", "the", "a", "playlist", "playlists", "list", "please", "soittolista"})

# Rough sound-alike spelling for speech-to-text errors. Order matters ("sch" before "ch"/"c").
_SOUNDS = (
    ("sch", "sh"), ("tch", "ch"), ("ph", "f"), ("ck", "k"), ("qu", "kw"), ("x", "ks"), ("z", "s"),
    ("ce", "se"), ("ci", "si"), ("cy", "si"), ("c", "k"), ("y", "i"), ("w", "v"), ("dt", "t"),
    ("th", "t"), ("gh", "g"),
)
_REPEATS = re.compile(r"(.)\1+")


def normalize(text: str) -> str:
    """Lowercase words without accents or punctuation, one space apart."""
    text = unicodedata.normalize("NFKD", text or "").replace("&", " and ")
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold()
    return " ".join(re.sub(r"[\W_]+", " ", text).split())


def key(text: str) -> str:
    """normalize() without filler words, unless that leaves nothing."""
    words = normalize(text).split()
    kept = [w for w in words if w not in FILLER]
    return " ".join(kept or words)


def sound(text: str) -> str:
    """A rough sound-alike spelling of key(text), without spaces."""
    text = key(text).replace(" ", "")
    for old, new in _SOUNDS:
        text = text.replace(old, new)
    return _REPEATS.sub(r"\1", text)


def same_name(a: str, b: str) -> bool:
    return normalize(a) == normalize(b)


def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def _score(q: str, span: str) -> float:
    return max(_ratio(q, span), _ratio(sound(q), sound(span)))


def similarity(query: str, name: str) -> float:
    """0..1: the best difflib ratio of the query against the name or a run of its words."""
    q = key(query)
    words = key(name).split()
    if not q or not words:
        return 0.0
    size = len(q.split())
    spans = {" ".join(words)}
    for width in range(max(1, size - 1), size + 2):
        for start in range(0, max(1, len(words) - width + 1)):
            spans.add(" ".join(words[start:start + width]))
    return max(_score(q, span) for span in spans)


def _partial(query: str, name: str) -> bool:
    q_words = key(query).split()
    n_words = key(name).split()
    if not q_words or not n_words:
        return False
    if all(any(n.startswith(q) for n in n_words) for q in q_words):
        return True
    return len("".join(n_words)) >= 3 and all(n in q_words for n in n_words)


def _prefer_owned(found: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Two playlists with the same name, one of them the user's: the user means theirs."""
    owned = [p for p in found if p.get("owned")]
    return owned if len(found) > 1 and len(owned) == 1 else found


def _order(found: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(found, key=lambda p: (not p.get("owned"), normalize(p.get("name", ""))))


def match(query: str, playlists: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """(tier, playlists). One playlist: a match. Several: ambiguous. Tier NONE: the closest ones."""
    query = (query or "").strip().strip("\"'“”‘’")
    folded = query.casefold()
    found = [p for p in playlists if (p.get("name") or "").strip().casefold() == folded]
    if found:
        return EXACT, _order(_prefer_owned(found))

    q_key = key(query)
    found = [p for p in playlists if q_key and key(p.get("name") or "") == q_key]
    if found:
        return NORMALIZED, _order(_prefer_owned(found))

    found = [p for p in playlists if _partial(query, p.get("name") or "")]
    if found:
        return PARTIAL, _order(found)

    # (best score on any run of words, score on the whole name): "shrance" fits both "Schranz"
    # and "Hard Schranz 2024" word for word, but "Schranz" is the closer name.
    scored = [((similarity(query, p.get("name") or ""), _score(q_key, key(p.get("name") or ""))), p)
              for p in playlists]
    scored.sort(key=lambda pair: (-pair[0][0], -pair[0][1], normalize(pair[1].get("name", ""))))
    if scored and scored[0][0][0] >= FUZZY_MIN and len(sound(query)) >= 3:
        best, best_whole = scored[0][0]
        return FUZZY, [p for (score, whole), p in scored
                       if score >= max(FUZZY_MIN, best - FUZZY_MARGIN) and whole >= best_whole - FUZZY_MARGIN]
    return NONE, [p for (score, _whole), p in scored[:3] if score >= CLOSEST_MIN]
