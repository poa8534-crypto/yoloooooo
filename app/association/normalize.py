"""Deterministic, versioned text normalization for the association engine.

Nothing in this module calls a model. Every function is a pure function of its
inputs so that an association recorded today can be recomputed byte-for-byte
tomorrow. Raw titles and descriptions are never mutated: callers keep the raw
text and store the derived views alongside it.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# v2 tokenizes Unicode letters instead of ASCII only. Under v1 every
# non-Latin title tokenized to nothing, which made it score zero against every
# candidate and get flagged as a generic title.
NORMALIZATION_VERSION = "norm-v2"

# Removed only from the boilerplate-stripped feature view. The raw text and the
# plain normalized text keep these words.
BOILERPLATE_TOKENS = frozenset({
    "official", "roblox", "gameplay", "guide", "guides", "walkthrough",
    "tutorial", "review", "tips", "tricks", "update", "new", "best", "top",
    "free", "codes", "code", "shorts", "live", "stream", "full", "part",
    "episode", "ep", "playthrough", "montage", "compilation", "showcase",
    "video", "youtube",
})

STOPWORD_TOKENS = frozenset({
    "the", "a", "an", "of", "and", "or", "in", "on", "at", "for", "with",
    "to", "is", "it", "this", "that", "my", "your", "i", "you", "we",
})

TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "utm_name", "utm_reader", "gclid", "gclsrc", "dclid", "fbclid",
    "msclkid", "yclid", "igshid", "mc_cid", "mc_eid", "_ga", "_gl", "ref",
    "ref_src", "ref_url", "referrer", "si", "feature", "pp", "spm", "scid",
    "vero_id", "vero_conv", "s_kwcid", "cmpid", "campaignid", "adgroupid",
})

# Titles that describe a list or a category rather than one experience.
GENERIC_TITLE_PATTERNS = (
    re.compile(r"\b(?:top|best|worst)\s*\d*\s+roblox\b"),
    re.compile(r"\broblox\s+games?\s+(?:to\s+play|you\s+should|list|tier)\b"),
    re.compile(r"\b(?:every|all)\s+roblox\s+games?\b"),
    re.compile(r"\broblox\s+(?:funny|random|moments|memes|tier\s*list)\b"),
    re.compile(r"\bplaying\s+random\s+roblox\b"),
    re.compile(r"\bi\s+played\s+\d+\s+roblox\b"),
)

# Text that tries to issue instructions to a downstream reader. Detected so the
# content can be stored as untrusted. Detection never changes a verdict.
INJECTION_PATTERNS = (
    re.compile(r"\bignore\s+(?:all\s+|any\s+)?(?:previous|prior|above)\s+instructions?\b"),
    re.compile(r"\bdisregard\s+(?:the\s+)?(?:previous|prior|above|system)\b"),
    re.compile(r"\b(?:you\s+are\s+now|act\s+as|pretend\s+to\s+be)\s+(?:a|an|the)\b"),
    re.compile(r"\bsystem\s*(?:prompt|message)\s*:"),
    re.compile(r"\b(?:always|must)\s+(?:auto[-\s]?)?(?:approve|associate|match)\b"),
    re.compile(r"\bthis\s+(?:video|page)\s+is\s+(?:definitely|certainly|officially)\s+about\b"),
    re.compile(r"</?\s*(?:system|assistant|instructions?)\s*>"),
)

# Unicode word characters minus the underscore. An ASCII-only class silently
# erased Japanese, Korean, Chinese, Cyrillic, Greek, Thai and accented Latin
# titles, which is most of Roblox outside English.
_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)
_WS_RE = re.compile(r"\s+")

PLACE_URL_RE = re.compile(
    r"roblox\.com/(?:[a-z]{2}(?:-[a-z]{2})?/)?games/(\d+)", re.IGNORECASE
)
PLACE_PARAM_RE = re.compile(r"\bplaceid=(\d+)", re.IGNORECASE)
UNIVERSE_PATH_RE = re.compile(r"roblox\.com/universes/(\d+)", re.IGNORECASE)
UNIVERSE_PARAM_RE = re.compile(r"\buniverseids?=(\d+)", re.IGNORECASE)
ROBLOX_GAME_URL_RE = re.compile(
    r"https?://(?:www\.)?roblox\.com/(?:[a-z]{2}(?:-[a-z]{2})?/)?games/(\d+)[^\s\"'<>]*",
    re.IGNORECASE,
)
YOUTUBE_VIDEO_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?(?:[^\s]*&)?v=|shorts/|embed/|live/)|youtu\.be/)"
    r"([A-Za-z0-9_-]{11})(?![A-Za-z0-9_-])",
    re.IGNORECASE,
)
YOUTUBE_CHANNEL_RE = re.compile(r"youtube\.com/channel/(UC[A-Za-z0-9_-]{22})", re.IGNORECASE)
HANDLE_RE = re.compile(r"(?:^|[\s(/])@([A-Za-z0-9._-]{3,30})")


def nfkc(text: str) -> str:
    """Unicode NFKC, so that visually identical titles compare equal."""
    return unicodedata.normalize("NFKC", text or "")


def normalize_text(raw: str) -> str:
    """Lowercased, NFKC-folded, whitespace-collapsed view of the raw text."""
    folded = nfkc(raw).casefold()
    folded = folded.replace("’", "'").replace("‘", "'")
    folded = re.sub(r"[^\w\s'@:/?&=.\-]", " ", folded, flags=re.UNICODE)
    return _WS_RE.sub(" ", folded).strip()


def word_tokens(raw: str) -> list[str]:
    return _WORD_RE.findall(normalize_text(raw))


def content_tokens(raw: str) -> list[str]:
    """Word tokens with Roblox boilerplate and stopwords removed.

    Used only for similarity features; the stored raw text is untouched.
    """
    return [
        token for token in word_tokens(raw)
        if token not in BOILERPLATE_TOKENS and token not in STOPWORD_TOKENS
    ]


def char_ngrams(raw: str, n: int = 3) -> set[str]:
    compact = "".join(word_tokens(raw))
    if len(compact) < n:
        return {compact} if compact else set()
    return {compact[i:i + n] for i in range(len(compact) - n + 1)}


def normalized_name(raw: str) -> str:
    """Canonical comparison key for an experience or channel name."""
    return " ".join(word_tokens(raw))


def content_key(raw: str) -> str:
    return " ".join(content_tokens(raw))


def is_generic_title(raw: str) -> bool:
    text = normalize_text(raw)
    if any(pattern.search(text) for pattern in GENERIC_TITLE_PATTERNS):
        return True
    # A title that is nothing but boilerplate names no experience at all.
    return not content_tokens(raw)


def detect_injection(raw: str) -> list[str]:
    """Codes for instruction-shaped text found inside untrusted content."""
    text = normalize_text(raw)
    return sorted(
        f"injection_pattern_{index}"
        for index, pattern in enumerate(INJECTION_PATTERNS)
        if pattern.search(text)
    )


def normalize_url(url: str) -> str:
    """Canonical URL with tracking parameters removed.

    Returns "" for anything that is not resolvable as an absolute http(s) URL,
    so a caller can never mistake a fragment of prose for a link.
    """
    candidate = nfkc(url or "").strip()
    if not candidate:
        return ""
    if candidate.startswith("//"):
        candidate = "https:" + candidate
    if not re.match(r"^https?://", candidate, re.IGNORECASE):
        if not re.match(r"^[\w.-]+\.[a-z]{2,}(?:[/:?#]|$)", candidate, re.IGNORECASE):
            return ""
        candidate = "https://" + candidate
    parts = urlsplit(candidate)
    try:
        host = (parts.hostname or "").lower().removeprefix("www.")
        port = parts.port
    except ValueError:
        return ""
    if not host:
        return ""
    if port and port not in (80, 443):
        host = f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", parts.path) or "/"
    if len(path) > 1:
        path = path.rstrip("/")
    query = sorted(
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=False)
        if key.lower() not in TRACKING_PARAMS and not key.lower().startswith("utm_")
    )
    return urlunsplit(("https", host, path, urlencode(query), ""))


@dataclass(frozen=True)
class Identifiers:
    """Explicit identifiers lifted out of a title, description or URL."""

    place_ids: frozenset[str] = frozenset()
    universe_ids: frozenset[str] = frozenset()
    roblox_game_urls: tuple[str, ...] = ()
    youtube_video_ids: frozenset[str] = frozenset()
    youtube_channel_ids: frozenset[str] = frozenset()
    creator_handles: frozenset[str] = frozenset()

    @property
    def roblox_ids(self) -> frozenset[str]:
        return self.place_ids | self.universe_ids

    def as_json(self) -> dict[str, list[str]]:
        return {
            "place_ids": sorted(self.place_ids),
            "universe_ids": sorted(self.universe_ids),
            "roblox_game_urls": list(self.roblox_game_urls),
            "youtube_video_ids": sorted(self.youtube_video_ids),
            "youtube_channel_ids": sorted(self.youtube_channel_ids),
            "creator_handles": sorted(self.creator_handles),
        }

    @classmethod
    def from_json(cls, payload: dict | None) -> Identifiers:
        payload = payload or {}
        return cls(
            place_ids=frozenset(payload.get("place_ids", [])),
            universe_ids=frozenset(payload.get("universe_ids", [])),
            roblox_game_urls=tuple(payload.get("roblox_game_urls", [])),
            youtube_video_ids=frozenset(payload.get("youtube_video_ids", [])),
            youtube_channel_ids=frozenset(payload.get("youtube_channel_ids", [])),
            creator_handles=frozenset(payload.get("creator_handles", [])),
        )


def canonical_game_url(place_id: str) -> str:
    """Identity form of a Roblox game URL.

    The trailing slug is a display name that changes when an experience is
    renamed, so it is dropped: only the place ID identifies the game.
    """
    return f"https://roblox.com/games/{place_id}"


def extract_identifiers(*texts: str) -> Identifiers:
    """Pull direct identifiers out of arbitrary untrusted text.

    Only syntactically unambiguous identifiers are returned. A bare number is
    never treated as a Roblox ID: it has to appear inside a Roblox URL or an
    explicit `placeId=` / `universeIds=` parameter.
    """
    blob = " ".join(nfkc(text or "") for text in texts)
    lowered = blob.lower()
    place_ids = {match.group(1) for match in PLACE_URL_RE.finditer(blob)}
    place_ids |= {match.group(1) for match in PLACE_PARAM_RE.finditer(lowered)}
    universe_ids = {match.group(1) for match in UNIVERSE_PATH_RE.finditer(blob)}
    universe_ids |= {match.group(1) for match in UNIVERSE_PARAM_RE.finditer(lowered)}
    urls = tuple(dict.fromkeys(
        canonical_game_url(match.group(1))
        for match in ROBLOX_GAME_URL_RE.finditer(blob)
        if match.group(1)
    ))
    return Identifiers(
        place_ids=frozenset(place_ids),
        universe_ids=frozenset(universe_ids),
        roblox_game_urls=tuple(url for url in urls if url),
        youtube_video_ids=frozenset(
            match.group(1) for match in YOUTUBE_VIDEO_RE.finditer(blob)
        ),
        youtube_channel_ids=frozenset(
            match.group(1) for match in YOUTUBE_CHANNEL_RE.finditer(blob)
        ),
        creator_handles=frozenset(
            match.group(1).casefold() for match in HANDLE_RE.finditer(blob)
        ),
    )


@dataclass(frozen=True)
class NormalizedText:
    """Every derived view of one piece of raw text, plus its identifiers."""

    raw_title: str
    raw_description: str = ""
    raw_url: str = ""
    version: str = NORMALIZATION_VERSION
    normalized_title: str = field(default="", compare=False)
    normalized_description: str = field(default="", compare=False)
    canonical_url: str = field(default="", compare=False)
    name_key: str = field(default="", compare=False)
    content_key_value: str = field(default="", compare=False)
    tokens: tuple[str, ...] = field(default=(), compare=False)
    content_token_tuple: tuple[str, ...] = field(default=(), compare=False)
    identifiers: Identifiers = field(default_factory=Identifiers, compare=False)
    generic: bool = field(default=False, compare=False)
    injection_codes: tuple[str, ...] = field(default=(), compare=False)

    def as_json(self) -> dict:
        return {
            "version": self.version,
            "normalized_title": self.normalized_title,
            "normalized_description": self.normalized_description,
            "canonical_url": self.canonical_url,
            "name_key": self.name_key,
            "content_key": self.content_key_value,
            "tokens": list(self.tokens),
            "content_tokens": list(self.content_token_tuple),
            "identifiers": self.identifiers.as_json(),
            "generic_title": self.generic,
            "injection_codes": list(self.injection_codes),
        }


def normalize_record(title: str, description: str = "", url: str = "") -> NormalizedText:
    """Build the full normalized view of one subject or candidate."""
    return NormalizedText(
        raw_title=title or "",
        raw_description=description or "",
        raw_url=url or "",
        normalized_title=normalize_text(title),
        normalized_description=normalize_text(description),
        canonical_url=normalize_url(url),
        name_key=normalized_name(title),
        content_key_value=content_key(title),
        tokens=tuple(word_tokens(title)),
        content_token_tuple=tuple(content_tokens(title)),
        identifiers=extract_identifiers(title, description, url),
        generic=is_generic_title(title),
        injection_codes=tuple(detect_injection(f"{title} {description}")),
    )


def content_fingerprint(title: str, description: str = "") -> str:
    """Stable hash used to collapse duplicated or syndicated content."""
    payload = f"{NORMALIZATION_VERSION}|{content_key(title)}|{content_key(description)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
