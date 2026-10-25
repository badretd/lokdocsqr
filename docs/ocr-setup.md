# OCR Setup — Tesseract

LokDocSQR's OCR node uses
[Tesseract OCR](https://github.com/tesseract-ocr/tesseract) via the
`pytesseract` Python wrapper. Tesseract is a **system binary**, not a
pip package, so it must be installed on the host (or be present in the
development Docker image) before the OCR node can run.

No tesseract binaries and no `*.traineddata` files are committed to
this repo — see `.gitignore`.

## Quick check

```bash
python scripts/check_system_deps.py
```

Exit code `0` = every required binary is available. Exit code `1` =
at least one is missing; install hints are printed to stderr.

## Install

### Debian / Ubuntu (apt)

```bash
sudo apt-get update
sudo apt-get install -y tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng
```

### macOS (brew)

```bash
brew install tesseract
brew install tesseract-lang     # includes rus, eng, and others
```

### Windows (choco)

```powershell
choco install tesseract
```

Or download the official installer from the UB-Mannheim build page:

https://github.com/UB-Mannheim/tesseract/wiki

### Development Docker

The repository ships `Dockerfile.dev`, which layers
`tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng` on top of
`python:3.12-slim` and then runs `pip install -e ".[ocr,dev]"`:

```bash
docker build -f Dockerfile.dev -t lokdocsqr-dev .
docker run --rm -it -v "$PWD:/app" lokdocsqr-dev
```

## Required languages

- `eng` — English
- `rus` — Russian

Additional packs can be installed via apt (`tesseract-ocr-<lang>`) or
brew (`tesseract-lang`), but the OCR node currently advertises `eng+rus`
in its default configuration.
