C  税額計算のメインルーチン（variables.py セルフテスト用フィクスチャ）
      PROGRAM MAIN
C  RATE: ANNUAL TAX RATE  年間税率
      REAL*8 RATE
      INTEGER LIMIT
      COMMON /TAXCOM/ RATE, LIMIT
      REAL*8 AMT, WRK, TAX
      REAL*8 FXR, YEN, YEN2
      INTEGER NCNT
      DATA AMT /1000.0D0/
      RATE = 0.08D0
      LIMIT = 500
      WRK = RATE
      CALL CALCTAX(AMT, WRK, TAX)
      WRITE(6,100) TAX
100   FORMAT(1X,'TAX AMOUNT = ',F10.2)
      FXR = 145.0D0
      CALL CONVRT(FXR, YEN)
      CALL CONVRT(2.0D0, YEN2)
      NCNT = 0
      CALL PACKIT(NCNT)
      END
