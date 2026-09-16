import ast
import textwrap

from execution.audit import classify
from execution.audit.parse import parse_patch

BUGGY = textwrap.dedent('''\
    """Module docstring."""
    import os

    LIMIT = 3


    class Thing:
        def a(self):
            return 1

        @property
        def b(self):
            return 2


    def outer(x):
        def inner(y):
            return y + 1
        return inner(x)
''')

FIXED = textwrap.dedent('''\
    """Module docstring."""
    import os
    import sys

    LIMIT = 4


    class Thing:
        def a(self):
            return 10

        @property
        def b(self):
            return 2


    def outer(x):
        def inner(y):
            return y + 2
        return inner(x)
''')

PATCH = """diff --git a/pkg/m.py b/pkg/m.py
--- a/pkg/m.py
+++ b/pkg/m.py
@@ -2,3 +2,4 @@
 import os
+import sys

-LIMIT = 3
+LIMIT = 4
@@ -8,2 +9,2 @@ class Thing:
     def a(self):
-        return 1
+        return 10
@@ -17,2 +18,2 @@ def outer(x):
     def inner(y):
-        return y + 1
+        return y + 2
diff --git a/pkg/tests/test_m.py b/pkg/tests/test_m.py
--- a/pkg/tests/test_m.py
+++ b/pkg/tests/test_m.py
@@ -1,1 +1,2 @@
 x = 1
+y = 2
"""


def test_patch_size_units_imports_ignored_module_level_counted_nested_innermost():
    ps = classify.patch_size(parse_patch(PATCH), {"pkg/m.py": BUGGY}, {"pkg/m.py": FIXED})
    assert ps["undetermined"] == []
    assert ps["files"] == ["pkg/m.py"]  # test file excluded
    assert ps["units"] == ["pkg/m.py::<module/class level>", "pkg/m.py::Thing.a", "pkg/m.py::outer.inner"]
    assert ps["passed"] is True  # 1 file, 3 units


def test_patch_size_snapshot_mismatch_is_undetermined():
    ps = classify.patch_size(parse_patch(PATCH), {"pkg/m.py": FIXED}, {"pkg/m.py": FIXED})
    assert ps["passed"] is None and ps["undetermined"]


def _scan(src: str, func: str = "test_x"):
    tree = ast.parse(textwrap.dedent(src))
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == func)
    return sorted({e["flag"] for e in classify.scan_target(classify.Target("t", "f.py", fn, tree, textwrap.dedent(src)))})


def test_scan_tmp_path_and_tempfile_and_ensure_clean_allowed():
    assert _scan("""
        import tempfile
        import pandas._testing as tm
        def test_x(tmp_path):
            p = tmp_path / "a.csv"
            open(p, "w").write("x")
            with tempfile.TemporaryDirectory() as d:
                open(d + "/b", "w")
            with tm.ensure_clean("c.csv") as path:
                df.to_csv(path)
    """) == []


def test_scan_external_write_and_read_flagged_repo_read_allowed():
    assert _scan("""
        import os
        def test_x():
            open("/etc/passwd").read()
            open("out.txt", "w").write("x")
    """) == ["EXTERNAL_PATH"]
    assert _scan("""
        import os
        import pandas as pd
        from io import StringIO
        def test_x(datapath):
            open(os.path.join(os.path.dirname(__file__), "data", "x.csv")).read()
            pd.read_csv(datapath("io", "data", "a.csv"))
            pd.read_csv(StringIO("a,b\\n1,2"))
    """) == []


def test_scan_string_replace_is_not_a_file_write():
    """Regression (tqdm/2 smoke): str.replace was flagged as pathlib Path.replace."""
    assert _scan("""
        def test_x(bar_format):
            s = bar_format.replace("{desc}: ", "")
            df = df.rename(columns={"a": "b"})
    """) == []


def test_scan_network_subprocess_clock_random():
    assert _scan("""
        import socket
        def test_x():
            s = socket.socket()
    """) == ["NETWORK"]
    assert _scan("""
        from urllib.request import urlopen
        def test_x():
            urlopen("http://example.com")
    """) == ["NETWORK"]
    assert _scan("""
        import pytest
        @pytest.mark.network
        def test_x():
            pass
    """) == ["NETWORK"]
    assert _scan("""
        import subprocess
        def test_x():
            subprocess.check_call(["ls"])
    """) == ["SUBPROCESS"]
    assert _scan("""
        from datetime import datetime
        import time
        def test_x():
            datetime.now(); time.time()
    """) == ["WALL_CLOCK_TZ"]
    assert _scan("""
        import numpy as np
        def test_x():
            np.random.randn(3)
    """) == ["UNSEEDED_RANDOM"]
    assert _scan("""
        import numpy as np
        def test_x():
            np.random.seed(0)
            np.random.randn(3)
    """) == []


def test_parse_test_ids_pytest_and_unittest():
    ids = classify.parse_test_ids(["pytest pandas/tests/a.py::TestX::test_y[int64]",
                                   "python3 -m pytest tqdm/tests/t.py::test_e",
                                   "python -m unittest -q test.test_utils.TestUtil.test_match_str"])
    assert ids[0]["file"] == "pandas/tests/a.py" and ids[0]["class"] == "TestX" and ids[0]["func"] == "test_y"
    assert ids[1]["class"] is None and ids[1]["func"] == "test_e"
    u = classify.resolve_unittest_id(ids[2], {"test/test_utils.py": "x"})
    assert (u["file"], u["class"], u["func"]) == ("test/test_utils.py", "TestUtil", "test_match_str")
