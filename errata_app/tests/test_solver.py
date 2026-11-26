import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from errata_app import solver
from errata_app.validation import (MAX_CELL, ValidationError, parse_grid_csv,
                                   parse_margin, parse_proposed)


class TestDigitNeighbors(unittest.TestCase):
    def test_zero(self):
        self.assertEqual(solver.digit_neighbors(0), list(range(1, 10)))

    def test_single_digit(self):
        self.assertEqual(sorted(solver.digit_neighbors(5)),
                         [0, 1, 2, 3, 4, 6, 7, 8, 9])

    def test_multi_digit_keeps_length(self):
        nbrs = solver.digit_neighbors(123)
        self.assertNotIn(123, nbrs)
        for v in nbrs:
            self.assertEqual(len(str(v)), 3)
        self.assertIn(103, nbrs)
        self.assertIn(120, nbrs)
        # 首位不能变 0
        self.assertFalse(any(len(str(v)) < 3 for v in nbrs))

    def test_distance(self):
        self.assertEqual(solver.digit_distance(1300, 1200), 1)
        self.assertIsNone(solver.digit_distance(99, 100))
        self.assertEqual(solver.digit_distance(1234, 1234), 0)
        self.assertEqual(solver.digit_distance(10, 23), 2)


class TestCandidates(unittest.TestCase):
    def test_1x1_candidate_count(self):
        cands = solver.enumerate_candidates([[500]])
        # 500 自身 + 三位各 9 个邻值（首位排除 0，后两位各 9）
        self.assertEqual(len(cands), 1 + 8 + 9 + 9)
        self.assertEqual(cands[0], ((500,),))

    def test_candidates_respect_bounds_and_model(self):
        grid = [[12, 34], [56, 78]]
        cands = solver.enumerate_candidates(grid)
        for table in cands:
            changed = [(i, j) for i in range(2) for j in range(2)
                       if table[i][j] != grid[i][j]]
            self.assertLessEqual(len(changed), 2)
            for i, j in changed:
                dist = solver.digit_distance(grid[i][j], table[i][j])
                self.assertEqual(dist, 1)
                self.assertLessEqual(table[i][j], MAX_CELL)

    def test_pack_roundtrip(self):
        table = ((1234, 0, 7), (9999, 10, 42))
        sig = solver._pack(table)
        self.assertEqual(solver._unpack(sig, 2, 3), table)


class TestAnalyzeProven(unittest.TestCase):
    TRUE_GRID = [
        [1200, 340, 260],
        [560, 780, 410],
        [190, 620, 330],
    ]

    def setUp(self):
        self.ocr = [row[:] for row in self.TRUE_GRID]
        self.ocr[0][0] = 1300  # 一位误识
        r = c = 3
        self.margins = [sum(row) for row in self.TRUE_GRID] + [
            sum(self.TRUE_GRID[i][j] for i in range(r)) for j in range(c)
        ]

    def test_proven_minimal_subset(self):
        res = solver.analyze(self.ocr, self.margins, 0, 0, 1200)
        self.assertEqual(res["status"], "proven")
        self.assertEqual(res["subset_size"], 3)
        # 可行候选在目标格全部等于拟改值（无反例）
        cands = solver.enumerate_candidates(self.ocr)
        index = solver.build_margin_index(cands, 3, 3)
        feasible = set(range(len(cands)))
        for cited in res["cited_entries"]:
            feasible &= index[cited["entry"]][cited["value"]]
        self.assertTrue(feasible)
        self.assertTrue(all(cands[k][0][0] == 1200 for k in feasible))

    def test_minimality_no_two_entry_subset_works(self):
        res = solver.analyze(self.ocr, self.margins, 0, 0, 1200)
        cands = solver.enumerate_candidates(self.ocr)
        index = solver.build_margin_index(cands, 3, 3)
        from itertools import combinations
        entered = [k for k, v in enumerate(self.margins) if v is not None]
        for pair in combinations(entered, 2):
            feasible = set(range(len(cands)))
            for k in pair:
                feasible &= index[k][self.margins[k]]
            if feasible and any(cands[x][0][0] != 1200 for x in feasible):
                break
        else:
            self.fail("存在 2 条目子集即可证明，与最小性结论冲突")
        self.assertEqual(res["subset_size"], 3)

    def test_single_margin_proof_1x1(self):
        res = solver.analyze([[500]], [501, 501], 0, 0, 501)
        self.assertEqual(res["status"], "proven")
        self.assertEqual(res["subset_size"], 1)
        self.assertEqual(res["cited_entries"][0]["value"], 501)


class TestAnalyzeCounterexample(unittest.TestCase):
    def test_counterexample_with_two_margins(self):
        true_grid = [
            [1200, 340, 260],
            [560, 780, 410],
            [190, 620, 330],
        ]
        ocr = [row[:] for row in true_grid]
        ocr[0][0] = 1300
        res = solver.analyze(ocr, [1800, None, None, 1950, None, None], 0, 0, 1200)
        self.assertEqual(res["status"], "counterexample")
        self.assertNotEqual(res["counterexample"][0][0], 1200)
        # 反例必须真的满足所录入的合计
        self.assertEqual(sum(res["counterexample"][0]), 1800)
        self.assertEqual(sum(row[0] for row in res["counterexample"]), 1950)
        self.assertLessEqual(len(res["counterexample_error_cells"]), 2)
        for e in res["counterexample_error_cells"]:
            self.assertEqual(e["digit_errors"], 1)

    def test_no_margins_always_counterexample(self):
        res = solver.analyze([[10, 20]], [None, None, None], 0, 0, 10)
        self.assertEqual(res["status"], "counterexample")


class TestAnalyzeContradiction(unittest.TestCase):
    def setUp(self):
        self.ocr = [
            [1300, 340, 260],
            [560, 780, 410],
            [190, 620, 330],
        ]

    def test_row_col_totals_unequal(self):
        res = solver.analyze(self.ocr,
                             [1800, 1750, 1140, 1900, 1740, 1000], 0, 0, 1200)
        self.assertEqual(res["status"], "contradiction")
        self.assertIn("不相等", res["reason"])

    def test_margin_above_cell_bound(self):
        res = solver.analyze([[500]], [10000, 10000], 0, 0, 501)
        self.assertEqual(res["status"], "contradiction")
        self.assertIn("0..9999", res["reason"])

    def test_model_external_but_bounded_feasible(self):
        ocr = [[100] * 3 for _ in range(3)]
        true = [row[:] for row in ocr]
        for (i, j) in [(0, 0), (0, 1), (1, 0), (2, 2)]:
            true[i][j] = 200
        margins = [sum(row) for row in true] + [
            sum(true[i][j] for i in range(3)) for j in range(3)]
        self.assertTrue(solver.margins_bounded_feasible(margins, 3, 3))
        res = solver.analyze(ocr, margins, 0, 0, 200)
        self.assertEqual(res["status"], "contradiction")
        self.assertIn("误识", res["reason"])


class TestBoundedFeasibility(unittest.TestCase):
    def test_cases(self):
        cases = [
            ([None] * 6, 3, 3, True),
            ([10, None, None, None], 2, 2, True),
            ([-1, 3, 2, 0], 2, 2, False),
            ([3, 7, 4, 6], 2, 2, True),
            ([None, None, 20000, None], 2, 2, True),
            ([None, None, 40000, None], 2, 2, False),
            ([20000, None, None], 1, 2, False),
            ([19998, None, None], 1, 2, True),
            ([1800, 1750, 1140, 1900, 1740, 1000], 3, 3, False),
            ([1800, 1750, 1140, 1950, 1740, 1000], 3, 3, True),
            ([0, None, 0, None], 2, 2, True),
            ([0, 0, 0, 0], 2, 2, True),
            ([5, None, 6, None], 2, 2, False),
            ([10, None, 6, 4], 2, 2, True),
            ([500, 500], 1, 1, True),
            ([500, 501], 1, 1, False),
            ([10000, 10000], 1, 1, False),
        ]
        for margins, r, c, expect in cases:
            self.assertEqual(
                solver.margins_bounded_feasible(margins, r, c), expect,
                msg=(margins, r, c))


class TestValidation(unittest.TestCase):
    def test_valid_csv(self):
        grid = parse_grid_csv("1,20\n300,4000\n")
        self.assertEqual(grid, [[1, 20], [300, 4000]])

    def test_rejects_bad_values(self):
        for text in ["1,-2\n3,4", "1.5,2\n", "a,2\n", "10000,2\n",
                     "1,2\n3\n", "", "\n\n"]:
            with self.assertRaises(ValidationError):
                parse_grid_csv(text)

    def test_rejects_oversize(self):
        big = "\n".join(",".join("1" for _ in range(5)) for _ in range(2))
        with self.assertRaises(ValidationError):
            parse_grid_csv(big)
        tall = "\n".join("1" for _ in range(5))
        with self.assertRaises(ValidationError):
            parse_grid_csv(tall)

    def test_margin_and_proposed_parsers(self):
        self.assertIsNone(parse_margin(""))
        self.assertEqual(parse_margin(" 123 "), 123)
        with self.assertRaises(ValidationError):
            parse_margin("-1")
        with self.assertRaises(ValidationError):
            parse_proposed("10000")
        self.assertEqual(parse_proposed("0"), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
