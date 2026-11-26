"""抽测包生成器单元测试：容量、误识规则、整包去重、名额上限与内存约束。"""

import csv
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from errata_app import sampling
from errata_app import solver

SAMPLE_3X3 = [
    [1200, 340, 260],
    [560, 780, 410],
    [190, 620, 330],
]


def _sample_4x4():
    path = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "samples", "sample4x4.csv")
    with open(path, encoding="utf-8") as fh:
        return [[int(v) for v in row] for row in csv.reader(fh)]


class TestCapacity(unittest.TestCase):
    def test_neighbor_count_matches_enumeration(self):
        for v in [0, 1, 5, 9, 10, 12, 99, 100, 500, 1234, 9999]:
            self.assertEqual(
                sampling.neighbor_count(v),
                len(solver.digit_neighbors(v)), v)

    def test_tiny_grids_capacity(self):
        self.assertEqual(sampling.tier_capacity([[0]], 1), 9)
        self.assertEqual(sampling.tier_capacity([[0]], 2), 0)
        # 1x2 单位数：每格 9 个邻值；两格组合 9*9
        self.assertEqual(sampling.tier_capacity([[0, 0]], 1), 18)
        self.assertEqual(sampling.tier_capacity([[0, 0]], 2), 81)
        # 500 有 26 个一位误识邻值
        self.assertEqual(sampling.tier_capacity([[500]], 1), 26)

    def test_stream_count_and_uniqueness_equals_capacity(self):
        cases = [
            [[0]], [[5]], [[0, 0]], [[1, 2]],
            [[1, 2], [3, 4]],
            [[12, 34, 56], [78, 90, 12]],
            SAMPLE_3X3,
        ]
        for grid in cases:
            for tier in (1, 2):
                items = list(sampling.mutation_stream(grid, tier))
                keys = [k for k, _ in items]
                self.assertEqual(len(items),
                                 sampling.tier_capacity(grid, tier),
                                 msg=(grid, tier))
                self.assertEqual(len(keys), len(set(keys)),
                                 msg=(grid, tier))


class TestMutationRule(unittest.TestCase):
    def test_every_candidate_is_exact_tier_one_digit_errors(self):
        grid = [[12, 340], [5, 78]]
        for tier in (1, 2):
            for _key, errors in sampling.mutation_stream(grid, tier):
                self.assertEqual(len(errors), tier)
                changed = set()
                for i, j, original, nv in errors:
                    self.assertEqual(grid[i][j], original)
                    self.assertNotEqual(original, nv)
                    self.assertEqual(
                        solver.digit_distance(original, nv), 1)
                    changed.add((i, j))
                self.assertEqual(len(changed), tier)

    def test_key_includes_dimensions(self):
        # [[1]] 的压缩签名与 [[0,1]] 相同（都是 1），必须靠尺寸区分
        k1 = sampling.table_key([[1]])
        k2 = sampling.table_key([[0, 1]])
        self.assertEqual(k1[2], k2[2])
        self.assertNotEqual(k1, k2)


class TestAllocator(unittest.TestCase):
    def test_greedy_trap_is_resolved_by_augmentation(self):
        # A={x,y} 要 1；B={x,z} 要 2。朴素先到先得会让 B 失败，
        # 增广分配应让 A 改取 y、B 取 x,z，全部凑齐。
        slots = [
            {"demand": 1, "candidates": lambda: iter(
                [("x", None), ("y", None)])},
            {"demand": 2, "candidates": lambda: iter(
                [("x", None), ("z", None)])},
        ]
        results, total = sampling.allocate_slots(slots)
        self.assertEqual(total, 3)
        self.assertEqual(sorted(k for r in results for k, _ in r),
                         ["x", "y", "z"])
        self.assertEqual([k for k, _ in results[0]], ["y"])
        self.assertEqual(sorted(k for k, _ in results[1]), ["x", "z"])

    def test_round_robin_coverage_for_identical_tiny_tables(self):
        # 4 张相同 1x1 零表抢 9 个错一格候选：每档都必须覆盖到
        slots = [{
            "demand": 24,
            "candidates": sampling.CachingCandidates([[0]], 1),
        } for _ in range(4)]
        results, total = sampling.allocate_slots(slots)
        counts = [len(r) for r in results]
        self.assertEqual(total, 9)
        self.assertEqual(sum(counts), 9)
        self.assertTrue(all(c >= 1 for c in counts), counts)
        keys = [k for r in results for k, _ in r]
        self.assertEqual(len(keys), len(set(keys)))


class TestBuildPackage(unittest.TestCase):
    def test_happy_path_schema_and_semantics(self):
        doc = sampling.build_package([
            {"dataset_id": 7, "name": "真值A", "grid": SAMPLE_3X3,
             "quotas": {1: 3, 2: 4}},
        ])
        self.assertTrue(doc["complete"])
        self.assertEqual(doc["case_count"], 7)
        self.assertEqual(doc["total_quota"], 7)
        # summary 两档
        s1, s2 = doc["summary"]
        self.assertEqual((s1["tier"], s1["quota"], s1["generated"]),
                         (1, 3, 3))
        self.assertEqual((s2["tier"], s2["quota"], s2["generated"]),
                         (2, 4, 4))

        seen = set()
        for no, case in enumerate(doc["cases"], start=1):
            self.assertEqual(case["case_no"], no)
            self.assertEqual(case["source_dataset_id"], 7)
            self.assertEqual(case["source_table_no"], 7)
            self.assertEqual(case["来源表号"], 7)
            tier = case["error_count"]
            self.assertEqual(len(case["error_cells"]), tier)
            sim = case["simulated_recognition_grid"]
            truth = case["truth_grid"]
            # 尺寸一致
            self.assertEqual(len(sim), 3)
            self.assertEqual(len(sim[0]), 3)
            # 整包去重
            key = sampling.table_key(sim)
            self.assertNotIn(key, seen)
            seen.add(key)
            # 合计来自真值
            rows, cols = sampling.truth_totals(truth)
            self.assertEqual(case["row_totals"], rows)
            self.assertEqual(case["column_totals"], cols)
            self.assertEqual(case["行合计"], rows)
            self.assertEqual(case["列合计"], cols)
            # 错格坐标/原值/误识值自洽
            diffs = 0
            for e in case["error_cells"]:
                i, j = e["row_index"], e["column_index"]
                self.assertEqual(e["row"], i + 1)
                self.assertEqual(e["column"], j + 1)
                self.assertEqual(e["坐标"], [i + 1, j + 1])
                self.assertEqual(truth[i][j], e["original_value"])
                self.assertEqual(truth[i][j], e["原值"])
                self.assertEqual(sim[i][j], e["misrecognized_value"])
                self.assertEqual(sim[i][j], e["误识值"])
                self.assertEqual(
                    solver.digit_distance(truth[i][j], sim[i][j]), 1)
            for i in range(3):
                for j in range(3):
                    if sim[i][j] != truth[i][j]:
                        diffs += 1
            self.assertEqual(diffs, tier)

    def test_package_wide_dedup_across_identical_tables(self):
        reqs = [
            {"dataset_id": 1, "name": "a", "grid": [[0, 0], [0, 0]],
             "quotas": {1: 12, 2: 0}},
            {"dataset_id": 2, "name": "a-copy", "grid": [[0, 0], [0, 0]],
             "quotas": {1: 12, 2: 0}},
        ]
        doc = sampling.build_package(reqs)
        self.assertTrue(doc["complete"])
        keys = [sampling.table_key(c["simulated_recognition_grid"])
                for c in doc["cases"]]
        self.assertEqual(len(keys), 24)
        self.assertEqual(len(set(keys)), 24)

    def test_unfulfillable_reports_maxima(self):
        doc = sampling.build_package([
            {"dataset_id": 9, "name": "one-zero", "grid": [[0]],
             "quotas": {1: 24, 2: 24}},
        ])
        self.assertFalse(doc["complete"])
        self.assertEqual(doc["case_count"], 9)
        by_tier = {s["tier"]: s for s in doc["summary"]}
        one = by_tier[1]
        self.assertEqual(one["capacity"], 9)
        self.assertEqual(one["max_within_quota"], 9)
        self.assertEqual(one["generated"], 9)
        self.assertFalse(one["complete"])
        two = by_tier[2]
        self.assertEqual(two["capacity"], 0)
        self.assertEqual(two["max_within_quota"], 0)
        self.assertEqual(two["generated"], 0)
        self.assertFalse(two["complete"])

    def test_zero_quota_slots_trivially_complete(self):
        doc = sampling.build_package([
            {"dataset_id": 1, "name": "g", "grid": SAMPLE_3X3,
             "quotas": {1: 2, 2: 0}},
        ])
        self.assertTrue(doc["complete"])
        self.assertEqual(doc["case_count"], 2)
        tiers = {s["tier"]: s for s in doc["summary"]}
        self.assertTrue(tiers[2]["complete"])
        self.assertEqual(tiers[2]["generated"], 0)

    def test_retained_cases_never_exceed_quota_on_4x4(self):
        grid = _sample_4x4()
        self.assertEqual((len(grid), len(grid[0])), (4, 4))
        t0 = time.perf_counter()
        doc = sampling.build_package([
            {"dataset_id": 1, "name": "4x4", "grid": grid,
             "quotas": {1: 24, 2: 24}},
        ])
        elapsed = time.perf_counter() - t0
        self.assertTrue(doc["complete"])
        self.assertLessEqual(elapsed, 10.0, "4x4 组包耗时过长")
        self.assertEqual(doc["case_count"], 48)
        self.assertLessEqual(doc["memory"]["retained_cases"],
                             doc["memory"]["bound_by_total_quota"])
        self.assertEqual(doc["memory"]["retained_cases"], 48)
        # 只流式枚举了极小部分候选，绝不是 14.7 万张全量
        self.assertLess(doc["memory"]["candidates_generated"], 200)
        keys = [sampling.table_key(c["simulated_recognition_grid"])
                for c in doc["cases"]]
        self.assertEqual(len(set(keys)), 48)

    def test_four_4x4_tables_worst_case_bounded_and_fast(self):
        grid = _sample_4x4()
        reqs = [{
            "dataset_id": i + 1, "name": f"4x4-{i}", "grid": grid,
            "quotas": {1: 24, 2: 24},
        } for i in range(4)]
        t0 = time.perf_counter()
        doc = sampling.build_package(reqs)
        elapsed = time.perf_counter() - t0
        self.assertTrue(doc["complete"])
        self.assertEqual(doc["case_count"], 192)
        self.assertLessEqual(elapsed, 10.0)
        keys = [sampling.table_key(c["simulated_recognition_grid"])
                for c in doc["cases"]]
        self.assertEqual(len(set(keys)), 192)


if __name__ == "__main__":
    unittest.main(verbosity=2)
