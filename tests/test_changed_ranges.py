from __future__ import annotations

import unittest

from scripts.changed_ranges import ranges, render

DIFF = """diff --git a/src/a.ts b/src/a.ts
index 1..2 100644
--- a/src/a.ts
+++ b/src/a.ts
@@ -3,0 +4,2 @@ export function a() {
+  const x = 1
+  const y = 2
@@ -10 +12 @@
-old
+new
@@ -20,3 +21,0 @@
-gone
-gone
-gone
diff --git a/src/gone.ts b/src/gone.ts
deleted file mode 100644
--- a/src/gone.ts
+++ /dev/null
@@ -1,2 +0,0 @@
-a
-b
diff --git a/src/b.vue b/src/b.vue
--- a/src/b.vue
+++ b/src/b.vue
@@ -1,0 +1,3 @@
+<template>
+  <p />
+</template>
"""


class ReadingADiff(unittest.TestCase):
    def test_each_hunk_that_adds_lines_is_a_range_of_its_new_lines(self) -> None:
        self.assertEqual(ranges(DIFF)["src/a.ts"], [(4, 5), (12, 12)])

    def test_a_hunk_that_only_removes_lines_is_no_range(self) -> None:
        self.assertNotIn((21, 20), ranges(DIFF)["src/a.ts"])

    def test_a_deleted_file_has_no_ranges(self) -> None:
        self.assertNotIn("src/gone.ts", ranges(DIFF))

    def test_every_changed_file_is_read(self) -> None:
        self.assertEqual(ranges(DIFF)["src/b.vue"], [(1, 3)])

    def test_an_empty_diff_changes_nothing(self) -> None:
        self.assertEqual(ranges(""), {})


class Rendering(unittest.TestCase):
    def test_each_file_is_a_line_of_its_stryker_ranges_in_path_order(self) -> None:
        self.assertEqual(
            render(ranges(DIFF)),
            "src/a.ts\tsrc/a.ts:4-5,src/a.ts:12-12\nsrc/b.vue\tsrc/b.vue:1-3\n",
        )


if __name__ == "__main__":
    unittest.main()
