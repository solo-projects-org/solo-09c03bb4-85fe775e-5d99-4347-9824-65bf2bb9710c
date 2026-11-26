import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from errata_app.app import create_app
from errata_app import db as dbmod

TRUE_GRID = [
    [1200, 340, 260],
    [560, 780, 410],
    [190, 620, 330],
]
OCR_GRID = [row[:] for row in TRUE_GRID]
OCR_GRID[0][0] = 1300
TRUE_MARGINS = [sum(row) for row in TRUE_GRID] + [
    sum(TRUE_GRID[i][j] for i in range(3)) for j in range(3)
]


class WebFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.app = create_app(self.tmp.name)
        self.client = self.app.test_client()

    def tearDown(self):
        os.unlink(self.tmp.name)

    def _upload(self, grid):
        text = "\n".join(",".join(str(v) for v in row) for row in grid)
        return self.client.post(
            "/datasets/new",
            data={"name": "单元测试表", "csv_text": text},
            follow_redirects=True,
        )

    def _set_margins(self, dataset_id, margins):
        data = {f"margin_{k}": ("" if v is None else str(v))
                for k, v in enumerate(margins)}
        return self.client.post(
            f"/datasets/{dataset_id}/margins", data=data, follow_redirects=True)

    def test_upload_rejects_bad_csv(self):
        resp = self.client.post(
            "/datasets/new",
            data={"name": "bad", "csv_text": "1,-2\n3,4"},
            follow_redirects=True,
        )
        self.assertIn("不是非负整数", resp.get_data(as_text=True))

    def test_full_proof_flow_creates_voucher(self):
        self._upload(OCR_GRID)
        self._set_margins(1, TRUE_MARGINS)
        resp = self.client.post(
            "/datasets/1/analyze",
            data={"target_row": "1", "target_col": "1", "proposed": "1200"},
            follow_redirects=True,
        )
        body = resp.get_data(as_text=True)
        self.assertIn("证明成立", body)
        self.assertIn("凭据 #1", body)
        # 凭据列表与详情
        resp = self.client.get("/vouchers/1")
        body = resp.get_data(as_text=True)
        self.assertIn("有效", body)
        self.assertIn("1800", body)  # 引用快照值

    def test_uncited_margin_change_keeps_voucher_valid(self):
        self._upload(OCR_GRID)
        # 只录前 3 条，分析时再补录；为保证证明可生成，先录全部
        self._set_margins(1, TRUE_MARGINS)
        self.client.post(
            "/datasets/1/analyze",
            data={"target_row": "1", "target_col": "1", "proposed": "1200"},
        )
        with self.app.app_context():
            conn = dbmod.get_db(self.tmp.name)
            try:
                cited = {c["entry"] for c in
                         dbmod.get_voucher_citations(conn, 1)}
                uncited = [k for k in range(6) if k not in cited]
                self.assertTrue(uncited)
                target_entry = uncited[0]
                outcome = dbmod.update_margins(
                    conn, 1, {target_entry: TRUE_MARGINS[target_entry] + 1})
                self.assertIn(target_entry, outcome["changed"])
                self.assertEqual(outcome["invalidated_voucher_ids"], [])
                self.assertEqual(dbmod.get_voucher(conn, 1)["status"], "valid")
            finally:
                conn.close()

    def test_cited_margin_change_invalidates_voucher_immediately(self):
        self._upload(OCR_GRID)
        self._set_margins(1, TRUE_MARGINS)
        self.client.post(
            "/datasets/1/analyze",
            data={"target_row": "1", "target_col": "1", "proposed": "1200"},
        )
        with self.app.app_context():
            conn = dbmod.get_db(self.tmp.name)
            try:
                cited_entry = dbmod.get_voucher_citations(conn, 1)[0]["entry"]
                outcome = dbmod.update_margins(
                    conn, 1, {cited_entry: TRUE_MARGINS[cited_entry] + 5})
                self.assertEqual(outcome["invalidated_voucher_ids"], [1])
                v = dbmod.get_voucher(conn, 1)
                self.assertEqual(v["status"], "invalid")
                self.assertIsNotNone(v["invalidated_at"])
            finally:
                conn.close()
        # 页面展示失效与快照对比
        body = self.client.get("/vouchers/1").get_data(as_text=True)
        self.assertIn("已失效", body)
        self.assertIn("已被修改", body)

    def test_same_value_rewrite_does_not_invalidate(self):
        self._upload(OCR_GRID)
        self._set_margins(1, TRUE_MARGINS)
        self.client.post(
            "/datasets/1/analyze",
            data={"target_row": "1", "target_col": "1", "proposed": "1200"},
        )
        with self.app.app_context():
            conn = dbmod.get_db(self.tmp.name)
            try:
                cited_entry = dbmod.get_voucher_citations(conn, 1)[0]["entry"]
                outcome = dbmod.update_margins(
                    conn, 1, {cited_entry: TRUE_MARGINS[cited_entry]})
                self.assertEqual(outcome["changed"], [])
                self.assertEqual(dbmod.get_voucher(conn, 1)["status"], "valid")
            finally:
                conn.close()

    def test_counterexample_saves_nothing(self):
        self._upload(OCR_GRID)
        self._set_margins(1, [1800, None, None, 1950, None, None])
        resp = self.client.post(
            "/datasets/1/analyze",
            data={"target_row": "1", "target_col": "1", "proposed": "1200"},
        )
        self.assertIn("存在反例", resp.get_data(as_text=True))
        with self.app.app_context():
            conn = dbmod.get_db(self.tmp.name)
            try:
                self.assertEqual(dbmod.list_vouchers(conn), [])
            finally:
                conn.close()

    def test_contradiction_reported(self):
        self._upload(OCR_GRID)
        self._set_margins(1, [1800, 1750, 1140, 1900, 1740, 1000])
        resp = self.client.post(
            "/datasets/1/analyze",
            data={"target_row": "1", "target_col": "1", "proposed": "1200"},
        )
        self.assertIn("合计矛盾", resp.get_data(as_text=True))

    def test_sample_loader(self):
        for sample in ("sample3x3", "sample4x4"):
            resp = self.client.post(
                "/samples/load", data={"sample": sample}, follow_redirects=True)
            self.assertEqual(resp.status_code, 200)
            self.assertIn("OCR 识别表", resp.get_data(as_text=True))

    def test_voucher_persists_original_and_proposed(self):
        self._upload(OCR_GRID)
        self._set_margins(1, TRUE_MARGINS)
        self.client.post(
            "/datasets/1/analyze",
            data={"target_row": "1", "target_col": "1", "proposed": "1200"},
        )
        with self.app.app_context():
            conn = dbmod.get_db(self.tmp.name)
            try:
                v = dbmod.get_voucher(conn, 1)
                self.assertEqual(v["original_value"], 1300)
                self.assertEqual(v["proposed_value"], 1200)
                citations = dbmod.get_voucher_citations(conn, 1)
                self.assertTrue(citations)
                for c in citations:
                    self.assertEqual(c["value_at_cite"], c["current_value"])
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
