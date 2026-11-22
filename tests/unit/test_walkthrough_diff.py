"""Unit tests for pure-stdlib unified diff hunk parser and walkthrough chunk planner."""

from __future__ import annotations

from backend.app.domains.artifacts.walkthrough_diff import (
    ParsedWalkthroughChunk,
    build_default_walkthrough_plan,
    parse_unified_diff,
    validate_walkthrough_chunks,
)

SAMPLE_DIFF = """diff --git a/backend/app/main.py b/backend/app/main.py
index 1111111..2222222 100644
--- a/backend/app/main.py
+++ b/backend/app/main.py
@@ -10,4 +10,5 @@ def create_app():
     app = FastAPI()
+    app.state.ready = True
     return app
diff --git a/frontend/src/App.tsx b/frontend/src/App.tsx
new file mode 100644
index 0000000..3333333
--- /dev/null
+++ b/frontend/src/App.tsx
@@ -0,0 +1,2 @@
+export function App() {
+  return null;
+}
"""


def test_parse_unified_diff_extracts_hunks_and_first_changed_line_names() -> None:
    files = parse_unified_diff(SAMPLE_DIFF)
    assert len(files) == 2

    main_file = files[0]
    assert main_file.path == "backend/app/main.py"
    assert main_file.status == "modified"
    assert len(main_file.hunks) == 1
    assert main_file.hunks[0].name == "+11"

    app_file = files[1]
    assert app_file.path == "frontend/src/App.tsx"
    assert app_file.status == "added"
    assert app_file.hunks[0].name == "+1"


def test_build_and_validate_walkthrough_plan() -> None:
    files = parse_unified_diff(SAMPLE_DIFF)
    plan = build_default_walkthrough_plan(files, git_head="abc123")
    assert plan.git_head == "abc123"
    assert len(plan.chunks) >= 1
    assert validate_walkthrough_chunks(plan.chunks, files) == []

    bad = [
        ParsedWalkthroughChunk(
            id="bad-1",
            title="Nonexistent hunk",
            explanation="Invalid",
            file_hunks={"backend/app/main.py": ["+999"]},
        )
    ]
    errors = validate_walkthrough_chunks(bad, files)
    assert len(errors) == 1
    assert "+999" in errors[0]
