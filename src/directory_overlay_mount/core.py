"""Core overlay logic for composing two read-only directory trees."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterator, List, Optional


@dataclass(frozen=True)
class OverlayEntry:
    """A single file or directory in the merged view.

    ``layer`` records which physical directory provided the entry.  It is
    always ``'upper'`` or ``'lower'``.  We expose it because callers often
    need to know provenance to decide how to handle the file (e.g.
    read-only semantics differ by layer).
    """

    name: str
    path: str
    is_dir: bool
    layer: str


class OverlayView:
    """Read-only merged view of two directory trees.

    The upper directory shadows the lower one.  If an upper-layer entry
    is a *file*, every entry with the same relative name in the lower
    layer is hidden (whether directory or file).  This is the standard
    overlay-shadow rule.  We intentionally do **not** merge directories
    when the upper layer has a file at the same name — that would be
    surprising and is not how union filesystems behave.

    The view is purely logical: no files are copied, and no writes are
    performed.  All paths returned are absolute paths into the real
    underlying directories.
    """

    def __init__(self, lower: str, upper: str) -> None:
        if not os.path.isdir(lower):
            raise ValueError(f"lower is not a directory: {lower!r}")
        if not os.path.isdir(upper):
            raise ValueError(f"upper is not a directory: {upper!r}")

        self._lower = os.path.abspath(lower)
        self._upper = os.path.abspath(upper)

    @property
    def lower(self) -> str:
        """Absolute path of the lower directory."""
        return self._lower

    @property
    def upper(self) -> str:
        """Absolute path of the upper directory."""
        return self._upper

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve(self, rel: str, upper_root: str, lower_root: str,
                 is_top: bool) -> Optional[str]:
        """Return the real path of *rel* preferring upper, or ``None``.

        *upper_root* and *lower_root* are pre-resolved roots for the
        current call.  At the top level they equal ``self._upper`` and
        ``self._lower``.  During recursion into a lower-layer directory,
        *upper_root* may point to a path that does not exist (when the
        upper layer has no directory at that name) and *lower_root* is
        the resolved sub-path inside the lower tree.

        A leading slash is tolerated only at the top level for caller
        convenience.  Empty string is treated as the root.
        """
        if not is_top:
            # Nested calls are always relative, never absolute.
            if os.path.isabs(rel):
                return None
        elif rel.startswith("/"):
            rel = rel.lstrip("/")

        if rel == "":
            return upper_root

        parts = rel.split("/")
        upper_path = upper_root
        lower_path = lower_root

        for i, part in enumerate(parts):
            if part == "":
                # Double-slash or trailing slash: reject for safety.
                return None
            if part == ".":
                continue
            if part == "..":
                # Disallow escaping the merged root.
                return None

            upper_next = os.path.join(upper_path, part)
            lower_next = os.path.join(lower_path, part)

            upper_exists = os.path.exists(upper_next)
            lower_exists = os.path.exists(lower_next)

            if not upper_exists and not lower_exists:
                return None

            if i == len(parts) - 1:
                # Final component: prefer upper unconditionally.
                if upper_exists:
                    return upper_next
                return lower_next

            # Intermediate component: both must be directories for the
            # overlay to continue into them.
            upper_is_dir = upper_exists and os.path.isdir(upper_next)
            lower_is_dir = lower_exists and os.path.isdir(lower_next)

            if upper_is_dir and lower_is_dir:
                upper_path = upper_next
                lower_path = lower_next
            elif upper_is_dir:
                upper_path = upper_next
                lower_path = upper_next  # no lower subtree
            elif lower_is_dir:
                upper_path = lower_next  # no upper subtree
                lower_path = lower_next
            else:
                return None

        return upper_path

    def _list_dir(self, rel: str) -> List[OverlayEntry]:
        """List a directory *rel*, merging upper and lower entries."""
        upper_entries: dict = {}
        lower_entries: dict = {}

        upper_dir = os.path.join(self._upper, rel) if rel else self._upper
        lower_dir = os.path.join(self._lower, rel) if rel else self._lower

        if os.path.isdir(upper_dir):
            try:
                for name in os.listdir(upper_dir):
                    full = os.path.join(upper_dir, name)
                    upper_entries[name] = {
                        "path": full,
                        "is_dir": os.path.isdir(full),
                    }
            except OSError:
                pass

        if os.path.isdir(lower_dir):
            try:
                for name in os.listdir(lower_dir):
                    full = os.path.join(lower_dir, name)
                    lower_entries[name] = {
                        "path": full,
                        "is_dir": os.path.isdir(full),
                    }
            except OSError:
                pass

        merged: List[OverlayEntry] = []
        seen = set()

        for name, info in upper_entries.items():
            merged.append(OverlayEntry(
                name=name,
                path=info["path"],
                is_dir=info["is_dir"],
                layer="upper",
            ))
            seen.add(name)

        for name, info in lower_entries.items():
            if name in seen:
                continue
            merged.append(OverlayEntry(
                name=name,
                path=info["path"],
                is_dir=info["is_dir"],
                layer="lower",
            ))

        merged.sort(key=lambda e: e.name)
        return merged

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def listdir(self, rel: str = "") -> List[OverlayEntry]:
        """Return merged directory entries for *rel*, sorted by name.

        *rel* is a POSIX-style relative path (``""`` or ``"."`` for the
        merged root).  Entries present in both layers appear once, with
        ``layer='upper'``.  Returns an empty list if the merged path does
        not exist or is a file.
        """
        if os.path.isabs(rel) and not rel.startswith("/"):
            return []
        if rel.startswith("/"):
            rel = rel.lstrip("/")
        if rel == ".":
            rel = ""

        resolved = self._resolve(rel, self._upper, self._lower, is_top=True)
        if resolved is None or not os.path.isdir(resolved):
            return []
        return self._list_dir(rel)

    def exists(self, rel: str) -> bool:
        """Return ``True`` if *rel* exists in the merged view."""
        resolved = self._resolve(rel, self._upper, self._lower, is_top=True)
        return resolved is not None

    def is_dir(self, rel: str) -> bool:
        """Return ``True`` if *rel* resolves to a directory in the merged view."""
        resolved = self._resolve(rel, self._upper, self._lower, is_top=True)
        return resolved is not None and os.path.isdir(resolved)

    def is_file(self, rel: str) -> bool:
        """Return ``True`` if *rel* resolves to a regular file."""
        resolved = self._resolve(rel, self._upper, self._lower, is_top=True)
        return resolved is not None and os.path.isfile(resolved)

    def resolve(self, rel: str) -> Optional[str]:
        """Return the real on-disk path for *rel*, preferring upper.

        Returns ``None`` if *rel* does not exist in the merged view.
        """
        return self._resolve(rel, self._upper, self._lower, is_top=True)

    def walk(self, rel: str = "") -> Iterator[List[OverlayEntry]]:
        """Yield the merged entry-list of *rel* and every sub-directory.

        Each yielded value is the list that :meth:`listdir` would return for
        that directory.  Sub-directories are visited in sorted order for
        determinism.  Only directories that exist in the merged view are
        visited.
        """
        if os.path.isabs(rel) and not rel.startswith("/"):
            return
        if rel.startswith("/"):
            rel = rel.lstrip("/")
        if rel == ".":
            rel = ""

        stack = [rel]
        while stack:
            current = stack.pop(0)
            entries = self.listdir(current)
            if entries:
                yield entries
            for entry in entries:
                if entry.is_dir:
                    sub_rel = current + "/" + entry.name if current else entry.name
                    stack.append(sub_rel)
            stack.sort()
