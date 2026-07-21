-- 表と図の体裁を整える前処理。
-- Quarto 本体のフィルタ(crossref 等)の**後**に走らせる前提
-- (_quarto.yml の filters で `- quarto` の次に置く)。
--
-- 1) 列幅指定の解除
--    Pandoc は行の長い pipe table に相対列幅を割り当て、Quarto はそれを
--    Typst の columns: (33.33%, ...) として出力する。結果、内容量と無関係に
--    等間隔になる。幅指定を落とすと columns: N となり Typst が内容量で配分する。
--
-- 2) キャプションなしの表・図に番号を振る
--    Quarto はキャプションのあるものだけを float にするため、素の表・図には
--    番号が付かない。ここで Quarto と同じ kind の #figure で包み、番号
--    カウンタを共有させる。キャプション本文は空なので番号だけが出る
--    (区切り記号は base.typ の show ルールが落とす)。
--    Quarto の float はこの時点で class="quarto-scaffold" の Div に入っているため、
--    その中へは降りない(降りると二重に包んでしまう)。

local tbl_supplement = "Table"
local fig_supplement = "Figure"

local meta_and_widths = {
  Meta = function(meta)
    local function s(key, fallback)
      local v = meta[key]
      return v and pandoc.utils.stringify(v) or fallback
    end
    tbl_supplement = s("tbl-supplement", tbl_supplement)
    fig_supplement = s("fig-supplement", fig_supplement)
    return meta
  end,

  Table = function(tbl)
    for _, spec in ipairs(tbl.colspecs) do
      spec[2] = nil -- ColWidthDefault
    end
    return tbl
  end,
}

local function wrap(block, kind, supplement)
  return {
    pandoc.RawBlock("typst", "#figure(["),
    block,
    pandoc.RawBlock("typst",
      '], caption: figure.caption(separator: "", position: top, []),'
      .. ' kind: "' .. kind .. '", supplement: "' .. supplement .. '")'),
  }
end

-- 画像1枚だけの段落。セル出力の中では Para でなく Plain になることがある。
local function bare_image(block)
  return (block.t == "Para" or block.t == "Plain")
    and #block.content == 1
    and block.content[1].t == "Image"
    and #block.content[1].caption == 0
end

-- Quarto が float 化済みの入れ物か。
-- class では判定できない(quarto-scaffold はセル出力の包みにも付く)。
-- float の入れ物は直下に Typst の `#figure(` 生片を持つので、それで見分ける。
local function is_float_container(div)
  for _, b in ipairs(div.content) do
    if b.t == "Plain" or b.t == "Para" then
      for _, inl in ipairs(b.content) do
        if inl.t == "RawInline"
          and (inl.format == "typst" or inl.format == "typst-raw")
          and inl.text:find("#figure(", 1, true) then
          return true
        end
      end
    end
  end
  return false
end

local promote = {
  traverse = "topdown",

  Div = function(div)
    if is_float_container(div) then
      return div, false -- Quarto が float 化済み。中へは降りない
    end
    return nil
  end,

  Blocks = function(blocks)
    local out = {}

    for _, b in ipairs(blocks) do
      local wrapped = nil

      if b.t == "Table" then
        local cap = b.caption
        if not (cap and cap.long and #cap.long > 0) then
          wrapped = wrap(b, "quarto-float-tbl", tbl_supplement)
        end
      elseif bare_image(b) then
        wrapped = wrap(b, "quarto-float-fig", fig_supplement)
      end

      if wrapped then
        for _, nb in ipairs(wrapped) do
          out[#out + 1] = nb
        end
      else
        out[#out + 1] = b
      end
    end

    return out
  end,
}

return { meta_and_widths, promote }
