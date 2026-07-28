# autoconsent (vendored)

Two static files pulled from DuckDuckGo's
[autoconsent](https://github.com/duckduckgo/autoconsent) library (pinned at
`16.16.0`, MPL-2.0) — the same cookie-consent auto-handling engine used in
Firefox and Brave. Used by `maps_crawler/site_crawler.py` to opt in to
cookie banners (across ~780 known consent-management platforms — OneTrust,
Cookiebot, Quantcast, Didomi, Sourcepoint, etc., plus a generic fallback)
before screenshotting a page, so the screenshot shows the actual site
instead of a consent overlay.

No Node/npm needed at runtime — these are the library's pre-built
browser bundle and rule data, fetched directly:

```
curl -sL -o autoconsent.playwright.js \
  https://cdn.jsdelivr.net/npm/@duckduckgo/autoconsent@16.16.0/dist/autoconsent.playwright.js
curl -sL -o rules.json \
  https://cdn.jsdelivr.net/npm/@duckduckgo/autoconsent@16.16.0/rules/rules.json
```

To update, bump the version in both URLs above and re-fetch. This only
handles known/opt-in-able consent flows — sites with a hard "accept or
pay" wall (e.g. some UK news sites) aren't cleared by this and fall
through to `site_crawler.py`'s text-match and vision-based fallbacks.
