from __future__ import annotations

from urllib.parse import quote, unquote, urlsplit, urlunsplit


def normalize_remote_url(url: str) -> str:
    """Encode non-ASCII path/query parts so urllib can open Chinese URLs."""
    parts = urlsplit(url.strip())
    netloc = parts.netloc.encode("idna").decode("ascii")
    path = quote(unquote(parts.path), safe="/%:@")
    query = quote(unquote(parts.query), safe="=&/%:@,+;")
    fragment = quote(unquote(parts.fragment), safe="")
    return urlunsplit((parts.scheme, netloc, path, query, fragment))
