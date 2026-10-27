"""Index / Search node.

Node id: ``access.index_search``.

Read-only, in-memory text search over files. Text extraction is dispatched
exclusively through ``extract_text.HANDLERS`` — this module deliberately does
not contain per-extension parsing or ``if ext == ...`` / ``elif ext == ...``
dispatch.

Risk notes (R13–R19)
--------------------
R13: Read-only. This node only stats and reads bytes. It never creates,
     mutates, renames, moves, or deletes files, and it never calls a mutating
     ``safe_fs.*`` function.
R14: Dispatch is ``extract_text.HANDLERS`` (module-level import of the
     internal ``extract_text`` module). The handler key is the lower-case
     extension without a dot. No custom format parsing is implemented here.
R15: ``mode`` is exactly one of ``substring``, ``word``, ``regex``; any other
     value is a payload validation error raised before filesystem traversal.
     ``case_sensitive`` defaults to ``False``.
R16: ``context`` is the window around each match clipped to the start line of
     the match and to file boundaries. Intersecting matches do not merge
     contexts; every match receives its own ``context`` string.
R17: Limits are enforced during a single pass. Files larger than
     ``max_bytes_per_file`` are skipped with reason ``too_large`` and are not
     opened. ``max_matches_per_file`` stops searching that file after the
     limit is crossed. ``max_total_matches`` stops the whole walk and sets
     ``truncated=True``.
R18: Determinism: ``matches`` sorted by ``(path, line, col)``;
     ``files_skipped`` and ``errors`` sorted by path. ``files_scanned`` counts
     only files whose handler completed without a per-item error.
R19: No module-level optional-dependency imports (no pytesseract/PIL/pypdfium2
     here). ``extract_text`` itself is safe to import without ``[ocr]``.
"""
from __future__ import annotations

import re
import stat as stat_module
from pathlib import Path
from typing import Any

from lokdocsqr.core import safe_fs
from lokdocsqr.core.errors import LokError, NodeExecutionError, ScopeViolation
from lokdocsqr.core.node_api.decorators import register_node

from . import extract_text
from ._base import AccessBase, AccessLimitError, DEFAULT_MAX_BYTES_PER_FILE

__all__ = ["IndexSearchNode"]


@register_node
class IndexSearchNode(AccessBase):
    """Search file text via extract_text.HANDLERS, read-only and in-memory."""

    id = "access.index_search"
    title = "Index / Search"
    inputs = {"paths": "str|list[str]"}
    outputs = {"matches": "dict[]"}

    def __init__(
        self,
        paths: list[str] | str | None = None,
        query: str = "",
        mode: str = "substring",
        case_sensitive: bool = False,
        context_chars: int = 40,
        max_matches_per_file: int = 100,
        max_total_matches: int = 1000,
        max_bytes_per_file: int = DEFAULT_MAX_BYTES_PER_FILE,
    ) -> None:
        self.paths = paths
        self.query = query
        self.mode = mode
        self.case_sensitive = case_sensitive
        self.context_chars = context_chars
        self.max_matches_per_file = max_matches_per_file
        self.max_total_matches = max_total_matches
        self.max_bytes_per_file = max_bytes_per_file

    # ── invariant 5: run/dry_run in the class body ─────────────────
    def run(self, ctx: Any, payload: Any) -> dict[str, Any]:
        return self._run_template(ctx, payload)

    def dry_run(self, ctx: Any, payload: Any) -> dict[str, Any]:
        return self._dry_run_template(ctx, payload)

    # ── invariant 5: _execute/_preview in the class body ───────────
    def _execute(self, ctx: Any, payload: dict[str, Any]) -> dict[str, Any]:
        return self._process(ctx, payload, dry_run=False)

    def _preview(self, ctx: Any, payload: dict[str, Any]) -> dict[str, Any]:
        """Validate payload and count candidates without reading bytes.

        This still traverses directories and stats files so the returned
        summary is useful, but it never calls ``safe_fs.read_bytes`` and never
        invokes an extract_text handler.
        """
        return self._process(ctx, payload, dry_run=True)

    @staticmethod
    def _raw_paths(value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, (str, Path)):
            return [value]
        if isinstance(value, (list, tuple)):
            return list(value)
        raise NodeExecutionError(
            "index_search: 'paths' must be str, Path, or list of those"
        )

    @staticmethod
    def _int_param(payload: dict[str, Any], key: str, default: Any) -> int:
        try:
            value = int(payload.get(key, default))
        except (TypeError, ValueError) as exc:
            raise NodeExecutionError(
                f"index_search: {key} must be an integer"
            ) from exc
        if value < 0:
            raise NodeExecutionError(
                f"index_search: {key} must be non-negative"
            )
        return value

    @staticmethod
    def _compile_pattern(
        query: str, mode: str, case_sensitive: bool
    ) -> re.Pattern[str]:
        flags = 0 if case_sensitive else re.IGNORECASE
        if mode == "substring":
            expression = re.escape(query)
        elif mode == "word":
            expression = r"\b" + re.escape(query) + r"\b"
        elif mode == "regex":
            expression = query
        else:
            raise NodeExecutionError(
                "index_search: mode must be one of "
                "'substring', 'word', 'regex'"
            )
        try:
            return re.compile(expression, flags)
        except re.error as exc:
            raise AccessLimitError(
                f"index_search: invalid regex {query!r}: {exc}"
            ) from exc

    def _process(
        self, ctx: Any, payload: dict[str, Any], dry_run: bool
    ) -> dict[str, Any]:
        query = str(payload.get("query", self.query))
        if not query:
            raise NodeExecutionError(
                "index_search: 'query' must be non-empty"
            )

        mode = str(payload.get("mode", self.mode)).strip().lower()
        if mode not in ("substring", "word", "regex"):
            raise NodeExecutionError(
                "index_search: mode must be one of "
                "'substring', 'word', 'regex'"
            )

        case_sensitive = bool(
            payload.get("case_sensitive", self.case_sensitive)
        )
        context_chars = self._int_param(
            payload, "context_chars", self.context_chars
        )
        max_matches_per_file = self._int_param(
            payload, "max_matches_per_file", self.max_matches_per_file
        )
        max_total_matches = self._int_param(
            payload, "max_total_matches", self.max_total_matches
        )
        max_bytes_per_file = self._int_param(
            payload, "max_bytes_per_file", self.max_bytes_per_file
        )

        # R15/R17: payload validation and regex compilation happen before
        # any filesystem traversal or read.
        pattern = self._compile_pattern(query, mode, case_sensitive)

        raw_paths = self._raw_paths(payload.get("paths", self.paths))
        if not raw_paths:
            raise NodeExecutionError("index_search: no paths provided")

        matches: list[dict[str, Any]] = []
        files_skipped: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        visited_dirs: set[Path] = set()

        files_scanned = 0
        files_would_scan = 0
        truncated = False
        stop_all = False

        def search_text(path: Path, text: str) -> None:
            nonlocal truncated, stop_all
            file_matches: list[dict[str, Any]] = []
            per_file_truncated = False

            for match in pattern.finditer(text):
                if len(file_matches) >= max_matches_per_file:
                    per_file_truncated = True
                    break
                if len(matches) + len(file_matches) >= max_total_matches:
                    truncated = True
                    stop_all = True
                    break

                start = match.start()
                end = match.end()

                line = text.count("\n", 0, start) + 1
                last_newline = text.rfind("\n", 0, start)
                col = start - last_newline

                # R16: clip context to the match's start line and file bounds.
                line_start = last_newline + 1
                line_end = text.find("\n", start)
                if line_end == -1:
                    line_end = len(text)
                context_start = max(line_start, start - context_chars)
                context_end = min(line_end, end + context_chars)

                file_matches.append(
                    {
                        "path": str(path),
                        "line": line,
                        "col": col,
                        "match": match.group(0),
                        "context": text[context_start:context_end],
                    }
                )

            if per_file_truncated:
                truncated = True
            matches.extend(file_matches)

        def process_file(path: Path, st: Any) -> None:
            nonlocal files_scanned, files_would_scan

            # R17: too large at stat time -> skip, never open.
            if st.st_size > max_bytes_per_file:
                files_skipped.append(
                    {"path": str(path), "reason": "too_large"}
                )
                return

            extension = path.suffix.lower().lstrip(".")
            handler = extract_text.HANDLERS.get(extension)
            if handler is None:
                errors.append(
                    {
                        "path": str(path),
                        "error": (
                            f"no handler for format '{extension}'"
                        ),
                    }
                )
                return

            if dry_run:
                files_would_scan += 1
                return

            try:
                # read_content-style stat-first bounded read.
                data = safe_fs.read_bytes(
                    path, limit=max_bytes_per_file + 1
                )
            except LokError as exc:
                errors.append({"path": str(path), "error": str(exc)})
                return

            if len(data) > max_bytes_per_file:
                # TOCTOU: file grew after stat. Do not silently truncate.
                files_skipped.append(
                    {"path": str(path), "reason": "too_large"}
                )
                return

            try:
                extracted = handler(data, {})
            except Exception as exc:  # noqa: BLE001 — per-item error boundary
                errors.append({"path": str(path), "error": str(exc)})
                return

            # Handler contract: "error" may be present with value None to
            # mean "no error" (mirrors how test fakes and future handlers
            # express optional error fields). Only a non-None error is fatal.
            if extracted.get("error") is not None:
                errors.append(
                    {
                        "path": str(path),
                        "error": str(extracted.get("error")),
                    }
                )
                return

            text = extracted.get("text") or ""
            if not isinstance(text, str):
                text = str(text)

            # Cross-platform: files written via Path.write_text on Windows
            # carry CRLF line endings; users writing regex \n (or substring
            # queries spanning lines) expect a match regardless of platform.
            # Normalization does not change line/col semantics because line
            # counting uses "\n" as the sole boundary and col is measured
            # from the preceding "\n".
            if "\r\n" in text:
                text = text.replace("\r\n", "\n")

            files_scanned += 1
            search_text(path, text)

        def scan_dir(directory: Path) -> None:
            try:
                directory_key = directory.resolve(strict=False)
            except OSError:
                directory_key = directory
            if directory_key in visited_dirs:
                return
            visited_dirs.add(directory_key)

            if stop_all:
                return

            try:
                entries = sorted(
                    directory.iterdir(),
                    key=lambda p: (p.name.lower(), p.name),
                )
            except OSError as exc:
                errors.append(
                    {"path": str(directory), "error": str(exc)}
                )
                return

            for entry in entries:
                if stop_all:
                    return

                try:
                    checked = ctx.scope.check(entry, write=False)
                except ScopeViolation:
                    # Mirrors list_files: out-of-scope discovered entries are
                    # not traversed/read.
                    continue

                try:
                    is_symlink = entry.is_symlink()
                except OSError:
                    is_symlink = False
                if is_symlink:
                    # Mirrors list_files default (follow_symlinks=False).
                    continue

                try:
                    st = safe_fs.stat(checked)
                except LokError as exc:
                    errors.append(
                        {"path": str(checked), "error": str(exc)}
                    )
                    continue

                if stat_module.S_ISDIR(st.st_mode):
                    scan_dir(checked)
                else:
                    process_file(checked, st)

        for raw in raw_paths:
            if stop_all:
                break
            try:
                roots = self._resolve_input(ctx, raw)
            except LokError as exc:
                errors.append({"path": str(raw), "error": str(exc)})
                continue

            for root in roots:
                if stop_all:
                    break
                try:
                    st = safe_fs.stat(root)
                except LokError as exc:
                    errors.append(
                        {"path": str(root), "error": str(exc)}
                    )
                    continue

                if stat_module.S_ISDIR(st.st_mode):
                    scan_dir(root)
                else:
                    process_file(root, st)

        matches.sort(key=lambda item: (item["path"], item["line"], item["col"]))
        files_skipped.sort(key=lambda item: item["path"])
        errors.sort(key=lambda item: item["path"])

        result: dict[str, Any] = {
            "matches": matches,
            "files_scanned": 0 if dry_run else files_scanned,
            "files_skipped": files_skipped,
            "truncated": truncated,
            "errors": errors,
        }
        if dry_run:
            result["files_would_scan"] = files_would_scan
        return result
