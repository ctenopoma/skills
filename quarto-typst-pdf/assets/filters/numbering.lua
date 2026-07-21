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

-- 全角は2文字分として見た目の幅を測る。
local function display_width(s)
  local w = 0
  for _, cp in utf8.codes(s) do
    w = w + (cp > 0x2000 and 2 or 1)
  end
  return w
end

local MIN_SHARE = 0.08 -- これ以下に潰れると読めない
local CAP = 60         -- 1列がこれ以上長くても幅は増やさない(折り返す前提)
local LINE_CAPACITY = 86 -- 版面いっぱいに入る等幅文字数のおよその目安
local MAX_FLOOR = 0.4  -- 1列が下限として要求できる上限

--- 空白で切れない最長の連なり。コード中の識別子は折り返せないので、
--- ここが列の最小幅を決める。
local function longest_token(s)
  local longest = 0
  for token in s:gmatch("%S+") do
    local w = display_width(token)
    if w > longest then
      longest = w
    end
  end
  return longest
end

--- 各列の内容量から、合計 100% になる列幅を決める。
--
-- 幅指定を落として `columns: N`(全列 auto)にすると、Typst は内容の自然幅で
-- 組むため、長い列があると版面をはみ出す。かといって等分だと内容量を無視する。
-- そこで列ごとの最大セル幅を測り、それに比例した割合を明示する。
local function proportional_widths(tbl)
  local n = #tbl.colspecs
  if n == 0 then
    return
  end

  local widest, token = {}, {}
  for i = 1, n do
    widest[i], token[i] = 0, 0
  end

  local function scan(rows)
    for _, row in ipairs(rows or {}) do
      for i, cell in ipairs(row.cells) do
        if i <= n then
          local text = pandoc.utils.stringify(cell.contents)
          widest[i] = math.max(widest[i], display_width(text))
          token[i] = math.max(token[i], longest_token(text))
        end
      end
    end
  end

  scan(tbl.head and tbl.head.rows)
  for _, body in ipairs(tbl.bodies or {}) do
    scan(body.head)
    scan(body.body)
  end

  local total = 0
  for i = 1, n do
    widest[i] = math.max(1, math.min(widest[i], CAP))
    total = total + widest[i]
  end

  -- 比例配分したうえで、各列の下限(折り返せない語が収まる幅)を保証する。
  -- 合計が 100% を超えた分は、余裕のある列から余裕に比例して差し引く。
  local shares, floor = {}, {}
  for i = 1, n do
    floor[i] = math.max(MIN_SHARE, math.min(MAX_FLOOR, (token[i] + 2) / LINE_CAPACITY))
    shares[i] = math.max(widest[i] / total, floor[i])
  end

  local sum = 0
  for i = 1, n do
    sum = sum + shares[i]
  end
  local excess = sum - 1
  if excess > 0 then
    local slack = 0
    for i = 1, n do
      slack = slack + math.max(0, shares[i] - floor[i])
    end
    if slack > 0 then
      local cut = math.min(excess, slack)
      for i = 1, n do
        local room = math.max(0, shares[i] - floor[i])
        shares[i] = shares[i] - cut * room / slack
      end
    end
  end

  -- 下限が積み上がって 100% を超えることがある(列が多い表)。
  -- その場合は下限をあきらめて一律に縮める。版面をはみ出すよりは、
  -- 長い識別子が少しはみ出すほうがましなため。
  sum = 0
  for i = 1, n do
    sum = sum + shares[i]
  end
  for i = 1, n do
    tbl.colspecs[i][2] = shares[i] / sum
  end
end

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
    proportional_widths(tbl)
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
