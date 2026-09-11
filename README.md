# Peter's Starred Repos

星星按下去容易,回頭找很難。累積到 553 個之後,GitHub 內建的 star 清單只剩下「按時間倒序的一長串連結」,
既看不出主題分布,也想不起來當初為什麼收藏。這個站把那份清單重新整理過:依主題分成七類、
補上繁體中文的一句話說明、標出星數與最後更新時間。

資料來自 GitHub REST API 的 `/user/starred`,是某個時間點的快照(見 `data/data.js` 開頭的日期)。
**只收公開 repo** —— 這個 API 用登入的 token 呼叫時會連私有 repo 一起回傳,抓取腳本會把它們濾掉。

## 線上版

英文版 <https://stars.peteraim.com/>
中文版 <https://stars.peteraim.com/zh-Hant/>

> 每個 repo 都有專屬 `#<slug>`,可直接分享單一項目,
> 例如 <https://stars.peteraim.com/agents.html#browser-use-browser-use>。

## 這個站能做什麼

- **首頁是一張 553 個點的場**:一個點一個 repo,顏色是主題、位置是我收藏它的順序。
  點一個主題就把那一類挑亮出來 —— 順便看得出那個主題是哪一段時間開始熱的。
- **七個主題分頁**,各自一個獨立網址:Agent 與自動化、編碼助手與開發工具、RAG 與知識庫、
  模型訓練與推論、語音影像與多模態、學習資源與精選清單、應用與其他。
- **553 筆都有繁體中文說明**,逐筆讀過描述後重寫。
- **搜尋同時比對** repo 名稱、說明與 topics;總表可以用主題和語言兩個條件一起篩。
- **數據總覽**看語言組成、各主題數量、star 累積;**收藏軌跡**一季一格,看得出重心怎麼移動。
- **中英文各一個網址**,root 是英文、`/zh-Hant/` 是中文,右上角切換。

## 網站結構

```
peter-starred-repos/
├── index.html            # 首頁:553 個點的可篩選場
├── overview.html         # 數據總覽(圖表)
├── agents / coding / rag / llm / media / learning / apps .html   # 七個主題分頁
├── all.html              # 全部 553 筆的可排序總表
├── timeline.html         # 一季一格的收藏軌跡
├── zh-Hant/              # 中文雙生頁(由 build_i18n.py 產生,不要手改)
├── assets/
│   ├── shell.js          # 共用 chrome:app bar、跨頁導覽、footer、dialog
│   ├── app.js            # 版型引擎:依 body[data-page] 選 renderer
│   └── styles.css        # 色票(深淺兩套)+ chrome + 各版型樣式
├── data/
│   ├── data.js           # 每頁都載:站台 meta + 各頁輕量欄位
│   ├── page-<slug>.js    # 該頁自己載:items / rows / events
│   └── zh-summaries.json # 553 筆繁體中文說明(這份沒有腳本能重建)
└── scripts/
    ├── fetch_stars.py    # 重抓 star 快照到 tmp/
    ├── classify.py       # 主題分類
    └── build_data.py     # 產生 data/*.js
```

> 本站是個人整理,repo 的描述、星數、最後更新時間都是抓取當下的狀態,之後會變。繁體中文說明是我讀過描述後重寫的理解,不是官方文案。要看準確資訊請以各 repo 的 GitHub 頁面為準。

## 本機跑起來

```bash
# 1. clone 專案
git clone git@github.com:tingwei161803/peter-starred-repos.git
cd peter-starred-repos

# 2. 啟動本機伺服器(直接開 index.html 的話,跨頁導覽會壞掉)
uv run python -m http.server 4173
# 然後瀏覽 http://localhost:4173
```

> 純靜態網站,不需安裝任何依賴。若要跑本機伺服器或重建資料,一律使用 `uv`。

### 更新資料

要更新:

```bash
# 1. 重抓 star 清單(會提示哪些新 repo 還沒有繁中說明)
uv run python scripts/fetch_stars.py

# 2. 補上新 repo 的繁中說明到 data/zh-summaries.json,然後重建資料層
uv run python scripts/build_data.py

# 3. 重跑產生物管線,順序不能亂:
#    i18n 先(後面兩步要處理兩個語言的檔案)、seo 動 <head>、prerender 動 <main>
uv run python scripts/build_i18n.py --dir .
uv run python scripts/build_seo.py  --dir .
uv run --with pillow python scripts/build_og.py --dir .
uv run --with playwright python scripts/prerender.py --dir .

# 4. 驗收
uv run --with playwright python scripts/verify.py --dir .
```

`prerender.py` 一定要跑。網站的 `<main>` 原本是空的、內容靠 JS 注入,
而 Google 的第一波索引只抓原始 HTML,第二波(執行 JS)不保證會發生。
不 prerender 的話,搜尋引擎看到的是一片空白。

## 聲明與授權

- 本站為非官方整理,各 repo 的名稱、描述與著作權歸原作者所有。
- 本站自身的程式碼以 MIT 授權釋出。
- 若你是某個 repo 的作者、希望調整或移除這裡的描述,請開 issue 告訴我。
