"""地方志勘误凭据 Flask 应用。"""

from __future__ import annotations

import json
import os
import sqlite3

from flask import (Flask, abort, flash, redirect, render_template, request,
                   url_for)

from . import db as dbmod
from . import solver
from .validation import (MAX_CELL, ValidationError, parse_grid_csv,
                         parse_margin, parse_proposed)

DB_PATH = os.environ.get(
    "ERRATA_DB_PATH",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "data", "errata.db"),
)


def create_app(db_path: str | None = None) -> Flask:
    app = Flask(__name__)
    app.secret_key = os.environ.get("ERRATA_SECRET_KEY", "dev-secret-change-me")
    app.config["DB_PATH"] = db_path or DB_PATH

    def db() -> sqlite3.Connection:
        conn = dbmod.get_db(app.config["DB_PATH"])
        # 幂等建表：数据库文件被外部清空/替换后仍可自愈
        dbmod.init_db(conn)
        return conn

    @app.route("/")
    def index():
        conn = db()
        try:
            datasets = [dict(d) for d in dbmod.list_datasets(conn)]
            for d in datasets:
                d["grid"] = json.loads(d["grid_json"])
                d["voucher_count"] = conn.execute(
                    "SELECT COUNT(*) FROM vouchers WHERE dataset_id = ?",
                    (d["id"],),
                ).fetchone()[0]
            return render_template("index.html", datasets=datasets)
        finally:
            conn.close()

    @app.route("/datasets/new", methods=["POST"])
    def new_dataset():
        name = (request.form.get("name") or "").strip() or "未命名表"
        file = request.files.get("csv_file")
        raw_text = None
        if file is not None and file.filename:
            raw_text = file.read().decode("utf-8-sig", errors="replace")
        elif request.form.get("csv_text", "").strip():
            raw_text = request.form["csv_text"]
        conn = db()
        try:
            try:
                grid = parse_grid_csv(raw_text or "")
            except ValidationError as exc:
                flash(str(exc), "error")
                return redirect(url_for("index"))
            dataset_id = dbmod.create_dataset(conn, name[:80], grid)
            flash(f"已创建数据集 #{dataset_id}，请录入原书核准的行列合计。", "ok")
            return redirect(url_for("dataset_detail", dataset_id=dataset_id))
        finally:
            conn.close()

    @app.route("/datasets/<int:dataset_id>")
    def dataset_detail(dataset_id: int):
        conn = db()
        try:
            row = dbmod.get_dataset(conn, dataset_id)
            if row is None:
                abort(404)
            grid = json.loads(row["grid_json"])
            margins = [dict(m) for m in dbmod.get_margins(conn, dataset_id)]
            # 预填 OCR 表自带的行列和，编辑可直接改成原书核准值
            ocr_margins = [sum(r_) for r_ in grid] + [
                sum(grid[i][j] for i in range(len(grid))) for j in range(len(grid[0]))
            ]
            vouchers = []
            for v in dbmod.list_vouchers(conn, dataset_id):
                vd = dict(v)
                vd["result"] = json.loads(v["result_json"])
                vouchers.append(vd)
            return render_template(
                "dataset.html",
                d=dict(row), grid=grid, margins=margins,
                ocr_margins=ocr_margins, vouchers=vouchers,
                max_cell=MAX_CELL,
            )
        finally:
            conn.close()

    @app.route("/datasets/<int:dataset_id>/margins", methods=["POST"])
    def edit_margins(dataset_id: int):
        conn = db()
        try:
            row = dbmod.get_dataset(conn, dataset_id)
            if row is None:
                abort(404)
            r, c = row["rows_n"], row["cols_n"]
            try:
                values = {}
                for k in range(r + c):
                    values[k] = parse_margin(request.form.get(f"margin_{k}", ""))
            except ValidationError as exc:
                flash(str(exc), "error")
                return redirect(url_for("dataset_detail", dataset_id=dataset_id))
            outcome = dbmod.update_margins(conn, dataset_id, values)
            if outcome["changed"]:
                flash(
                    f"已更新 {len(outcome['changed'])} 条合计。", "ok"
                )
            else:
                flash("合计没有变化。", "ok")
            if outcome["invalidated_voucher_ids"]:
                ids = "、".join(f"#{i}" for i in outcome["invalidated_voucher_ids"])
                flash(
                    f"被引用合计发生修改，凭据 {ids} 已立即标记为失效；"
                    "未被引用合计的更正不影响其他凭据。",
                    "warn",
                )
            return redirect(url_for("dataset_detail", dataset_id=dataset_id))
        finally:
            conn.close()

    @app.route("/datasets/<int:dataset_id>/analyze", methods=["POST"])
    def analyze(dataset_id: int):
        conn = db()
        try:
            row = dbmod.get_dataset(conn, dataset_id)
            if row is None:
                abort(404)
            grid = json.loads(row["grid_json"])
            r, c = row["rows_n"], row["cols_n"]
            try:
                tr = int(request.form.get("target_row", "")) - 1
                tc = int(request.form.get("target_col", "")) - 1
                if not (0 <= tr < r and 0 <= tc < c):
                    raise ValidationError("目标格下标越界。")
                proposed = parse_proposed(request.form.get("proposed", ""))
            except (ValueError, ValidationError) as exc:
                msg = str(exc) if isinstance(exc, ValidationError) else "目标格坐标必须是整数。"
                flash(msg, "error")
                return redirect(url_for("dataset_detail", dataset_id=dataset_id))

            margin_rows = dbmod.get_margins(conn, dataset_id)
            margins = [None] * (r + c)
            for m in margin_rows:
                margins[m["entry"]] = m["value"]

            result = solver.analyze(grid, margins, tr, tc, proposed)
            voucher_id = None
            if result["status"] == "proven":
                voucher_id = dbmod.save_voucher(
                    conn, dataset_id, tr, tc,
                    grid[tr][tc], proposed, result,
                )
            return render_template(
                "result.html",
                d=dict(row), grid=grid, tr=tr, tc=tc, proposed=proposed,
                result=result, voucher_id=voucher_id,
            )
        finally:
            conn.close()

    @app.route("/vouchers/<int:voucher_id>")
    def voucher_detail(voucher_id: int):
        conn = db()
        try:
            v = dbmod.get_voucher(conn, voucher_id)
            if v is None:
                abort(404)
            d = dbmod.get_dataset(conn, v["dataset_id"])
            citations = [dict(x) for x in dbmod.get_voucher_citations(conn, voucher_id)]
            result = json.loads(v["result_json"])
            grid = json.loads(d["grid_json"])
            stale = [
                cit for cit in citations
                if cit["current_value"] != cit["value_at_cite"]
            ]
            return render_template(
                "voucher.html", v=dict(v), d=dict(d), citations=citations,
                result=result, grid=grid, stale=stale,
            )
        finally:
            conn.close()

    @app.route("/vouchers")
    def voucher_list():
        conn = db()
        try:
            items = []
            for v in dbmod.list_vouchers(conn):
                vd = dict(v)
                d = dbmod.get_dataset(conn, v["dataset_id"])
                vd["dataset_name"] = d["name"] if d else "(已删除)"
                vd["result"] = json.loads(v["result_json"])
                items.append(vd)
            return render_template("vouchers.html", vouchers=items)
        finally:
            conn.close()

    @app.route("/datasets/<int:dataset_id>/delete", methods=["POST"])
    def delete_dataset(dataset_id: int):
        conn = db()
        try:
            if dbmod.get_dataset(conn, dataset_id) is None:
                abort(404)
            conn.execute("DELETE FROM datasets WHERE id = ?", (dataset_id,))
            conn.commit()
            flash(f"数据集 #{dataset_id} 及其凭据已删除。", "ok")
            return redirect(url_for("index"))
        finally:
            conn.close()

    @app.route("/samples/load", methods=["POST"])
    def load_sample():
        name = request.form.get("sample", "sample3x3")
        path = os.path.join(os.path.dirname(__file__), "samples", f"{name}.csv")
        if not os.path.isfile(path):
            abort(404)
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        try:
            grid = parse_grid_csv(text)
        except ValidationError as exc:
            flash(str(exc), "error")
            return redirect(url_for("index"))
        conn = db()
        try:
            dataset_id = dbmod.create_dataset(conn, f"样例：{name}", grid)
            flash(f"已载入样例数据集 #{dataset_id}。", "ok")
            return redirect(url_for("dataset_detail", dataset_id=dataset_id))
        finally:
            conn.close()

    with app.app_context():
        conn = db()
        try:
            dbmod.init_db(conn)
        finally:
            conn.close()
    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
