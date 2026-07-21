-- 改訂マーカーの Div を Typst の改訂バーに橋渡しする。
--
-- Quarto は `::: {.revision-added}` を class 情報を落とした素の #block[...] として
-- 出力するため、Typst 側の show ルールでは種類を見分けられない。
-- ここで raw ブロックに置き換え、assets/revision.typ の関数を直接呼ぶ。
--
-- 改訂マーカーが無い文書では何もしないので、常時有効にしておいて構わない。

local KIND = {
  ["revision-added"] = "added",
  ["revision-modified"] = "modified",
}

function Div(div)
  local kind
  for _, class in ipairs(div.classes) do
    kind = kind or KIND[class]
  end
  if not kind then
    return nil
  end

  local out = { pandoc.RawBlock("typst", "#revision-bar(\"" .. kind .. "\")[") }
  for _, block in ipairs(div.content) do
    out[#out + 1] = block
  end
  out[#out + 1] = pandoc.RawBlock("typst", "]")
  return out
end
