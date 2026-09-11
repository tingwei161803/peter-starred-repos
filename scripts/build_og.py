#!/usr/bin/env python3
"""build_og.py — 產生站台的社群分享預覽圖(og-image.png)並補進每一頁的 <head>。

    uv run --with pillow python scripts/build_og.py --dir ./my-site
    uv run --with pillow python scripts/build_og.py --dir ./my-site --accent "#2563EB"
    uv run --with pillow python scripts/build_og.py --dir ./my-site --force    # 重產圖
    uv run --with pillow python scripts/build_og.py --dir ./my-site --check    # CI:缺就 exit 1

**為什麼需要**

網址貼到 Facebook / LinkedIn / Threads 時,預覽卡吃的是 head 裡的 og:image;
沒有它就只剩純文字小卡。站群的頁面內容是 JS 注入的,平台爬蟲也抓不到頁內
圖片來湊。曾經整個站群 50 站只有一站帶 og:image,而且還是 SVG ——
Facebook / LinkedIn 不支援 SVG,等於沒有。從產生器補一次,之後的站不會再漏。

**做法**

- 圖:1200×630 PNG。主標=中文站名(取 `zh-Hant/index.html` 的 `<title>`)、
  副標=英文站名(取 root `index.html` 的 `<title>`;root 是英文本尊)、
  域名與品牌列。單語站只放一個標題。強調色 `--accent` 沒給時,從 assets CSS
  的 `--md-sys-color-primary` 撿,再不行用預設藍。
- meta:每頁確保有 og:image / og:image:width / og:image:height / twitter:image;
  twitter:card 是 summary 就升級 summary_large_image,沒有就補。
  **既有的 og:image 不覆蓋**(作者可能放了手工設計的圖)——唯一例外是指向
  SVG 的,直接改指到新 PNG(那正是要修的 bug)。
- `og-image.png` 已存在時不重產(`--force` 才重產),理由同上。

站台網址從 CNAME 讀(同 build_seo.py,`--base-url` 可覆寫)。需要 Pillow,
所以跑法是 `uv run --with pillow`。字型依序找:華康黑體(使用者字型)、
PingFang、Hiragino、Arial Unicode;都沒有就明講並失敗,不產缺字的圖。
"""
from __future__ import annotations

import argparse
import re
import sys
from html import unescape
from pathlib import Path

from build_seo import base_from_cname, pages  # 同目錄;頁面清單與網址來源一致

W, H = 1200, 630
# 跟網站的深色色票對齊(assets/styles.css 的 --bg / --fg / --fg2)——
# 社群預覽圖跟點進來看到的頁面是同一個底色,不然會像兩個不同的站。
BG, FG, SUB = "#07070A", "#EDEDF0", "#A8A8B4"
DEFAULT_ACCENT = "#9A8CFF"
HOME = Path.home()
FONT_SETS = [  # (粗體, 中等, 細體)
    (HOME / "Library/Fonts/DFHeiStd-W9.otf", HOME / "Library/Fonts/DFHeiStd-W5.otf",
     HOME / "Library/Fonts/DFHeiStd-W3.otf"),
    tuple([Path("/System/Library/Fonts/PingFang.ttc")] * 3),
    tuple([Path("/System/Library/Fonts/Hiragino Sans GB.ttc")] * 3),
    tuple([Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf")] * 3),
]

TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
OGIMG_RE = re.compile(r'<meta\b[^>]*\bproperty=["\']og:image["\'][^>]*>', re.I)
# 從 styles.css 自動讀強調色。`--md-sys-color-primary` 是 MD3 原本的 token 名,
# 這個站用的是 `--primary` —— 兩個都認,重跑管線就不用記得加 --accent。
# (`--on-primary` / `--primary-container` 不會誤中:冒號必須緊接在 primary 後面。)
ACCENT_RE = re.compile(r"--(?:md-sys-color-)?primary\s*:\s*(#[0-9a-fA-F]{6})")


def page_title(path: Path) -> str | None:
    if not path.exists():
        return None
    m = TITLE_RE.search(path.read_text(encoding="utf-8"))
    return unescape(m.group(1)).strip() if m else None


def pick_accent(root: Path, override: str | None) -> str:
    if override:
        return override
    for css in sorted(root.rglob("*.css")):
        m = ACCENT_RE.search(css.read_text(encoding="utf-8", errors="ignore"))
        if m:
            return m.group(1)
    return DEFAULT_ACCENT


def pick_fonts():
    from PIL import ImageFont  # noqa: F401 - 只確認可載入
    for bold, med, light in FONT_SETS:
        if bold.exists() and med.exists() and light.exists():
            return str(bold), str(med), str(light)
    sys.exit("✗ 找不到可用的中文字型(華康黑體 / PingFang / Hiragino / Arial Unicode)")


def _tokenize(text: str) -> list[str]:
    """CJK 逐字、latin 整個單字當一個 token —— 斷行不能從英文單字中間切開。"""
    return re.findall(
        r"[一-鿿　-〿＀-￯]"
        r"|\s*[^一-鿿　-〿＀-￯\s]+", text)


def _fit_wrap(draw, text, font_path, max_size, max_width, max_lines):
    from PIL import ImageFont
    toks = _tokenize(text)
    size = max_size
    while size >= 30:
        font = ImageFont.truetype(font_path, size)
        lines, cur = [], ""
        for tok in toks:
            trial = cur + tok
            if not cur or draw.textlength(trial, font=font) <= max_width:
                cur = trial
            else:
                lines.append(cur)
                cur = tok.lstrip()
        lines.append(cur)
        if len(lines) <= max_lines and all(
                draw.textlength(ln, font=font) <= max_width for ln in lines):
            return font, lines
        size -= 6
    return font, lines[:max_lines]


def render_card(out: Path, host: str, main: str, sub: str | None, accent: str) -> None:
    from PIL import Image, ImageDraw, ImageFont
    f_bold, f_med, f_light = pick_fonts()
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    margin = 80
    d.text((margin, 72), host, font=ImageFont.truetype(f_med, 34), fill=accent)
    f_main, main_lines = _fit_wrap(d, main, f_bold, 92, W - margin * 2, 2)
    y = 170
    for ln in main_lines:
        d.text((margin, y), ln, font=f_main, fill=FG)
        y += int(f_main.size * 1.28)
    if sub and sub.strip() and sub.strip().lower() not in main.lower():
        f_sub, sub_lines = _fit_wrap(d, sub, f_light, 44, W - margin * 2, 2)
        y += 18
        for ln in sub_lines:
            d.text((margin, y), ln, font=f_sub, fill=SUB)
            y += int(f_sub.size * 1.35)
    d.text((margin, H - 96), "peteraim.com", font=ImageFont.truetype(f_light, 30), fill=SUB)
    d.rectangle([0, H - 14, W, H], fill=accent)
    img.save(out, "PNG", optimize=True)


def patch_head(html: str, img_url: str) -> str:
    html = re.sub(r'(name=["\']twitter:card["\'][^>]*content=["\'])summary(["\'])',
                  r"\g<1>summary_large_image\g<2>", html)
    html = re.sub(r'(content=["\'])summary(["\'][^>]*name=["\']twitter:card["\'])',
                  r"\g<1>summary_large_image\g<2>", html)
    existing = OGIMG_RE.search(html)
    if existing and re.search(r'\.svg["\']', existing.group(0), re.I):
        # 指向 SVG 的既有 og:image 直接改指 PNG —— 社群平台不吃 SVG
        html = re.sub(r'(property=["\']og:image["\'][^>]*content=["\'])[^"\']*(["\'])',
                      r"\g<1>" + img_url + r"\g<2>", html)
        html = re.sub(r'(name=["\']twitter:image["\'][^>]*content=["\'])[^"\']*(["\'])',
                      r"\g<1>" + img_url + r"\g<2>", html)
        existing = None if not OGIMG_RE.search(html) else OGIMG_RE.search(html)

    def insert(snippet: str) -> str:
        m = re.search(r"</head\s*>", html, re.I)
        return html[: m.start()] + snippet + html[m.start():] if m else html

    if not OGIMG_RE.search(html):
        html = insert(f'  <meta property="og:image" content="{img_url}">\n')
    if "og:image:width" not in html:
        html = insert('  <meta property="og:image:width" content="1200">\n'
                      '  <meta property="og:image:height" content="630">\n')
    if "twitter:image" not in html:
        html = insert(f'  <meta name="twitter:image" content="{img_url}">\n')
    if "twitter:card" not in html:
        html = insert('  <meta name="twitter:card" content="summary_large_image">\n')
    return html


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", required=True)
    ap.add_argument("--base-url", help="站台網址;預設從 CNAME 讀")
    ap.add_argument("--accent", help="卡片強調色;預設從 CSS 的 --md-sys-color-primary 撿")
    ap.add_argument("--force", action="store_true", help="og-image.png 已存在也重產")
    ap.add_argument("--check", action="store_true", help="只檢查不寫檔;缺就 exit 1")
    args = ap.parse_args()
    root = Path(args.dir).resolve()
    base = (args.base_url or base_from_cname(root) or "").rstrip("/")
    if not base:
        print("✗ 找不到站台網址:請放 CNAME 檔,或用 --base-url 指定")
        return 1
    img_url = f"{base}/og-image.png"
    img_path = root / "og-image.png"

    if args.check:
        missing = [p.relative_to(root) for p in pages(root)
                   if not OGIMG_RE.search(p.read_text(encoding="utf-8"))]
        ok = img_path.exists() and not missing
        print("✓ og-image.png 與每頁 og:image 都在" if ok
              else f"✗ 缺:{'og-image.png ' if not img_path.exists() else ''}{missing}")
        return 0 if ok else 1

    if img_path.exists() and not args.force:
        print(f"og-image.png 已存在,不重產(--force 可重產):{img_path}")
    else:
        zh = page_title(root / "zh-Hant" / "index.html")
        en = page_title(root / "index.html")
        if not (zh or en):
            print("✗ 讀不到任何 <title>,無法產圖")
            return 1
        render_card(img_path, base.removeprefix("https://"),
                    zh or en, en if zh else None, pick_accent(root, args.accent))
        print(f"✓ 產生 {img_path.name}(1200×630)")

    changed = 0
    for p in pages(root):
        html = p.read_text(encoding="utf-8")
        new = patch_head(html, img_url)
        if new != html:
            p.write_text(new, encoding="utf-8")
            changed += 1
    total = len(pages(root))
    print(f"HTML {total} 頁:補 meta {changed} 頁,原已合規 {total - changed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
