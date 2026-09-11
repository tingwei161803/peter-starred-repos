#!/usr/bin/env python3
"""重抓 GitHub star 快照到 tmp/data/。

需要已登入的 gh CLI(`gh auth status` 要是綠的)。輸出的兩個檔案都在 tmp/,
不進版控 —— 它們是可重新取得的快照,真正需要保存的是人工寫的 data/zh-summaries.json。

用法: uv run python scripts/fetch_stars.py
之後: uv run python scripts/build_data.py
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tmp/data"


def gh(args):
    r = subprocess.run(["gh"] + args, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"gh 失敗:{' '.join(args)}\n{r.stderr.strip()}")
    return r.stdout


def main():
    OUT.mkdir(parents=True, exist_ok=True)

    raw = json.loads(gh(["api", "user/starred", "--paginate"]))

    # 丟掉私有 repo。`user/starred` 是用已登入的 token 呼叫的,所以它會**連私有
    # repo 一起回傳** —— 包含別人的私有 repo(我們有讀取權就看得到)。這個站是
    # 公開的,把它們放上去等於替別人公開他們的私有專案名稱、描述與 owner。
    # 在這裡就丟掉,tmp/ 的快照也不會留著這些資料。
    public = [r for r in raw
              if not r.get("private") and r.get("visibility") != "private"]
    dropped = len(raw) - len(public)
    repos = json.dumps(public, ensure_ascii=False)
    (OUT / "starred-raw.json").write_text(repos)
    n = len(public)

    # starred_at 只在 star+json 這個 media type 下才回傳,所以要分開抓一次。
    stars = gh(["api", "user/starred", "--paginate",
                "-H", "Accept: application/vnd.github.star+json",
                "--jq", '.[] | {starred_at, full_name: .repo.full_name}'])
    (OUT / "starred-at.jsonl").write_text(stars)

    print(f"抓到 {n} 筆公開 repo → tmp/data/"
          + (f"(另有 {dropped} 筆私有 repo 已排除,不會上站)" if dropped else ""))

    # 提醒哪些新 repo 還沒有中文摘要 —— 沒補的話網站上中文版會 fallback 到英文。
    zh_file = ROOT / "data/zh-summaries.json"
    if zh_file.exists():
        zh = json.loads(zh_file.read_text())
        missing = [r["full_name"] for r in json.loads(repos) if r["full_name"] not in zh]
        if missing:
            print(f"\n⚠ 有 {len(missing)} 筆還沒有繁中摘要,請補進 data/zh-summaries.json:")
            for name in missing[:20]:
                print(f"    {name}")
            if len(missing) > 20:
                print(f"    …還有 {len(missing) - 20} 筆")
        else:
            print("繁中摘要覆蓋率 100%")


if __name__ == "__main__":
    main()
