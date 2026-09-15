from execution.audit import parse, status

PATCH = """diff --git a/pkg/core.py b/pkg/core.py
index 1dddacf..935ab63 100644
--- a/pkg/core.py
+++ b/pkg/core.py
@@ -10,7 +10,8 @@ def f(x):
     a = 1
-    return old_helper(x)
+    return new_helper(x, strict=True)
+    # trailing


 def g():
@@ -40,2 +41,4 @@ def h():
     pass
+def new_api(y):
+    return y
"""


def test_parse_patch_lines_and_identifiers():
    p = parse.parse_patch(PATCH)
    assert p.files == ["pkg/core.py"]
    assert p.removed_linenos["pkg/core.py"] == [11]
    assert p.added_linenos["pkg/core.py"] == [11, 12, 42, 43]
    assert p.insertion_anchor_linenos["pkg/core.py"] == [40]  # pure insertion after old line 40 ("pass")
    assert "new_helper" in p.added_identifiers() and "old_helper" in p.removed_identifiers()
    assert "new_api" in p.changed_def_names()


def test_is_test_path():
    assert parse.is_test_path("pandas/tests/dtypes/test_dtypes.py")
    assert parse.is_test_path("test/server_test.py")
    assert parse.is_test_path("tqdm/tests/tests_contrib.py")
    assert not parse.is_test_path("tqdm/contrib/__init__.py")
    assert not parse.is_test_path("pandas/_testing.py")


def test_categorize_api_surface_vs_runtime():
    p = parse.parse_patch(PATCH)
    assert parse.categorize("AttributeError", "module 'pkg.core' has no attribute 'new_api'", p, "pkg")[0] == "API_SURFACE_CRASH"
    assert parse.categorize("AttributeError", "'NoneType' object has no attribute 'shape'", p, "pkg")[0] == "RUNTIME_EXCEPTION"
    assert parse.categorize("TypeError", "new_helper() got an unexpected keyword argument 'strict'", p, "pkg")[0] == "API_SURFACE_CRASH"
    assert parse.categorize("TypeError", "unsupported operand type(s) for +: 'int' and 'str'", p, "pkg")[0] == "RUNTIME_EXCEPTION"
    assert parse.categorize("TypeError", "takes 2 positional arguments but 3 were given", p, "pkg")[0] == "UNRESOLVED_CRASH"
    assert parse.categorize("ImportError", "cannot import name 'new_api' from 'pkg.core'", p, "pkg")[0] == "API_SURFACE_CRASH"
    assert parse.categorize("ModuleNotFoundError", "No module named 'numpy'", p, "pkg")[0] == "IMPORT_ERROR"
    assert parse.categorize("ValueError", "bad", p, "pkg")[0] == "RUNTIME_EXCEPTION"
    assert parse.categorize("AssertionError", "assert 1 == 2", p, "pkg")[0] == "ASSERTION"
    assert parse.categorize("Failed", "DID NOT RAISE <class 'ValueError'>", p, "pkg")[0] == "ASSERTION"
    assert parse.categorize(None, "", p, "pkg")[0] == "UNKNOWN"


JUNIT = """<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite errors="1" failures="2" name="pytest" tests="4">
<testcase classname="t.test_a" name="test_assert"><failure message="AssertionError: assert 1 == 2">def test():
&gt;       assert 1 == 2
E       assert 1 == 2</failure></testcase>
<testcase classname="t.test_a" name="test_attr"><failure message="AttributeError: module 'pkg.core' has no attribute 'new_api'">x
E       AttributeError: module 'pkg.core' has no attribute 'new_api'</failure></testcase>
<testcase classname="" name="t.test_b"><error message="collection failure">ImportError while importing test module
E   ModuleNotFoundError: No module named 'numpy'</error></testcase>
<testcase classname="t.test_a" name="test_ok" time="0.1"/>
</testsuite></testsuites>"""


def test_parse_junit_types():
    j = parse.parse_junit(JUNIT)
    assert j["total"] == 4 and j["passed"] == 1
    types = [f.exc_type for f in j["failures"]]
    assert types == ["AssertionError", "AttributeError", "ModuleNotFoundError"]


def test_parse_junit_old_pytest_assert_message_without_type():
    xml = '<testsuite><testcase classname="a" name="b"><failure message="assert 3 == 4">E   assert 3 == 4</failure></testcase></testsuite>'
    assert parse.parse_junit(xml)["failures"][0].exc_type == "AssertionError"


def test_parse_junit_dotted_custom_exception():
    xml = ('<testsuite><testcase classname="a" name="b"><failure message="pandas._libs.tslibs.np_datetime.OutOfBoundsDatetime: '
           'Out of bounds">E   pandas._libs.tslibs.np_datetime.OutOfBoundsDatetime: Out of bounds</failure></testcase></testsuite>')
    f = parse.parse_junit(xml)["failures"][0]
    assert parse.short_type(f.exc_type) == "OutOfBoundsDatetime"


UNITTEST_OUT = """

======================================================================
FAIL: test_match_str (test.test_utils.TestUtil)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/x/test/test_utils.py", line 1076, in test_match_str
    self.assertFalse(match_str('is_live', {'is_live': False}))
AssertionError: True is not false

======================================================================
ERROR: test_other (test.test_utils.TestUtil)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/x/test/test_utils.py", line 10, in test_other
    foo()
KeyError: 'bar'

----------------------------------------------------------------------
Ran 2 tests in 0.004s

FAILED (failures=1, errors=1)
"""


def test_parse_unittest():
    u = parse.parse_unittest(UNITTEST_OUT)
    assert u["total"] == 2
    assert [(f.kind, parse.short_type(f.exc_type)) for f in u["failures"]] == [("unittest_fail", "AssertionError"),
                                                                                ("unittest_error", "KeyError")]


def _run(verdict, cats=()):
    return {"verdict": verdict, "commands": [{"verdict": verdict, "failures": [
        {"test": f"t{i}", "exc_type": c, "category": c, "message": ""} for i, c in enumerate(cats)]}]}


def test_status_reproduces_strict_and_crash_only():
    ok = {"setup_ok": True}
    s = status.determine_status({**ok, "runs": [_run("fail", ["ASSERTION"])] * 3}, {**ok, "runs": [_run("pass")] * 3})
    assert s["status"] == "REPRODUCES" and s["strict"]["strict_assertion_only"]
    s = status.determine_status({**ok, "runs": [_run("fail", ["API_SURFACE_CRASH"])] * 3}, {**ok, "runs": [_run("pass")] * 3})
    assert s["status"] == "REPRODUCES_CRASH_ONLY"
    s = status.determine_status({**ok, "runs": [_run("fail", ["RUNTIME_EXCEPTION"])] * 3}, {**ok, "runs": [_run("pass")] * 3})
    assert s["status"] == "REPRODUCES" and not s["strict"]["strict_assertion_only"]


def test_status_flaky_timeout_expected_setup():
    ok = {"setup_ok": True}
    fixed_pass = {**ok, "runs": [_run("pass")] * 3}
    s = status.determine_status({**ok, "runs": [_run("fail", ["ASSERTION"]), _run("pass"), _run("fail", ["ASSERTION"])]}, fixed_pass)
    assert s["status"] == "FLAKY"
    s = status.determine_status({**ok, "runs": [_run("timeout")] * 3}, fixed_pass)
    assert s["status"] == "TIMEOUT"
    s = status.determine_status({**ok, "runs": [_run("pass")] * 3}, fixed_pass)
    assert s["status"] == "FAILS_EXPECTED_BEHAVIOR" and s["reason"] == "buggy_passes"
    s = status.determine_status({"setup_ok": False, "setup_error": "env: boom", "runs": []}, fixed_pass)
    assert s["status"] == "FAILS_SETUP"
    s = status.determine_status({**ok, "runs": [_run("fail", ["IMPORT_ERROR"])] * 3},
                                {**ok, "runs": [_run("fail", ["IMPORT_ERROR"])] * 3})
    assert s["status"] == "FAILS_SETUP"


def test_command_verdict_pytest_timeout_and_not_found():
    p = parse.parse_patch(PATCH)
    v = status.command_verdict("pytest t.py::x", 124, 1200.5, 1200, "", None, p, "pkg")
    assert v["verdict"] == "timeout"
    v = status.command_verdict("pytest t.py::x", 4, 1.0, 1200, "ERROR: not found", None, p, "pkg")
    assert v["verdict"] == "not_run"
    v = status.command_verdict("pytest t.py::x", 1, 1.0, 1200, "", JUNIT, p, "pkg")
    assert v["verdict"] == "fail" and [f["category"] for f in v["failures"]] == ["ASSERTION", "API_SURFACE_CRASH", "IMPORT_ERROR"]


def test_marked_block_and_values():
    from execution.audit import steps
    m = "@@abc@@"
    out = f"noise\n{m} HEAD deadbeef\n{m} FREEZE_BEGIN\na==1\nb==2\n{m} FREEZE_END\n"
    assert steps.marked_value(out, m, "HEAD") == "deadbeef"
    assert steps.marked_block(out, m, "FREEZE") == "a==1\nb==2"
    assert steps.marked_block(out, m, "MISSING") is None


def test_launcher_of():
    from execution.audit import steps
    assert steps.launcher_of("python3 -m pytest tqdm/tests/tests_contrib.py::test_enumerate") == ("pytest", "python3 -m pytest")
    assert steps.launcher_of("pytest pandas/tests/x.py::T::t") == ("pytest", "pytest")
    assert steps.launcher_of("python -m unittest -q test.test_utils.TestUtil.test_match_str") == ("unittest", "python -m unittest")
    assert steps.launcher_of("tox tests/test_hooks.py") is None
