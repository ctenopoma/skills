# dashboard モード: データダッシュボードの設計

雛形: `python scripts/new_page.py dashboard 出力.html --title "..."`
(assets/page.css がインライン展開される)

「Excel でグラフを作ってスクショ共有」の置き換え。**データ埋め込みの自己完結HTML 1枚**で、
受け取った人がフィルタ・並べ替えをしながら見られるようにする。

## 構成の型(上から)

1. **ヘッダ**: タイトル+データ更新日+出典(`.page-header`)
2. **KPI行**: いちばん知りたい数字を3〜4個(`.grid.cols-4` > `.card.kpi`)
3. **コントロール**: フィルタ・期間切替(`.controls`)
4. **グラフ+明細**: `.grid.cols-2` にグラフカードと表カード

## データの持ち方

```html
<script>
const DATA = [
  {"period": "2026-01", "series": "A", "value": 12.3},
  ...
];
</script>
```

- 整形済みの「1行=1レコード」の配列にして埋め込む。集計はJS側でやる
- 数百〜数万行なら埋め込みで問題ない。数十MB級は要約してから埋める
- 更新は「DATA を差し替えて再配布」。生成スクリプト(CSV→この形式)を
  ユーザーの手元に残すと運用が楽

## グラフは SVG を手描きする

外部チャートライブラリは使わない(自己完結の鉄則)。棒・折れ線・散布図程度なら
SVG の直書きで十分きれいに作れる。型:

```js
function drawBars(el, rows) {
  const w = 560, h = 280, pad = {t: 16, r: 12, b: 36, l: 48};
  const max = Math.max(...rows.map(r => r.value));
  const bw = (w - pad.l - pad.r) / rows.length;
  const y = v => pad.t + (h - pad.t - pad.b) * (1 - v / max);
  el.innerHTML = `<svg viewBox="0 0 ${w} ${h}">` +
    rows.map((r, i) =>
      `<rect x="${pad.l + i * bw + 2}" y="${y(r.value)}"
             width="${bw - 4}" height="${h - pad.b - y(r.value)}"
             fill="var(--accent)"/>` +
      `<text x="${pad.l + i * bw + bw / 2}" y="${h - pad.b + 16}"
             text-anchor="middle" font-size="11">${r.label}</text>`
    ).join("") + `</svg>`;
}
```

- 軸ラベル・目盛りは `<text>`。フォントサイズ11〜12px
- 色は CSS 変数(`var(--accent)` 等)で。系列が増えたら teal / amber を足す
- ホバーで値を出したいときは `<title>` 要素を図形の子に入れる(ツールチップになる)

## 表

- `th.sortable` + クリックでソートを付けるなら、`render()` を「状態→全描き直し」
  の一方向にする(部分更新は事故のもと)
- 数値列は `td.num`(右寄せ・等幅数字)

## 状態管理の型

フィルタ状態はグローバル1オブジェクトに集約し、変更イベントで `render()` を呼ぶだけにする:

```js
const state = { period: "all", sortKey: "value", sortDesc: true };
function render() { /* state と DATA から全カードを描き直す */ }
```
