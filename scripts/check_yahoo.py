"""Yahoo!ショッピングの売れ筋上位を見て、自店の出入りを知らせる。

## 「公式ランキング」とは名乗らない

**Yahoo のランキングAPI（V1 ranking）は 500 を返す。**
カテゴリを指定してもしなくても同じ（2026-09-04 実測）。動いていない。

代わりに商品検索APIの `sort=-sold`（売れ筋順）を使う。これは実質の順位だが、
**Yahooが公式に発表しているランキングではない。**
「売れ筋順の◯位」と書き、「公式ランキング1位」とは絶対に書かない。
そこを混ぜると、店が景表法の優良誤認を問われる。

## 何を知らせるか

  入った   自店の商品が上位に現れた
  上がった / 下がった
  抜けた   上位から消えた。**これがいちばん急ぐ**

**抜けたものを先に並べる。** 良い知らせから並べると悪い知らせが埋もれる。

## 上限に注意

appid には1日の上限がある。**35分ほど（2,000件強）で 429 が返るようになる**
（2026-09-04・別のツールで実測）。キーワードを絞って使う。

## 使い方

    python scripts/check_yahoo.py

    config/watch.json  見るキーワードと自店の seller id
    data/history.json  日ごとの順位

環境変数:
  YAHOO_APP_ID  Yahoo! デベロッパーネットワークの Client ID
  TOP_N         上位何件を見るか（既定 30・最大100）
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / 'config' / 'watch.json'
HISTORY = ROOT / 'data' / 'history.json'

ENDPOINT = 'https://shopping.yahooapis.jp/ShoppingWebService/V3/itemSearch'
PAUSE = 1.2


def load(path, fallback):
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return fallback


def fetch(url, tries=4, wait=5.0):
    for attempt in range(tries):
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode('utf-8', 'replace'))
        except urllib.error.HTTPError as error:
            body = ''
            try:
                body = error.read().decode('utf-8', 'replace')[:120]
            except Exception:
                pass
            if error.code not in (429, 500, 502, 503):
                print(f'    {error.code}: {body}', file=sys.stderr)
                return {}
            time.sleep(wait * (attempt + 1))
        except Exception as error:
            if attempt == tries - 1:
                print(f'    あきらめます: {error}', file=sys.stderr)
                return {}
            time.sleep(wait * (attempt + 1))
    return {}


def main():
    app_id = os.environ.get('YAHOO_APP_ID', '').strip()
    if not app_id:
        print('YAHOO_APP_ID が要ります。', file=sys.stderr)
        return 1

    config = load(CONFIG, None)
    if not config:
        print(f'{CONFIG} がありません。', file=sys.stderr)
        return 1

    top_n = min(int(os.environ.get('TOP_N') or 30), 100)
    today = date.today().isoformat()

    history = load(HISTORY, {'rows': []})
    rows = history.get('rows') or []
    before = {(r['keyword'], r['seller']): r for r in rows if r.get('date') == max(
        (x['date'] for x in rows), default='')} if rows else {}

    changes = []

    for site in config.get('sites', []):
        seller = site['seller']
        name = site.get('name') or seller
        print(f'{name}（{seller}）', file=sys.stderr)

        for keyword in site.get('keywords', []):
            query = urllib.parse.urlencode({
                'appid': app_id, 'query': keyword,
                'sort': '-sold', 'results': top_n, 'in_stock': 'true',
            })
            payload = fetch(f'{ENDPOINT}?{query}')
            time.sleep(PAUSE)

            # **応答が無いときは記録しない。** 0件と混同すると
            # 「抜けた」と誤って知らせてしまう。
            if not payload:
                print(f'  {keyword}: 応答なし。記録を飛ばします。', file=sys.stderr)
                continue

            hits = payload.get('hits') or []
            rank, item = None, None
            for index, hit in enumerate(hits, 1):
                if str((hit.get('seller') or {}).get('sellerId') or '') == seller:
                    rank, item = index, hit
                    break

            row = {
                'date': today, 'site': name, 'seller': seller, 'keyword': keyword,
                'rank': rank, 'checked': len(hits),
                'item': (item or {}).get('name', '')[:100] if item else '',
                'url': (item or {}).get('url', '') if item else '',
            }
            rows.append(row)

            past = before.get((keyword, seller))
            if past:
                if past.get('rank') and not rank:
                    changes.append(('抜けた', keyword, past['rank'], None))
                elif rank and not past.get('rank'):
                    changes.append(('入った', keyword, None, rank))
                elif rank and past.get('rank') and rank != past['rank']:
                    changes.append(('動いた', keyword, past['rank'], rank))

            label = f'売れ筋 {rank}位' if rank else f'上位{len(hits)}件に無し'
            print(f'  {keyword} → {label}', file=sys.stderr)

    # **抜けたものを先に。** 悪い知らせのほうが急ぐ。
    order = {'抜けた': 0, '動いた': 1, '入った': 2}
    changes.sort(key=lambda c: order.get(c[0], 9))

    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    HISTORY.write_text(json.dumps({'confirmedOn': today, 'rows': rows}, ensure_ascii=False),
                       encoding='utf-8')

    print(f'\n変化 {len(changes)}件 / 記録 {len(rows):,}行', file=sys.stderr)
    for kind, keyword, was, now in changes[:10]:
        print(f'  {kind}  {keyword}  {was or "—"} → {now or "圏外"}', file=sys.stderr)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
