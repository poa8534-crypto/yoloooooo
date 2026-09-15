"""Rank search leads before spending the bounded game-inspection budget.

Search titles and snippets are discovery hints only. This module never creates
facts, niche associations, confidence values, or recommendation scores.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from urllib.parse import unquote, urlparse

from .connectors import roblox_place_id_from_url
from .query_planner import VOCABULARY, keywords

WORD = re.compile(r"[^\W_]+", re.UNICODE)
SOURCE_ORDER = ("roblox_search", "searxng_search", "tavily_search")
# These are useful qualifiers but are too broad to establish the subject of a
# multi-part niche on their own. "Simulator" from a search expansion must not
# turn every simulator in Roblox into a farming lead.
BROAD_TERMS = {"simulator", "cooperative", "multiplayer", "team", "social", "roleplay"}


@dataclass(frozen=True)
class SearchLead:
    kind: str  # universe or place; neither is a verified niche match
    entity_id: str
    source: str
    title: str
    description: str
    position: int
    rank: int  # internal ordering only, never a market score


def _tokens(text: str) -> list[str]:
    return [word.casefold() for word in WORD.findall(text or "")]


def _matches(term: str, word: str) -> bool:
    # Farm/farming, fish/fishing and similar title variations should still find
    # one another. A fixed four-character window was wrong in both directions:
    # it missed the short genre words the vocabulary is built on ("pet" never
    # reached "pets", "rp" never reached anything) and it joined words that
    # merely start alike ("tower"/"towel", "trade"/"tradition"). Requiring one
    # whole word to begin the other keeps the inflections and drops the rest.
    if term == word:
        return True
    shorter, longer = sorted((term, word), key=len)
    return len(shorter) >= 3 and longer.startswith(shorter)


def _rank(title: str, description: str, niche: str, query: str) -> int:
    core = keywords(niche)
    if not core:
        return 0
    title_words, detail_words = _tokens(title), _tokens(description)
    subject = [term for term in core if term not in BROAD_TERMS] or core
    subject_aliases = list(dict.fromkeys(
        alias for term in subject for phrase in VOCABULARY.get(term, ())
        for alias in keywords(phrase) if alias not in core
    ))

    def count(terms, words):
        return sum(any(_matches(term, word) for word in words) for term in terms)

    subject_title = count(subject, title_words)
    subject_detail = count(subject, detail_words)
    alias_title = count(subject_aliases, title_words)
    alias_detail = count(subject_aliases, detail_words)
    # Model-proposed query expansions cannot qualify a game. A source with no
    # lexical matches is retained, low-ranked, by leads_from_search so titles
    # using unexpected player vocabulary are not silently lost.
    if (title or description) and not (subject_title or subject_detail or alias_title or alias_detail):
        return -1
    broad_title = count([term for term in core if term not in subject], title_words)
    query_bonus = count([term for term in keywords(query) if term not in core + subject_aliases], title_words)
    return (subject_title * 8 + alias_title * 5 + subject_detail * 2 + alias_detail
            + broad_title + min(query_bonus, 2))


def niche_relevance_rank(title: str, description: str, niche: str) -> int:
    """Internal ordering from captured Roblox metadata, not a niche verdict."""
    return _rank(title, description, niche, niche)


def leads_from_search(payload: dict, source: str, niche: str, query: str) -> list[SearchLead]:
    leads: list[SearchLead] = []
    if source == "roblox_search":
        entries = [item for group in payload.get("searchResults", []) or []
                   for item in group.get("contents", []) or []]
        for position, item in enumerate(entries):
            uid = item.get("universeId")
            if not uid:
                continue
            title = str(item.get("name") or item.get("title") or "")
            detail = str(item.get("description") or "")
            rank = _rank(title, detail, niche, query)
            leads.append(SearchLead("universe", str(uid), source, title, detail, position, rank))
    else:
        for position, item in enumerate(payload.get("results", []) or []):
            url = str(item.get("url") or "")
            place_id = roblox_place_id_from_url(url)
            if not place_id:
                continue
            title = str(item.get("title") or "")
            # URL slugs help when a search engine supplies only a generic title.
            slug = unquote(urlparse(url).path.rsplit("/", 1)[-1]).replace("-", " ")
            detail = f"{slug} {str(item.get('content') or '')}"
            rank = _rank(title, detail, niche, query)
            leads.append(SearchLead("place", place_id, source, title, detail, position, rank))
    # Ranking may reorder and trim a source, never empty one that answered.
    # Roblox's own search has already ranked its results for the query, and a
    # niche whose games are named in words the niche does not contain -- a
    # "toilet simulator" answered with "Skibidi Battle" and "Bathroom Escape
    # Obby" -- would otherwise yield no candidates at all, with every source
    # recorded as having answered and nothing recorded as having failed.
    if leads and all(lead.rank < 0 for lead in leads):
        leads = [replace(lead, rank=0) for lead in leads]
    else:
        leads = [lead for lead in leads if lead.rank >= 0]
    best: dict[tuple[str, str], SearchLead] = {}
    for lead in leads:
        key = (lead.kind, lead.entity_id)
        if key not in best or (lead.rank, -lead.position) > (best[key].rank, -best[key].position):
            best[key] = lead
    return sorted(best.values(), key=lambda lead: (-lead.rank, lead.position, lead.entity_id))


def select_diverse(leads_by_source: dict[str, list[SearchLead]], limit: int) -> list[SearchLead]:
    """Take the highest-ranked leads from each answering source in turns.

    One large Roblox response can no longer exhaust the universe budget before
    web leads are considered. If a source has no usable leads, the others fill
    the unused slots. Duplicate URLs from two engines count only once.
    """
    selected: list[SearchLead] = []
    seen: set[tuple[str, str]] = set()
    cursors = {source: 0 for source in SOURCE_ORDER}
    while len(selected) < limit:
        progressed = False
        for source in SOURCE_ORDER:
            pool = leads_by_source.get(source, [])
            while cursors[source] < len(pool):
                lead = pool[cursors[source]]
                cursors[source] += 1
                key = (lead.kind, lead.entity_id)
                if key in seen:
                    continue
                seen.add(key)
                selected.append(lead)
                progressed = True
                break
            if len(selected) >= limit:
                break
        if not progressed:
            break
    return selected
