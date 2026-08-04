-- キャプション無し画像に番号だけの figure を与える。
--
-- **Quarto 本体(`- quarto`)より前に走らせること。**
-- Quarto はキャプション付き画像を早期に float 化してキャプションを退避するため、
-- 後段では「元からキャプションが無い画像」と「退避されて空になった画像」が
-- 区別できない(id・class・属性すべて空になる)。後段で包むと、キャプション付き
-- 画像が二重の #figure になり、番号だけの空キャプションが図の上に出る。
-- 前段なら caption の有無がそのまま見えるので、素の画像だけを安全に包める。
--
-- 表の採番は従来どおり numbering.lua(Quarto の後)が担当する。役割を混ぜないこと。

local fig_supplement = "Figure"

local function wrap(block)
  return {
    pandoc.RawBlock("typst", "#figure(["),
    block,
    pandoc.RawBlock("typst",
      '], caption: figure.caption(separator: "", position: top, []),'
      .. ' kind: "quarto-float-fig", supplement: "' .. fig_supplement .. '")'),
  }
end

local function bare_image(block)
  return (block.t == "Para" or block.t == "Plain")
    and #block.content == 1
    and block.content[1].t == "Image"
    and #block.content[1].caption == 0
end

-- Meta と Blocks を別パスに分ける。同一パスだと pandoc は型別順(Blocks → Meta)で
-- 走らせるため、fig-supplement を読む前に画像を包んでしまう(常に "Figure" になる)。
return {
  {
    Meta = function(meta)
      local v = meta["fig-supplement"]
      if v then
        fig_supplement = pandoc.utils.stringify(v)
      end
      return meta
    end,
  },
  {
    Blocks = function(blocks)
      local out = {}
      for _, b in ipairs(blocks) do
        if bare_image(b) then
          for _, nb in ipairs(wrap(b)) do
            out[#out + 1] = nb
          end
        else
          out[#out + 1] = b
        end
      end
      return out
    end,
  },
}
