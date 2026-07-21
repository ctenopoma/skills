-- 目次のあとと、章(レベル1見出し)の前で改頁する。
--
-- Typst の show ルールで `pagebreak()` を書くと
-- 「pagebreaks are not allowed inside of containers」で落ちる。
-- Quarto のテンプレートは本文を関数の引数として組み立てるため、
-- show ルールの出力がコンテナ扱いになるのが原因(素の Typst では通る)。
--
-- そこで AST の最上位に生の `#pagebreak(weak: true)` を挿し込む。
-- 本文の流れそのものに入るので確実に効く。
--
-- weak なので、すでにページ先頭にいるときは何も起きない。
-- 「目次のあとの改頁」と「最初の章の改頁」が重なっても空白ページはできない。
--
-- メタデータ `chapter-breaks: false` で無効にできる(記事のように
-- 章ごとに改頁すると間延びする文書向け)。

local enabled = true

local read_flag = {
  Meta = function(meta)
    local v = meta["chapter-breaks"]
    if v ~= nil then
      enabled = not (v == false or pandoc.utils.stringify(v) == "false")
    end
    return meta
  end,
}

local function brk()
  return pandoc.RawBlock("typst", "#pagebreak(weak: true)")
end

local insert = {
  Pandoc = function(doc)
    if not enabled then
      return nil
    end

    -- 「章」は見出しレベル1とは限らない。H1 を title へ上げた文書は
    -- shift-heading-level-by で後からずらされるため、この時点では 2 のことがある。
    -- 最上位に現れる見出しの最小レベルを実測して、それを章とみなす。
    local top = nil
    for _, block in ipairs(doc.blocks) do
      if block.t == "Header" and (top == nil or block.level < top) then
        top = block.level
      end
    end

    -- 先頭の1つが「目次と本文の切れ目」になる。
    local out = { brk() }
    for _, block in ipairs(doc.blocks) do
      if block.t == "Header" and block.level == top then
        -- 章の直前の区切り線は落とす。改頁が区切りになるので二重だし、
        -- 線だけが前ページの末尾に取り残されて空きページのように見える。
        if #out > 0 and out[#out].t == "HorizontalRule" then
          table.remove(out)
        end
        out[#out + 1] = brk()
      end
      out[#out + 1] = block
    end

    doc.blocks = out
    return doc
  end,
}

return { read_flag, insert }
