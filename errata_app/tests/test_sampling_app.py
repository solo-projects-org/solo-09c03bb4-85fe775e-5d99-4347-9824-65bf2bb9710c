"""抽测包网页全流程测试：页面入口、凑齐/凑不齐、JSON 下载门禁与表单校验。"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from errata_app.app import create_app
from errata_app import sampling
from errata_app import solver

GRID_A = [
    [1200, 340, 260],
    [560, 780, 410],
    [190, 620, 330],
]
GRID_B = [[0, 0], [0, 0]]


class SamplingWebTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.app = create_app(self.tmp.name)
        self.client = self.app.test_client()

    def tearDown(self):
        os.unlink(self.tmp.name)

    def _upload(self, grid, name="抽测真值表"):
        text = "\n".join(",".join(str(v) for v in row) for row in grid)
        resp = self.client.post(
            "/datasets/new",
            data={"name": name, "csv_text": text},
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)

    def _build_form(self, fields):
        return self.client.post("/sampling/build", data=fields)

    def test_builder_page_lists_datasets_and_limits(self):
        self._upload(GRID_A)
        resp = self.client.get("/sampling")
        body = resp.get_data(as_text=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("抽测包", body)
        self.assertIn("错一格", body)
        self.assertIn("错两格", body)
        self.assertIn('max="24"', body)
        self.assertIn('value="1"', body)  # dataset id checkbox value

    def test_fulfillable_html_flow_downloads_valid_json(self):
        self._upload(GRID_A, "A")
        self._upload(GRID_B, "B")
        resp = self._build_form({
            "table_ids": ["1", "2"],
            "one_1": "3", "two_1": "2",
            "one_2": "4", "two_2": "4",
        })
        self.assertEqual(resp.status_code, 302)
        location = resp.headers["Location"]
        self.assertIn("/sampling/packages/", location)

        page = self.client.get(location)
        body = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn("各档名额已全部凑齐", body)
        self.assertIn("/download", body)

        dl = self.client.get(location + "/download")
        self.assertEqual(dl.status_code, 200)
        self.assertIn("attachment", dl.headers["Content-Disposition"])
        self.assertTrue(dl.is_json)
        doc = dl.get_json()
        self.assertTrue(doc["complete"])
        self.assertEqual(doc["case_count"], 13)  # 3+2+4+4

        # 深度校验 JSON 中每一例
        seen = set()
        for case in doc["cases"]:
            ds_id = case["source_dataset_id"]
            truth = GRID_A if ds_id == 1 else GRID_B
            tier = case["error_count"]
            sim = case["simulated_recognition_grid"]
            truth = GRID_A if ds_id == 1 else GRID_B
            rows, cols = sampling.truth_totals(truth)
            self.assertEqual(case["row_totals"], rows)
            self.assertEqual(case["column_totals"], cols)
            self.assertEqual(len(case["error_cells"]), tier)
            for e in case["error_cells"]:
                i, j = e["row_index"], e["column_index"]
                self.assertEqual(e["truth_value"], truth[i][j])
                self.assertEqual(e["misrecognized_value"], sim[i][j])
                self.assertEqual(solver.digit_distance(
                    truth[i][j], sim[i][j]), 1)
            actual_diff = sum(
                1 for i in range(len(truth))
                for j in range(len(truth[0]))
                if sim[i][j] != truth[i][j])
            self.assertEqual(actual_diff, tier)
            key = sampling.table_key(sim)
            self.assertNotIn(key, seen)
            seen.add(key)
        self.assertEqual(len(seen), 13)

    def test_package_wide_dedup_with_duplicate_truth_tables(self):
        # 两份完全相同的 2x2 表，各要 12 份错一格：共 24 个不重复候选，应凑齐
        self._upload(GRID_B, "B1")
        self._upload(GRID_B, "B2")
        resp = self._build_form({
            "table_ids": ["1", "2"],
            "one_1": "12", "two_1": "0",
            "one_2": "12", "two_2": "0",
        })
        location = resp.headers["Location"]
        dl = self.client.get(location + "/download?format=json")
        self.assertEqual(dl.status_code, 200)
        doc = dl.get_json()
        self.assertTrue(doc["complete"])
        grids = [json.dumps(c["simulated_recognition_grid"])
                 for c in doc["cases"]]
        self.assertEqual(len(grids), 24)
        self.assertEqual(len(set(grids)), 24)

    def test_unfulfillable_shows_maxima_and_blocks_download(self):
        self._upload([[0]], "tiny")
        resp = self._build_form({
            "table_ids": ["1"],
            "one_1": "24", "two_1": "24",
        })
        self.assertEqual(resp.status_code, 302)
        location = resp.headers["Location"]

        page = self.client.get(location)
        body = page.get_data(as_text=True)
        self.assertIn("部分档位凑不齐", body)
        self.assertIn("名额限制内", body)
        # 错一格最多 9、错两格最多 0（<strong> 数字 </strong>）
        self.assertRegex(body, r"错一格[\s\S]{0,400}?<strong>9</strong>")
        self.assertRegex(body, r"错两格[\s\S]{0,400}?<strong>0</strong>")
        # 页面不提供下载按钮/链接
        self.assertNotIn("下载标准答案 JSON", body)

        # 浏览器方式直接访问下载 -> 重定向回预检页并报错
        dl = self.client.get(location + "/download")
        self.assertEqual(dl.status_code, 302)
        self.assertIn(location, dl.headers["Location"])

        # JSON 客户端 -> 409 + 各档报告
        dl_json = self.client.get(location + "/download?format=json")
        self.assertEqual(dl_json.status_code, 409)
        payload = dl_json.get_json()
        self.assertFalse(payload["ok"])
        self.assertEqual(len(payload["summary"]), 2)

    def test_build_format_json_returns_200_or_409(self):
        self._upload([[0]], "tiny")
        ok = self.client.post("/sampling/build?format=json", data={
            "one_1": "3", "two_1": "0"})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.mimetype, "application/json")
        self.assertTrue(ok.get_json()["complete"])

        bad = self.client.post("/sampling/build?format=json", data={
            "one_1": "24", "two_1": "24"})
        self.assertEqual(bad.status_code, 409)
        self.assertFalse(bad.get_json()["complete"])

    def test_json_body_is_supported(self):
        self._upload(GRID_A, "A")
        resp = self.client.post(
            "/sampling/build?format=json",
            json={"table_ids": [1], "one_1": 2, "two_1": 1},
        )
        self.assertEqual(resp.status_code, 200)
        doc = resp.get_json()
        self.assertTrue(doc["complete"])
        self.assertEqual(doc["case_count"], 3)

    def test_more_than_four_tables_rejected(self):
        for i in range(5):
            self._upload(GRID_B, f"t{i}")
        ids = [str(i) for i in range(1, 6)]
        resp = self._build_form({
            "table_ids": ids,
            **{f"one_{i}": "1" for i in ids},
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn("最多只能选 4 张", resp.get_data(as_text=True))

        resp_json = self.client.post("/sampling/build?format=json", json={
            "table_ids": [1, 2, 3, 4, 5],
            "one_1": 1, "one_2": 1, "one_3": 1, "one_4": 1, "one_5": 1,
        })
        self.assertEqual(resp_json.status_code, 400)
        self.assertFalse(resp_json.get_json()["ok"])

    def test_quota_out_of_range_rejected(self):
        self._upload(GRID_A)
        for bad in ("25", "-1", "1.5", "abc"):
            resp = self._build_form({"table_ids": ["1"], "one_1": bad})
            self.assertEqual(resp.status_code, 400, bad)
            self.assertIn("0..24", resp.get_data(as_text=True))

    def test_checked_but_zero_both_tiers_rejected(self):
        self._upload(GRID_A)
        resp = self._build_form(
            {"table_ids": ["1"], "one_1": "0", "two_1": "0"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("两个档位", resp.get_data(as_text=True))

    def test_positive_quota_implies_selection(self):
        self._upload(GRID_A)
        resp = self._build_form({"one_1": "2", "two_1": "0"})
        self.assertEqual(resp.status_code, 302)

    def test_empty_submission_rejected(self):
        self._upload(GRID_A)
        resp = self._build_form({"one_1": "0", "two_1": "0"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("至少", resp.get_data(as_text=True))

    def test_unknown_token_404(self):
        self.assertEqual(self.client.get("/sampling/packages/nope").status_code,
                         404)
        self.assertEqual(
            self.client.get("/sampling/packages/nope/download").status_code,
            404)

    def test_unknown_dataset_id_rejected(self):
        resp = self.client.post("/sampling/build?format=json",
                                json={"table_ids": [999], "one_999": 1})
        self.assertEqual(resp.status_code, 400)

    def test_4x4_dataset_can_package(self):
        import csv
        path = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "samples", "sample4x4.csv")
        with open(path, encoding="utf-8") as fh:
            grid = [[int(v) for v in row] for row in csv.reader(fh)]
        self._upload(grid, "4x4")
        resp = self.client.post("/sampling/build?format=json", data={
            "one_1": "24", "two_1": "24"})
        self.assertEqual(resp.status_code, 200)
        doc = resp.get_json()
        self.assertTrue(doc["complete"])
        self.assertEqual(doc["case_count"], 48)


if __name__ == "__main__":
    unittest.main(verbosity=2)
