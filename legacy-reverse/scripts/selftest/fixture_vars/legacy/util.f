      SUBROUTINE CONVRT(RATE, OUT)
C  RATE: 円換算に使う相場
      REAL*8 RATE
      REAL*8 OUT
      OUT = RATE * 100.0D0
      RETURN
      END
      SUBROUTINE PACKIT(N)
      INTEGER N
      REAL*8 WORK(10)
      REAL*8 WBUF(10)
      EQUIVALENCE (WORK, WBUF)
      WORK(1) = 0.0D0
      N = 1
      RETURN
      END
