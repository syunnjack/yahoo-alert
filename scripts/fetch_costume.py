# -*- coding: utf-8 -*-
"""Yahoo!ショッピングから、コスプレ衣装の相場データを取る。

## なぜ yahoo-alert に置くか

**`YAHOO_APP_ID` がこのリポジトリの Secrets にしか無い。** 鍵を移さずに使うため、
取得だけをここで走らせて、結果の JSON をコミットする。
使う側（halloween-event.com）は、その JSON を読むだけにする。

## 何を出すか

**商品1件ずつのページは作らない。** 作ると数千ページになり、
darekore.jp と同じ「発見済み-未登録」になる。出すのは**衣装の種類ごとの数字**。

    件数 / 価格の最小・中央・最大 / 価格帯ごとの件数 / 店舗数

取得は売れ筋順（`sort=-sold`）。**これは Yahoo の公式ランキングではない**ので、
「公式ランキング◯位」とは書かない（景表法の優良誤認）。
**順位は出さず、価格の分布だけ**を出す。

安い順（`+price`）にすると、最安50件が小物やパーツになり、相場が実態とずれる
（2026-09-25 に実測。メイド服の最安が15円になった）。

## 上限

appid は1日に上限がある（35分・2,000件強で429）。
1種類につき1リクエスト、**15種類で15リクエスト**に抑える。

使い方: YAHOO_APP_ID=... python scripts/fetch_costume.py
"""
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timezone, timedelta

ENDPOINT = 'https://shopping.yahooapis.jp/ShoppingWebService/V3/itemSearch'
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   'data', 'costume.json')
PAUSE = 1.5
RESULTS = 50          # 1リクエストで取れる上限
JST = timezone(timedelta(hours=9))

# halloween-event.com に既にある衣装の記事と対応させる。
# **記事が無い種類は足さない。** 受け皿の無いデータを作らない。
TYPES = [
    ('majo-cosplay',     '魔女',         'ハロウィン 魔女 コスプレ 衣装'),
    ('vampire-cosplay',  'ヴァンパイア',  'ハロウィン ヴァンパイア コスプレ 衣装'),
    ('zombie-cosplay',   'ゾンビ',       'ハロウィン ゾンビ コスプレ 衣装'),
    ('nurse-cosplay',    'ナース',       'ナース コスプレ 衣装'),
    ('police-cosplay',   'ポリス',       'ポリス コスプレ 衣装'),
    ('china-cosplay',    'チャイナ',      'チャイナ服 コスプレ 衣装'),
    ('maid-cosplay',     'メイド',       'メイド服 コスプレ 衣装'),
    ('gothloli-cosplay', 'ゴスロリ',      'ゴシックロリータ 衣装'),
    ('tenshi-cosplay',   '天使',         '天使 コスプレ 衣装'),
    ('akuma-cosplay',    '悪魔',         '悪魔 コスプレ 衣装'),
    ('shinigami-cosplay', '死神',        '死神 コスプレ 衣装'),
    ('pierrot-cosplay',  'ピエロ',       'ピエロ コスプレ 衣装'),
    ('miira-cosplay',    'ミイラ',       'ミイラ コスプレ 衣装'),
    ('kuroneko-cosplay', '黒猫',         '黒猫 コスプレ 衣装'),
    ('pumpkin-cosplay',  'かぼちゃ',      'かぼちゃ コスプレ 衣装'),
]


def fetch(url, tries=4, wait=5.0):
    for attempt in range(tries):
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode('utf-8'))
        except urllib.error.HTTPError as error:
            # 429 は上限。待っても復帰しないことがあるので、そこで止める。
            if error.code == 429:
                print('  429（1日の上限）。ここで止めます。', file=sys.stderr)
                return None
            if attempt == tries - 1:
                print(f'  HTTP {error.code}', file=sys.stderr)
                return None
        except Exception as error:
            if attempt == tries - 1:
                print(f'  {error}', file=sys.stderr)
                return None
        time.sleep(wait)
    return None


# **この語が題名に入っている商品は、集計から外す。**
# 売れ筋順で取ると「童貞を殺すセーター」「ランジェリー・下着」のような
# 成人向け寄りの商品が混ざる（2026-09-25 に実測）。
# halloween-event.com は一般向けのサイトで、アダルト判定されると
# 楽天・バリューコマース経由 Yahoo! のアフィリエイトが使えなくなる。
# **相場を出す目的から見ても、これらは衣装の値段ではない。**
NG_WORDS = [
    '童貞', 'ランジェリー', '下着', 'セクシー', 'エロ', 'アダルト', '過激',
    '透け', 'ベビードール', 'Tバック', 'ガーター', '勝負下着', '夜用',
    'ボンテージ', 'ボンデージ', '大人のおもちゃ', '18禁', 'R18',
]

# 衣装ではなく小物だけが並ぶ種類を落とすための語。
# **カチューシャや付け爪は「衣装の相場」ではない。**
ACCESSORY_ONLY = ['カチューシャ', '付け爪', 'ネイル', 'ピアス', 'イヤリング',
                  'ウィッグ', 'カラコン', 'マスクのみ']

# 総件数がこれ未満の種類は、相場として出さない。
MIN_TOTAL = 300


def is_ng(name):
    return any(w in name for w in NG_WORDS)


def is_accessory(name):
    return any(w in name for w in ACCESSORY_ONLY) and '衣装' not in name


def band(price):
    for limit, label in [(1000, '〜999円'), (2000, '1,000〜1,999円'),
                         (3000, '2,000〜2,999円'), (5000, '3,000〜4,999円'),
                         (10000, '5,000〜9,999円')]:
        if price < limit:
            return label
    return '10,000円〜'


def main():
    app_id = os.environ.get('YAHOO_APP_ID', '').strip()
    if not app_id:
        print('YAHOO_APP_ID が要ります。', file=sys.stderr)
        return 1

    out = {'updated': date.today().isoformat(), 'source': 'Yahoo!ショッピング 商品検索API v3',
           'types': []}

    for slug, name, keyword in TYPES:
        query = urllib.parse.urlencode({
            'appid': app_id, 'query': keyword, 'results': RESULTS,
            # **`+price`（安い順）は使わない。** 最安50件は衣装ではなく
            # 小物やパーツが並び、相場が実態とかけ離れる（メイド15円など）。
            # 売れ筋順にすると、実際に衣装として買われているものが取れる。
            'in_stock': 'true', 'sort': '-sold',
        })
        payload = fetch(f'{ENDPOINT}?{query}')
        time.sleep(PAUSE)

        # **応答が無いときは、その種類を飛ばす。** 0件として書かない。
        if not payload:
            print(f'{name}: 応答なし。飛ばします。', file=sys.stderr)
            continue

        hits = payload.get('hits') or []
        total = int(payload.get('totalResultsAvailable') or 0)

        # **総件数が少ない種類は相場にならない。** ミイラは52件しか無く、
        # 中身もカチューシャばかりだった。
        if total < MIN_TOTAL:
            print(f'{name}: 総件数 {total}件。少ないので出しません。', file=sys.stderr)
            continue

        kept, dropped = [], 0
        for h in hits:
            nm = h.get('name') or ''
            if not h.get('price'):
                continue
            if is_ng(nm) or is_accessory(nm):
                dropped += 1
                continue
            kept.append(h)

        prices = sorted(int(h['price']) for h in kept)
        # 半分以上落ちる種類は、そもそも衣装の棚ではない。
        if len(prices) < 20:
            print(f'{name}: 除外後 {len(prices)}件。少ないので出しません。', file=sys.stderr)
            continue
        if dropped:
            print(f'{name}: {dropped}件を除外しました。', file=sys.stderr)

        # 中央値にいちばん近い3件を選ぶ
        mid = statistics.median(prices)
        picks = [{
            'name': h.get('name', '')[:70],
            'price': int(h['price']),
            'url': h.get('url', ''),
            'store': (h.get('seller') or {}).get('name', '')[:30],
        } for h in sorted(kept, key=lambda x: abs(int(x['price']) - mid))[:3]]

        bands = {}
        for p in prices:
            bands[band(p)] = bands.get(band(p), 0) + 1
        stores = {(h.get('seller') or {}).get('name') for h in kept if h.get('seller')}

        out['types'].append({
            'slug': slug, 'name': name, 'keyword': keyword,
            'total': total,
            'sampled': len(prices),
            'dropped': dropped,
            # **中身を必ず残す。** 数字だけ見ていると、衣装でないものが
            # 混ざっていても気づけない。公開前に目で確かめるための控え。
            'examples': [h.get('name', '')[:60] for h in kept[:5]],
            # **中央値に近い3件を代表として残す。** 最安や最高は外れ値で、
            # 「その種類の普通の衣装」を示さない。リンクの単位は商品ページ。
            'picks': picks,
            'min': prices[0], 'median': int(statistics.median(prices)), 'max': prices[-1],
            'bands': bands,
            'stores': len([s for s in stores if s]),
        })
        print(f'{name}: {len(prices)}件 {prices[0]}〜{prices[-1]}円', file=sys.stderr)

    if not out['types']:
        print('1件も取れませんでした。ファイルを書き換えません。', file=sys.stderr)
        return 1

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f'{len(out["types"])} 種類を書き出しました。', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
