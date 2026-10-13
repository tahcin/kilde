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
