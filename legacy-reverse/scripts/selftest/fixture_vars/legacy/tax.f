      SUBROUTINE CALCTAX(A, R, T)
C  A: 課税対象額（控除前の金額）
      REAL*8 A
      REAL*8 R
      REAL*8 T
      REAL*8 RTBL
      INTEGER LIM
      COMMON /TAXCOM/ RTBL, LIM
      T = A * R
      IF (T .GT. LIM) THEN
         T = LIM
      END IF
      RETURN
      END
