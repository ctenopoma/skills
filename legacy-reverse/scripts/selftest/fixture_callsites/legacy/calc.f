C     固定形式のサンプル。COMMON・継続行・配列要素・式・リテラルを含む
      PROGRAM MAIN
      REAL*8 AMT, TAX, TBL(10)
      COMMON /CFG/ RATE, MODE
      REAL*8 RATE
      INTEGER MODE
      AMT = 1000.0D0
      MODE = 2
      CALL CALCTAX(AMT, RATE, TAX)
      CALL REPORT(TAX, TBL(3), AMT * 2.0D0,
     &            'FIXED', MODE)
      TAX = TAX + FACTOR(MODE)
      CALL FINISH
      END

      SUBROUTINE CALCTAX(A, R, T)
      REAL*8 A, R, T
      COMMON /CFG/ RATE, MODE
      REAL*8 RATE
      INTEGER MODE
      T = A * R
      RETURN
      END
