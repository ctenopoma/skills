C     hazard 検出のフィクスチャ（固定形式）。検知すべき／してはならない両方を含む。
      SUBROUTINE HAZCALC(A, B, N, TBL, R)
      REAL A, B, R, HALF, Q, S, T, C2, V, X, Y, QRT
      INTEGER N
      REAL TBL(10)
      DIMENSION WK(5)
      COMMON /CFG/ LIMIT, COEF(4)
      DATA HALF /0.5/
C     --- 検知する: 分母が変数
      R = A / B
C     --- 検知しない: 分母が純リテラル
      QRT = A / 4
      Q = A / 2.0D0
C     --- 検知する: 分母が式（vars は A と B の両方）
      S = R / (A + B)
C     --- 検知する: SQRT 系の引数に変数
      T = SQRT(A)
      V = DSQRT(B + 1.0D0)
C     --- 検知しない: 定数だけの SQRT
      C2 = SQRT(2.0)
C     --- 検知する: LOG 系の引数に変数
      X = ALOG(B)
C     --- 検知する: 宣言済み配列の変数添字
      Y = TBL(N)
      WK(N) = Y
      COEF(N) = 1.0
C     --- 検知しない: 定数添字
      Y = TBL(3)
  100 FORMAT(1X, A / 1X, I5)
      RETURN
      END
