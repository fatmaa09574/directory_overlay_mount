import os
import shutil
import tempfile
import unittest

from directory_overlay_mount import OverlayView


class _TempTree:
    """Create and tear down a temporary pair of directories."""

    def __init__(self):
        self.tmp = tempfile.mkdtemp(prefix="overlay_test_")
        self.lower = os.path.join(self.tmp, "lower")
        self.upper = os.path.join(self.tmp, "upper")
        os.makedirs(self.lower)
        os.makedirs(self.upper)

    def mkdir(self, layer, *parts):
        root = self.lower if layer == "lower" else self.upper
        path = os.path.join(root, *parts)
        os.makedirs(path, exist_ok=True)
        return path

    def write(self, layer, *parts_and_content):
        *parts, content = parts_and_content
        root = self.lower if layer == "lower" else self.upper
        path = os.path.join(root, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
        return path

    def cleanup(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestOverlayViewConstruction(unittest.TestCase):
    def test_rejects_nonexistent_lower(self):
        with self.assertRaises(ValueError):
            OverlayView(lower="/nonexistent/lower", upper=tempfile.gettempdir())

    def test_rejects_nonexistent_upper(self):
        with self.assertRaises(ValueError):
            OverlayView(lower=tempfile.gettempdir(), upper="/nonexistent/upper")

    def test_lower_must_be_directory_not_file(self):
        t = _TempTree()
        try:
            f = os.path.join(t.tmp, "file.txt")
            with open(f, "w") as fh:
                fh.write("x")
            with self.assertRaises(ValueError):
                OverlayView(lower=f, upper=t.upper)
        finally:
            t.cleanup()


class TestOverlayListing(unittest.TestCase):
    def setUp(self):
        self.t = _TempTree()

    def tearDown(self):
        self.t.cleanup()

    def test_lower_only_entries(self):
        self.t.write("lower", "a.txt", "lower-a")
        view = OverlayView(self.t.lower, self.t.upper)
        entries = view.listdir()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].name, "a.txt")
        self.assertEqual(entries[0].layer, "lower")
        self.assertFalse(entries[0].is_dir)

    def test_upper_only_entries(self):
        self.t.write("upper", "b.txt", "upper-b")
        view = OverlayView(self.t.lower, self.t.upper)
        entries = view.listdir()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].name, "b.txt")
        self.assertEqual(entries[0].layer, "upper")

    def test_upper_shadows_lower_file(self):
        self.t.write("lower", "shared.txt", "lower-content")
        self.t.write("upper", "shared.txt", "upper-content")
        view = OverlayView(self.t.lower, self.t.upper)
        entries = view.listdir()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].layer, "upper")
        with open(entries[0].path) as f:
            self.assertEqual(f.read(), "upper-content")

    def test_merge_directories(self):
        self.t.mkdir("lower", "sub")
        self.t.write("lower", "sub", "lower.txt", "x")
        self.t.mkdir("upper", "sub")
        self.t.write("upper", "sub", "upper.txt", "y")
        view = OverlayView(self.t.lower, self.t.upper)
        entries = view.listdir("sub")
        names = [e.name for e in entries]
        self.assertEqual(names, ["lower.txt", "upper.txt"])

    def test_upper_file_shadows_lower_directory(self):
        self.t.mkdir("lower", "data")
        self.t.write("lower", "data", "inside.txt", "x")
        self.t.write("upper", "data", "file-content")
        view = OverlayView(self.t.lower, self.t.upper)
        entries = view.listdir()
        self.assertEqual(len(entries), 1)
        self.assertFalse(entries[0].is_dir)
        self.assertEqual(entries[0].layer, "upper")

    def test_empty_root(self):
        view = OverlayView(self.t.lower, self.t.upper)
        self.assertEqual(view.listdir(), [])

    def test_entries_sorted_by_name(self):
        for name in ["z.txt", "a.txt", "m.txt"]:
            self.t.write("lower", name, name)
        view = OverlayView(self.t.lower, self.t.upper)
        names = [e.name for e in view.listdir()]
        self.assertEqual(names, ["a.txt", "m.txt", "z.txt"])

    def test_listdir_nonexistent_returns_empty(self):
        view = OverlayView(self.t.lower, self.t.upper)
        self.assertEqual(view.listdir("does-not-exist"), [])

    def test_listdir_on_file_returns_empty(self):
        self.t.write("upper", "file.txt", "x")
        view = OverlayView(self.t.lower, self.t.upper)
        self.assertEqual(view.listdir("file.txt"), [])


class TestOverlayResolve(unittest.TestCase):
    def setUp(self):
        self.t = _TempTree()

    def tearDown(self):
        self.t.cleanup()

    def test_resolve_prefers_upper(self):
        self.t.write("lower", "f.txt", "lower")
        self.t.write("upper", "f.txt", "upper")
        view = OverlayView(self.t.lower, self.t.upper)
        resolved = view.resolve("f.txt")
        self.assertEqual(resolved, os.path.join(self.t.upper, "f.txt"))

    def test_resolve_lower_only(self):
        self.t.write("lower", "f.txt", "lower")
        view = OverlayView(self.t.lower, self.t.upper)
        resolved = view.resolve("f.txt")
        self.assertEqual(resolved, os.path.join(self.t.lower, "f.txt"))

    def test_resolve_missing(self):
        view = OverlayView(self.t.lower, self.t.upper)
        self.assertIsNone(view.resolve("missing.txt"))

    def test_resolve_nested_prefers_upper(self):
        self.t.mkdir("lower", "sub")
        self.t.mkdir("upper", "sub")
        self.t.write("lower", "sub", "f.txt", "lower")
        self.t.write("upper", "sub", "f.txt", "upper")
        view = OverlayView(self.t.lower, self.t.upper)
        resolved = view.resolve("sub/f.txt")
        self.assertEqual(resolved, os.path.join(self.t.upper, "sub", "f.txt"))

    def test_resolve_into_lower_only_subdir(self):
        self.t.mkdir("lower", "onlylower")
        self.t.write("lower", "onlylower", "f.txt", "x")
        view = OverlayView(self.t.lower, self.t.upper)
        resolved = view.resolve("onlylower/f.txt")
        self.assertEqual(resolved, os.path.join(self.t.lower, "onlylower", "f.txt"))

    def test_resolve_dot_as_root(self):
        view = OverlayView(self.t.lower, self.t.upper)
        resolved = view.resolve(".")
        self.assertEqual(resolved, self.t.upper)

    def test_resolve_trailing_slash(self):
        self.t.mkdir("upper", "dir")
        view = OverlayView(self.t.lower, self.t.upper)
        resolved = view.resolve("dir/")
        self.assertIsNone(resolved)

    def test_resolve_double_slash(self):
        self.t.mkdir("upper", "dir")
        view = OverlayView(self.t.lower, self.t.upper)
        self.assertIsNone(view.resolve("dir//sub"))

    def test_resolve_dotdot_rejected(self):
        view = OverlayView(self.t.lower, self.t.upper)
        self.assertIsNone(view.resolve("../escape"))


class TestOverlayPredicates(unittest.TestCase):
    def setUp(self):
        self.t = _TempTree()

    def tearDown(self):
        self.t.cleanup()

    def test_exists_is_dir_is_file(self):
        self.t.mkdir("lower", "dir")
        self.t.write("lower", "dir", "file.txt", "x")
        self.t.write("upper", "top.txt", "y")
        view = OverlayView(self.t.lower, self.t.upper)

        self.assertTrue(view.exists("dir"))
        self.assertTrue(view.is_dir("dir"))
        self.assertFalse(view.is_file("dir"))

        self.assertTrue(view.exists("dir/file.txt"))
        self.assertFalse(view.is_dir("dir/file.txt"))
        self.assertTrue(view.is_file("dir/file.txt"))

        self.assertTrue(view.exists("top.txt"))
        self.assertTrue(view.is_file("top.txt"))

        self.assertFalse(view.exists("nope"))
        self.assertFalse(view.is_dir("nope"))
        self.assertFalse(view.is_file("nope"))

    def test_file_shadowing_directory_makes_is_dir_false(self):
        self.t.mkdir("lower", "shadow")
        self.t.write("upper", "shadow", "content")
        view = OverlayView(self.t.lower, self.t.upper)
        self.assertTrue(view.exists("shadow"))
        self.assertFalse(view.is_dir("shadow"))
        self.assertTrue(view.is_file("shadow"))


class TestOverlayWalk(unittest.TestCase):
    def setUp(self):
        self.t = _TempTree()

    def tearDown(self):
        self.t.cleanup()

    def test_walk_yields_each_directory_once(self):
        self.t.mkdir("lower", "sub")
        self.t.write("lower", "sub", "lower.txt", "x")
        self.t.mkdir("upper", "sub")
        self.t.write("upper", "sub", "upper.txt", "y")
        self.t.write("upper", "top.txt", "z")
        view = OverlayView(self.t.lower, self.t.upper)

        batches = list(view.walk())
        self.assertEqual(len(batches), 2)
        root_names = sorted(e.name for e in batches[0])
        self.assertEqual(root_names, ["sub", "top.txt"])
        sub_names = sorted(e.name for e in batches[1])
        self.assertEqual(sub_names, ["lower.txt", "upper.txt"])

    def test_walk_into_lower_only_subtree(self):
        self.t.mkdir("lower", "deeply", "nested")
        self.t.write("lower", "deeply", "nested", "leaf.txt", "x")
        view = OverlayView(self.t.lower, self.t.upper)
        batches = list(view.walk())
        self.assertEqual(len(batches), 3)
        self.assertEqual([e.name for e in batches[0]], ["deeply"])
        self.assertEqual([e.name for e in batches[1]], ["nested"])
        self.assertEqual([e.name for e in batches[2]], ["leaf.txt"])

    def test_walk_empty(self):
        view = OverlayView(self.t.lower, self.t.upper)
        self.assertEqual(list(view.walk()), [])

    def test_walk_upper_file_shadows_lower_dir(self):
        self.t.mkdir("lower", "data")
        self.t.write("lower", "data", "inside.txt", "x")
        self.t.write("upper", "data", "file-content")
        view = OverlayView(self.t.lower, self.t.upper)
        batches = list(view.walk())
        self.assertEqual(len(batches), 1)
        self.assertEqual([e.name for e in batches[0]], ["data"])
        self.assertFalse(batches[0][0].is_dir)


class TestOverlayProperties(unittest.TestCase):
    def test_lower_and_upper_are_absolute(self):
        t = _TempTree()
        try:
            view = OverlayView(t.lower, t.upper)
            self.assertTrue(os.path.isabs(view.lower))
            self.assertTrue(os.path.isabs(view.upper))
            self.assertEqual(view.lower, os.path.abspath(t.lower))
            self.assertEqual(view.upper, os.path.abspath(t.upper))
        finally:
            t.cleanup()


if __name__ == "__main__":
    unittest.main()
