"""通过真实 HTTP 接口走通全链路（multipart 上传、表单、失效规则）。"""
import http.cookiejar
import sys
import urllib.parse
import urllib.request
import uuid

BASE = "http://127.0.0.1:5058"
BOUNDARY = "----erratabound" + uuid.uuid4().hex

cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))


def get(path):
    with opener.open(BASE + path, timeout=120) as r:
        return r.status, r.read().decode("utf-8")


def post_form(path, fields):
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(BASE + path, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with opener.open(req, timeout=120) as r:
        return r.status, r.read().decode("utf-8"), r.url


def post_multipart(path, fields, file_field, filename, content):
    lines = []
    for name, value in fields.items():
        lines.append(f"--{BOUNDARY}".encode())
        lines.append(f'Content-Disposition: form-data; name="{name}"'.encode())
        lines.append(b"")
        lines.append(str(value).encode())
    lines.append(f"--{BOUNDARY}".encode())
    lines.append(
        f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"'.encode())
    lines.append(b"Content-Type: text/csv")
    lines.append(b"")
    lines.append(content.encode())
    lines.append(f"--{BOUNDARY}--".encode())
    body = b"\r\n".join(lines)
    req = urllib.request.Request(BASE + path, data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={BOUNDARY}")
    with opener.open(req, timeout=120) as r:
        return r.status, r.read().decode("utf-8"), r.url


def check(cond, msg):
    if not cond:
        print("FAIL:", msg)
        sys.exit(1)
    print("ok -", msg)


status, body = get("/")
check(status == 200 and "区县作物面积数据集" in body, "首页可访问")

csv_text = "1300,340,260\n560,780,410\n190,620,330\n"
status, body, url = post_multipart(
    "/datasets/new", {"name": "冒烟测试表"}, "csv_file", "grid.csv", csv_text)
check(status == 200 and "/datasets/1" in url, f"CSV 上传成功并跳转（{url}）")
check("1300" in body, "数据集页展示 OCR 原值")

margins = [1800, 1750, 1140, 1950, 1740, 1000]
fields = {f"margin_{k}": str(v) for k, v in enumerate(margins)}
status, body, _ = post_form("/datasets/1/margins", fields)
check("已更新 6 条合计" in body, "6 条合计保存成功")

status, body, _ = post_form(
    "/datasets/1/analyze",
    {"target_row": "1", "target_col": "1", "proposed": "1200"})
check("证明成立" in body and "凭据 #1" in body, "分析成功并生成凭据 #1")
check("行 1 合计" in body and "1800" in body, "结果页列出引用合计及值")
check(">> 3 条" in body or "（3 条）" in body, "引用条目数为 3（最少）")

status, body = get("/vouchers/1")
check("有效" in body and "1300" in body and "1200" in body,
      "凭据详情含原值/拟改值且状态有效")
check("1950" in body, "凭据详情含列合计快照 1950")

# 反例路径：新数据集只录两条
post_multipart("/datasets/new", {"name": "反例表"}, "csv_file", "g2.csv", csv_text)
post_form("/datasets/2/margins",
          {"margin_0": "1800", "margin_1": "", "margin_2": "",
           "margin_3": "1950", "margin_4": "", "margin_5": ""})
status, body, _ = post_form(
    "/datasets/2/analyze",
    {"target_row": "1", "target_col": "1", "proposed": "1200"})
check("存在反例" in body and "反例候选原表" in body, "两条合计时报告反例")
status, body = get("/vouchers")
check("冒烟测试表" in body and body.count("查看") == 1,
      "反例不生成凭据，总览只有 1 张凭据")

# 矛盾路径
post_multipart("/datasets/new", {"name": "矛盾表"}, "csv_file", "g3.csv", csv_text)
post_form("/datasets/3/margins",
          {"margin_0": "1800", "margin_1": "1750", "margin_2": "1140",
           "margin_3": "1900", "margin_4": "1740", "margin_5": "1000"})
status, body, _ = post_form(
    "/datasets/3/analyze",
    {"target_row": "1", "target_col": "1", "proposed": "1200"})
check("合计矛盾" in body and "不相等" in body, "行列总和不等时报矛盾原因")

# 失效规则：修改凭据 #1 引用的行 1 合计（entry 0）
status, body, _ = post_form("/datasets/1/margins",
                            {**{f"margin_{k}": str(v) for k, v in enumerate(margins)},
                             "margin_0": "1805"})
check("凭据 #1 已立即标记为失效" in body, "修改被引用合计立即提示失效")
status, body = get("/vouchers/1")
check("已失效" in body and "已被修改" in body and "1805" in body,
      "凭据页展示失效状态、快照与当前值对比")

# 未引用合计更正不影响：先再生成一张凭据（数据集 4，全新合计）
post_multipart("/datasets/new", {"name": "失效规则表"}, "csv_file", "g4.csv", csv_text)
post_form("/datasets/4/margins", fields)
post_form("/datasets/4/analyze",
          {"target_row": "1", "target_col": "1", "proposed": "1200"})
# 凭据 #2 引用条目为 0,1,3；修改未引用的 entry 5（列 3 合计 1000->1001）
fields2 = dict(fields)
fields2["margin_5"] = "1001"
status, body, _ = post_form("/datasets/4/margins", fields2)
check("已更新 1 条合计" in body and "已立即标记为失效" not in body,
      "更正未引用合计不触发失效提示")
status, body = get("/vouchers/2")
check("有效" in body, "凭据 #2 仍为有效")

# 非法输入
status, body, _ = post_multipart(
    "/datasets/new", {"name": "坏数据"}, "csv_file", "bad.csv",
    "1,10000\n2,3\n")
check("超过四位" in body, "超过 9999 的格值被拒绝")
status, body, _ = post_multipart(
    "/datasets/new", {"name": "坏数据2"}, "csv_file", "bad2.csv",
    "1,-2\n3,4\n")
check("不是非负整数" in body, "负数被拒绝")

# 样例载入
status, body, _ = post_form("/samples/load", {"sample": "sample4x4"})
check(status == 200 and "4×4" in body, "4x4 样例一键载入")

print("\n全部冒烟检查通过。")
