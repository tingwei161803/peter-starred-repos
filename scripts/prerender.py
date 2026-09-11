#!/usr/bin/env python3
"""prerender.py — 把 JS 渲染出來的內容寫回靜態 HTML,讓搜尋引擎第一波就看得到。

    uv run python scripts/prerender.py --dir ./my-site       # 寫入
    uv run python scripts/prerender.py --dir ./my-site --check   # 只檢查,CI 用

**為什麼需要這一步**

這些模板的 `<main>` 是空的,內容由 `app.js` 在瀏覽器端注入。但 Google 的索引分兩波:

    第一波  抓原始 HTML,直接索引裡面的文字        快,幾乎必然發生
    第二波  排進渲染佇列,執行 JS 後再看一次       慢、要排隊、**不保證**

第二波的優先級跟網域權重掛鉤,新網域排在最後面。所以只有 JS 渲染的站,
Google 第一波看到的是**空白頁** —— 看起來像「檢索了但決定不收錄」,
實際上它從來沒看到內容。

**做法:用網站自己的 renderer**

不重寫任何渲染邏輯。開一個 headless Chromium 載入頁面、等 `app.js` 畫完,
把 `<main>` 的 innerHTML 抓下來寫回原始檔。好處是產出**必然**與使用者看到的一致,
不會有「prerender 版本和真實版本漂移」這種只在 SEO 上出事、平常看不出來的 bug。

**為什麼安全(不會內容重複)**

所有模板的 `app.js` 都是**取代**語意 —— `x.innerHTML = ...`,或
`x.innerHTML = ""` 之後才 `appendChild`。開瀏覽器時 `app.js` 會把 `<main>`
整個蓋掉,所以寫進去的靜態內容對有 JS 的使用者不會顯示兩次。
沒有 JS 的訪客(以及第一波爬蟲)則直接看到內容 —— 這就是漸進增強。

**冪等**:重跑會得到相同結果。第二次跑時 server 送出的是已 prerender 的檔案,
但 `app.js` 照樣蓋掉,抓下來的仍是同一份渲染結果。

依賴與 `verify.py` 相同(Playwright + Chromium),沒有新增任何東西。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify import start_static_server  # noqa: E402  共用同一個靜態 server

RENDER_TIMEOUT_MS = 15000
# 寫入後可見字數超過原本這個倍數,就判定為內容重複。留一點餘裕給
# 隨機出題、時間戳這類本來就會變動的內容。
DUP_TOLERANCE = 1.3
# 連續兩次取樣相同才算穩定 —— 用來等 count-up 之類的動畫跑完
STABLE_INTERVAL_MS = 400
STABLE_MAX_TRIES = 12
REVEAL_MAX_TRIES = 6       # 逐一捲進視窗、等 observer 觸發的回合數
REVEAL_SETTLE_MS = 250
# 「看不見」的門檻。不能用「不等於 1」:0.85 的引用文字、0.9 的卡片圖示、0.55 的
# 引號裝飾都是**刻意的設計**,不是沒顯示出來。抓錯會讓每一頁都白跑滿回合數。
INVISIBLE_OPACITY = 0.1

# 內容容器一律是 <main>。單頁模板的 id 各不相同(#grid / #board / #matrix…),
# 有幾個甚至沒有 id,所以用標籤而不是 id 來鎖定。
MAIN_OPEN = re.compile(r"<main\b[^>]*>", re.I)


def visible_words(html: str) -> int:
    """去掉 script/style 後的可見字數 —— 這是爬蟲第一波看到的量。"""
    body = html[html.lower().find("<body"):] if "<body" in html.lower() else html
    body = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", body, flags=re.S | re.I)
    return len(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)).split())


def split_main(src: str) -> tuple[str, str, str] | None:
    """把原始碼切成 (main 開標籤前, main 內容, main 閉標籤後)。

    `<main>` 不能巢狀,而且沒有任何 renderer 會吐出 `<main>`,
    所以「恰好一個開標籤 + 一個閉標籤」是可以驗證的前提 —— 不成立就放棄,
    不要用猜的去改別人的 HTML。
    """
    opens = MAIN_OPEN.findall(src)
    if len(opens) != 1 or src.lower().count("</main>") != 1:
        return None
    m = MAIN_OPEN.search(src)
    close = src.lower().index("</main>")
    return src[:m.end()], src[m.end():close], src[close:]


def page_lang(src: str) -> str:
    """把 <html lang> 對應成 app.js 認得的語言碼,預設 en。"""
    m = re.search(r"<html[^>]*\blang=[\"']([^\"']+)", src, re.I)
    return "zh" if m and m.group(1).lower().startswith("zh") else "en"


def html_files(root: Path) -> list[Path]:
    # tmp/ 是本機工作區(設計原型、部署筆記),在 .gitignore 裡、不是網站的一部分。
    # 不排除的話每次重跑都會多載入近 30 個原型頁,而且會真的去改寫它們。
    skip = {"assets", "data", "scripts", "node_modules", ".git", "tmp"}
    return sorted(p for p in root.rglob("*.html")
                  if not any(part in skip for part in p.relative_to(root).parts[:-1]))


def _trigger_lazy(page) -> None:
    """把整頁捲過一遍,觸發 IntersectionObserver 之後再捲回頂端。

    **不做會靜靜寫進錯的數字。** 有些站的 count-up 統計是捲動到可視範圍才啟動的,
    而那個區塊常常在第一屏以下(實測有站在 top: 828px,預設 viewport 是 1280x720)。
    headless 瀏覽器不會自己捲動,observer 永遠不觸發,擷取到的「穩定值」就是動畫
    的**起始值 0**。

    這比擷取到中間值更難發現:因為 0 跟原始碼裡本來就有的 0 一樣,寫回去等於
    「沒有變動」,腳本連「已寫入」都不會計數 —— 這個 bug 對工具完全隱形。
    """
    try:
        page.evaluate(
            "async () => {"
            # `scroll-behavior: smooth` 會讓每次 scrollTo 變成一段動畫,而下一次
            # scrollTo 在動畫還沒到位時就把它打斷 —— 於是實際掃過的位置是跳躍的,
            # 中間好幾屏根本沒被經過,那些區塊的 observer 也就不會觸發。
            # 擷取不需要動畫,直接關掉。
            "  const prev = document.documentElement.style.scrollBehavior;"
            "  document.documentElement.style.scrollBehavior = 'auto';"
            "  const step = Math.max(200, window.innerHeight * 0.8);"
            "  for (let y = 0; y < document.body.scrollHeight; y += step) {"
            "    window.scrollTo(0, y);"
            "    await new Promise(r => setTimeout(r, 120));"
            "  }"
            "  window.scrollTo(0, 0);"
            "  await new Promise(r => setTimeout(r, 200));"
            "  document.documentElement.style.scrollBehavior = prev;"
            "}"
        )
    except Exception:  # noqa: BLE001 - 捲不動就算了,後面的穩定等待仍會執行
        pass


# 「還沒顯示出來」的判準:問瀏覽器算完的 opacity / visibility,不猜 class 名稱。
# 各站的捲動顯示 class 不一樣(實測有 `is-visible` 也有 `is-in`),寫死任何一個
# 都會對另一種誤判 —— 這種比對做過六次、錯過六次。computed style 只有一種。
_REVEAL_HIDDEN_JS = """() => {
  const hidden = [];
  document.querySelectorAll('main *').forEach(el => {
    if (!el.textContent || !el.textContent.trim()) return;
    const s = getComputedStyle(el);
    if (parseFloat(s.opacity) < %s || s.visibility === 'hidden') hidden.push(el);
  });
  hidden.forEach(el => el.scrollIntoView({block: 'center'}));
  return hidden.length;
}""" % INVISIBLE_OPACITY


def _force_reveal(page) -> int:
    """把 `<main>` 裡仍然透明的內容逐一捲進視窗,直到不再有隱藏內容。

    捲動顯示的 CSS 是 `.reveal { opacity: 0 }` 加上 JS 在 observer 觸發時補一個
    class。整頁捲一遍通常就夠,但**會有漏網**:實測同一頁連跑兩次,某個 `<figure>`
    有時拿到 class、有時沒有 —— 捲動每步只停 120ms,來不及讓它的 observer 觸發。

    漏掉的後果不只是不決定性:那個元素會**以 `opacity: 0` 的狀態被寫進靜態檔**,
    關掉 JS 的訪客永遠看不到,而那正是 prerender 要解決的事。

    回傳最後仍然隱藏的元素數(0 表示全部顯示了)。有些站本來就有刻意淡出的裝飾
    元素,所以次數用完就放行,不強求歸零。
    """
    remaining = 0
    for _ in range(REVEAL_MAX_TRIES):
        remaining = page.evaluate(_REVEAL_HIDDEN_JS)
        if not remaining:
            break
        page.wait_for_timeout(REVEAL_SETTLE_MS)
    page.evaluate("() => window.scrollTo(0, 0)")
    return remaining


# 跟「已經顯示的同類元素」學該補哪個 class。不寫死任何名稱:比對的是同一個 tag、
# 同一個首要 class 的元素,已顯示的那些比沒顯示的多了什麼。
#
# 對 hover 才顯示的裝飾元素天然免疫 —— 那種元素全部都是隱藏的,找不到「已顯示的
# 同類」,就不會被動到。判準自帶不誤判的性質,不需要額外的例外清單。
_LEARN_REVEAL_JS = """() => {
  const main = document.querySelector('main');
  if (!main) return 0;
  const key = el => el.tagName + '|' + (el.classList[0] || '');
  const shown = new Map(), hidden = [];
  main.querySelectorAll('*').forEach(el => {
    if (!el.textContent || !el.textContent.trim()) return;
    const s = getComputedStyle(el);
    const off = parseFloat(s.opacity) < %s || s.visibility === 'hidden';
    if (off) hidden.push(el);
    else if (!shown.has(key(el))) shown.set(key(el), [...el.classList]);
  });
  let fixed = 0;
  hidden.forEach(el => {
    const peer = shown.get(key(el));
    if (!peer) return;
    const add = peer.filter(c => !el.classList.contains(c));
    if (!add.length) return;
    el.classList.add(...add);
    if (parseFloat(getComputedStyle(el).opacity) >= %s) fixed++;
    else el.classList.remove(...add);   // 沒解決就還原,不要留下沒根據的 class
  });
  return fixed;
}""" % (INVISIBLE_OPACITY, INVISIBLE_OPACITY)


def _learn_reveal(page) -> int:
    """捲不出來的元素,照「已顯示的同類元素」補上缺的 class。

    捲動觸發解不掉的情況是真的存在:實測有站的來源卡片是在捲動掃過**之後**才被
    JS 注入的,於是它們的 observer 永遠等不到下一次捲動事件,`.reveal` 就這樣以
    `opacity: 0` 停在那裡。頁面本身沒壞(真人再捲一次就會顯示),但存進靜態檔
    之後,關掉 JS 的訪客永遠讀不到那段內容。

    補完會驗一次「真的變得看得見了嗎」,沒有就把 class 拿掉 —— 不留下沒根據的改動。
    """
    return page.evaluate(_LEARN_REVEAL_JS)


def _drop_runtime_flags(page) -> None:
    """把頁面 JS 寫上的「這個元素已經處理過」旗標拿掉,再存進靜態檔。

    模板的 count-up 用 `data-done="1"` 標記「動畫跑過了,別再跑」。它是**執行期
    狀態**而不是內容,但擷取 innerHTML 時會連同數字一起被寫進檔案。於是真人載入
    頁面時,JS 看到旗標就跳過重算:擷取到什麼數字,就對所有訪客永遠停在什麼數字。

    實測有一站把動畫中間值 `2`(`data-count="88"`)凍結給所有訪客看 —— 旗標把頁面
    的自我修正能力關掉了。拿掉之後最壞情況是動畫沒被觸發、靜態數字原地保留,
    那仍然是對的值。

    只清 `<main>` 內部:外面的 header/footer 不是這支腳本寫的,不去動它。
    """
    page.evaluate(
        "() => document.querySelectorAll('main [data-done]')"
        ".forEach(el => el.removeAttribute('data-done'))"
    )


def _stable_html(page) -> str:
    """等畫面不再變動,才擷取 <main> 的 innerHTML。

    **不等會寫進錯的內容**:數字 count-up 動畫在跑的時候擷取,抓到的是中間值 ——
    實測有頁面把 `data-count="530"` 的展位數寫成 `52`、`data-count="10000"` 寫成
    `980`。那些數字會原封不動進到靜態 HTML,等於對搜尋引擎謊報數據。

    順帶讓輸出變成決定性的:沒有這一步,同一頁連跑兩次會產生不同結果,
    `--check` 就不能當 CI 關卡用(每次都說「需要更新」)。
    """
    _trigger_lazy(page)
    last = page.eval_on_selector("main", "el => el.innerHTML")
    for _ in range(STABLE_MAX_TRIES):
        page.wait_for_timeout(STABLE_INTERVAL_MS)
        now = page.eval_on_selector("main", "el => el.innerHTML")
        if now == last:
            break
        last = now
    _force_reveal(page)
    _learn_reveal(page)
    _drop_runtime_flags(page)
    return page.eval_on_selector("main", "el => el.innerHTML")


def _rendered_words(browser, url: str) -> int | None:
    """重新載入頁面,回傳使用者實際看到的 <main> 字數。"""
    ctx = browser.new_context()
    try:
        pg = ctx.new_page()
        pg.goto(url, wait_until="domcontentloaded", timeout=RENDER_TIMEOUT_MS)
        pg.wait_for_timeout(800)
        _trigger_lazy(pg)   # 與擷取時同樣捲過一遍,兩邊條件才對稱
        return len(pg.eval_on_selector("main", "el => el.innerText").split())
    except Exception:  # noqa: BLE001 - 量不到就不判定,交給人工比對
        return None
    finally:
        ctx.close()


# 站台存語言偏好的 localStorage key 名稱各不相同:實測有 `lang`、`aw.lang`、
# `codex.lang`、`genai.lang`。寫死其中一個,其餘站的語言就鎖不住 —— 頁面會落回
# app.js 的預設語言(通常是英文),於是**整段中文內容被換成英文寫回檔案**。
#
# 這種損壞是靜默的:`git diff` 看起來只是「內容變得不一樣」,不數中文字元根本看不出來。
#
# 改成攔截讀取端:任何名字裡含 lang 的 key 都回傳指定語言。不必事先知道站台用哪個名字。
def _lang_init(lang: str) -> str:
    return (
        "(() => {"
        "  try { localStorage.setItem('lang', %r); } catch (e) {}"
        "  try {"
        "    const orig = Storage.prototype.getItem;"
        "    Storage.prototype.getItem = function (k) {"
        "      if (/lang/i.test(k)) return %r;"
        "      return orig.call(this, k);"
        "    };"
        "  } catch (e) {}"
        "})()" % (lang, lang)
    )


_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
LANG_CJK_MIN = 0.10        # 原檔中文比例低於此值就不做語言把關(本來就是英文站)
LANG_CJK_TOLERANCE = 0.5   # 渲染後的中文比例掉到原本的一半以下 = 語言被換掉了


def _cjk_ratio(html: str) -> float:
    """去掉標籤後,中文字元佔可見文字的比例。"""
    text = re.sub(r"<[^>]+>", " ", re.sub(r"<(script|style)[^>]*>.*?</\1>", " ",
                                          html, flags=re.S | re.I))
    text = re.sub(r"\s+", "", text)
    return len(_CJK_RE.findall(text)) / len(text) if text else 0.0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", required=True, help="產出的網站資料夾(含 index.html)")
    ap.add_argument("--check", action="store_true",
                    help="只檢查不寫入;有任何頁面需要更新就以 exit 1 結束(CI 用)")
    args = ap.parse_args()

    root = Path(args.dir).resolve()
    if not (root / "index.html").exists():
        print(f"✗ {root} 底下找不到 index.html")
        return 1

    pages = html_files(root)
    if not pages:
        print("✗ 找不到任何 .html")
        return 1

    from playwright.sync_api import sync_playwright

    base_url, server, thread = start_static_server(str(root))
    changed, skipped, failed, duplicated = [], [], [], []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            for path in pages:
                rel = path.relative_to(root).as_posix()
                src = path.read_text(encoding="utf-8")
                parts = split_main(src)
                if parts is None:
                    skipped.append((rel, "沒有剛好一組 <main>…</main>"))
                    continue
                head, _old, tail = parts

                ctx = browser.new_context()
                # 讓渲染出來的語言與 <html lang> 一致,否則中文站會被寫入英文內容
                lang = page_lang(src)
                ctx.add_init_script(_lang_init(lang))
                page = ctx.new_page()
                try:
                    page.goto(f"{base_url}/{rel}", wait_until="domcontentloaded",
                              timeout=RENDER_TIMEOUT_MS)
                    page.wait_for_function(
                        "() => { const m = document.querySelector('main');"
                        " return m && m.innerHTML.trim().length > 0; }",
                        timeout=RENDER_TIMEOUT_MS)
                    rendered = _stable_html(page)
                    landed = urlparse(page.url).path.lstrip("/")
                except Exception as e:  # noqa: BLE001
                    failed.append((rel, str(e).splitlines()[0][:80]))
                    continue
                finally:
                    ctx.close()

                # 頁面把自己轉走了 → 擷取到的是**別頁**的內容,寫回去會把這一頁蓋掉。
                # 實測:一個測驗結果頁在沒有作答紀錄時會導回測驗頁,於是測驗題目
                # 被寫進了結果頁,原本的內容(象限定位、契合度排行)整段消失。
                # 這種頁面的內容本來就依賴使用者狀態,不該有靜態版本。
                if landed and landed != rel:
                    skipped.append((rel, f"頁面自己轉址到 {landed},擷取到的不是這一頁"))
                    continue

                if not rendered or not rendered.strip():
                    skipped.append((rel, "渲染結果是空的"))
                    continue

                # 語言把關:原本是中文的頁面,渲染結果也必須是中文。
                # 不成立表示語言沒鎖成功(多半是站台用了別的 localStorage key),
                # 這時寫回去等於把整段中文換成英文 —— 寧可跳過,也不要靜默覆蓋。
                was, now = _cjk_ratio(_old), _cjk_ratio(rendered)
                if was >= LANG_CJK_MIN and now < was * LANG_CJK_TOLERANCE:
                    skipped.append((rel, f"渲染出來的語言與原檔不符"
                                         f"(中文比例 {was:.0%} → {now:.0%}),沒有寫入"))
                    continue

                new_src = head + "\n" + rendered.strip() + "\n" + tail
                before, after = visible_words(src), visible_words(new_src)
                if new_src == src:
                    continue
                if args.check:
                    changed.append((rel, before, after))
                    continue

                # 寫入前先量一次「使用者實際看到多少字」。**必須和寫入後用同一種
                # 量法**(同樣是全新 context、同樣的等待),否則兩個數字沒有可比性 ——
                # 用當下這個 page 量會得到不同條件下的值,誤判成內容重複。
                words_before = _rendered_words(browser, f"{base_url}/{rel}")

                path.write_text(new_src, encoding="utf-8")

                # ---- 重複內容防護 ----
                # 這支腳本假設頁面的 JS 是「取代」語意(載入時把 <main> 整個蓋掉)。
                # 那個假設**會破**:實際遇過網站用 appendChild 但前面沒有清空容器,
                # 於是靜態內容 + JS 再跑一次 = 內容顯示兩份。
                # 光看程式碼判斷不可靠(要逐檔追每個 appendChild 前面有沒有清空),
                # 所以改用實測:寫入後重新載入,看使用者看到的字數有沒有變多。
                words_after = _rendered_words(browser, f"{base_url}/{rel}")
                if words_after is not None and words_before and \
                        words_after > words_before * DUP_TOLERANCE:
                    path.write_text(src, encoding="utf-8")   # 還原,不留下壞掉的頁面
                    duplicated.append((rel, words_before, words_after))
                    continue

                changed.append((rel, before, after))
            browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=2)

    verb = "需要更新" if args.check else "已寫入"
    print(f"\n{'頁面':<28} {'改前':>6} {'改後':>6}")
    print("─" * 44)
    for rel, b, a in changed:
        print(f"{rel:<28} {b:>6} {a:>6}")
    for rel, why in skipped:
        print(f"{rel:<28} 跳過 — {why}")
    for rel, why in failed:
        print(f"{rel:<28} ✗ 失敗 — {why}")
    for rel, b, a in duplicated:
        print(f"{rel:<28} ✗ 內容重複 — 寫入後可見字數 {b} → {a},已還原")

    print(f"\n{verb}:{len(changed)} 頁,跳過 {len(skipped)} 頁,"
          f"失敗 {len(failed)} 頁,內容重複 {len(duplicated)} 頁")
    if duplicated:
        print("\n→ 內容重複的頁面已還原,沒有留下壞掉的檔案。")
        print("  原因通常是該頁的 JS 用 appendChild 但插入前沒有清空容器,")
        print("  於是靜態內容加上 JS 再跑一次 = 顯示兩份。")
        print("  修法:在對應的 forEach/appendChild 之前補一行 `容器.innerHTML = '';`")
        print("  —— 容器原本就是空的,這行對既有行為是 no-op。補完再跑一次本腳本。")
    if failed or duplicated:
        return 1
    if args.check and changed:
        print("→ 有頁面缺少 prerender 內容,請跑一次不加 --check 的版本")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
