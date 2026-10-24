# Portable Tesseract (Day 4)

Binary blobs are gitignored; the folder is committed as a placeholder so
`smoke.spec` can resolve `packaging/tesseract/` on Day 1.

## Fetch (Day 4, manual — no automation yet)

1. Download a portable Tesseract build (e.g. UB-Mannheim).
2. Extract `tesseract.exe` + DLLs + `tessdata/` here.
3. Ensure `tessdata/eng.traineddata` and `tessdata/rus.traineddata` exist.

Until then the smoke test prints `warn tesseract.exe not found` and continues.
