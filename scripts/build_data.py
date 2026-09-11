#!/usr/bin/env python3
"""把 GitHub star 快照 + 繁中摘要組成網站的資料層 data/data.js。

輸入:
  tmp/data/starred-raw.json   gh api user/starred --paginate 的原始輸出(不進版控)
  tmp/data/starred-at.jsonl   每筆的 starred_at 時間(不進版控)
  data/zh-summaries.json      繁中一句話說明,keyed by full_name(進版控,重建成本高)

輸出:
  data/data.js                window.SITE_META + window.SITE_PAGES

用法: uv run python scripts/build_data.py
"""

import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from classify import CATEGORIES, classify  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REPO = "tingwei161803/peter-starred-repos"

# gallery 頁的 chip 是「語言」這個次要軸:主軸(分類)已經是頁面本身,
# 所以頁內再切一刀最有用的是「這是不是我的技術棧」。
LANG_BUCKETS = [
    ("python", "Python", "Python", {"Python", "Jupyter Notebook"}),
    ("ts", "TypeScript / JS", "TypeScript / JS", {"TypeScript", "JavaScript", "Vue", "MDX"}),
    ("other", "Other languages", "其他語言", None),   # 其餘全部
    ("docs", "Docs only", "純文件", {None, "Markdown", "HTML", "TeX"}),
]


def load():
    raw = json.loads((ROOT / "tmp/data/starred-raw.json").read_text())
    # 再擋一次私有 repo。fetch_stars.py 已經在抓取時濾掉了,這裡是第二道 ——
    # tmp/ 的快照不進版控,可能是舊的、也可能是別的方式弄來的。
    # 這個站是公開的,漏一筆就等於替別人公開他們的私有專案。
    raw = [r for r in raw
           if not r.get("private") and r.get("visibility") != "private"]
    starred_at = {}
    for line in (ROOT / "tmp/data/starred-at.jsonl").read_text().splitlines():
        if line.strip():
            rec = json.loads(line)
            starred_at[rec["full_name"]] = rec["starred_at"]
    zh = json.loads((ROOT / "data/zh-summaries.json").read_text())
    return raw, starred_at, zh


def slugify(full_name):
    return re.sub(r"[^a-z0-9]+", "-", full_name.lower()).strip("-")


def lang_bucket(language):
    for key, _en, _zh, langs in LANG_BUCKETS:
        if langs is not None and language in langs:
            return key
    return "other"


def human_stars(n):
    """1455 萬要顯示成 14.6M,不是 14556.8k —— 只做到 k 的話大數字反而更難讀。"""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}".rstrip("0").rstrip(".") + "M"
    if n >= 1000:
        return f"{n / 1000:.1f}".rstrip("0").rstrip(".") + "k"
    return str(n)


def build_items(raw, starred_at, zh):
    items = []
    for r in raw:
        fn = r["full_name"]
        desc = (r.get("description") or "").strip()
        lang = r.get("language")
        items.append({
            "slug": slugify(fn),
            "full_name": fn,
            "owner": r["owner"]["login"],
            "category": classify(r),
            "lang_bucket": lang_bucket(lang),
            "language": lang or "—",
            "stars": r["stargazers_count"],
            "forks": r["forks_count"],
            # 留完整的 topics。[:5] 的截斷原本是為了卡片上不要塞 18 個 tag,
            # 但那是**顯示**的考量 —— 拿來限制搜尋就會出現「frontend 明明是這個
            # repo 的 topic 卻搜不到」(它排第 8 個)。顯示端各自再截。
            "topics": r.get("topics") or [],
            "url": r["html_url"],
            "homepage": (r.get("homepage") or "").strip(),
            "archived": bool(r.get("archived")),
            "pushed": (r.get("pushed_at") or "")[:7],
            "starred": (starred_at.get(fn) or "")[:7],
            # 首頁點陣要依收藏時間排到「同一天內也穩定」,所以留完整 ISO 時間;
            # [:7] 那個只夠做季度分組,同月的 60 幾筆排序會隨 dict 順序漂動。
            "starred_at": starred_at.get(fn) or "",
            # 英文用 GitHub 原始描述(它就是權威原文);沒有描述的就留空,
            # 讓 renderer 自己決定要不要 fallback 到中文摘要。
            "en": desc,
            "zh": zh.get(fn, ""),
        })
    return items


def bilingual(item):
    """一筆 repo 在網站上的 {en, zh} 說明。"""
    en = item["en"] or item["zh"]      # 沒有英文描述時,中文摘要總比空白好
    zh = item["zh"] or item["en"]
    return {"en": en, "zh": zh}


def gallery_page(key, en, zh, items):
    """一個分類 = 一頁 gallery,頁內用語言做次要篩選。"""
    mine = [i for i in items if i["category"] == key]
    mine.sort(key=lambda i: -i["stars"])
    present = {i["lang_bucket"] for i in mine}
    cats = [{"key": k, "en": e, "zh": z}
            for k, e, z, _ in LANG_BUCKETS if k in present]
    return {
        "slug": key,
        "layout": "gallery",
        "icon": {"agents": "smart_toy", "coding": "terminal", "rag": "hub",
                 "llm": "network_node", "media": "graphic_eq",
                 "learning": "school", "apps": "apps"}[key],
        "title": {"en": en, "zh": zh},
        "subtitle": {
            "en": f"{len(mine)} starred repositories in this area.",
            "zh": f"這個主題底下的 {len(mine)} 個 repo。",
        },
        "categories": cats,
        "items": [{
            "slug": i["slug"],
            "category": i["lang_bucket"],
            "title": {"en": i["full_name"], "zh": i["full_name"]},
            "summary": bilingual(i),
            # 語言未知時 GitHub 回 null,我們填了 "—" 當顯示用的佔位符 ——
            # 但佔位符不該變成一個可讀的標籤,那只是雜訊。
            "tags": ([i["language"]] if i["language"] != "—" else [])
                    + i["topics"][:3] + (["archived"] if i["archived"] else []),
            "overview": bilingual(i),
            # 同上:卡片上的 tags 只放 3 個 topic(版面考量),搜尋要吃全部
            "search": " ".join(i["topics"]),
            "url": i["url"],
            "homepage": i["homepage"],
            "meta": {
                "en": f"★ {human_stars(i['stars'])} · {i['language']} · updated {i['pushed']}",
                "zh": f"★ {human_stars(i['stars'])} · {i['language']} · 最後更新 {i['pushed']}",
            },
        } for i in mine],
    }


def overview_page(items):
    total_stars = sum(i["stars"] for i in items)
    alive = sum(1 for i in items if i["pushed"] >= "2026-01")
    langs = len({i["language"] for i in items if i["language"] != "—"})

    cat_counts = Counter(i["category"] for i in items)
    # 帶 key 是為了讓柱子用首頁點陣的同一套分類色(renderer 轉成 .k-<key>)——
    # 兩張圖講的是同一件事(各主題比重),顏色不一致只會讓人以為是兩回事。
    # 依數量排序:CATEGORIES 的原序在這裡看起來是亂的(70、144、66、65…)。
    bars = sorted(
        [{"key": k, "label": {"en": s_en, "zh": s_zh}, "value": cat_counts[k]}
         for k, _en, _zh, s_en, s_zh in CATEGORIES],
        key=lambda b: -b["value"])

    by_q = defaultdict(int)
    for i in items:
        if i["starred"]:
            y, m = i["starred"].split("-")
            by_q[f"{y}Q{(int(m) - 1) // 3 + 1}"] += 1
    quarters = sorted(by_q)
    running = 0
    points = []
    for q in quarters:
        running += by_q[q]
        points.append({"x": q, "y": running})

    top = sorted(items, key=lambda i: -i["stars"])[:15]
    cat_zh = {k: zh for k, _en, zh, _se, _sz in CATEGORIES}
    cat_en = {k: en for k, en, _zh, _se, _sz in CATEGORIES}
    return {
        "slug": "overview",
        "layout": "dashboard",
        "icon": "insights",
        "title": {"en": "By the Numbers", "zh": "數據總覽"},
        "subtitle": {
            "en": f"What {len(items)} stars add up to: language mix, topic weight, "
                  f"and how the collection grew.",
            "zh": f"{len(items)} 顆星加起來長什麼樣:語言組成、主題比重,以及這份收藏怎麼長出來的。",
        },
        "stats": [
            {"key": "repos", "label": {"en": "Repositories", "zh": "收藏的 repo"}, "value": len(items)},
            {"key": "stars", "label": {"en": "Combined stars", "zh": "這些 repo 的總星數"},
             "value": human_stars(total_stars)},
            {"key": "alive", "label": {"en": "Pushed in 2026", "zh": "2026 年仍有更新"}, "value": alive},
            {"key": "langs", "label": {"en": "Languages", "zh": "涵蓋語言數"}, "value": langs},
        ],
        "bars": {"title": {"en": "Repositories per topic", "zh": "各主題的 repo 數量"}, "series": bars},
        "line": {"title": {"en": "Stars accumulated over time", "zh": "star 數的累積軌跡"},
                 "points": points},
        "table": {
            "columns": [
                {"key": "repo", "label": {"en": "Repository", "zh": "Repo"}},
                {"key": "cat", "label": {"en": "Topic", "zh": "主題"}},
                {"key": "stars", "label": {"en": "Stars", "zh": "星數"}},
                {"key": "what", "label": {"en": "What it does", "zh": "在做什麼"}},
            ],
            "rows": [{
                "repo": i["full_name"],
                "cat": {"en": cat_en[i["category"]], "zh": cat_zh[i["category"]]},
                "stars": human_stars(i["stars"]),
                "what": bilingual(i),
            } for i in top],
        },
    }


def all_page(items):
    cat_zh = {k: zh for k, _en, zh, _se, _sz in CATEGORIES}
    cat_en = {k: en for k, en, _zh, _se, _sz in CATEGORIES}
    rows = sorted(items, key=lambda i: -i["stars"])
    return {
        "slug": "all",
        "layout": "table",
        "icon": "table_rows",
        "title": {"en": "Every Repository", "zh": f"全部 {len(rows)} 個"},
        "subtitle": {
            "en": "One sortable, filterable table. Search matches the name, the description and the topics.",
            "zh": "一張可排序、可多軸篩選的總表。搜尋會同時比對名稱、說明與主題。",
        },
        "columns": [
            {"key": "repo", "label": {"en": "Repository", "zh": "Repo"}, "type": "repo"},
            {"key": "what", "label": {"en": "What it does", "zh": "在做什麼"}},
            {"key": "cat", "label": {"en": "Topic", "zh": "主題"}, "filter": True},
            {"key": "lang", "label": {"en": "Language", "zh": "語言"}, "filter": True},
            {"key": "stars", "label": {"en": "Stars", "zh": "星數"}, "type": "num"},
            {"key": "pushed", "label": {"en": "Updated", "zh": "最後更新"}},
        ],
        "rows": [{
            "repo": i["full_name"],
            "url": i["url"],
            "what": bilingual(i),
            "cat": {"en": cat_en[i["category"]], "zh": cat_zh[i["category"]]},
            "lang": i["language"],
            "stars": i["stars"],
            "pushed": i["pushed"],
            # 只給搜尋用的額外文字,畫面上不顯示。少了它就會出現「這個字明明是
            # 這個 repo 的 topic 卻搜不到」—— 「frontend」就是這種情況。
            # 空白串接而不是陣列:同樣的內容省掉引號與逗號,整份小十幾 KB。
            "search": " ".join(i["topics"]),
        } for i in rows],
    }


def timeline_page(items):
    """依季度看這份收藏怎麼長出來的 —— 數量之外,更重要的是主題重心怎麼移動。"""
    cat_zh = {k: zh for k, _en, zh, _se, _sz in CATEGORIES}
    cat_en = {k: en for k, en, _zh, _se, _sz in CATEGORIES}
    by_q = defaultdict(list)
    for i in items:
        if i["starred"]:
            y, m = i["starred"].split("-")
            by_q[f"{y} Q{(int(m) - 1) // 3 + 1}"].append(i)

    events = []
    for q in sorted(by_q, reverse=True):
        group = by_q[q]
        top_cats = Counter(i["category"] for i in group).most_common(2)
        notable = sorted(group, key=lambda i: -i["stars"])[:3]
        names = ", ".join(i["full_name"] for i in notable)
        events.append({
            "date": {"en": q, "zh": q},
            "title": {"en": f"{len(group)} repositories starred",
                      "zh": f"這一季 star 了 {len(group)} 個"},
            "body": {
                "en": f"Mostly {' and '.join(cat_en[c] for c, _ in top_cats)}. "
                      f"Biggest: {names}.",
                "zh": f"重心落在{'、'.join(cat_zh[c] for c, _ in top_cats)}。"
                      f"其中星數最高的是 {names}。",
            },
        })
    return {
        "slug": "timeline",
        "layout": "timeline",
        "icon": "timeline",
        "title": {"en": "How It Grew", "zh": "收藏軌跡"},
        "subtitle": {
            "en": "Quarter by quarter, newest first — the shifting centre of gravity of what caught my eye.",
            "zh": "一季一格、由新到舊 —— 看得出注意力的重心怎麼移動。",
        },
        "events": events,
    }


def home_page(items):
    """首頁 = 可篩選的點陣場(layout "field")。

    一個 repo 一個點、顏色是主題、位置是收藏順序。點某個主題就把不屬於它的點
    淡出去 —— 「篩選」這個動作本身就是主視覺,不用另外畫一張分布圖。
    """
    total_stars = sum(i["stars"] for i in items)
    cat_counts = Counter(i["category"] for i in items)
    cat_meta = {k: (en, zh, s_en, s_zh) for k, en, zh, s_en, s_zh in CATEGORIES}

    # 分類由多到少 —— 這個版面在講「比重」,排序就該是比重本身。
    # (nav 的順序仍是 CATEGORIES 的原序,那是目錄,不是分布圖。)
    keys = sorted(cat_meta, key=lambda k: -cat_counts[k])
    idx = {k: n for n, k in enumerate(keys)}

    def top_of(pool, n=5):
        return [{
            "repo": i["full_name"],
            # 連回站內該分類頁的 #slug(gallery 版型會自己開詳情),不要跳出去 GitHub
            "href": f"{i['category']}.html#{i['slug']}",
            "stars": human_stars(i["stars"]),
            "what": bilingual(i),
        } for i in sorted(pool, key=lambda i: -i["stars"])[:n]]

    cats = []
    for k in keys:
        en, zh, s_en, s_zh = cat_meta[k]
        mine = [i for i in items if i["category"] == k]
        cats.append({
            "key": k,
            "name": {"en": en, "zh": zh},
            "short": {"en": s_en, "zh": s_zh},
            "n": len(mine),
            "href": f"{k}.html",
            "top": top_of(mine),
        })

    # 點陣順序:依收藏時間(最早在左上角)。CSS Grid 預設 row-major 填格,
    # 所以「閱讀順序 = 時間順序」是天然成立的,篩一類就看得出它集中在哪一段時間。
    #
    # 為什麼順序要在這裡算完、寫成資料:prerender.py 會把 JS 渲染的結果寫回靜態
    # HTML。若順序在瀏覽器端隨機產生,靜態檔存的排列跟重畫出來的不一樣 ——
    # 載入時會閃一下重排,而且每次重跑 prerender 都是一份無意義的 diff。
    #
    # 存成一條字串(每個字元是 cats 的索引)而不是一個等長的 JSON 陣列:
    # 一個 repo 一個位元組,比 JSON 陣列小四倍,而且只有首頁載這份資料。
    chron = sorted(items, key=lambda i: (not i["starred_at"], i["starred_at"], i["full_name"]))
    order = "".join(str(idx[i["category"]]) for i in chron)
    dated = [i["starred_at"][:7] for i in chron if i["starred_at"]]

    return {
        "slug": "home",
        "layout": "field",
        "icon": "home",
        # title / subtitle 是「目錄用」的名字:nav pill 與 document.title 吃這個。
        # 版面上那句大標另外放在 field.headline —— 它要配合點陣解釋自己,
        # 跟目錄標籤不是同一個東西。
        "title": {"en": "Peter's Starred Repositories",
                  "zh": "Peter 的 GitHub 收藏"},
        # nav pill 用的短名:完整站名已經在左上角的 brand 上了,不要再重複一次
        "navTitle": {"en": "Home", "zh": "首頁"},
        "subtitle": {
            "en": "A browsable, searchable index of every public repository I have starred — "
                  "sorted into seven themes, each with a plain-language note on what it actually does.",
            "zh": "把我 star 過的公開 repo 全部整理成可瀏覽、可搜尋的索引 —— "
                  "分成七個主題,每一個都附一句白話說明它到底在做什麼。",
        },
        "stats": [
            {"value": len(items), "label": {"en": "Repositories", "zh": "個 repo"}},
            {"value": 7, "label": {"en": "Themes", "zh": "個主題"}},
            {"value": human_stars(total_stars), "label": {"en": "Combined stars", "zh": "總星數"}},
            {"value": len({i["language"] for i in items if i["language"] != "—"}),
             "label": {"en": "Languages", "zh": "種語言"}},
        ],
        "field": {
            # *…* 之間的字會被 renderer 包成強調色 —— 只有一處,不是 markdown。
            "headline": {
                "en": f"The *{len(items)} repositories* I starred, in seven themes",
                "zh": f"我 star 過的 *{len(items)} 個 repo*,分成七類",
            },
            "lede": {
                "en": "Starring is easy; finding things again is not. Pick a theme to light up "
                      "its share of the dots below — they run in the order I starred them, "
                      "oldest at the top left.",
                "zh": "星星按下去容易,回頭找很難。點一個主題,就把那一類從底下的點陣裡挑亮出來 —— "
                      "點的順序就是我收藏的順序,最早的在左上角。",
            },
            "all": {"en": "All themes", "zh": "全部主題"},
            "allShort": {"en": "All", "zh": "全部"},
            "more": {"en": "See all {n} →", "zh": "看這一類的全部 {n} 個 →"},
            "moreAll": {"en": "Open the full table of {n} →", "zh": "打開全部 {n} 個的總表 →"},
            "allHref": "all.html",
            "axis": {"from": dated[0] if dated else "", "to": dated[-1] if dated else ""},
            # 點陣對讀屏來說是一張統計圖,不是幾百個可讀項目 —— 給整塊一句摘要,
            # 不要讓它逐點念過去。{n} / {name} 由 renderer 代入。
            "fieldAlt": {
                "en": "A grid of {n} squares, one per starred repository, coloured by theme "
                      "and ordered by when I starred it.",
                "zh": "{n} 個小方塊,一個代表一個收藏的 repo,顏色是主題、順序是收藏時間。",
            },
            "fieldAltOne": {
                "en": "The same grid of squares with only the {n} in {name} highlighted.",
                "zh": "同一張點陣,只把屬於「{name}」的 {n} 個點亮起來。",
            },
            "cats": cats,
            "order": order,
            "top": top_of(items),
        },
    }


def main():
    raw, starred_at, zh = load()
    items = build_items(raw, starred_at, zh)

    pages = [home_page(items), overview_page(items)]
    pages += [gallery_page(k, en, z, items) for k, en, z, _se, _sz in CATEGORIES]
    pages += [all_page(items), timeline_page(items)]

    meta = {
        "repo": REPO,
        "title": {"en": "Peter's Starred Repos", "zh": "Peter 的 GitHub 收藏"},
        "subtitle": {"en": "What I starred, and what it actually does",
                     "zh": "我 star 了什麼,以及它們到底在做什麼"},
    }

    # ---- 資料拆檔 ----------------------------------------------------------
    # 全部塞進一個 data.js 是 842 KB(gzip 177 KB),而且每一頁都要載入 —— 這正好
    # 抵銷掉「多頁面」原本要換來的載入速度。所以拆成:
    #   data/data.js         每頁都載:META + 各頁的輕量欄位(nav 需要 slug/icon/title)
    #   data/page-<slug>.js  只有該頁自己載:items / rows / events 這些重的
    LIGHT = {"slug", "layout", "icon", "title", "navTitle", "subtitle", "stats", "categories"}
    HEAVY = {"items", "rows", "events", "columns", "bars", "line", "table", "field"}

    light_pages = []
    for pg in pages:
        light_pages.append({k: v for k, v in pg.items() if k in LIGHT})
        # 就算某頁沒有重欄位也要寫一個空檔 ——
        # 每個 .html 都無條件載入 data/page-<slug>.js,少一個就是一發 404。
        heavy = {k: v for k, v in pg.items() if k in HEAVY}
        (ROOT / f"data/page-{pg['slug']}.js").write_text(
            "window.SITE_PAGE_DATA = "
            + json.dumps(heavy, ensure_ascii=False, indent=1) + ";\n")

    header = (
        "/* 由 scripts/build_data.py 產生 —— 不要手改。\n"
        f"   快照時間:{datetime.now(timezone.utc):%Y-%m-%d}(UTC)\n"
        f"   資料來源:GitHub REST API /user/starred,共 {len(items)} 筆。\n"
        "   每頁的重資料在 data/page-<slug>.js,由該頁自己載入。\n"
        "   要更新:重跑 scripts/fetch_stars.py 再跑本檔。 */\n"
    )
    out = ROOT / "data/data.js"
    out.write_text(
        header
        + "window.SITE_META = " + json.dumps(meta, ensure_ascii=False, indent=2) + ";\n\n"
        + "window.SITE_PAGES = " + json.dumps(light_pages, ensure_ascii=False, indent=1) + ";\n"
    )
    print(f"data/data.js — {out.stat().st_size / 1024:.0f} KB(每頁都載)")
    for pg in pages:
        f = ROOT / f"data/page-{pg['slug']}.js"
        n = len(pg.get("items") or pg.get("rows") or pg.get("events") or [])
        size = f"{f.stat().st_size / 1024:>5.0f} KB" if f.exists() else "     —"
        print(f"  {pg['slug']:10s} {pg['layout']:10s} {n or '':>4}  {size}")


if __name__ == "__main__":
    main()
