"""地方志勘误凭据 Flask 应用。"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid

from flask import (Flask, Response, abort, flash, jsonify, redirect,
                   render_template, request, url_for)

from . import db as dbmod
from . import sampling
from . import solver
from .validation import (MAX_CELL, ValidationError, parse_grid_csv,
                         parse_margin, parse_proposed)

DB_PATH = os.environ.get(
    "ERRATA_DB_PATH",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "data", "errata.db"),
)


def _list_datasets_asc(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM datasets ORDER BY id ASC").fetchall()]


def _with_capacities(datasets: list[dict]) -> list[dict]:
    """为配置页/错误重渲染补上每档规则上限（不做候选枚举）。"""
    for d in datasets:
        grid = json.loads(d["grid_json"])
        d["cap_one"] = sampling.tier_capacity(grid, 1)
        d["cap_two"] = sampling.tier_capacity(grid, 2)
        d["max_one"] = min(sampling.TIER_MAX, d["cap_one"])
        d["max_two"] = min(sampling.TIER_MAX, d["cap_two"])
    return datasets


def _parse_quota(raw, dataset_id: int, tier_label: str) -> int:
    text = ("" if raw is None else str(raw)).strip()
    if text == "":
        return 0
    if not text.isascii() or not text.isdigit():
        raise ValidationError(
            f"数据表 #{dataset_id} 的“{tier_label}”名额“{text}”"
            "不是 0..24 的非负整数。")
    value = int(text)
    if not (sampling.TIER_MIN <= value <= sampling.TIER_MAX):
        raise ValidationError(
            f"数据表 #{dataset_id} 的“{tier_label}”名额为 {value}，"
            f"每档只能填 {sampling.TIER_MIN}..{sampling.TIER_MAX} 份。")
    return value


class _JSONInput:
    """把 JSON 请求体适配成 parser 需要的 ``get`` / ``getlist`` 接口。"""

    def __init__(self, data: dict):
        self._data = data

    def get(self, key, default=None):
        value = self._data.get(key, default)
        if value is None:
            return default
        return str(value)

    def getlist(self, key):
        value = self._data.get(key, [])
        if value is None:
            return []
        if isinstance(value, list):
            return [str(v) for v in value]
        return [str(value)]


def _parse_sampling_form(form, datasets_by_id: dict):
    """解析抽测包表单，返回 ``[(dataset_id, {1: q1, 2: q2}), ...]``。

    选择规则（对网页与直接 POST 的接口调用都尽量宽容）：

    - 勾选 ``table_ids`` 的表必选；某档名额 >0 时即使漏勾选也自动纳入；
    - 勾选但两档都为 0 属于误操作，直接报错提示；
    - 最多 :data:`sampling.MAX_TABLES` 张，且至少有一个 >0 的名额。
    """
    checked_raw = form.getlist("table_ids")
    checked: set[int] = set()
    for raw_id in checked_raw:
        try:
            did = int(raw_id)
        except (TypeError, ValueError):
            raise ValidationError(f"提交的数据表编号“{raw_id}”不是整数。")
        if did not in datasets_by_id:
            raise ValidationError(f"数据表 #{did} 不存在，可能已被删除。")
        checked.add(did)

    quotas_by_id: dict[int, dict[int, int]] = {}
    positive: set[int] = set()
    # 表单为每张现有表都带了 one_<id>/two_<id> 输入框
    for did, d in datasets_by_id.items():
        q1 = _parse_quota(form.get(f"one_{did}"), did, "错一格")
        q2 = _parse_quota(form.get(f"two_{did}"), did, "错两格")
        quotas_by_id[did] = {1: q1, 2: q2}
        if q1 or q2:
            positive.add(did)

    selected = set(checked) | positive
    for did in checked:
        if quotas_by_id[did][1] == 0 and quotas_by_id[did][2] == 0:
            raise ValidationError(
                f"数据表 #{did}（{datasets_by_id[did]['name']}）已勾选，"
                "但两个档位的名额都是 0；如不需要请取消勾选，"
                "或至少填写一个大于 0 的名额。")

    if not selected:
        raise ValidationError(
            "请勾选至少一张数据表，并填写至少一个大于 0 的名额。")
    if len(selected) > sampling.MAX_TABLES:
        raise ValidationError(
            f"最多只能选 {sampling.MAX_TABLES} 张数据表作为真值，"
            f"本次选了 {len(selected)} 张，请减少后再试。")

    ordered = sorted(selected)
    return [(did, quotas_by_id[did]) for did in ordered]


def _json_response(document: dict, token: str, status: int = 200,
                    attachment: bool = True) -> Response:
    body = json.dumps(document, ensure_ascii=False, indent=2)
    resp = Response(body, status=status,
                    mimetype="application/json; charset=utf-8")
    if attachment:
        resp.headers["Content-Disposition"] = (
            f'attachment; filename="errata_sampling_{token[:8]}.json"'
        )
    return resp


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

    # ------------------------------------------------------------------ #
    # 抽测包
    # ------------------------------------------------------------------ #
    @app.route("/sampling")
    def sampling_builder():
        conn = db()
        try:
            datasets = _with_capacities(_list_datasets_asc(conn))
            # 配置页直接给出每档“名额内最多可生成”的提示（组合公式，零枚举）
            return render_template(
                "sampling.html",
                datasets=datasets,
                max_tables=sampling.MAX_TABLES,
                tier_min=sampling.TIER_MIN,
                tier_max=sampling.TIER_MAX,
            )
        finally:
            conn.close()

    @app.route("/sampling/build", methods=["POST"])
    def sampling_build():
        json_body = request.get_json(silent=True)
        wants_json = (
            request.values.get("format") == "json"
            or request.is_json
            or request.accept_mimetypes.best == "application/json"
        ) and request.values.get("format") != "html"
        conn = db()
        try:
            datasets = _with_capacities(_list_datasets_asc(conn))
            by_id = {d["id"]: d for d in datasets}
            source = _JSONInput(json_body) if isinstance(json_body, dict) \
                else request.form
            try:
                selections = _parse_sampling_form(source, by_id)
            except ValidationError as exc:
                if wants_json:
                    return jsonify({"ok": False, "error": str(exc)}), 400
                return render_template(
                    "sampling.html",
                    datasets=datasets,
                    max_tables=sampling.MAX_TABLES,
                    tier_min=sampling.TIER_MIN,
                    tier_max=sampling.TIER_MAX,
                    form_error=str(exc),
                ), 400

            requests_spec = []
            for dataset_id, quotas in selections:
                d = by_id[dataset_id]
                requests_spec.append({
                    "dataset_id": dataset_id,
                    "name": d["name"],
                    "grid": json.loads(d["grid_json"]),
                    "quotas": quotas,
                })

            document = sampling.build_package(requests_spec)
            document["generated_at"] = dbmod.now_iso()
            token = uuid.uuid4().hex
            params = {
                "selected": [
                    {"dataset_id": did, "quotas": q}
                    for did, q in selections
                ],
            }
            dbmod.save_sampling_package(
                conn, token, document["complete"], document["case_count"],
                params, document,
            )

            if wants_json:
                status = 200 if document["complete"] else 409
                return _json_response(document, token, status=status,
                                      attachment=document["complete"])
            return redirect(url_for("sampling_package", token=token))
        finally:
            conn.close()

    @app.route("/sampling/packages/<token>")
    def sampling_package(token: str):
        conn = db()
        try:
            row = dbmod.get_sampling_package(conn, token)
            if row is None:
                abort(404)
            document = json.loads(row["document_json"])
            return render_template(
                "package.html", token=token, doc=document,
            )
        finally:
            conn.close()

    @app.route("/sampling/packages/<token>/download")
    def sampling_download(token: str):
        conn = db()
        try:
            row = dbmod.get_sampling_package(conn, token)
            if row is None:
                abort(404)
            document = json.loads(row["document_json"])
            wants_json = (
                request.values.get("format") == "json"
                or request.accept_mimetypes.best == "application/json"
            )
            if not row["complete"]:
                if wants_json:
                    return jsonify({
                        "ok": False,
                        "error": "各档名额尚未凑齐，不提供 JSON 下载。",
                        "summary": document["summary"],
                    }), 409
                flash("各档名额尚未凑齐，暂不提供 JSON 下载；"
                      "请参照“最多可生成”数量调小名额后重新组包。", "error")
                return redirect(url_for("sampling_package", token=token))
            return _json_response(document, token, status=200,
                                  attachment=True)
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
