# LokDoc Core — Build & License Report

## 1. Overview

LokDoc is a file-automation core that processes files, extracts metadata, and routes them into target directories based on the extracted data. This report documents two decisions required before the GUI layer is integrated:

1. The choice of the project license (Apache 2.0).
2. The results of the compilation / import smoke test and the size budget for the final build.

## 2. License Selection: Apache 2.0

LokDoc Core is released under the **Apache License 2.0**. The choice is driven by the following considerations:

- **Permissive model.** Apache 2.0 does not require derivative works to be open-sourced. This allows LokDoc to be embedded into proprietary automation pipelines and internal corporate tooling without forcing disclosure of the surrounding code.
- **Explicit patent grant.** Unlike MIT or BSD, Apache 2.0 includes an express patent license from contributors. This reduces legal risk for adopters — a relevant factor for an automation tool that may be deployed inside organizations.
- **No copyleft.** Apache 2.0 does not "infect" code that LokDoc interacts with, which keeps integration with third-party file processing tools and proprietary formats unconstrained.
- **Industry acceptance.** Apache 2.0 is a de-facto standard for infrastructure and developer tooling, which lowers friction for both adoption and external contribution.

## 3. Build Configuration

- **Language / runtime:** Python
- **GUI framework (planned):** PySide
- **Packaging:** PyInstaller, **one-dir** mode (`--onedir`)

One-dir mode was chosen over one-file because it keeps the Python runtime and shared libraries as discrete files. This makes the size of the distribution directly measurable, speeds up application startup, and avoids the per-launch unpacking overhead of one-file builds.

## 4. Smoke Test: Compilation and Import Verification

### 4.1 Objective

Verify that LokDoc Core builds successfully with all declared dependencies and that the resulting distribution can start and import every required module.

### 4.2 Procedure

1. Build the distribution:

       pyinstaller --onedir lokdoc.spec

2. Measure the size of the produced distribution directory:

       du -sh dist/LokDoc

   On Windows (PowerShell):

       "{0:N2} MB" -f ((Get-ChildItem -Recurse dist\LokDoc |
           Measure-Object -Property Length -Sum).Sum / 1MB)

3. Launch the built executable. On startup the binary performs an import check of all required dependencies and exits with code `0` if every import succeeds, or a non-zero code otherwise.

### 4.3 Results

| Check | Result |
|---|---|
| PyInstaller build | PASS |
| Distribution size (one-dir) | 260 MB |
| Binary launch | PASS |
| Dependency import check | PASS |

The measured 260 MB represents the **entire one-dir distribution directory**, i.e. the Python runtime, the bundled standard library, PyInstaller bootloader, and all resolved third-party dependencies.

### 4.4 Size Budget

The size budget for the final build (core + GUI) is **400 MB**.

| Metric | Value |
|---|---|
| Current one-dir build | 260 MB |
| Final build budget | 400 MB |
| Remaining headroom | 140 MB |
| Headroom as share of budget | 35 % |

The current build uses 65 % of the budget. The remaining 140 MB is reserved for the GUI layer.

### 4.5 Expected Size Impact of the GUI Layer

The planned GUI is built on PySide and includes a **node-based editor** for composing file-processing pipelines. Compared to a conventional forms-and-widgets interface, a node editor pulls in additional Qt modules and runtime resources that must be bundled by PyInstaller:

- **QtWidgets and QtGui** — the base widget and painting stack required by any PySide application. This is a fixed cost for the GUI regardless of its layout.
- **QtGraphicsView framework** — the node editor is built on `QGraphicsScene`, `QGraphicsView` and `QGraphicsItem`. These classes live in QtWidgets, but the editor relies on the full graphics-view pipeline, including item rendering, transformations, and scene event handling.
- **QSS / stylesheets and icons.** A node editor typically ships with a theme (node styles, port icons, connection graphics). Stylesheet parsing and image format plugins (PNG, SVG) must be collected explicitly.
- **Qt plugins.** PyInstaller only bundles plugins it detects; for a node editor the relevant set includes platform plugins (`platforms/`), image formats (`imageformats/`), SVG support (`iconengines/` and `imageformats/qsvg`), and — if a dark/modern theme is used — the `styles/` plugin. Bundling the full plugin set via `--collect-all PySide6` is the safest option, but it is also the heaviest.
- **Translations.** If the GUI is localized, Qt translation files (`translations/*.qm`) add a small but non-zero cost per supported locale.

Based on typical PySide one-dir builds, the GUI layer plus node editor is expected to add roughly **50–90 MB**, depending on how aggressively the Qt plugin set is trimmed. This estimate is consistent with the 140 MB headroom: even at the upper end of the range the final distribution stays well under the 400 MB budget. The estimate will be replaced by a measured value after GUI integration using the same procedure described in §4.2.

### 4.6 Conclusion

The core builds cleanly, starts successfully, and imports all required dependencies. At 260 MB the distribution leaves a 140 MB (35 %) margin against the 400 MB budget. This margin is sufficient to accommodate the planned PySide GUI layer with its node-based editor, including the Qt plugins and resources that the node editor requires. The size budget will be re-verified after GUI integration using the same measurement method.