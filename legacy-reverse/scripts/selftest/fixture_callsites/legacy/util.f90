! レポート出力と補助関数（自由形式）
subroutine report(t, v, e, tag, mode)
  real*8 :: t, v, e
  character(len=*) :: tag
  integer :: mode
  real*8 :: adj
  adj = factor(mode)
  call putline(tag, t * adj, e, 1)
end subroutine report

real*8 function factor(m)
  integer :: m
  factor = 1.0d0 + m
end function factor

subroutine putline(tag, val, ext, unit)
  character(len=*) :: tag
  real*8 :: val, ext
  integer :: unit
  write(*, *) tag, val, ext, unit
end subroutine putline
