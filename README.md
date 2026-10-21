# directory_overlay_mount

Presents two directory trees as a single read-only logical view, preferring the upper layer and hiding lower-layer files that are shadowed.

```python
import os
import tempfile

from directory_overlay_mount import OverlayView

lower = tempfile.mkdtemp(prefix="base_")
upper = tempfile.mkdtemp(prefix="overrides_")

view = OverlayView(lower=lower, upper=upper)

for entry in view.listdir("subdir"):
    print(entry.name, entry.layer, entry.is_dir, entry.path)

path = view.resolve("subdir/config.txt")  # returns the real on-disk path
print(view.is_dir("subdir"), view.is_file("subdir/config.txt"))

for batch in view.walk():
    for entry in batch:
        print(entry.layer, entry.path)
```

## Why this exists

When you have a read-only base tree (a shipped default, a golden image, a previous build) and a sparse override tree that should win on conflict, you need a merged view without copying files or mutating either side. This library gives you that view as plain Python objects so callers can decide how to open, copy, or serve the resolved paths.

The trade-off: it is purely logical. It resolves and lists paths; it does not open, read, or serve file contents. If you need a real FUSE mount or a WSGI file server, build that on top of the paths this returns.

## Edge cases worth knowing

- If the upper layer places a **file** where the lower layer has a **directory**, the upper file wins and the entire lower subtree at that name is hidden. This matches standard overlay-shadow semantics but can surprise you if you expected a merge.
- Trailing slashes and double slashes in paths are rejected (resolve returns `None`, listdir returns `[]`). Use clean relative paths.
- `..` components are rejected to prevent escaping the merged root.
- Directory listings are sorted by name for determinism.
