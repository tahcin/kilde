"""Site-builder default pages and posts (Wix, WordPress, Squarespace, Elementor, Webflow).

A fresh site ships with demo content such as Wix's "This Is Your First Post", WordPress's "Hello world!"
and "Sample Page", blank or copied pages ("blank-1", "copy-of-home") and test pages. They exist on the
real site but say nothing about the company, so they are never published as page updates or news.
One shared helper, so the news collector, sitemap page updates and the gate drop the same pages.
"""
from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit

# The last path segment (slug) of a platform default page or post. Matched against the whole slug,
# so a real page such as /hydraulic-test-system is not caught by "test".
TEMPLATE_SLUG = re.compile(
    r"(?i)^(?:"
    r"hello-world(?:-\d+)?|hei-verden(?:-\d+)?|hallo-verden(?:-\d+)?|sample-page(?:-\d+)?|sample-post(?:-\d+)?|"
    r"eksempelside(?:-\d+)?|eksempelinnlegg(?:-\d+)?|lorem-ipsum(?:-[\w-]*)?|"
    r"(?:this-is-)?(?:the-title-of-)?(?:your|my)-(?:very-)?first-(?:blog-)?post(?:-\d+)?|"
    r"welcome-to-(?:your|my|our)-(?:new-)?(?:blog|website|site)(?:-\d+)?|"
    r"velkommen-til-(?:bloggen|min-blogg|var-blogg|din-blogg)(?:-\d+)?|"
    r"grow-your-blog(?:-community)?(?:-\d+)?|design-a-stunning-blog(?:-\d+)?|blog-post-title(?:-\w+)?|"
    r"blank(?:-page)?(?:-\d+)?|untitled(?:-page)?(?:-\d+)?|uten-tittel(?:-\d+)?|new-page(?:-\d+)?|ny-side(?:-\d+)?|"
    r"copy-of-[\w-]+|kopi-av-[\w-]+|"
    r"test(?:-?side|-?page|-?post|-?innlegg)?(?:-\d+)?|"
    r"elementor-\d+|post-format-[\w-]+|template-(?:page|post)(?:-[\w-]+)?"
    r")$")

# Default titles of those pages and posts (whole title, case-insensitive, trailing punctuation ignored).
TEMPLATE_TITLE = re.compile(
    r"(?i)^(?:hello,? world|hei,? verden|hallo,? verden|sample page|sample post|eksempelside|eksempelinnlegg|"
    r"(?:this is )?(?:the title of )?(?:your|my) (?:very )?first (?:blog )?post|your blog'?s first post|"
    r"welcome to (?:your|my|our) (?:new )?(?:blog|website|site)|"
    r"velkommen til (?:bloggen|min blogg|vår blogg|din blogg)|"
    r"dette er (?:tittelen på )?(?:ditt|mitt) første (?:blogg)?innlegg|"
    r"grow your blog(?: community)?|design a stunning blog|blog post title(?: \w+)?|blank(?: page)?(?: \d+)?|"
    r"untitled(?: page)?(?: \d+)?|uten tittel(?: \d+)?|new page(?: \d+)?|ny side(?: \d+)?|copy of .+|kopi av .+|"
    r"test(?: ?side| ?page| ?post| ?innlegg)?(?: \d+)?|testinnlegg|lorem ipsum.*)[\s.!]*$")

def _slug(url: str) -> str:
    try:
        path = urlsplit(url.strip()).path
    except ValueError:
        return ""
    segs = [s for s in unquote(path).split("/") if s]
    if not segs:
        return ""
    last = segs[-1]
    return re.sub(r"\.(?:html?|php|aspx?)$", "", last, flags=re.I)


def is_template_url(url: str) -> bool:
    """True when the URL's last path segment is a site builder's default page or post."""
    s = _slug(url or "")
    return bool(s) and bool(TEMPLATE_SLUG.match(s))


def is_template_title(title: str) -> bool:
    """True when a page or post title is a site builder's default title."""
    t = " ".join(str(title or "").split())
    return bool(t) and bool(TEMPLATE_TITLE.match(t))


def template_post(title: str | None, url: str | None) -> bool:
    """True for a site builder's default post or page, by its title or its URL slug."""
    return bool((title and is_template_title(title)) or (url and is_template_url(url)))


# Stub posts: a post with (almost) no body text is not news when something else marks it as filler: a one-word
# title (a product or demo item such as "Purefloor"), every other post on the site being a builder default
# ("Hello world!"), or a theme's foreign-language default category in its path (an imported shop demo).
# Real posts on page-builder sites often have empty excerpts too, so emptiness alone never decides.
# Data (WordPress REST excerpts on 19 sites from the 1,000-company runs): posts with body text had 5 or
# more words (median about 50); empty excerpts were page-builder posts with descriptive titles.
MIN_BODY_WORDS = 5
DEMO_CATEGORIES = {"tienda", "sin-categoria", "sin-categorizar", "non-classe", "non-classifie", "senza-categoria",
                   "geen-categorie", "sem-categoria", "bez-kategorii", "allgemein", "shop-demo", "demo"}


def body_word_count(html_text: str | None) -> int | None:
    """Visible words in a post body or excerpt (tags, entities and the [...] read-more marker removed);
    None when the source gave no body at all."""
    if html_text is None:
        return None
    import html as _html
    text = _html.unescape(re.sub(r"<[^>]+>", " ", str(html_text)))
    text = re.sub(r"\[(?:…|\.\.\.)\]", " ", text)
    return len(re.findall(r"[^\W\d_]{2,}", text))


def stub_posts(items: list[dict]) -> set[str]:
    """URLs of items ({title, url, body_words}) that are filler, not news (see the rule above)."""
    out: set[str] = set()
    for it in items:
        n = it.get("body_words")
        if n is None or n >= MIN_BODY_WORDS or template_post(it.get("title"), it.get("url")):
            continue
        others = [x for x in items if x is not it]
        segs = {x.lower() for x in unquote(urlsplit(it.get("url") or "").path).split("/") if x}
        if (len(str(it.get("title") or "").split()) <= 1
                or (others and all(template_post(x.get("title"), x.get("url")) for x in others))
                or segs & DEMO_CATEGORIES):
            out.add(it.get("url"))
    return out
