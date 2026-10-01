from calista_scorer.crawl import normalize, pick_pages


def L(href, nav=False):
    return {"href": href, "nav": nav}


def test_normalize_drops_fragment_and_lowercases_host():
    assert normalize("https://WWW.A.com/menu#top") == "https://www.a.com/menu"
    assert normalize("https://a.com") == "https://a.com/"


def test_pick_pages_prefers_nav_links_and_stays_on_site():
    links = [L("https://a.com/blog/post-1"), L("https://www.a.com/menu", nav=True),
             L("https://other.com/x", nav=True), L("https://a.com/about", nav=True),
             L("mailto:hi@a.com"), L("https://a.com/#contact", nav=True)]
    assert pick_pages("https://a.com/", links, 3) == ["https://a.com/", "https://www.a.com/menu", "https://a.com/about"]


def test_pick_pages_skips_files_logins_and_duplicates():
    links = [L("https://a.com/menu.pdf"), L("https://a.com/cart"), L("https://a.com/account/login"),
             L("https://a.com/about/"), L("https://a.com/about"), L("https://a.com/wp-admin/")]
    assert pick_pages("https://a.com/", links, 10) == ["https://a.com/", "https://a.com/about/"]


def test_pick_pages_respects_robots_and_limit():
    links = [L(f"https://a.com/p{i}") for i in range(10)] + [L("https://a.com/private/x", nav=True)]
    picked = pick_pages("https://a.com/", links, 3, allowed=lambda u: "/private/" not in u)
    assert picked == ["https://a.com/", "https://a.com/p0", "https://a.com/p1"]


def test_max_pages_one_is_homepage_only():
    assert pick_pages("https://a.com/", [L("https://a.com/x", nav=True)], 1) == ["https://a.com/"]


def test_pick_pages_spreads_across_sections_shallow_first():
    # A mega-menu where one category tree dominates the nav (seen on blinkee.com).
    nav = ["/product-category/led/", "/product-category/led/jewelry/", "/product-category/led/jewelry/necklaces/",
           "/product-category/glow/", "/about/", "/custom/", "/blog/2026/post/"]
    links = [L(f"https://a.com{p}", nav=True) for p in nav]
    assert pick_pages("https://a.com/", links, 5) == [
        "https://a.com/", "https://a.com/product-category/led/", "https://a.com/about/",
        "https://a.com/custom/", "https://a.com/blog/2026/post/"]
    assert pick_pages("https://a.com/", links, 7)[5:] == [
        "https://a.com/product-category/glow/", "https://a.com/product-category/led/jewelry/"]
