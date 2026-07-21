-- 図版をテキスト幅に収める。
-- Quarto の mermaid 出力は自然サイズ(inch)を width/height に直接指定してくるため、
-- A4 の本文幅を超えて見切れる。height を落とし width=100% にしてアスペクト比を保って収める。
-- Draw.io SVG (サイズ指定なし) も同様に本文幅へ揃える。

local function is_diagram(src)
  return src:match("mermaid%-figure") or src:match("%.drawio%.svg$")
end

function Image(img)
  local src = img.src or ""
  if is_diagram(src) then
    img.attributes.height = nil
    img.attributes.width = "100%"
  end
  return img
end
