"""抽测包真实 HTTP 全链路自测。

用法：先启动服务（任意 ERRATA_DB_PATH 指向全新库），然后
    python3 scratch/smoke_sampling.py [BASE_URL]

覆盖：
  1. 网页入口与配置页；
  2. 凑齐请求（含两张相同底表的整包去重）-> 预检页 -> JSON 下载并深度校验；
  3. 凑不齐请求（1x1 零表，错一格上限 9、错两格上限 0）-> 显示最多可生成数、
     页面无下载入口、下载地址 HTML 被重定向 / JSON 得 409；
  4. 超过 4 张表被拒；名额越界被拒；
  5. 4x4 底表 24/24 可组包且响应迅速。
"""
import http.cookiejar
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5061"
BOUNDARY = "----samplepkg" + uuid.uuid4().hex

cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))


def check(cond, msg):
    if not cond:
        print("FAIL:", msg)
        sys.exit(1)
    print("ok -", msg)


def get(path, allow_error=False):
    req = urllib.request.Request(BASE + path)
    try:
        with opener.open(req, timeout=120) as r:
            return r.status, r.read().decode("utf-8"), dict(r.headers)
    except urllib.error.HTTPError as e:
        if allow_error:
            return e.code, e.read().decode("utf-8"), dict(e.headers)
        raise


def post_form(path, fields, allow_error=False):
    data = urllib.parse.urlencode(fields, doseq=True).encode()
    req = urllib.request.Request(BASE + path, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with opener.open(req, timeout=120) as r:
            return r.status, r.read().decode("utf-8"), r.url, dict(r.headers)
    except urllib.error.HTTPError as e:
        if allow_error:
            return e.code, e.read().decode("utf-8"), e.url, dict(e.headers)
        raise


def post_multipart(path, fields, fname, content):
    lines = []
    for k, v in fields.items():
        lines += [f"--{BOUNDARY}".encode(),
                  f'Content-Disposition: form-data; name="{k}"'.encode(), b"",
                  str(v).encode()]
    lines += [f"--{BOUNDARY}".encode(),
              f'Content-Disposition: form-data; name="csv_file"; '
              f'filename="{fname}"'.encode(),
              b"Content-Type: text/csv", b"", content.encode(),
              f"--{BOUNDARY}--".encode()]
    body = b"\r\n".join(lines)
    req = urllib.request.Request(BASE + path, data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={BOUNDARY}")
    with opener.open(req, timeout=120) as r:
        return r.status, r.read().decode(), r.url


def upload(csv_text, name):
    status, body, url = post_multipart(
        "/datasets/new", {"name": name}, "g.csv", csv_text)
    m = re.search(r"/datasets/(\d+)", url)
    assert m, url
    return int(m.group(1))


def digit_distance(a, b):
    sa, sb = str(a), str(b)
    if len(sa) != len(sb):
        return None
    return sum(x != y for x, y in zip(sa, sb))


# --------------------------------------------------------------------------- #
# 0. 入口
# --------------------------------------------------------------------------- #
status, body, _ = get("/")
check("抽测包" in body and 'href="/sampling"' in body, "首页有抽测包入口")
status, body, _ = get("/sampling")
check(status == 200 and "按档生成带标准答案的错例" in body,
      "抽测包配置页可访问（空库显示引导）")

# --------------------------------------------------------------------------- #
# 1. 准备数据表（全新库，id 从 1 开始）
# --------------------------------------------------------------------------- #
grid_a = "1200,340,260\n560,780,410\n190,620,330\n"
grid_b = "0,0\n0,0\n"
id_a = upload(grid_a, "3x3真值")
id_b1 = upload(grid_b, "2x2零表甲")
id_b2 = upload(grid_b, "2x2零表乙（与甲同值，测整包去重）")
id_tiny = upload("0\n", "1x1零表（凑不齐用）")
check((id_a, id_b1, id_b2, id_tiny) == (1, 2, 3, 4),
      f"四张底表上传成功 id={id_a, id_b1, id_b2, id_tiny}")

# 载入 4x4 样例 -> id 5
status, body, url, _ = post_form("/samples/load", {"sample": "sample4x4"})
check(status == 200, "4x4 样例载入（id=5）")

status, body, _ = get("/sampling")
check('name="table_ids"' in body and "生成抽测包并预检" in body
      and 'max="24"' in body, "有数据后配置页列出勾选框与 0..24 名额框")

# --------------------------------------------------------------------------- #
# 2. 凑齐请求：1(3x3) + 2/3(相同2x2)，小名额
# --------------------------------------------------------------------------- #
fields = [
    ("table_ids", "1"), ("table_ids", "2"), ("table_ids", "3"),
    ("one_1", "2"), ("two_1", "1"),
    ("one_2", "3"), ("two_2", "2"),
    ("one_3", "3"), ("two_3", "2"),
]
status, body, url, headers = post_form("/sampling/build", fields)
m = re.search(r"/sampling/packages/([0-9a-f]+)", url)
check(status == 200 and m, f"凑齐请求提交后落到预检页 {url}")
token = m.group(1)
check("各档名额已全部凑齐" in body, "预检页显示已凑齐")
check("/download" in body and "下载标准答案 JSON" in body,
      "凑齐后页面提供 JSON 下载按钮")

status, text, headers = get(f"/sampling/packages/{token}/download")
check(status == 200 and "attachment" in headers.get("Content-Disposition", ""),
      "下载返回 200 且为附件")
doc = json.loads(text)
check(doc["complete"] is True and doc["case_count"] == 13,
      f"JSON 共 13 例（实际 {doc['case_count']}）")

truths = {
    1: [[1200, 340, 260], [560, 780, 410], [190, 620, 330]],
    2: [[0, 0], [0, 0]],
    3: [[0, 0], [0, 0]],
}
seen = set()
covered = {}
for c in doc["cases"]:
    did = c["source_dataset_id"]
    truth = truths[did]
    tier = c["error_count"]
    sim = c["simulated_recognition_grid"]
    covered[(did, tier)] = covered.get((did, tier), 0) + 1
    # 来源表号（含中文键）
    check(c["source_table_no"] == did and c["来源表号"] == did,
          f"例 {c['case_no']} 来源表号正确") if c["case_no"] == 1 else None
    # 合计
    rt = [sum(r) for r in truth]
    ct = [sum(truth[i][j] for i in range(len(truth)))
          for j in range(len(truth[0]))]
    assert c["row_totals"] == rt and c["column_totals"] == ct
    assert c["行合计"] == rt and c["列合计"] == ct
    # 错格
    assert len(c["error_cells"]) == tier
    diffs = 0
    for e in c["error_cells"]:
        i, j = e["row_index"], e["column_index"]
        assert e["row"] == i + 1 and e["column"] == j + 1
        assert e["坐标"] == [i + 1, j + 1]
        assert e["truth_value"] == e["original_value"] == e["原值"] \
            == truth[i][j]
        assert e["misrecognized_value"] == e["ocr_value"] == e["误识值"] \
            == sim[i][j]
        assert digit_distance(truth[i][j], sim[i][j]) == 1
        diffs += 1
    actual = sum(sim[i][j] != truth[i][j]
                 for i in range(len(truth)) for j in range(len(truth[0])))
    assert actual == tier == diffs
    # 整包去重键：尺寸+格值
    key = (len(sim), len(sim[0]), json.dumps(sim))
    assert key not in seen, f"重复模拟表 {key}"
    seen.add(key)

check(len(seen) == 13, "13 例模拟识别表按尺寸+格值整包互不重复")
for did in (1, 2, 3):
    for tier, want in ((1, {1: 2, 2: 3, 3: 3}[did]),
                       (2, {1: 1, 2: 2, 3: 2}[did])):
        check(covered.get((did, tier)) == want,
              f"底表 #{did} {tier} 格档覆盖 {want} 份")
print("ok - 每例来源/模拟表/合计/坐标/原值/误识值全部自洽")

# --------------------------------------------------------------------------- #
# 3. 凑不齐：1x1 零表 24/24
# --------------------------------------------------------------------------- #
fields = {"table_ids": "4", "one_4": "24", "two_4": "24"}
status, body, url, _ = post_form("/sampling/build", fields)
m = re.search(r"/sampling/packages/([0-9a-f]+)", url)
token_bad = m.group(1)
check("部分档位凑不齐" in body, "凑不齐时预检页明确提示")
check(re.search(r"错一格[\s\S]{0,400}?<strong>9</strong>", body) is not None,
      "页面显示错一格名额内最多 9 份")
check(re.search(r"错两格[\s\S]{0,400}?<strong>0</strong>", body) is not None,
      "页面显示错两格名额内最多 0 份")
check("下载标准答案 JSON" not in body, "凑不齐时页面没有 JSON 下载按钮")

# HTML 直接访问下载 -> 302 回预检页（urllib 自动跟随，最终 200 预检页）
status, body, _ = get(f"/sampling/packages/{token_bad}/download")
check(status == 200 and "尚未凑齐" in body,
      "浏览器直取下载地址被拦回并提示")

# JSON 客户端 -> 409
status, body, _ = get(
    f"/sampling/packages/{token_bad}/download?format=json",
    allow_error=True)
check(status == 409, f"JSON 下载门禁返回 409（实际 {status}）")
payload = json.loads(body)
check(payload["ok"] is False and len(payload["summary"]) == 2,
      "409 响应带各档名额报告")

# format=json 的构建：凑不齐直接 409
status, body, _, _ = post_form("/sampling/build?format=json",
                               {"one_4": "24", "two_4": "24"},
                               allow_error=True)
check(status == 409, "format=json 构建凑不齐时返回 409")

# --------------------------------------------------------------------------- #
# 4. 校验：>4 张、名额越界
# --------------------------------------------------------------------------- #
five = [("table_ids", str(i)) for i in range(1, 6)]
five += [(f"one_{i}", "1") for i in range(1, 6)]
status, body, _, _ = post_form("/sampling/build", five, allow_error=True)
check(status == 400 and "最多只能选 4 张" in body, "选择 5 张表被拒绝")

status, body, _, _ = post_form(
    "/sampling/build", {"table_ids": "1", "one_1": "25"}, allow_error=True)
check(status == 400 and "0..24" in body, "名额 25 越界被拒绝")

status, body, _, _ = post_form(
    "/sampling/build", {"table_ids": "1", "one_1": "-3"}, allow_error=True)
check(status == 400, "负数名额被拒绝")

# --------------------------------------------------------------------------- #
# 5. 4x4 24/24 真实 HTTP 组包
# --------------------------------------------------------------------------- #
t0 = time.perf_counter()
status, body, _, headers = post_form(
    "/sampling/build?format=json",
    {"table_ids": "5", "one_5": "24", "two_5": "24"})
dt = time.perf_counter() - t0
check(status == 200, f"4x4 24/24 组包成功（HTTP {status}，{dt:.2f}s）")
doc4 = json.loads(body)
check(doc4["complete"] and doc4["case_count"] == 48,
      "4x4 凑齐 48 例")
keys4 = [(len(c["simulated_recognition_grid"]),
          len(c["simulated_recognition_grid"][0]),
          json.dumps(c["simulated_recognition_grid"]))
         for c in doc4["cases"]]
check(len(set(keys4)) == 48, "4x4 的 48 例整包唯一")
check(doc4["memory"]["candidates_generated"] < 200,
      "4x4 只流式枚举了极少量候选，未全量物化 14.7 万张")

print("\n抽测包 HTTP 冒烟全部通过。")
