! hazard 検出のフィクスチャ（自由形式）。文字列連結・比較演算子・配列構成子の誤検知よけ。
subroutine hazmsg(tag, name, msg, cnt, den, tbl)
  character(len=8) :: tag, name
  character(len=32) :: msg
  integer :: cnt, tbl(4)
  real :: den, rate, scale
  ! 文字列連結 // は除算ではない（検知してはならない）
  msg = tag // name
  msg = trim(tag)//trim(name)
  ! 比較演算子 /= も除算ではない
  if (cnt /= 0) then
     rate = real(cnt) / den
  end if
  ! 分母がユーザ定義関数の呼び出し（vars は引数側の変数だけになる）
  scale = rate / ratio(cnt)
  ! 配列構成子 (/ ... /) も除算ではない
  tbl = (/ 1, 2, 3, 4 /)
end subroutine hazmsg

real function ratio(k)
  integer :: k
  ratio = 1.0 / real(k + 1)
end function ratio
