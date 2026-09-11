#!/usr/bin/env python3
"""build_i18n.py — 從英文頁產生中文雙生頁:`X.html` → `zh-Hant/X.html`。

    uv run python scripts/build_i18n.py --dir ./my-site
    uv run python scripts/build_i18n.py --dir ./my-site --check   # CI:有雙生頁缺漏/過期就 exit 1

**為什麼是「一語言一個 URL」**

同一個 URL 用 JS 切換中英文,Google 只會索引預設語言 —— 爬蟲不會去按語言鈕,
另一個語言等於不存在於索引裡;中英夾雜也會讓語言偵測失準,兩邊排名都被稀釋。
Google 官方建議就是每個語言版本一個獨立 URL,彼此用 hreflang 互指。

**網址結構:預設語言在 root,另一語言放子目錄**

    <site>/index.html            ← 英文(本 skill 的預設語言,root 本尊)
    <site>/zh-Hant/index.html    ← 中文(鏡射整個頁面樹的雙生頁)

中文版**不是手寫的第二份檔案** —— 單一事實來源是英文版,雙生頁永遠可以重生,
不會跟本尊漂移。子目錄鏡射還有個結構性好處:雙生頁之間的站內連結
(`href="Y.html"`)是同目錄兄弟,**原樣就對**,不需要改寫;只有指向 root 的
資產(assets/、data/)要補一層 `../`。

> 對**既有已收錄**的站補第二語言時,原則同樣是「現有語言留在 root、新語言放
> 子目錄」—— 絕不把 root 內容搬走或轉址(那會讓所有已收錄網址一次全變,
> 把先前換來的收錄成果重置)。root 是中文的舊站就把英文放 `/en/`,維持即可;
> 這支腳本假設的是本 skill 新建站的佈局(root=en、中文在 `zh-Hant/`)。

**轉換內容**(對每個英文頁)

1. `<html lang="en">` → `<html lang="zh-Hant">` —— 這是整個架構的語言開關:
   `app.js` 與 `prerender.py` 都以 `<html lang>` 決定渲染語言,不再讀 localStorage。
2. head 裡帶 `data-zh` 的 `<title>` / `<meta>`:文字或 `content` 換成 `data-zh`
   的值(中文版標題/描述寫在英文版的 `data-zh` 屬性裡,不用維護第二份 head)。
   `data-zh` 還是 `{{…}}` 佔位符就保留英文並提出警告,不把佔位符寫進成品。
3. 相對資產路徑(assets/、data/ 等非頁面連結)補一層 `../`,讓子目錄裡的
   雙生頁仍指向同一份資產 —— 零 build 的站只有一份 CSS/JS/資料。
   script / pre / code / textarea 的**內容**會先遮罩,教學示例碼不會被改壞。
4. JSON-LD 跟著語言走:根層 name / headline / description 換成中文字串、
   `url` 插入 `/zh-Hant`;解析失敗原樣保留並警告。
5. `id="langLink"`(appbar)與 `id="langAlt"`(靜態備援)的語言連結:
   指回英文本尊、標籤 `中`/`中文版` 換 `EN`/`English`。

**`<main>` 與 SEO 標籤不參與**:雙生頁的 `<main>` 之後由 `prerender.py` 以中文
重新渲染;canonical / og:url / hreflang 則由 `build_seo.py` 依正式網址逐頁產生
—— 兩者都不是這支腳本的產出,所以產生雙生頁時一律**剝掉**從英文本尊帶來的
SEO 標籤(抄過來必然是錯的:canonical 會指向另一語言),`--check` 比對時
兩者也都剔除。這樣三支腳本各管各的,任意順序、任意次數都收斂。

**執行順序**:build_i18n → build_seo → prerender → verify。
build_seo 會替每組雙生頁補上 canonical 與 hreflang 互指(需要正式網址),
prerender 再把兩個語言各自的內容寫回靜態檔。改了英文頁或資料層,照同樣順序重跑。

只用標準函式庫,不需要瀏覽器。
"""
from __future__ import annotations

import argparse
import json
import posixpath
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_seo import pages  # noqa: E402  共用同一套「哪些 .html 算站頁」的判定(git-ignore aware)

# 中文雙生頁所在的子目錄(鏡射英文頁面樹)。
TWIN_DIR = "zh-Hant"
# 不產生雙生頁的頁面:錯誤頁沒有語言版本的意義,列進 hreflang/sitemap 反而是雜訊。
TWIN_EXCLUDE = {"404.html"}

HTML_TAG_RE = re.compile(r"<html\b[^>]*>", re.I)
LANG_ATTR_RE = re.compile(r'(\blang=["\'])([^"\']*)(["\'])', re.I)
TITLE_RE = re.compile(r"(<title\b[^>]*>)(.*?)(</title>)", re.I | re.S)
META_ZH_RE = re.compile(r"<meta\b[^>]*\bdata-zh=[\"'][^\"']*[\"'][^>]*>", re.I)
# 語言連結:appbar 的 #langLink(單頁模板)與 </body> 前的靜態備援 #langAlt(multipage)
LANGLINK_RE = re.compile(r"<a\b[^>]*\bid=[\"'](?:langLink|langAlt)[\"'][\s\S]*?</a>", re.I)
URL_ATTR_RE = re.compile(r"""(\b(?:href|src)=["'])([^"']+)(["'])""", re.I)
MAIN_RE = re.compile(r"<main\b[^>]*>", re.I)
DATA_ZH_ATTR_RE = re.compile(r"""\s*\bdata-zh=["'][^"']*["']""", re.I)
CONTENT_ATTR_RE = re.compile(r'(\bcontent=["\'])([^"\']*)(["\'])', re.I)
DATA_ZH_VALUE_RE = re.compile(r"""\bdata-zh=["']([^"']*)["']""", re.I)
DESC_META_RE = re.compile(r"<meta\b[^>]*\bname=[\"']description[\"'][^>]*>", re.I)
JSONLD_RE = re.compile(
    r"(<script\b[^>]*type=[\"']application/ld\+json[\"'][^>]*>)([\s\S]*?)(</script>)", re.I)
# 內文不該被連結改寫碰到的區塊:跳脫過的教學示例碼(&lt;a href=...&gt;)與 JS
# 樣板字串裡的 href=/src= 都長得跟真的一樣,直接掃全文會把「顯示給讀者看的
# 程式範例」改壞(站群實戰踩過兩次)。開標籤本身(<script src=...>)仍要處理,
# 所以只遮內容、不遮標籤。
PROTECTED_RE = re.compile(r"(<(script|pre|code|textarea)\b[^>]*>)([\s\S]*?)(</\2\s*>)", re.I)
# 這三種標籤逐頁不同、由 build_seo 依正式網址產生 —— 是它的地盤。
# 從英文本尊原樣抄過來的話:canonical 會指向另一語言(自己宣告不是本尊,
# 蓋掉整組 hreflang),而且兩支腳本每輪互相覆寫、--check 永遠紅。
SEO_CANON_RE = re.compile(r"[ \t]*<link\b[^>]*\brel=[\"']canonical[\"'][^>]*>\n?", re.I)
SEO_OGURL_RE = re.compile(r"[ \t]*<meta\b[^>]*\bproperty=[\"']og:url[\"'][^>]*>\n?", re.I)
SEO_HREFLANG_RE = re.compile(r"[ \t]*<link\b[^>]*\bhreflang=[\"'][^\"']*[\"'][^>]*>\n?", re.I)

warnings: list[str] = []


def twin_of(rel: str) -> str:
    """index.html → zh-Hant/index.html(鏡射整個頁面樹)。"""
    return f"{TWIN_DIR}/{rel}"


def is_twin(rel: str) -> bool:
    return rel.startswith(TWIN_DIR + "/")


def split_main(src: str) -> tuple[str, str, str] | None:
    """(main 開標籤含之前, main 內容, </main> 起之後)。恰好一組才可靠,否則不動。"""
    opens = MAIN_RE.findall(src)
    if len(opens) != 1 or src.lower().count("</main>") != 1:
        return None
    m = MAIN_RE.search(src)
    close = src.lower().index("</main>")
    return src[: m.end()], src[m.end():close], src[close:]


def strip_seo_tags(src: str) -> str:
    """剝掉 canonical / og:url / hreflang —— 逐頁不同、由 build_seo 產生。"""
    return SEO_HREFLANG_RE.sub("", SEO_OGURL_RE.sub("", SEO_CANON_RE.sub("", src)))


def comparable(src: str) -> str:
    """比對用:剔除 <main> 內容(prerender 的地盤)與 SEO 標籤(build_seo 的地盤)。

    兩者都不是這支腳本的產出 —— 比進去的話,每跑一次 prerender 或 build_seo,
    --check 就會誤報雙生頁過期,pipeline 永遠不收斂。
    """
    parts = split_main(src)
    return strip_seo_tags(parts[0] + parts[2] if parts else src)


def _swap_lang_attr(src: str) -> str:
    m = HTML_TAG_RE.search(src)
    if not m:
        return src
    tag = m.group(0)
    if LANG_ATTR_RE.search(tag):
        new_tag = LANG_ATTR_RE.sub(lambda g: g.group(1) + "zh-Hant" + g.group(3), tag, count=1)
    else:
        new_tag = tag[:-1] + ' lang="zh-Hant">'
    return src[: m.start()] + new_tag + src[m.end():]


def _swap_head_zh(src: str, rel: str) -> str:
    """head 裡帶 data-zh 的 <title>/<meta>:內容換成中文、拿掉 data-zh 屬性。"""

    def usable(zh: str) -> bool:
        return bool(zh.strip()) and "{{" not in zh

    def do_title(m: re.Match) -> str:
        open_tag, text, close = m.group(1), m.group(2), m.group(3)
        zh_m = DATA_ZH_VALUE_RE.search(open_tag)
        if not zh_m:
            return m.group(0)
        zh = zh_m.group(1)
        if not usable(zh):
            warnings.append(f"{rel}: <title> 的 data-zh 還是佔位符/空值,中文版沿用英文標題")
            zh = text
        return "<title>" + zh + "</title>"

    def do_meta(m: re.Match) -> str:
        tag = m.group(0)
        zh_m = DATA_ZH_VALUE_RE.search(tag)
        zh = zh_m.group(1) if zh_m else ""
        if usable(zh):
            tag = CONTENT_ATTR_RE.sub(lambda g: g.group(1) + zh + g.group(3), tag, count=1)
        else:
            warnings.append(f"{rel}: 某個 <meta> 的 data-zh 還是佔位符/空值,中文版沿用英文")
        return DATA_ZH_ATTR_RE.sub("", tag)

    src = TITLE_RE.sub(do_title, src, count=1)
    return META_ZH_RE.sub(do_meta, src)


def _extract_zh_strings(src: str) -> tuple[str, str]:
    """從英文本尊的 data-zh 屬性取中文標題與描述(給 head 與 JSON-LD 共用)。"""

    def usable(s: str) -> str:
        return s if s.strip() and "{{" not in s else ""

    title = desc = ""
    m = re.search(r"<title\b[^>]*>", src, re.I)
    if m:
        zm = DATA_ZH_VALUE_RE.search(m.group(0))
        if zm:
            title = zm.group(1)
    dm = DESC_META_RE.search(src)
    if dm:
        zm = DATA_ZH_VALUE_RE.search(dm.group(0))
        if zm:
            desc = zm.group(1)
    return usable(title), usable(desc)


def _zh_url(u: str) -> str:
    """絕對網址插入 /zh-Hant:https://x.com/ → https://x.com/zh-Hant/。"""
    m = re.match(r"^(https?://[^/]+)(/.*)?$", u)
    if not m or "{{" in u:
        return u
    origin, path = m.group(1), m.group(2) or "/"
    if path.startswith("/zh-Hant/") or path == "/zh-Hant":
        return u
    return origin + "/zh-Hant" + path


def _swap_jsonld(src: str, zh_title: str, zh_desc: str, rel: str) -> str:
    """JSON-LD 跟著語言走:name/headline/description 換中文、url 指向中文版。

    不做的話,中文版的結構化資料整包是英文的、`url` 還指向英文網址 ——
    跟 `<html lang="zh-Hant">` 自相矛盾。只動**根層** key(深層的 author.name
    之類不是站名,碰了反而錯);解析失敗就原樣保留並警告,不猜著改。

    同理 **`@graph` 內的節點不會自動中文化**:graph 是異質節點的集合
    (WebSite、Person、Organization…),對每個節點都換 name 會把作者名之類
    換成站名。模板若哪天改用 @graph,這裡要跟著設計 —— 先偵測並警告,不靜默。
    """

    def patch_top(obj: object) -> None:
        if isinstance(obj, list):
            for item in obj:
                patch_top(item)
            return
        if not isinstance(obj, dict):
            return
        for key in ("name", "headline"):
            if zh_title and isinstance(obj.get(key), str):
                obj[key] = zh_title
        if zh_desc and isinstance(obj.get("description"), str):
            obj["description"] = zh_desc
        if isinstance(obj.get("url"), str):
            obj["url"] = _zh_url(obj["url"])
        if isinstance(obj.get("inLanguage"), str):
            obj["inLanguage"] = "zh-Hant"

    def do(m: re.Match) -> str:
        try:
            obj = json.loads(m.group(2))
        except ValueError:
            warnings.append(f"{rel}: JSON-LD 不是合法 JSON,中文版原樣保留(請人工確認)")
            return m.group(0)
        if isinstance(obj, dict) and "@graph" in obj:
            warnings.append(f"{rel}: JSON-LD 使用 @graph —— graph 內節點不會自動中文化,請人工確認")
        patch_top(obj)
        body = json.dumps(obj, ensure_ascii=False, indent=2)
        return m.group(1) + "\n  " + body.replace("\n", "\n  ") + "\n  " + m.group(3)

    return JSONLD_RE.sub(do, src)


def _flip_langlink(src: str, rel: str) -> str:
    """語言連結指回英文本尊:href 整個換掉、hreflang/標籤/aria 對調。"""
    back = posixpath.relpath(rel, posixpath.dirname(twin_of(rel)))   # index.html → ../index.html

    def flip(m: re.Match) -> str:
        tag = m.group(0)
        tag = re.sub(r'\bhref=["\'][^"\']*["\']', f'href="{back}"', tag, count=1)
        tag = re.sub(r'\bhreflang=["\']zh-Hant["\']', 'hreflang="en"', tag, count=1)
        tag = re.sub(r'\btitle=["\'][^"\']*["\']', 'title="English"', tag, count=1)
        tag = re.sub(r'\baria-label=["\'][^"\']*["\']',
                     'aria-label="English / 切換到英文版"', tag, count=1)
        tag = re.sub(r"(>\s*)中文版(\s*<)", r"\g<1>English\g<2>", tag, count=1)
        tag = re.sub(r"(>\s*)中(\s*<)", r"\g<1>EN\g<2>", tag, count=1)
        return tag

    return LANGLINK_RE.sub(flip, src)


def _rebase_urls(src: str, rel: str, en_pages: set[str]) -> str:
    """相對資產路徑補一層 `../`,讓子目錄裡的雙生頁仍指向同一份資產。

    子目錄鏡射的結構性好處:指向**其他頁面**的相對連結(`href="Y.html"`)在
    雙生頁裡是同目錄兄弟,**原樣就對**,不改;要改的只有指向 root 的資產
    (assets/、data/、favicon…)。絕對網址與 `/` 開頭、`#` 開頭的都不動。

    掃描前先把 script / pre / code / textarea 的**內容**遮罩起來(標籤本身照掃,
    `<script src>` 要 rebase):教學頁跳脫過的示例碼與 JS 樣板字串裡的
    `href=`/`src=` 長得跟真的一樣,不遮就會把顯示給讀者的程式範例改壞。
    """
    stash: list[str] = []

    def mask(m: re.Match) -> str:
        stash.append(m.group(3))
        return m.group(1) + f"\x00PROT{len(stash) - 1}\x00" + m.group(4)

    src = PROTECTED_RE.sub(mask, src)
    here = posixpath.dirname(rel)

    def resolve(target: str) -> str | None:
        path = target.split("#", 1)[0].split("?", 1)[0]
        if not path or re.match(r"^[a-z][a-z0-9+.-]*:", path, re.I) or path.startswith("//"):
            return None                      # 外部網址 / 純錨點
        if path.startswith("/"):
            return None                      # root-絕對路徑在子目錄照樣成立,不動
        return posixpath.normpath(posixpath.join(here, path))

    def fix(m: re.Match) -> str:
        prefix, target, quote = m.group(1), m.group(2), m.group(3)
        norm = resolve(target)
        if norm is None:
            return m.group(0)
        if norm in en_pages:                 # 頁面連結:鏡射後仍是兄弟,原樣保留
            return m.group(0)
        return prefix + "../" + target + quote

    src = URL_ATTR_RE.sub(fix, src)
    return re.sub(r"\x00PROT(\d+)\x00", lambda m: stash[int(m.group(1))], src)


def transform(src: str, rel: str, en_pages: set[str]) -> str:
    zh_title, zh_desc = _extract_zh_strings(src)
    src = _rebase_urls(src, rel, en_pages)
    src = _flip_langlink(src, rel)           # 在 rebase 之後:它的 href 是整個重算的
    src = _swap_lang_attr(src)
    src = _swap_head_zh(src, rel)
    src = _swap_jsonld(src, zh_title, zh_desc, rel)
    # SEO 標籤(canonical / og:url / hreflang)是 build_seo 依正式網址逐頁產生的,
    # 從英文本尊抄過來必然是錯的(canonical 指向另一語言)—— 一律剝掉,
    # 讓兩支腳本各管各的,任意順序、任意次數都收斂。
    return strip_seo_tags(src)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", required=True, help="產出的網站資料夾(含 index.html)")
    ap.add_argument("--check", action="store_true", help="只檢查不寫入,有缺漏/過期就 exit 1")
    args = ap.parse_args()

    root = Path(args.dir).resolve()
    if not (root / "index.html").exists():
        print(f"✗ {root} 底下找不到 index.html")
        return 1

    all_pages = [p.relative_to(root).as_posix() for p in pages(root)]
    en_pages = {r for r in all_pages if not is_twin(r) and r not in TWIN_EXCLUDE
                and Path(r).name not in TWIN_EXCLUDE}
    if not en_pages:
        print("✗ 找不到任何英文頁")
        return 1

    # 英文本尊已經不在了的雙生頁 —— 提醒清掉,不自動刪(那是使用者的檔案)。
    orphans = [r for r in all_pages if is_twin(r)
               and r[len(TWIN_DIR) + 1:] not in en_pages]

    changed: list[str] = []
    print(f"{'頁面':<30} 雙生頁")
    print("─" * 52)
    for rel in sorted(en_pages):
        src = (root / rel).read_text(encoding="utf-8")
        twin_rel = twin_of(rel)
        twin_path = root / twin_rel
        new = transform(src, rel, en_pages)

        if twin_path.exists():
            old = twin_path.read_text(encoding="utf-8")
            if comparable(old) == comparable(new):
                print(f"{rel:<30} 已正確 → {twin_rel}")
                continue
            # 保留既有雙生頁的 <main>(可能已是 prerender 過的中文內容)
            old_parts, new_parts = split_main(old), split_main(new)
            if old_parts and new_parts:
                new = new_parts[0] + old_parts[1] + new_parts[2]

        changed.append(twin_rel)
        print(f"{rel:<30} {'需要更新' if args.check else '已寫入'} → {twin_rel}")
        if not args.check:
            twin_path.parent.mkdir(parents=True, exist_ok=True)
            twin_path.write_text(new, encoding="utf-8")

    for w in warnings:
        print(f"⚠ {w}")
    for o in orphans:
        print(f"⚠ {o} 的英文本尊已不存在 —— 若該頁已移除,請一併刪掉這個雙生頁")

    verb = "需要更新" if args.check else "已寫入"
    print(f"\n{verb}:{len(changed)} 頁" + (f" — {', '.join(changed)}" if changed else ""))
    if args.check and changed:
        print("→ 請跑一次不加 --check 的版本")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
