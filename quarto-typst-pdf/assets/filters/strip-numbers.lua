-- 見出しの先頭にある手書きの番号を落とす。
--
-- 「## 1. 概要」のような原稿に style 側の自動採番を重ねると「1 1. 概要」と
-- 二重になる。番号を style に一本化したいときに使う。
--
-- Markdown だけでなく Jupyter Notebook の見出しにも効かせたいので、
-- 前処理(Python)ではなく AST の段階で落とす。
--
-- 常時読み込んでおき、メタデータ `strip-numbers` が真のときだけ働く。
-- (プロファイル側で filters を足すとリストが置き換わって既定の並びが壊れるため、
--  フィルタ自体は固定し、qtpdf.py は `-M strip-numbers:true` を渡すだけにする)
--
-- Meta は本文より後に処理されるので、判定と書き換えを2パスに分ける
-- (1つのフィルタにまとめると、判定前に見出しを通過してしまう)。

local LEADING_NUMBER = "^%d+[%.%d]*%.?$"
local enabled = false

local read_flag = {
  Meta = function(meta)
    local v = meta["strip-numbers"]
    enabled = v == true or (v ~= nil and pandoc.utils.stringify(v) == "true")
    return meta
  end,
}

local strip = {
  Header = function(el)
    if not enabled then
      return nil
    end

    local inlines = el.content
    if #inlines < 2 or inlines[1].t ~= "Str" then
      return nil
    end
    if not inlines[1].text:match(LEADING_NUMBER) then
      return nil
    end

    -- 番号の直後の空白ごと落とす。番号しか無い見出しは触らない(消すと空になる)。
    local drop = 1
    while inlines[drop + 1] and inlines[drop + 1].t == "Space" do
      drop = drop + 1
    end
    if drop >= #inlines then
      return nil
    end

    local rest = {}
    for i = drop + 1, #inlines do
      rest[#rest + 1] = inlines[i]
    end
    el.content = rest
    return el
  end,
}

return { read_flag, strip }
