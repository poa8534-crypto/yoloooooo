"""Turn a raw niche into search queries worth sending.

There was no step here at all. A niche was split on whitespace, stopwords were
dropped, and three slices were crossed with the fixed suffixes "cooperative",
"simulator" and "social". "toilet simulator" became, among others,
`toilet simulator simulator`, `toilet social` and `toilet cooperative` -- each
one a real search that spent a real request.

Two stages now. The local model proposes queries in the vocabulary Roblox
players actually use, and a curated vocabulary expands, normalises and refuses
the nonsense. Queries are not evidence: nothing here becomes a fact, and every
result still passes the whole capture and association pipeline.
"""

from __future__ import annotations

import re

# Words that carry no search value on a platform where everything is a Roblox
# game. "simulator" is deliberately absent: it is a real genre here.
NOISE = {
    "a", "an", "and", "the", "for", "with", "of", "in", "on", "to", "or",
    "game", "games", "roblox", "experience", "experiences", "style", "like",
    "some", "any", "new", "best", "good", "popular",
}

# Genre and mechanic words players and titles actually use. Mapped from the
# plain-language concepts someone types into terms that appear in game names.
VOCABULARY: dict[str, tuple[str, ...]] = {
    "obby": ("obby", "parkour", "tower of hell", "difficulty chart"),
    "parkour": ("obby", "parkour", "freerun"),
    "tycoon": ("tycoon", "factory", "business"),
    "pet": ("pet simulator", "pet trading", "hatching", "adopt"),
    "trading": ("trading", "trade plaza", "market"),
    "fishing": ("fishing", "fisch", "angler"),
    "farm": ("farming", "harvest", "garden"),
    "farming": ("farming", "harvest", "garden"),
    "horror": ("horror", "escape", "survival"),
    "escape": ("escape", "horror", "obby"),
    "tower": ("tower defense", "tower of hell", "climbing"),
    "defense": ("tower defense", "td"),
    "survival": ("survival", "sandbox", "open world"),
    "roleplay": ("roleplay", "rp", "town and city"),
    "social": ("hangout", "roleplay", "chat"),
    "simulator": ("simulator", "clicker", "incremental"),
    "clicker": ("clicker", "simulator", "incremental"),
    "racing": ("racing", "driving", "car"),
    "fighting": ("fighting", "battlegrounds", "pvp"),
    "anime": ("anime", "battlegrounds", "rng"),
    "gacha": ("rng", "gacha", "summon"),
    "cooperative": ("co op", "multiplayer", "team"),
    "boat": ("boat", "sailing", "ship"),
    "checkpoint": ("checkpoints", "stages"),
    "hatching": ("hatching", "eggs", "pet simulator"),
}

URL_LIKE = re.compile(r"https?://|www\.|\b[\w-]+\.(?:com|org|net|io|gg)\b", re.IGNORECASE)
WORD = re.compile(r"[^\W_]+", re.UNICODE)
SITE_OPERATOR = re.compile(r"\bsite:", re.IGNORECASE)

MAX_QUERIES = 10
MAX_WORDS = 6


def keywords(niche: str) -> list[str]:
    """The meaningful words in a niche, in order, without repeats."""
    words = [w.lower() for w in WORD.findall(niche or "")]
    return list(dict.fromkeys(w for w in words if w not in NOISE and len(w) > 1))


def sanitise(query: str) -> str:
    """Normalise one query, or return "" if it is not worth sending.

    Refuses anything carrying a URL, and collapses the repeated-word case that
    produced `toilet simulator simulator`.
    """
    # The caller adds the only site restriction. A proposed query carrying
    # one is refused outright rather than salvaged: partially trusting a
    # query that tried to redirect the search is worse than losing it.
    if not query or URL_LIKE.search(query) or SITE_OPERATOR.search(query):
        return ""
    words = [w.lower() for w in WORD.findall(query)]
    deduped: list[str] = []
    for word in words:
        if word in NOISE:
            continue  # a query of stopwords is not a query
        if deduped and deduped[-1] == word:
            continue  # "simulator simulator"
        if word in deduped:
            continue  # the same term twice in one query adds nothing
        deduped.append(word)
    if not deduped:
        return ""
    return " ".join(deduped[:MAX_WORDS])


def vocabulary_queries(niche: str) -> list[str]:
    """Queries derived from the curated vocabulary alone.

    Also the fallback: if the model is unavailable or answers with nothing
    usable, discovery still has something better than a word-chopper.
    """
    terms = keywords(niche)
    if not terms:
        return []
    base = " ".join(terms[:MAX_WORDS])
    queries = [base]
    # The niche's own words, paired with the idiom each one maps to.
    for term in terms:
        for synonym in VOCABULARY.get(term, ()):
            if synonym != term:
                queries.append(f"{term} {synonym}")
    # A two-word core, which often matches titles better than the full phrase.
    if len(terms) > 2:
        queries.append(" ".join(terms[:2]))
        queries.append(" ".join(terms[-2:]))
    return queries


def finalise(queries, niche: str, prefix: str = "site:roblox.com/games") -> list[str]:
    """Clean, de-duplicate and cap a set of proposed queries.

    Ordering is stable so a run's discovery is reproducible: the caller can see
    why a round searched what it did.
    """
    seen: list[str] = []
    for candidate in list(queries) + vocabulary_queries(niche):
        cleaned = sanitise(candidate)
        if cleaned and cleaned not in seen:
            seen.append(cleaned)
    if not seen:
        cleaned = sanitise(niche)
        seen = [cleaned] if cleaned else []
    return [f"{prefix} {query}".strip() for query in seen[:MAX_QUERIES]]
