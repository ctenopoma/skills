# SVG 直書きパターン集

単体ファイルや html-craft への埋め込み用。外部ライブラリなしで、
どこでも開ける図になる。ブラウザで開いて確認してから渡すこと。

## 基本骨格

```svg
<svg viewBox="0 0 720 240" xmlns="http://www.w3.org/2000/svg"
     font-family="'Hiragino Sans','Yu Gothic UI',Meiryo,sans-serif" font-size="14">
  <!-- 矢印の先端(1回定義すればどの線でも使える) -->
  <defs>
    <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5"
            markerWidth="7" markerHeight="7" orient="auto-start-reverse">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="#475569"/>
    </marker>
  </defs>

  <!-- 箱: rect + text のペア。中央揃えは text-anchor="middle" -->
  <rect x="20" y="80" width="160" height="60" rx="10"
        fill="#eef2ff" stroke="#4f46e5" stroke-width="2"/>
  <text x="100" y="115" text-anchor="middle">データ受領</text>

  <rect x="280" y="80" width="160" height="60" rx="10"
        fill="#eef2ff" stroke="#4f46e5" stroke-width="2"/>
  <text x="360" y="115" text-anchor="middle">品質チェック</text>

  <!-- 矢印: line + marker-end -->
  <line x1="180" y1="110" x2="278" y2="110"
        stroke="#475569" stroke-width="2" marker-end="url(#arrow)"/>
  <!-- 矢印のラベル -->
  <text x="229" y="100" text-anchor="middle" font-size="12" fill="#475569">profile</text>
</svg>
```

## レイアウトのコツ

- **先に座標設計**: 箱の幅・高さ・間隔を決め打ちしてから並べる
  (例: 幅160・高さ60・横間隔100)。等間隔なら計算で座標が出る
- viewBox は内容に合わせて最後に調整。余白は上下左右 20 程度
- 2行ラベルは `<text>` を2つ重ねる(`y` を 16px ずらす)か `<tspan>` を使う
- 色は html-craft と同じトークンに揃えると成果物に馴染む:
  枠 `#4f46e5` / 塗り `#eef2ff` / 線・補足 `#475569` / 強調枠 `#0d9488`+塗り `#f0fdfa`

## html-craft への埋め込み

生成した `<svg>...</svg>` をそのまま HTML に貼るだけ(画像化・base64 不要)。
幅は親要素に合わせるため `<svg>` に `width="100%"` を付けるか、
`.chart` コンテナ(page.css)に入れる。
