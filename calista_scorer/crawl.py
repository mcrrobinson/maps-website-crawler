"""Choose which internal pages of a site to score, from the links found on its homepage."""
from __future__ import annotations

import re
import urllib.request
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.robotparser import RobotFileParser

SKIP_EXT_RE = re.compile(
    r"\.(pdf|jpe?g|png|gif|webp|svg|ico|zip|rar|gz|mp3|mp4|mov|avi|webm|docx?|xlsx?|pptx?|csv|xml|json|txt|ics)$",
    re.I)
# Pages that are rarely designed (or are behind a login) and say little about the look of a site.
SKIP_PATH_RE = re.compile(
    r"/(login|log-in|signin|sign-in|signup|sign-up|register|account|my-account|cart|basket|checkout|"
    r"wp-admin|wp-login\.php|admin|search|feed|cdn-cgi)(/|$)", re.I)


def site_key(netloc: str) -> str:
    return netloc.lower().removeprefix("www.")


def normalize(url: str) -> str:
    url, _ = urldefrag(url)
    u = urlparse(url)
    path = u.path or "/"
    return u._replace(scheme=u.scheme.lower(), netloc=u.netloc.lower(), path=path).geturl()


def pick_pages(home_url: str, links: list[dict], max_pages: int, allowed=lambda url: True) -> list[str]:
    """Return up to max_pages URLs: the homepage first, then same-site links.

    Header/nav links come first (the sections the site itself considers primary). To get a
    spread of the site rather than one branch of a mega-menu, links are grouped by their
    first path segment and picked round-robin across groups, shallowest page first.
    """
    home = normalize(home_url)
    key = site_key(urlparse(home).netloc)
    seen = {home, home.rstrip("/")}
    nav, rest = [], []
    for link in links:
        href = link.get("href") or ""
        u = urlparse(href)
        if u.scheme not in ("http", "https") or site_key(u.netloc) != key:
            continue
        if SKIP_EXT_RE.search(u.path) or SKIP_PATH_RE.search(u.path):
            continue
        url = normalize(urljoin(home, href))
        if url in seen or url.rstrip("/") in seen:
            continue
        seen.update({url, url.rstrip("/")})
        (nav if link.get("nav") else rest).append(url)

    groups: dict[str, list[str]] = {}
    for url in nav + rest:
        if allowed(url):
            parts = [p for p in urlparse(url).path.split("/") if p]
            groups.setdefault(parts[0] if parts else "", []).append(url)
    queues = [sorted(g, key=lambda u: urlparse(u).path.strip("/").count("/")) for g in groups.values()]
    picked = [home]
    while len(picked) < max_pages and any(queues):
        for q in queues:
            if q and len(picked) < max_pages:
                picked.append(q.pop(0))
    return picked


def robots_checker(home_url: str, timeout: float = 10.0):
    """Return a function telling whether robots.txt allows fetching a URL.

    If robots.txt can't be fetched (missing, blocked, timeout) everything is allowed: a
    403 here usually means the bot filter rejected urllib, not that the owner asked
    crawlers to stay away.
    """
    u = urlparse(home_url)
    robots_url = f"{u.scheme}://{u.netloc}/robots.txt"
    rp = RobotFileParser()
    try:
        with urllib.request.urlopen(robots_url, timeout=timeout) as resp:
            rp.parse(resp.read().decode("utf-8", "replace").splitlines())
    except Exception:
        return lambda url: True
    return lambda url: rp.can_fetch("*", url)
