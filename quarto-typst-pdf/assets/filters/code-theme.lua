-- コードブロックを Typst 側で色分けさせるためのフィルタ。
--
-- 既定では Pandoc の Skylighting がコードを色付き span 列に変換してしまい、
-- Typst の `raw(theme: ...)` は完全に無視される。`syntax-highlighting: none` で
-- Skylighting は止められるが、それだけだと言語タグ(```python の python)ごと
-- 落ちてしまい、Typst 側も何語か分からず無色になる。
--
-- そこで CodeBlock を Typst の生マークアップとして書き出し、言語タグを保つ。
-- これで Typst 自身のシンタックスハイライタが働き、tmTheme の配色が効く。
--
-- --code-theme を指定したときだけ有効にする(qtpdf.py が一時プロファイルで足す)。

function CodeBlock(el)
  if FORMAT ~= "typst" then
    return nil
  end

  local lang = el.classes[1] or ""

  -- 本文にバッククォート3連が含まれると囲みが壊れるので、必要なら区切りを伸ばす。
  local fence = "```"
  while el.text:find(fence, 1, true) do
    fence = fence .. "`"
  end

  return pandoc.RawBlock("typst", fence .. lang .. "\n" .. el.text .. "\n" .. fence)
end
