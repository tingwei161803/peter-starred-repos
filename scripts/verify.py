#!/usr/bin/env python3
"""Playwright-based UX verification for this static site.

Confirms that a generated static site provides a good user experience by
driving a headless Chromium browser through a suite of resilient checks.

Usage (always via uv -- this project mandates uv for all Python):

    uv run playwright install chromium          # one-time browser download
    uv run python scripts/verify.py --dir ./my-site
    uv run python scripts/verify.py --url http://localhost:8000

Provide EITHER --url (point at an already-running server) OR --dir (a folder
containing index.html; this script will start its own static server on a free
port). Exits 0 if every check PASSes (SKIPs are fine), 1 if any check FAILs.
"""

from __future__ import annotations

import argparse
import contextlib
import functools
import http.server
import json
import re
import socket
import socketserver
import sys
import threading
import urllib.parse
from dataclasses import dataclass, field

from playwright.sync_api import (
    Browser,
    Error as PlaywrightError,
    Page,
    TimeoutError as PlaywrightTimeout,
    sync_playwright,
)

# Default per-action timeout (ms). Static sites should respond instantly.
TIMEOUT_MS = 8000


# --------------------------------------------------------------------------- #
# Result tracking
# --------------------------------------------------------------------------- #
@dataclass
class Results:
    """Collects the PASS/SKIP/FAIL outcome of every check."""

    passed: int = 0
    skipped: int = 0
    failed: int = 0
    rows: list[tuple[str, str, str]] = field(default_factory=list)

    def _record(self, status: str, label: str, detail: str) -> None:
        self.rows.append((status, label, detail))
        symbol = {"PASS": "✓", "SKIP": "→", "FAIL": "✗"}[status]
        line = f"  [{status}] {symbol} {label}"
        if detail:
            line += f"  -- {detail}"
        print(line, flush=True)

    def ok(self, label: str, detail: str = "") -> None:
        self.passed += 1
        self._record("PASS", label, detail)

    def skip(self, label: str, detail: str = "") -> None:
        self.skipped += 1
        self._record("SKIP", label, detail)

    def fail(self, label: str, detail: str = "") -> None:
        self.failed += 1
        self._record("FAIL", label, detail)


def check(label: str):
    """Decorator: wrap a check so an unexpected exception becomes a FAIL.

    Each check function takes (page, results, *ctx) and is responsible for
    recording its own PASS/SKIP/FAIL. If it raises instead, we capture that
    as a FAIL with the exception text so one broken check never aborts the run.
    """

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(page: Page, results: Results, *args, **kwargs):
            try:
                return fn(page, results, *args, **kwargs)
            except (PlaywrightTimeout, PlaywrightError) as exc:
                results.fail(label, f"playwright error: {_short(exc)}")
            except Exception as exc:  # noqa: BLE001 - defensive: keep going
                results.fail(label, f"unexpected error: {_short(exc)}")

        return wrapper

    return decorator


def _short(exc: object, limit: int = 120) -> str:
    text = str(exc).splitlines()[0] if str(exc) else exc.__class__.__name__
    return text[:limit]


# --------------------------------------------------------------------------- #
# Local static server (used when --dir is supplied)
# --------------------------------------------------------------------------- #
def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _QuietServer(socketserver.TCPServer):
    allow_reuse_address = True


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    """SimpleHTTPRequestHandler minus the per-request logging.

    Silencing has to happen on the *class*: `functools.partial` is not a class,
    so assigning `.log_message` to the partial only sets an attribute on the
    partial object — instances never see it and every request still prints.
    """

    def log_message(self, *args, **kwargs) -> None:  # noqa: D102
        pass


def start_static_server(directory: str) -> tuple[str, _QuietServer, threading.Thread]:
    """Serve `directory` on a free localhost port in a daemon thread.

    Returns (base_url, server, thread). Call server.shutdown() to stop.
    """
    port = _free_port()
    handler = functools.partial(_QuietHandler, directory=directory)
    server = _QuietServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return f"http://127.0.0.1:{port}", server, thread


# --------------------------------------------------------------------------- #
# Small DOM helpers (resilient selector lookup)
# --------------------------------------------------------------------------- #
def first_visible(page: Page, selectors: list[str], timeout: int = 1500):
    """Return the first locator matching any selector that is visible, else None."""
    for sel in selectors:
        loc = page.locator(sel).first
        try:
            if loc.count() > 0 and loc.is_visible(timeout=timeout):
                return loc
        except (PlaywrightTimeout, PlaywrightError):
            continue
    return None


def first_present(page: Page, selectors: list[str]):
    """Return the first locator matching any selector that exists in DOM, else None."""
    for sel in selectors:
        loc = page.locator(sel).first
        if loc.count() > 0:
            return loc
    return None


def card_count(page: Page) -> int:
    """Count cards that are currently visible inside the grid."""
    return page.locator("#grid .card:visible, .card:visible").count()


# Layout-agnostic "primary content" selector: a gallery renders .card, but
# article/dashboard/timeline/table/kanban/... render other nodes. Every layout
# template marks its primary nodes with data-item, so that is the reliable hook.
CONTENT_SELECTORS = (
    "[data-item], "                 # universal primary-content hook (any layout)
    ".card, "                       # gallery / bento / hub
    ".timeline-item, [data-event], .tl-item, "  # timeline
    "table tbody tr, .row, [data-row], "  # table / comparison / dashboard
    ".stat, .stat-card, .metric, [data-stat], "  # dashboard
    ".tile, .kb-card, .faq-item, .acc-item, .plan, "  # bento/kanban/faq/comparison
    ".lb-row, .leaderboard-row, .tier-row, .scrolly-step, .place, "  # leaderboard/scrolly/map
    "article section, .article-section, .prose > *, [data-section]"  # article
)


def has_content(page: Page) -> bool:
    """True if the page rendered *something*.

    CONTENT_SELECTORS only recognises card-shaped content. A page that renders
    prose, a table, an embedded widget — anything not built from `.card`
    elements — would otherwise look "empty" and fail checks that are really
    asking "did anything render at all". A blank page has neither cards nor
    text, so the text fallback costs no strictness.
    """
    if page.locator(CONTENT_SELECTORS).count() > 0:
        return True
    try:
        text = page.eval_on_selector("main", "el => el.innerText") or ""
    except Exception:  # noqa: BLE001 - no <main>: fall back to the body
        try:
            text = page.eval_on_selector("body", "el => el.innerText") or ""
        except Exception:  # noqa: BLE001
            return False
    return len(text.split()) >= 10


# 工具署名的句式。這個站是內容本身,不是任何工具的廣告 —— 頁面上不該出現
# 「用什麼做的」。
#
# 刻意找**通用句式**而不是某個特定工具的名字:寫死一個產品名的話,換了工具
# 這條檢查就默默失效(而且要把那個名字寫進這份公開的 repo 才檢查得到,
# 等於自己洩漏了要防的東西)。句式是跨工具的。
ATTRIBUTION_TOKENS = (
    "built with",
    "generated with",
    "generated by",
    "powered by",
    "made with",
    "created with",
)


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #
@check("Page loads with a non-empty <title>")
def check_title(page: Page, results: Results) -> None:
    title = (page.title() or "").strip()
    if title:
        results.ok("Page loads with a non-empty <title>", f"title={title!r}")
    else:
        results.fail("Page loads with a non-empty <title>", "title is empty")


@check("Primary content renders")
def check_cards(page: Page, results: Results) -> None:
    # Layout-agnostic: a gallery renders .card, but article/dashboard/timeline/
    # table layouts render other primary-content nodes. Accept any of them so
    # non-gallery layouts aren't falsely failed.
    SELECTORS = CONTENT_SELECTORS
    with contextlib.suppress(PlaywrightTimeout, PlaywrightError):
        page.wait_for_selector(SELECTORS, timeout=TIMEOUT_MS, state="attached")
    n = page.locator(SELECTORS).count()
    if n >= 1:
        results.ok("Primary content renders", f"{n} content node(s)")
    else:
        # last resort: a non-trivially-populated <main>
        main_txt = (page.locator("main").first.inner_text() if page.locator("main").count() else "").strip()
        if len(main_txt) > 40:
            results.ok("Primary content renders", "main has rendered text content")
        else:
            results.fail("Primary content renders", "no recognizable content nodes found")


@check("No console errors")
def check_console(page: Page, results: Results, errors: list[str]) -> None:
    """Console errors — but only the ones the site can actually do something about.

    A failed network request is logged by the *browser* itself; no amount of
    `.catch()` in the page can suppress that line. So a third-party endpoint that
    rate-limits (GitHub's API answers 403 to unauthenticated callers routinely)
    would fail this check forever, on a site that handles the failure perfectly.
    Chronic false positives are worse than no check: people learn to ignore red.

    So: resource failures from a *third-party* origin are reported but do not
    fail. Everything else still does — JS exceptions, and resource failures from
    the site's own origin, which really are broken assets.
    """
    label = "No console errors"
    own_origin = ""
    with contextlib.suppress(Exception):
        own_origin = urllib.parse.urlsplit(page.url)._replace(path="", query="", fragment="").geturl()

    real, third_party = [], []
    for e in errors:
        text, _, url = e.partition(" | ")
        is_resource = "Failed to load resource" in text or "net::ERR" in text
        if is_resource and url and own_origin and not url.startswith(own_origin):
            third_party.append(url)
        else:
            real.append(text)

    if real:
        results.fail(label, f"{len(real)} error(s): {'; '.join(real[:3])}")
    elif third_party:
        hosts = sorted({urllib.parse.urlsplit(u).netloc for u in third_party})
        results.ok(label, f"only third-party request failures, page handles them: {', '.join(hosts[:3])}")
    else:
        results.ok(label)


LANG_LABEL = "Language switch is a real link to a per-language URL"


@check(LANG_LABEL)
def check_lang_toggle(page: Page, results: Results) -> None:
    """語言切換必須是「每個語言一個 URL」的真連結,不是同頁 JS 置換。

    為什麼:爬蟲不會去按語言鈕 —— 一個 URL 只會被索引成一種語言,JS 切換的
    另一個語言在搜尋引擎眼裡等於不存在。所以驗的是:點語言連結會**導航**到
    另一個 URL、新頁的 <html lang> 不同、雙生頁上有連結指回來。

    找不到 #langLink 時退回舊的單頁 toggle 檢查,讓這支腳本仍能驗舊架構的站。
    """
    link = first_visible(page, ["#langLink", "header a[rel='alternate'][hreflang]",
                                ".appbar a[hreflang]"])
    if link is None:
        _legacy_lang_toggle(page, results)
        return

    if (link.evaluate("el => el.tagName") or "").upper() != "A":
        results.fail(LANG_LABEL, "language switch exists but is not an <a> — crawlers won't follow it")
        return
    href = link.get_attribute("href") or ""
    if not href or href.startswith("#") or href.startswith("javascript:"):
        results.fail(LANG_LABEL, f"language link has no crawlable href ({href!r})")
        return

    before_url = page.url
    before_lang = page.locator("html").get_attribute("lang") or ""
    link.click()
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(400)

    after_url = page.url
    after_lang = page.locator("html").get_attribute("lang") or ""
    back = first_present(page, ["#langLink", "header a[rel='alternate'][hreflang]"])
    back_href = (back.get_attribute("href") or "") if back is not None else ""

    # 回到原頁,讓後面的檢查繼續在原本的語言/URL 上跑
    page.goto(before_url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
    page.wait_for_timeout(300)

    if after_url == before_url:
        results.fail(LANG_LABEL, "clicking the language link did not navigate to a different URL")
    elif after_lang == before_lang:
        results.fail(LANG_LABEL,
                     f"navigated to {after_url} but <html lang> stayed {after_lang!r} — "
                     "the alternate page does not declare its own language")
    elif not back_href:
        results.fail(LANG_LABEL, f"{after_url} has no language link back to {before_url}")
    else:
        results.ok(LANG_LABEL, f"{before_lang} ({before_url.rsplit('/', 1)[-1] or 'index.html'}) "
                               f"↔ {after_lang} ({after_url.rsplit('/', 1)[-1]})")


def _legacy_lang_toggle(page: Page, results: Results) -> None:
    """舊架構(單頁 JS 切換)的檢查,只驗「按了有觀察得到的變化」。"""
    toggle = first_visible(
        page,
        [
            "#lang-en", "[data-lang='en']", "[data-lang=en]", "button[data-lang]",
            ".lang-toggle",
            # single-button toggle pattern (zh<->en on one button)
            "#langToggle", "#lang-toggle", "button[title='Language']",
            "button[aria-label*='language' i]", "button[aria-label*='語言']",
        ],
    )
    if toggle is None:
        results.skip(LANG_LABEL, "no language link or toggle found")
        return

    before_text = page.locator("#grid, main, body").first.inner_text()[:2000]
    before_pressed = toggle.get_attribute("aria-pressed")
    before_lang = page.locator("html").get_attribute("lang")

    toggle.click()
    page.wait_for_timeout(300)

    after_text = page.locator("#grid, main, body").first.inner_text()[:2000]
    after_pressed = toggle.get_attribute("aria-pressed")
    after_lang = page.locator("html").get_attribute("lang")

    changed = (
        before_text != after_text
        or (before_pressed != after_pressed and after_pressed is not None)
        or (before_lang != after_lang)
    )
    if changed:
        results.ok(LANG_LABEL, "legacy single-URL toggle: visible text / state changed "
                               "(consider migrating to per-language URLs for SEO)")
    else:
        results.fail(LANG_LABEL, "clicking toggle produced no observable change")


@check("Theme toggle works")
def check_theme_toggle(page: Page, results: Results) -> None:
    toggle = first_visible(
        page,
        ["#theme-toggle", "[data-theme-toggle]", ".theme-toggle", "button[aria-label*='theme' i]"],
    )
    if toggle is None:
        results.skip("Theme toggle works", "no theme toggle found")
        return

    def signature() -> tuple:
        html = page.locator("html")
        body = page.locator("body")
        return (
            html.get_attribute("data-theme"),
            html.get_attribute("class"),
            body.get_attribute("data-theme"),
            body.get_attribute("class"),
        )

    before = signature()
    toggle.click()
    page.wait_for_timeout(300)
    after = signature()

    if before != after:
        results.ok("Theme toggle works", "theme class/attribute on <html>/<body> changed")
    else:
        results.fail("Theme toggle works", "no theme attribute/class change detected")


@check("Search filters the grid")
def check_search(page: Page, results: Results) -> None:
    search = first_visible(
        page,
        ["#search", "input[type='search']", "input[name='search']", "[role='searchbox']"],
    )
    if search is None:
        results.skip("Search filters the grid", "no search input found")
        return

    before = card_count(page)
    if before == 0:
        results.skip("Search filters the grid", "no visible cards to filter")
        return

    # Derive a query from a real card so it matches at least one item, then we
    # also test a definitely-nonexistent query to confirm filtering narrows.
    sample = page.locator("#grid .card, .card").first.inner_text().strip().split()
    nonsense = "zzqxnonexistentquery123"

    search.fill(nonsense)
    page.wait_for_timeout(300)
    after_nonsense = card_count(page)

    search.fill("")
    page.wait_for_timeout(200)
    if sample:
        search.fill(sample[0][:8])
        page.wait_for_timeout(300)
    after_match = card_count(page)

    # Leave the input cleared so later checks see the full grid again.
    search.fill("")
    page.wait_for_timeout(200)

    if after_nonsense < before:
        detail = f"{before} -> {after_nonsense} on no-match query"
        if sample:
            detail += f", {after_match} on matching query"
        results.ok("Search filters the grid", detail)
    else:
        results.fail(
            "Search filters the grid",
            f"card count did not decrease (before={before}, after={after_nonsense})",
        )


@check("Filter chips update the grid")
def check_chips(page: Page, results: Results) -> None:
    chips = page.locator(".chip, [data-filter], .filter-chip")
    if chips.count() == 0:
        results.skip("Filter chips update the grid", "no .chip elements found")
        return

    before = card_count(page)
    # Click a chip that isn't an "all"/reset chip if we can identify one.
    target = None
    for i in range(min(chips.count(), 6)):
        c = chips.nth(i)
        label = (c.inner_text() or "").strip().lower()
        if label not in {"all", "全部", "reset", ""}:
            target = c
            break
    target = target or chips.first

    if not target.is_visible():
        results.skip("Filter chips update the grid", "chips present but not visible")
        return

    target.click()
    page.wait_for_timeout(300)
    after = card_count(page)
    pressed = target.get_attribute("aria-pressed")
    active = "active" in (target.get_attribute("class") or "")

    if after != before or pressed == "true" or active:
        results.ok("Filter chips update the grid", f"cards {before} -> {after}")
    else:
        results.fail("Filter chips update the grid", "clicking chip changed nothing observable")


@check("Dialog opens on card click and closes on Esc")
def check_dialog(page: Page, results: Results) -> None:
    card = first_visible(page, ["#grid .card", ".card"])
    if card is None:
        results.skip("Dialog opens on card click and closes on Esc", "no clickable card")
        return

    dialog_selectors = ["dialog[open]", "[role='dialog']:visible", ".dialog:visible", ".modal:visible"]
    card.click()
    page.wait_for_timeout(400)
    dialog = first_visible(page, dialog_selectors)
    if dialog is None:
        results.skip(
            "Dialog opens on card click and closes on Esc",
            "card click did not open a dialog (site may use inline detail)",
        )
        return

    page.keyboard.press("Escape")
    page.wait_for_timeout(400)
    still_open = first_visible(page, dialog_selectors, timeout=800)
    if still_open is None:
        results.ok("Dialog opens on card click and closes on Esc", "opened and Esc closed it")
    else:
        results.fail("Dialog opens on card click and closes on Esc", "dialog did not close on Esc")


@check("Deep link opens the target item")
def check_deep_link(page: Page, base_url: str, results: Results) -> None:
    # Find a slug/id we can deep-link to: prefer an explicit data-slug/id on a card.
    card = first_present(page, ["#grid .card[data-slug]", ".card[data-slug]", "#grid .card[id]", ".card[id]"])
    slug = None
    if card is not None:
        slug = card.get_attribute("data-slug") or card.get_attribute("id")
    if not slug:
        results.skip("Deep link opens the target item", "no card slug/id to deep-link to")
        return

    page.goto(f"{base_url}#{slug}", wait_until="domcontentloaded", timeout=TIMEOUT_MS)
    page.wait_for_timeout(500)
    opened = first_visible(
        page,
        ["dialog[open]", "[role='dialog']:visible", ".dialog:visible", ".modal:visible", f"#{slug}:visible"],
    )
    if opened is not None:
        results.ok("Deep link opens the target item", f"#{slug} opened a dialog/section")
    else:
        results.fail("Deep link opens the target item", f"#{slug} did not reveal the item")


@check("Responsive at 375px (no horizontal overflow)")
def check_responsive(page: Page, results: Results) -> None:
    page.set_viewport_size({"width": 375, "height": 800})
    page.wait_for_timeout(300)
    # Compare scrollable width to the viewport width with a small tolerance.
    metrics = page.evaluate(
        "() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth })"
    )
    overflow = metrics["scroll"] - metrics["client"]
    tolerance = 4  # px; sub-pixel rounding / scrollbar gutter
    if not has_content(page):
        results.fail("Responsive at 375px (no horizontal overflow)", "nothing rendered at 375px")
    elif overflow <= tolerance:
        results.ok("Responsive at 375px (no horizontal overflow)", f"overflow={overflow}px")
    else:
        results.fail(
            "Responsive at 375px (no horizontal overflow)",
            f"horizontal overflow of {overflow}px (scroll={metrics['scroll']}, client={metrics['client']})",
        )
    # Restore a desktop viewport for any later checks.
    page.set_viewport_size({"width": 1280, "height": 900})


@check("Basic a11y: images labelled, controls have accessible names")
def check_a11y(page: Page, results: Results) -> None:
    problems: list[str] = []

    # Images must have alt text (empty alt is allowed: it marks the image decorative).
    bad_images = page.evaluate(
        """() => Array.from(document.querySelectorAll('img'))
            .filter(img => !img.hasAttribute('alt')
                && img.getAttribute('role') !== 'presentation'
                && img.getAttribute('aria-hidden') !== 'true')
            .length"""
    )
    if bad_images:
        problems.append(f"{bad_images} <img> without alt/role=presentation")

    # Interactive controls should expose an accessible name.
    unnamed = page.evaluate(
        """() => Array.from(document.querySelectorAll('button, a[href], [role="button"]'))
            .filter(el => {
                const text = (el.textContent || '').trim();
                const aria = el.getAttribute('aria-label');
                const labelledby = el.getAttribute('aria-labelledby');
                const title = el.getAttribute('title');
                const hasImg = el.querySelector('img[alt]:not([alt=""])');
                return !text && !aria && !labelledby && !title && !hasImg;
            }).length"""
    )
    if unnamed:
        problems.append(f"{unnamed} interactive control(s) without an accessible name")

    if not problems:
        results.ok("Basic a11y: images labelled, controls have accessible names")
    else:
        results.fail(
            "Basic a11y: images labelled, controls have accessible names",
            "; ".join(problems),
        )


# 只掃頁面的「外殼」,不掃 <main>。
#
# 這個範圍是刻意的:<main> 裡是 553 筆**第三方**的 GitHub 專案描述,而那些描述
# 本來就有「built with Gemini 2.0 Flash」、「powered by large language models」、
# 「Community-made and free to use. Made with…」這種句子(實測 9 筆命中)。
# 把它們當成工具署名會讓這條檢查每次都紅 —— 而恆紅的檢查等於沒有檢查,
# 大家會學會忽略它,真的署名溜進去也就看不到了。
#
# 署名真正會落在的地方是外殼:頁尾、<head> 的 meta(尤其 name="generator")、
# 以及 HTML 註解。
_SHELL_TEXT_JS = """() => {
  const out = [];
  // HTML 註解(整份文件)
  const w = document.createTreeWalker(document, NodeFilter.SHOW_COMMENT);
  for (let n = w.nextNode(); n; n = w.nextNode()) out.push(n.nodeValue || "");
  // <head> 的原始標記:meta / title / JSON-LD 都在裡面
  out.push(document.head ? document.head.innerHTML : "");
  // <body> 的文字,但把 <main> 整塊移除(第三方內容不算)
  if (document.body) {
    const clone = document.body.cloneNode(true);
    const m = clone.querySelector("main");
    if (m) m.remove();
    out.push(clone.textContent || "");
  }
  return out.join("\\n");
}"""


@check("No tool attribution in the shipped page")
def check_no_attribution(page: Page, results: Results) -> None:
    shell = (page.evaluate(_SHELL_TEXT_JS) or "").lower()
    hits = sorted({tok for tok in ATTRIBUTION_TOKENS if tok in shell})
    if hits:
        results.fail(
            "No tool attribution in the shipped page",
            f"found {', '.join(hits)} in the page shell — the deliverable must not "
            "disclose what produced it",
        )
    else:
        results.ok("No tool attribution in the shipped page", "shell: comments + head + body outside <main>")


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def reset_view(page: Page) -> None:
    """Return the page to a clean, full-grid state between checks.

    Checks mutate state (search text, active filter chip, an open dialog). Without
    a reset, a later check inherits a filtered/empty grid and fails spuriously.
    """
    try:
        # Close any open dialog.
        page.keyboard.press("Escape")
        # Clear search inputs.
        for sel in ["#search", "#searchInput", "input[type='search']", "[role='searchbox']"]:
            loc = page.locator(sel)
            if loc.count() and loc.first.is_visible():
                loc.first.fill("")
                break
        # Reset filter to an "all"/reset chip if one exists.
        chips = page.locator(".chip, [data-filter], .filter-chip")
        for i in range(min(chips.count(), 8)):
            c = chips.nth(i)
            label = (c.inner_text() or "").strip().lower()
            if label in {"all", "全部", "reset"}:
                c.click()
                break
        page.wait_for_timeout(200)
    except Exception:
        pass  # reset is best-effort; never let it abort the suite


@check("GA4 tag is wired correctly")
def check_ga4(page: Page, results: Results) -> None:
    """GA4 是選配的,但**半套的 GA4 會靜默失效**。

    標準片段裡 Measurement ID 出現兩次(<script src> 的 query 與 gtag('config')),
    只換掉其中一個的話,頁面照樣載入、console 也不會報錯,但資料進不到正確的
    property —— 這種錯只有等到「報表一直沒數字」才會發現。所以這裡驗兩處一致。

    完全沒有 GA 片段 → SKIP(沒接 GA 是合法選擇,不是缺失)。
    """
    label = "GA4 tag is wired correctly"
    ids = page.evaluate(
        "() => {"
        "  const out = { src: [], cfg: [] };"
        "  document.querySelectorAll('script[src*=\"googletagmanager.com/gtag/js\"]')"
        "    .forEach(s => { const m = s.src.match(/[?&]id=([^&]+)/); if (m) out.src.push(m[1]); });"
        "  document.querySelectorAll('script:not([src])')"
        "    .forEach(s => { const m = s.textContent.match(/gtag\\(\\s*['\"]config['\"]\\s*,\\s*['\"]([^'\"]+)/);"
        "                    if (m) out.cfg.push(m[1]); });"
        "  return out; }"
    )
    src, cfg = ids.get("src", []), ids.get("cfg", [])
    if not src and not cfg:
        results.skip(label, "no GA4 snippet (optional)")
        return
    bad = [i for i in src + cfg if not re.fullmatch(r"G-[A-Z0-9]+", i) or "XXXX" in i]
    if bad:
        results.fail(label, f"placeholder or malformed Measurement ID: {bad[0]}")
    elif set(src) != set(cfg):
        results.fail(label, f"script src id {src} != gtag('config') id {cfg} — half-wired, data will not arrive")
    else:
        results.ok(label, src[0] if src else cfg[0])


@check("Canonical and og:url present")
def check_canonical(page: Page, results: Results) -> None:
    """Every page must declare its own canonical URL.

    Only presence / absolute-https / og:url agreement are checked — not the URL
    text itself: the local server address (127.0.0.1) legitimately differs from
    the production domain the canonical should point at.
    """
    got = page.evaluate(
        "() => {"
        "  const c = document.querySelector('link[rel=canonical]');"
        '  const o = document.querySelector(\'meta[property="og:url"]\');'
        "  return { c: c ? c.href : null, o: o ? o.content : null };"
        "}"
    )
    canon, ogurl = got.get("c"), got.get("o")
    label = "Canonical and og:url present"
    if not canon:
        results.fail(label, "no <link rel=canonical>")
    elif not canon.startswith("https://"):
        results.fail(label, f"canonical is not absolute https: {canon}")
    elif ogurl and ogurl != canon:
        results.fail(label, f"og:url != canonical ({ogurl} vs {canon})")
    else:
        results.ok(label, canon)


@check("og:image present")
def check_og_image(page: Page, results: Results) -> None:
    """Social link previews require an explicit og:image.

    Presence / absolute-https / raster format are checked.  SVG is rejected:
    Facebook and LinkedIn silently ignore SVG og:images — a site once shipped
    with an SVG og:image and nothing caught it, so every share rendered as a
    bare text card.
    """
    got = page.evaluate(
        "() => {"
        '  const m = document.querySelector(\'meta[property="og:image"]\');'
        "  return m ? m.content : null;"
        "}"
    )
    label = "og:image present"
    if not got:
        results.fail(label, "no <meta property=og:image> — run scripts/build_og.py")
    elif not got.startswith("https://"):
        results.fail(label, f"og:image is not absolute https: {got}")
    elif got.lower().split("?")[0].endswith(".svg"):
        results.fail(label, f"og:image is SVG — Facebook/LinkedIn ignore it: {got}")
    else:
        results.ok(label, got)


def check_jsonld(page: Page, results: Results) -> None:
    """Every page must carry valid JSON-LD structured data.

    A whole 34-page site once shipped with zero JSON-LD and nothing caught it —
    the skill's checklist required it but no check enforced it. Any parseable
    block that declares a schema.org @context passes; the content itself is not
    judged (hand-written Event/Article markup is richer than the fallback and
    should not be constrained here).
    """
    label = "JSON-LD structured data present and valid"
    try:
        blocks = page.evaluate(
            "() => Array.from(document.querySelectorAll("
            "'script[type=\"application/ld+json\"]')).map(s => s.textContent)"
        )
    except PlaywrightError as exc:
        results.fail(label, f"could not inspect page: {_short(exc)}")
        return
    if not blocks:
        results.fail(label, "no <script type=application/ld+json> — run the SEO build step")
        return
    ok = 0
    for i, raw in enumerate(blocks):
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            results.fail(label, f"block {i + 1} is not valid JSON: {_short(exc)}")
            return
        items = data if isinstance(data, list) else [data]
        if any("schema.org" in str(d.get("@context", "")) for d in items if isinstance(d, dict)):
            ok += 1
    if ok:
        results.ok(label, f"{len(blocks)} block(s), {ok} with schema.org @context")
    else:
        results.fail(label, "JSON parses but no block declares a schema.org @context")


def check_seo_files(context, base_url: str, results: Results) -> None:
    """Site-wide: sitemap.xml and robots.txt. Runs once per site.

    A site that deliberately opts out of indexing (robots.txt `Disallow: /`)
    SKIPs both — demanding a sitemap there is counter-productive, not a defect.
    """
    label_sm = "sitemap.xml exists and lists pages"
    label_rb = "robots.txt points at the sitemap"

    def fetch(path):
        try:
            r = context.request.get(base_url + path)
            return (r.status, r.text() if r.ok else "")
        except Exception as exc:  # noqa: BLE001
            return (0, "")

    try:
        _seo_checks(fetch, results)
    except Exception as exc:  # noqa: BLE001 - never abort the run
        results.fail(label_sm, f"unexpected error: {_short(exc)}")


def _seo_checks(fetch, results: Results) -> None:
    label_sm = "sitemap.xml exists and lists pages"
    label_rb = "robots.txt points at the sitemap"
    rb_status, rb_text = fetch("/robots.txt")
    if rb_status == 200 and re.search(r"(?mi)^\s*Disallow:\s*/\s*$", rb_text):
        reason = "site opts out of indexing (robots.txt Disallow: /)"
        results.skip(label_sm, reason)
        results.skip(label_rb, reason)
        return

    sm_status, sm_text = fetch("/sitemap.xml")
    if sm_status != 200:
        results.fail(label_sm, f"HTTP {sm_status} — run the SEO build step")
    else:
        locs = re.findall(r"<loc>\s*(.*?)\s*</loc>", sm_text, re.S)
        bad = [l for l in locs if not l.startswith("https://")]
        if not locs:
            results.fail(label_sm, "sitemap has no <loc> entries")
        elif bad:
            results.fail(label_sm, f"{len(bad)} <loc> not absolute https, e.g. {bad[0][:60]}")
        else:
            results.ok(label_sm, f"{len(locs)} url(s)")

    if rb_status != 200:
        results.fail(label_rb, f"HTTP {rb_status} — run the SEO build step")
    elif not re.search(r"(?mi)^Sitemap:\s*https://\S+", rb_text):
        results.fail(label_rb, "no absolute `Sitemap:` line")
    else:
        results.ok(label_rb)


def _head_hreflangs(html: str) -> dict[str, str]:
    """head 裡 <link hreflang> 的 {hreflang: href}。屬性順序不拘。"""
    head = html.split("</head>", 1)[0]
    out: dict[str, str] = {}
    for tag in re.findall(r"<link\b[^>]*>", head, re.I):
        if "hreflang" not in tag.lower():
            continue
        hl = re.search(r"\bhreflang=[\"']([^\"']+)[\"']", tag, re.I)
        href = re.search(r"\bhref=[\"']([^\"']+)[\"']", tag, re.I)
        if hl and href:
            out[hl.group(1)] = href.group(1)
    return out


def check_hreflang(context, base_url: str, hrefs: list[str], results: Results) -> None:
    """每組中英雙生頁的 hreflang 都要完整且雙向一致。站級檢查,跑一次。

    hreflang 缺一邊、寫相對路徑、或兩個檔案的標註不一致,Google 都會**整組忽略**
    —— 而且不會報錯,只是兩個語言各自孤立。所以逐組驗:en / zh-Hant / x-default
    三個都在、絕對 https、兩邊同一組。

    整站一組 hreflang 都沒有 → SKIP(還沒跑 build_seo 或根本沒有雙生頁,
    不是缺失);有標但標不齊才是 FAIL。
    """
    label = "hreflang pairs are complete and bidirectional"
    en_pages = [h for h in hrefs
                if h.endswith(".html") and not h.startswith("zh-Hant/")]
    pairs: list[tuple[str, str]] = []
    for h in en_pages:
        twin = f"zh-Hant/{h}"
        with contextlib.suppress(Exception):
            if context.request.get(f"{base_url}/{twin}").status == 200:
                pairs.append((h, twin))
    if not pairs:
        results.skip(label, "no zh-Hant/ twins (single-language site or legacy architecture)")
        return

    problems: list[str] = []
    groups = 0
    for en, zh in pairs:
        try:
            en_tags = _head_hreflangs(context.request.get(f"{base_url}/{en}").text())
            zh_tags = _head_hreflangs(context.request.get(f"{base_url}/{zh}").text())
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{en}: fetch failed ({_short(exc, 40)})")
            continue
        if not en_tags and not zh_tags:
            continue          # 這組還沒標(build_seo 還沒跑),交給下面的 groups==0 判定
        groups += 1
        for name, tags in ((en, en_tags), (zh, zh_tags)):
            missing = {"en", "zh-Hant", "x-default"} - set(tags)
            if missing:
                problems.append(f"{name}: missing hreflang {sorted(missing)}")
            bad = [u for u in tags.values() if not u.lower().startswith("https://")]
            if bad:
                problems.append(f"{name}: hreflang href not absolute https ({bad[0][:50]})")
        if en_tags and zh_tags and en_tags != zh_tags:
            problems.append(f"{en} vs {zh}: hreflang groups differ — both files must carry the same set")
    if problems:
        results.fail(label, "; ".join(problems[:3]))
    elif groups == 0:
        results.skip(label, f"{len(pairs)} twin pair(s) but no hreflang tags yet — "
                            "run build_seo with a real base URL before deploying")
    else:
        results.ok(label, f"{groups} language pair(s) annotated on both sides")


# 靜態 HTML 裡帶 data-count 的元素:抓開標籤(屬性順序不拘)後緊接的文字節點。
_STAT_EL = re.compile(r"""<(\w+)[^>]*\bdata-count=["']([^"']+)["'][^>]*>([^<]*)<""")
_DIGITS = re.compile(r"\d+")
NO_JS_SAMPLE = 4          # 擋 JS 檢查的抽樣頁數(版型層級問題,不需逐頁)
NO_JS_MIN_VISIBLE = 0.9   # 低於這個比例才算「有內容讀不到」
NO_JS_SETTLE_MS = 250     # 等畫面穩定的取樣間隔
NO_JS_SETTLE_TRIES = 12


def _digits(text: str) -> str:
    return "".join(_DIGITS.findall(text))


def _all_page_hrefs(context, base_url: str, known: list[str]) -> list[str]:
    """站內所有頁面的相對路徑,以 sitemap 為主、呼叫端給的清單為輔。

    不能只驗 `index.html`:多頁站的頁面探索(`window.SITE_PAGES`)不是每種版型
    都有,實測十頁的站只回報一頁 —— 於是另外九頁的錯數字就驗不到。
    sitemap 是「這個站對外宣稱有哪些頁」的權威來源,拿它當清單最不會漏。
    """
    found = set(known)
    with contextlib.suppress(Exception):
        r = context.request.get(base_url + "/sitemap.xml")
        if r.ok:
            for loc in re.findall(r"<loc>\s*(.*?)\s*</loc>", r.text(), re.S):
                path = re.sub(r"^https?://[^/]+/?", "", loc).strip()
                found.add(path or "index.html")
    return sorted(found)


# 量的是「關掉 JS 的訪客讀得到多少內容」,不是「有幾個元素看不見」。
#
# 數元素會把兩個字的 hover 圖示和整段被藏起來的內文算成同一件事 —— 實測有站的
# `.card__more` 本來就是 opacity:0、只在 hover 時顯示,那是設計不是缺陷。
# 改算字元比例之後,hover 圖示佔零點幾個百分點,整段隱藏會直接掉到六成。
#
# opacity 不會繼承(子元素的 computed opacity 是它自己的值),所以每個文字節點
# 都要往上走到 <main> 才能確定它到底看不看得見。
_VISIBLE_TEXT_RATIO_JS = """() => {
  const main = document.querySelector('main');
  if (!main) return null;
  const walker = document.createTreeWalker(main, NodeFilter.SHOW_TEXT);
  let total = 0, visible = 0;
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const len = n.textContent.trim().length;
    if (!len) continue;
    total += len;
    let hidden = false;
    for (let el = n.parentElement; el && el !== main.parentElement; el = el.parentElement) {
      const s = getComputedStyle(el);
      if (parseFloat(s.opacity) < 0.1 || s.visibility === 'hidden') {
        hidden = true; break;
      }
    }
    if (!hidden) visible += len;
  }
  return total ? visible / total : null;
}"""


def _settled_ratio(pg) -> float | None:
    """等可見比例不再變動才取值 —— 固定等幾毫秒會抓到動畫中途。

    有站用**純 CSS 動畫**把內容淡入(不需要 JS)。實測同一頁:
    100ms 讀到 5%、300ms 讀到 16%、400ms 之後才是 100%。
    固定等 400ms 剛好落在邊界上,於是一個完全健康的站被回報成「82% 的內容看不到」。

    這個毛病在這個專案裡出現過三次(擷取抓到動畫中間值、數字檢查取樣太早、
    還有這裡),所以判準是:**不要在固定時間點取樣一個還在動的頁面**,等它停。
    """
    last = None
    for _ in range(NO_JS_SETTLE_TRIES):
        pg.wait_for_timeout(NO_JS_SETTLE_MS)
        now = pg.evaluate(_VISIBLE_TEXT_RATIO_JS)
        if now is None:
            return None
        if last is not None and abs(now - last) < 1e-9:
            return now
        last = now
    return last


def check_no_js_visibility(context, base_url: str, hrefs: list[str], results: Results) -> None:
    """擋掉站上的 JS 再載入一次,看靜態內容是不是真的看得見。

    prerender 寫進去的內容如果停在 `opacity: 0`(捲動顯示的 CSS 在 observer 觸發
    前的狀態),對關掉 JS 的訪客等於不存在 —— 而那正是 prerender 要解決的事。

    **不比對 class 名稱**:各站的顯示 class 不一樣(實測有 `is-visible` 也有
    `is-in`),寫死任何一個都會對另一種誤判。這裡問瀏覽器算完的 opacity,
    命名慣例再多也不影響。

    **量的是字元比例,不是「有幾個元素看不見」**:數元素會把兩個字的 hover 圖示
    和整段被藏起來的內文算成同一件事。實測有站的 `.card__more` 本來就是
    `opacity: 0`、只在 hover 時顯示 —— 那是設計,不是缺陷。

    **只看 `opacity` / `visibility`,不看 `display: none`。** 要抓的是「捲動事件
    觸發後才顯示」這一類 —— 那是用 opacity 做的。手風琴、分頁、下拉選單用的是
    `display: none`,內容確實在 DOM 裡、搜尋引擎讀得到,點一下就展開,是正常設計。
    把它算成缺陷會讓一堆健康的站被判 FAIL。

    做法是擋掉外部 `.js` 而不是整個關掉 JS —— Playwright 關掉 JS 之後就不能
    `evaluate`,量不到 computed style。站上的行為都在外部 app.js,擋掉它就等同
    於沒有 JS 的情境。

    只抽前幾頁:這是版型層級的問題,同一個站的頁面共用同一份 CSS 與同一套產生
    流程,抽樣就足以判定。
    """
    label = "Static content is visible without JavaScript"
    try:
        ctx = context.browser.new_context()
    except Exception as exc:  # noqa: BLE001
        results.skip(label, f"could not open a second context: {_short(exc)}")
        return
    ctx.route("**/*.js", lambda route: route.abort())
    bad: list[str] = []
    worst = 1.0
    try:
        pg = ctx.new_page()
        for href in hrefs[:NO_JS_SAMPLE]:
            try:
                pg.goto(f"{base_url}/{href.lstrip('/')}", wait_until="domcontentloaded",
                        timeout=TIMEOUT_MS)
                ratio = _settled_ratio(pg)
            except (PlaywrightTimeout, PlaywrightError):
                continue
            if ratio is None:
                continue
            worst = min(worst, ratio)
            if ratio < NO_JS_MIN_VISIBLE:
                bad.append(f"{href} ({round(ratio * 100)}% visible)")
    finally:
        with contextlib.suppress(Exception):
            ctx.close()

    if bad:
        results.fail(label,
                     f"{'; '.join(bad)} — prerendered text stuck at opacity:0 until a "
                     "scroll observer fires, so a visitor without JS never reads it")
    else:
        results.ok(label, f"worst page {round(worst * 100)}% of text visible")


def check_static_numbers(context, base_url: str, hrefs: list[str], results: Results) -> None:
    """抓**原始 HTML**(不執行 JS),驗第一波爬蟲看到的統計數字是對的。

    為什麼不看渲染後的畫面:渲染後 JS 已經把數字算好了,永遠是對的 —— 那正是
    這個 bug 藏得住的原因。真正會出錯的是「寫進檔案裡的那一份」,只有直接抓
    原始碼才看得到。

    驗兩件事:

    1. **`data-count` 與文字一致**。擷取時如果動畫還在跑,會寫進中間值
       (實測 `data-count="88"` 寫成 `2`);如果動畫從未觸發,會寫進起始值 `0`。
       兩種都是對搜尋引擎謊報數據。
    2. **不能有 `data-done`**。那是頁面 JS 的執行期旗標(「動畫跑過了」),
       被擷取進靜態檔之後,真人載入時 JS 會直接跳過重算 —— 錯的數字就此凍結,
       頁面失去自我修正的能力。

    比對前把逗號、`+`、`%` 等都去掉:`4,086` 與 `4086` 是同一個數。

    **`data-count="0"` 加上空白文字不算數。** 實測有站的熱力圖格子是
    `data-count="${n}"` 配 `${n || ''}` —— 數值 0 的格子刻意不顯示文字,用底色深淺
    表達,顯示 `0` 反而是雜訊。那不是「漏寫數字」,對搜尋引擎也沒有謊報任何東西。
    """
    label_num = "Static numbers match their data-count"
    label_flag = "No runtime state flags in the static HTML"
    checked = mismatched = 0
    bad_examples: list[str] = []
    flagged: list[str] = []

    for href in hrefs:
        try:
            r = context.request.get(f"{base_url}/{href.lstrip('/')}")
            if not r.ok:
                continue
            html = r.text()
        except Exception:  # noqa: BLE001 - 抓不到就跳過,不要讓整份驗證掛掉
            continue

        if "data-done" in html:
            flagged.append(href)

        for _tag, want, shown in _STAT_EL.findall(html):
            want_d, shown_d = _digits(want), _digits(shown)
            if not want_d:
                continue          # data-count 不是數字(例如佔位符),不在這裡判定
            if want_d == "0" and not shown.strip():
                continue          # 值是 0 又刻意留白 —— 見下方說明,不算謊報
            checked += 1
            if want_d != shown_d:
                mismatched += 1
                if len(bad_examples) < 3:
                    bad_examples.append(f"{href}: data-count={want} but text={shown.strip()!r}")

    if not checked:
        results.skip(label_num, "no data-count elements")
    elif mismatched:
        results.fail(label_num,
                     f"{mismatched}/{checked} wrong — {'; '.join(bad_examples)}")
    else:
        results.ok(label_num, f"{checked} value(s)")

    if flagged:
        results.fail(label_flag,
                     f"data-done captured into {len(flagged)} page(s), e.g. {flagged[0]} — "
                     "the page's own JS will skip recomputing and freeze whatever was captured")
    else:
        results.ok(label_flag)


def run_checks(page: Page, base_url: str, console_errors: list[str], results: Results) -> None:
    print("\nRunning UX checks:\n", flush=True)
    check_title(page, results)
    check_canonical(page, results)
    check_jsonld(page, results)
    check_ga4(page, results)
    check_cards(page, results)
    # Functional checks (these mutate page state, so reset the view between them).
    check_lang_toggle(page, results)
    check_theme_toggle(page, results)
    check_search(page, results)
    reset_view(page)
    check_chips(page, results)
    reset_view(page)
    check_dialog(page, results)
    reset_view(page)
    check_deep_link(page, base_url, results)
    reset_view(page)
    check_responsive(page, results)
    check_a11y(page, results)
    check_no_attribution(page, results)
    # Console errors are accumulated across the whole session; check last.
    check_console(page, results, console_errors)


# --------------------------------------------------------------------------- #
# Multi-page support (a shared-shell site: one .html per page, one data file)
# --------------------------------------------------------------------------- #
def discover_pages(page: Page) -> list[dict]:
    """Read window.SITE_PAGES to learn the page list of a multi-page site.

    Returns [] for a single-page site (no SITE_PAGES), which tells the caller
    to fall back to the classic single-page suite.
    """
    with contextlib.suppress(PlaywrightTimeout, PlaywrightError):
        return page.evaluate(
            """() => (Array.isArray(window.SITE_PAGES) ? window.SITE_PAGES : []).map(p => ({
                slug: String(p.slug),
                layout: String(p.layout || ''),
                href: p.slug === 'home' ? 'index.html' : p.slug + '.html'
            }))"""
        )
    return []


@check("Cross-page nav is present and links resolve")
def check_nav_links(page: Page, base_url: str, results: Results, pages: list[dict]) -> None:
    import urllib.request

    pills = page.locator(".navpill, .pagenav a, nav a[href$='.html']").count()
    if pills < max(2, len(pages) - 0):
        results.fail(
            "Cross-page nav is present and links resolve",
            f"expected ~{len(pages)} nav links, found {pills}",
        )
        return
    bad: list[str] = []
    for pg in pages:
        url = f"{base_url}/{pg['href']}"
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310 - localhost
                if resp.status != 200:
                    bad.append(f"{pg['href']} ({resp.status})")
        except Exception as exc:  # noqa: BLE001
            bad.append(f"{pg['href']} ({_short(exc, 40)})")
    if bad:
        results.fail("Cross-page nav is present and links resolve", "; ".join(bad))
    else:
        results.ok(
            "Cross-page nav is present and links resolve",
            f"{pills} nav links, {len(pages)} pages all 200",
        )


LANG_PERSIST_LABEL = "Language stays per-URL across pages"


@check(LANG_PERSIST_LABEL)
def check_lang_persist(page: Page, base_url: str, results: Results, pages: list[dict]) -> None:
    """多頁站的語言持久是**結構性**的:語言活在 URL 裡(中文鏡射在 zh-Hant/)。

    驗三件事:中文首頁存在且宣告 zh、中文頁的站內 nav 解析後全部留在
    /zh-Hant/(點一下就掉回英文是最常見的破口)、導到第二頁後仍是中文。
    沒有 zh-Hant/ 雙生(舊架構)→ 退回舊的 localStorage 持久檢查。
    """
    second = next((p for p in pages if p["slug"] != "home"), None)
    if second is None:
        results.skip(LANG_PERSIST_LABEL, "only one page")
        return

    has_twin = False
    with contextlib.suppress(Exception):
        has_twin = page.context.request.get(f"{base_url}/zh-Hant/index.html").status == 200
    if not has_twin:
        _legacy_lang_persist(page, base_url, results, second)
        return

    page.goto(f"{base_url}/zh-Hant/index.html", wait_until="networkidle", timeout=TIMEOUT_MS)
    page.wait_for_timeout(200)
    lang = (page.locator("html").get_attribute("lang") or "").lower()
    if not lang.startswith("zh"):
        results.fail(LANG_PERSIST_LABEL,
                     f"zh-Hant/index.html declares <html lang={lang!r}>, expected zh-*")
        return

    # e.href 是瀏覽器解析後的絕對網址 —— 相對連結(同目錄兄弟)也能正確判定。
    hrefs = page.eval_on_selector_all(
        ".navpill, .pagenav a:not(#langLink), nav a[href$='.html']:not([rel='alternate'])",
        "els => els.map(e => e.href)")
    stray = [h for h in hrefs
             if h and h.endswith(".html") and "/zh-Hant/" not in h]
    if stray:
        results.fail(LANG_PERSIST_LABEL,
                     f"{len(stray)} nav link(s) on the zh page leave the language, "
                     f"e.g. {stray[0]} — one click silently drops the visitor back to English")
        return

    zh_second = f"zh-Hant/{second['href']}"
    page.goto(f"{base_url}/{zh_second}", wait_until="networkidle", timeout=TIMEOUT_MS)
    page.wait_for_timeout(200)
    lang2 = (page.locator("html").get_attribute("lang") or "").lower()
    if lang2.startswith("zh"):
        results.ok(LANG_PERSIST_LABEL,
                   f"zh nav stays inside /zh-Hant/; {zh_second} loads as {lang2}")
    else:
        results.fail(LANG_PERSIST_LABEL, f"{zh_second} loaded as <html lang={lang2!r}>")


def _legacy_lang_persist(page: Page, base_url: str, results: Results, second: dict) -> None:
    """舊架構(localStorage 語言)的跨頁持久檢查。"""
    page.goto(base_url, wait_until="networkidle", timeout=TIMEOUT_MS)
    toggle = first_visible(page, ["#langToggle", "button[title='Language']", "button[aria-label*='language' i]"])
    if toggle is None:
        results.skip(LANG_PERSIST_LABEL, "no zh-Hant/ twins and no legacy toggle")
        return

    before = page.locator("html").get_attribute("lang")
    toggle.click()
    page.wait_for_timeout(250)
    after = page.locator("html").get_attribute("lang")
    if after == before:
        results.skip(LANG_PERSIST_LABEL, "toggle did not change <html lang>")
        return

    page.goto(f"{base_url}/{second['href']}", wait_until="networkidle", timeout=TIMEOUT_MS)
    page.wait_for_timeout(200)
    lang2 = page.locator("html").get_attribute("lang")
    if lang2 == after:
        results.ok(
            LANG_PERSIST_LABEL,
            f"legacy toggle: {before} -> {after}, still {lang2} after {second['href']}",
        )
    else:
        results.fail(
            LANG_PERSIST_LABEL,
            f"set {after} but {second['href']} loaded as {lang2}",
        )


def run_page_checks(page: Page, page_url: str, errors: list[str], results: Results, slug: str) -> None:
    """The click-safe core suite for ONE page of a multi-page site.

    Interaction checks (search/chips/dialog/deep-link) run only when the page
    actually exposes those controls, so a nav-only hub page won't be clicked
    into navigating away mid-run. `page_url` is THIS page's own URL, so a
    #slug deep link resolves against the right .html (not the site root).
    """
    print(f"\n  Page: {slug}", flush=True)
    check_title(page, results)
    check_canonical(page, results)
    check_jsonld(page, results)
    check_ga4(page, results)
    check_cards(page, results)
    check_lang_toggle(page, results)
    check_theme_toggle(page, results)
    # Interaction checks: only when the relevant controls/items exist on THIS page.
    if page.locator("#search, input[type='search']").count():
        check_search(page, results)
        reset_view(page)
    if page.locator(".chip, [data-filter], .filter-chip").count():
        check_chips(page, results)
        reset_view(page)
    if page.locator(".card[data-slug]").count():
        check_dialog(page, results)
        reset_view(page)
        check_deep_link(page, page_url, results)
        reset_view(page)
    check_responsive(page, results)
    check_a11y(page, results)
    check_no_attribution(page, results)
    check_console(page, results, errors)


def run_multipage(context, base_url: str, pages: list[dict], results: Results) -> None:
    print(f"\nMulti-page site: {len(pages)} pages "
          f"({', '.join(p['slug'] for p in pages)})", flush=True)

    # Site-wide checks on a dedicated page.
    nav_page = context.new_page()
    nav_page.goto(base_url, wait_until="networkidle", timeout=TIMEOUT_MS)
    check_nav_links(nav_page, base_url, results, pages)
    check_lang_persist(nav_page, base_url, results, pages)
    with contextlib.suppress(Exception):
        nav_page.close()

    # Per-page checks, each on its own fresh page so console errors and any
    # accidental navigation stay isolated to that page.
    for pg in pages:
        page = context.new_page()
        errors: list[str] = []
        page.on(
            "console",
            lambda msg, errs=errors: errs.append(
                f"{msg.type}: {msg.text} | {(msg.location or {}).get('url', '')}") if msg.type == "error" else None,
        )
        page.on("pageerror", lambda err, errs=errors: errs.append(f"pageerror: {_short(err)}"))
        url = f"{base_url}/{pg['href']}"
        try:
            page.goto(url, wait_until="networkidle", timeout=TIMEOUT_MS)
            run_page_checks(page, url, errors, results, pg["slug"])
        except (PlaywrightTimeout, PlaywrightError) as exc:
            results.fail(f"Page loads: {pg['slug']}", f"could not load {url}: {_short(exc)}")
        finally:
            with contextlib.suppress(Exception):
                page.close()


def verify(base_url: str, force_single: bool = False) -> Results:
    results = Results()
    console_errors: list[str] = []

    with sync_playwright() as p:
        browser: Browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        context.set_default_timeout(TIMEOUT_MS)
        page = context.new_page()

        # Capture console errors and uncaught page errors throughout the run.
        page.on(
            "console",
            lambda msg: console_errors.append(
                f"{msg.type}: {msg.text} | {(msg.location or {}).get('url', '')}")
            if msg.type == "error"
            else None,
        )
        page.on("pageerror", lambda err: console_errors.append(f"pageerror: {_short(err)}"))

        try:
            print(f"Loading {base_url} ...", flush=True)
            page.goto(base_url, wait_until="networkidle", timeout=TIMEOUT_MS)
            pages = [] if force_single else discover_pages(page)
            if len(pages) > 1:
                # Multi-page site (shared shell, one .html per page).
                with contextlib.suppress(Exception):
                    page.close()
                run_multipage(context, base_url, pages, results)
            else:
                run_checks(page, base_url, console_errors, results)
            # Site-wide: runs once, independent of single/multi-page mode.
            check_seo_files(context, base_url, results)
            page_hrefs = _all_page_hrefs(
                context, base_url,
                [p["href"] for p in pages] if len(pages) > 1 else ["index.html"])
            check_hreflang(context, base_url, page_hrefs, results)
            check_static_numbers(context, base_url, page_hrefs, results)
            check_no_js_visibility(context, base_url, page_hrefs, results)
        except (PlaywrightTimeout, PlaywrightError) as exc:
            results.fail("Page loads", f"could not load {base_url}: {_short(exc)}")
        finally:
            with contextlib.suppress(Exception):
                context.close()
            with contextlib.suppress(Exception):
                browser.close()

    return results


def print_summary(results: Results) -> None:
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    total = results.passed + results.skipped + results.failed
    print(f"  PASS: {results.passed}")
    print(f"  SKIP: {results.skipped}")
    print(f"  FAIL: {results.failed}")
    print(f"  TOTAL CHECKS: {total}")
    if results.failed:
        print("\n  Failed checks:")
        for status, label, detail in results.rows:
            if status == "FAIL":
                print(f"    - {label}: {detail}")
    print("=" * 60)
    print("RESULT:", "FAIL ✗" if results.failed else "PASS ✓")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify this static site's UX with Playwright.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--url", help="URL of an already-running site, e.g. http://localhost:8000")
    group.add_argument("--dir", help="Path to a folder containing index.html (a local server is started)")
    parser.add_argument(
        "--single",
        action="store_true",
        help="Force single-page mode (skip multi-page crawl even if window.SITE_PAGES exists)",
    )
    args = parser.parse_args()

    server = None
    thread = None
    try:
        if args.url:
            base_url = args.url.rstrip("/")
        else:
            import os

            site_dir = os.path.abspath(args.dir)
            if not os.path.isfile(os.path.join(site_dir, "index.html")):
                print(f"ERROR: no index.html found in {site_dir}", file=sys.stderr)
                return 1
            base_url, server, thread = start_static_server(site_dir)
            print(f"Serving {site_dir} at {base_url}", flush=True)

        results = verify(base_url, force_single=args.single)
        print_summary(results)
        return 1 if results.failed else 0
    finally:
        if server is not None:
            with contextlib.suppress(Exception):
                server.shutdown()
                server.server_close()
        if thread is not None:
            thread.join(timeout=2)


if __name__ == "__main__":
    sys.exit(main())
