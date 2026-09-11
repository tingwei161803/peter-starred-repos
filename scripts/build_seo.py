#!/usr/bin/env python3
"""build_seo.py — 補齊每個站自己的 canonical / og:url / hreflang / JSON-LD / sitemap.xml / robots.txt。

    uv run python scripts/build_seo.py --dir ./my-site
    uv run python scripts/build_seo.py --dir ./my-site --check          # CI:有東西過期就 exit 1
    uv run python scripts/build_seo.py --dir ./my-site --base-url https://example.com/

**為什麼需要**

搜尋引擎要能回答兩個問題:「這個網址的本尊是誰」(canonical)、
「這個站有哪些頁」(sitemap)。少了它們:

- 多頁面站的每一頁都沒有 canonical,同一份內容若有多種網址寫法(帶不帶
  `index.html`、帶不帶查詢字串),Google 得自己猜要收哪一個。
- 沒有自己的 sitemap 時,Search Console 的「發現方式」會顯示
  **「未偵測到任何參照 Sitemap」** —— 就算上層網站的總 sitemap 有列到它,
  歸因也對不上,而子頁更是只能靠內部連結被發現。

**hreflang(中英雙生頁)**:站是「一語言一 URL」—— root 是英文本尊,中文
雙生頁鏡射在 `zh-Hant/` 子目錄(由 build_i18n.py 產生)。每組雙生頁的
**兩個檔案**都要有**完全相同**的一組三行互指(en / zh-Hant / x-default→英文版),
缺一邊或兩邊不一致,Google 會忽略整組標註;唯一逐頁不同的是 canonical ——
每頁指向**自己**(指到另一語言 = 自己宣告不是本尊,會蓋掉整組 hreflang)。
這些都需要絕對網址,所以由這支腳本一次補齊,不靠手寫。hreflang **只用
head 標註一種寫法**;sitemap 保持純 `<loc>`(兩個語言的網址都列)——
兩種寫法混用會讓失效面加倍,只改其一就分岔,且以哪一份為準沒有保證。

**JSON-LD(結構化資料)**:每頁沒有任何 `application/ld+json` 時,補一塊最小的
`WebSite`(root 首頁)/ `WebPage`(其他頁),name / description / url 取自該頁自己的
`<title>`、meta description 與 canonical。**已有 JSON-LD 的頁一律不動**——手寫的
Event / Article / @graph 都比這塊 fallback 豐富,蓋掉它是倒退。這一步存在的原因:
站群曾有一整個站(34 頁)完全沒有 JSON-LD 而沒被任何檢查抓到(docs/seo-analysis
11 §發現3 的追蹤項),從產生器補一次,之後的站就不會再漏。

**不寫 `<lastmod>`**:沒有可信的「內容最後修改時間」來源(git commit 時間不等於
內容變動時間)。給錯的 lastmod 比不給更糟,Google 會學會忽略整份 sitemap 的 lastmod。

只用標準函式庫,不需要瀏覽器。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from html import unescape
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

# 不是網站頁面的目錄。這份清單**一定會不完整** —— 真正的守門是下面的
# git ignore 過濾;這裡只是沒有 git 時的退路。
SKIP_DIRS = {"assets", "data", "scripts", "node_modules", "_tools", "tools",
             "sources", "tmp", "docs", ".git", ".github",
             ".venv", "venv", "env", "site-packages", "__pycache__",
             "dist", "build", "vendor", ".tox", ".next", ".cache"}

CANON_RE = re.compile(r'<link\b[^>]*\brel=["\']canonical["\'][^>]*>', re.I)
OGURL_RE = re.compile(r'<meta\b[^>]*\bproperty=["\']og:url["\'][^>]*>', re.I)
TITLE_RE = re.compile(r"</title\s*>", re.I)
HEAD_END = re.compile(r"</head\s*>", re.I)
# 只抓 <link hreflang>(head 的標註);body 裡的語言連結 <a hreflang> 不能動。
HREFLANG_LINK_RE = re.compile(r"[ \t]*<link\b[^>]*\bhreflang=[\"'][^\"']*[\"'][^>]*>\n?", re.I)

fails: list[str] = []


def base_from_cname(root: Path) -> str | None:
    """部署網址優先從 repo 的 CNAME 讀 —— 那是這個站真正對外的網域。"""
    f = root / "CNAME"
    if not f.exists():
        return None
    host = f.read_text(encoding="utf-8").strip().splitlines()[0].strip()
    return f"https://{host}" if host else None


def _git_ignored(root: Path, paths: list[Path]) -> set[Path]:
    """git 會忽略的檔案 —— 那些不是網站的一部分,不該進 sitemap。

    這是主要的守門機制。寫死的目錄清單永遠追不上現實:一個 repo 根目錄下的
    `.venv/` 裡就藏著 Playwright 內建的 HTML(trace viewer 等),把它們寫進
    sitemap 等於告訴搜尋引擎去索引第三方工具的介面。實際踩過。
    """
    if not paths:
        return set()
    try:
        r = subprocess.run(["git", "-C", str(root), "check-ignore", "--stdin"],
                           input="\n".join(str(p) for p in paths),
                           capture_output=True, text=True, timeout=30)
    except Exception:  # noqa: BLE001 - 不是 git repo / 沒有 git,就只靠 SKIP_DIRS
        return set()
    return {Path(line) for line in r.stdout.splitlines() if line.strip()}


def pages(root: Path) -> list[Path]:
    found = [p for p in root.rglob("*.html")
             if not any(part in SKIP_DIRS for part in p.relative_to(root).parts[:-1])]
    ignored = _git_ignored(root, found)
    return sorted(p for p in found if p not in ignored)


def url_of(root: Path, page: Path, base: str) -> str:
    """index.html → 目錄網址(結尾帶 /);其餘 → 檔名網址。"""
    rel = page.relative_to(root).as_posix()
    if rel == "index.html":
        return base + "/"
    if rel.endswith("/index.html"):
        return f"{base}/{rel[:-len('index.html')]}"
    return f"{base}/{rel}"


def upsert(html: str, pattern: re.Pattern, tag: str) -> tuple[str, bool]:
    """有就換掉、沒有就插進 <head>。回傳 (新內容, 是否有變動)。"""
    m = pattern.search(html)
    if m:
        return (html, False) if m.group(0) == tag else (html[:m.start()] + tag + html[m.end():], True)
    # 插在 </title> 之後最自然;沒有 title 就退到 </head> 前
    t = TITLE_RE.search(html)
    if t:
        return html[:t.end()] + "\n" + tag + html[t.end():], True
    h = HEAD_END.search(html)
    if h:
        return html[:h.start()] + tag + "\n" + html[h.start():], True
    return html, False


LDJSON_RE = re.compile(r'<script\b[^>]*type=["\']application/ld\+json["\']', re.I)
TITLE_TEXT_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
DESC_RE = re.compile(r'<meta\b[^>]*name=["\']description["\'][^>]*content=["\']([^"\']*)["\']', re.I)


def upsert_jsonld(html: str, rel: str, url: str) -> tuple[str, bool]:
    """頁面完全沒有 JSON-LD 時,補一塊最小的 WebSite / WebPage。

    已有任何 `application/ld+json` 就**原樣不動**——手寫的結構化資料
    (Event / Article / @graph)都比這塊 fallback 豐富,蓋掉是倒退。
    """
    if LDJSON_RE.search(html):
        return html, False
    name = ""
    if m := TITLE_TEXT_RE.search(html):
        name = unescape(re.sub(r"\s+", " ", m.group(1)).strip())
    data: dict = {
        "@context": "https://schema.org",
        "@type": "WebSite" if rel.rsplit("/", 1)[-1] == "index.html" and rel.count("/") <= 1 else "WebPage",
        "name": name or url,
        "url": url,
    }
    if m := DESC_RE.search(html):
        if desc := unescape(m.group(1).strip()):
            data["description"] = desc
    # </ 要斷開,免得 title/description 裡萬一出現 </script> 提早關掉標籤
    block = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    tag = f'  <script type="application/ld+json">\n  {block}\n  </script>'
    h = HEAD_END.search(html)
    if not h:
        return html, False
    return html[:h.start()] + tag + "\n" + html[h.start():], True


TWIN_DIR = "zh-Hant"   # 中文雙生頁的子目錄(root 是英文本尊;見 build_i18n.py)


def pair_of(rel: str, rel_set: set[str]) -> tuple[str, str] | None:
    """這一頁所屬的中英雙生組 (英文rel, 中文rel);沒有成對就 None。"""
    en = rel[len(TWIN_DIR) + 1:] if rel.startswith(TWIN_DIR + "/") else rel
    zh = f"{TWIN_DIR}/{en}"
    return (en, zh) if en in rel_set and zh in rel_set else None


def upsert_hreflang(html: str, root: Path, base: str,
                    pair: tuple[str, str] | None, rel: str = "") -> tuple[str, bool]:
    """整組換掉 head 的 hreflang 標註(en / zh-Hant / x-default→英文版)。

    hreflang 必須**雙向互指**且用絕對網址 —— 缺一邊或寫相對路徑,Google 會
    忽略整組。所以不做逐行 upsert,而是把舊的全部拆掉、按當前雙生組重建;
    沒有成對的頁面則把殘留的標註清乾淨。
    """
    stripped = HREFLANG_LINK_RE.sub("", html)
    if pair is None:
        return stripped, stripped != html
    en_url = url_of(root, root / pair[0], base)
    zh_url = url_of(root, root / pair[1], base)
    m = CANON_RE.search(stripped)
    if not m:
        # canonical 正常會在同一輪先被 upsert 好;走到這裡代表該頁連
        # </title>/</head> 錨點都沒有 —— 不硬插,但要讓人看見,不能安靜地漏。
        print(f"⚠ {rel}: 找不到 canonical 錨點,hreflang 未寫入(檢查該頁的 <head> 結構)")
        return stripped, stripped != html
    line_start = stripped.rfind("\n", 0, m.start()) + 1
    indent = stripped[line_start:m.start()]
    if indent.strip():
        indent = ""
    block = "".join(
        f'\n{indent}<link rel="alternate" hreflang="{hl}" href="{u}" />'
        for hl, u in (("en", en_url), ("zh-Hant", zh_url), ("x-default", en_url)))
    new = stripped[:m.end()] + block + stripped[m.end():]
    return new, new != html


def robots_blocks_all(text: str) -> bool:
    """該站是不是自己就不想被收錄。

    有些站是**刻意**用 `Disallow: /` 把自己藏起來的。對這種站補 sitemap 等於
    一邊請 Google 來、一邊把門關上 —— 要跳過,不要「順手修好」。
    """
    return bool(re.search(r"(?mi)^\s*Disallow:\s*/\s*$", text))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", required=True, help="產出的網站資料夾(含 index.html)")
    ap.add_argument("--base-url", help="站台網址;預設從 CNAME 讀")
    ap.add_argument("--check", action="store_true", help="只檢查不寫入,有東西過期就 exit 1")
    args = ap.parse_args()

    root = Path(args.dir).resolve()
    if not (root / "index.html").exists():
        print(f"✗ {root} 底下找不到 index.html")
        return 1

    base = (args.base_url or base_from_cname(root) or "").rstrip("/")
    if not base:
        print("✗ 找不到站台網址:請放 CNAME 檔,或用 --base-url 指定")
        return 1

    ps = pages(root)
    if not ps:
        print("✗ 找不到任何 .html")
        return 1

    print(f"站台網址:{base}\n")
    changed: list[str] = []

    # ---------- 每頁的 canonical / og:url / hreflang ----------
    rel_set = {p.relative_to(root).as_posix() for p in ps}
    print(f"{'頁面':<30} canonical  og:url     hreflang   json-ld")
    print("─" * 72)
    for p in ps:
        rel = p.relative_to(root).as_posix()
        url = url_of(root, p, base)
        html = p.read_text(encoding="utf-8")
        html, c1 = upsert(html, CANON_RE, f'<link rel="canonical" href="{url}" />')
        html, c2 = upsert(html, OGURL_RE, f'<meta property="og:url" content="{url}" />')
        pair = pair_of(rel, rel_set)
        html, c3 = upsert_hreflang(html, root, base, pair, rel)
        html, c4 = upsert_jsonld(html, rel, url)
        mark = lambda b: "更新" if b else "已正確"
        hl = mark(c3) if pair else ("清掉殘留" if c3 else "無雙生頁")
        print(f"{rel:<30} {mark(c1):<10} {mark(c2):<10} {hl:<10} {'補上' if c4 else '已有'}")
        if c1 or c2 or c3 or c4:
            changed.append(rel)
            if not args.check:
                p.write_text(html, encoding="utf-8")

    # 這個站是不是刻意不想被收錄?是的話 sitemap 與 robots 都不要碰 ——
    # 幫它做 sitemap 等於一邊請 Google 來、一邊把門關上。
    rb = root / "robots.txt"
    hidden = rb.exists() and robots_blocks_all(rb.read_text(encoding="utf-8"))

    # ---------- sitemap.xml ----------
    # hreflang 只用「head 標註」一種寫法;sitemap 保持純 <loc>(兩個語言都列)。
    # 兩種寫法同時存在會讓失效面加倍 —— 未來只改其一就分岔,而混用時以哪一份
    # 為準沒有保證。
    locs = [url_of(root, p, base) for p in ps]
    sitemap = ('<?xml version="1.0" encoding="UTF-8"?>\n'
               "<!--\n"
               "  本站自己的 sitemap。刻意不寫 <lastmod>:沒有可信的「內容最後修改時間」\n"
               "  來源(git commit 時間不等於內容變動時間),給錯的 lastmod 比不給更糟。\n"
               "-->\n"
               '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
               + "".join(f"  <url>\n    <loc>{xml_escape(u)}</loc>\n  </url>\n" for u in locs)
               + "</urlset>\n")
    sm = root / "sitemap.xml"
    sm_changed = False
    if not hidden:
        sm_changed = not sm.exists() or sm.read_text(encoding="utf-8") != sitemap
        if sm_changed:
            changed.append("sitemap.xml")
            if not args.check:
                sm.write_text(sitemap, encoding="utf-8")

    # ---------- robots.txt ----------
    line = f"Sitemap: {base}/sitemap.xml"
    rb_note = ""
    if hidden:
        rb_note = "跳過 — 這個站的 robots.txt 是 Disallow: /(刻意不收錄)"
    elif rb.exists():
        text = rb.read_text(encoding="utf-8")
        if re.search(r"(?mi)^Sitemap:", text):
            new = re.sub(r"(?mi)^Sitemap:.*$", line, text)
            if new != text:
                changed.append("robots.txt")
                if not args.check:
                    rb.write_text(new, encoding="utf-8")
        else:
            changed.append("robots.txt")
            if not args.check:
                rb.write_text(text.rstrip("\n") + f"\n\n{line}\n", encoding="utf-8")
    else:
        changed.append("robots.txt")
        if not args.check:
            rb.write_text(f"User-agent: *\nAllow: /\n\n{line}\n", encoding="utf-8")

    sm_note = rb_note if hidden else f"{'更新' if sm_changed else '已正確'}({len(locs)} 個網址)"
    print(f"\nsitemap.xml   {sm_note}")
    print(f"robots.txt    {rb_note or ('更新' if 'robots.txt' in changed else '已正確')}")

    verb = "需要更新" if args.check else "已寫入"
    print(f"\n{verb}:{len(changed)} 項" + (f" — {', '.join(changed)}" if changed else ""))
    if args.check and changed:
        print("→ 請跑一次不加 --check 的版本")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
