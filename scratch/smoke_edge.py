import http.cookiejar, sys, time, urllib.request, urllib.parse, uuid, subprocess, os, signal
BASE = "http://127.0.0.1:5057"
BOUNDARY = "----b" + uuid.uuid4().hex
cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

def post_multipart(path, fields, fname, content):
    lines = []
    for k, v in fields.items():
        lines += [f"--{BOUNDARY}".encode(),
                  f'Content-Disposition: form-data; name="{k}"'.encode(), b"",
                  str(v).encode()]
    if fname is not None:
        lines += [f"--{BOUNDARY}".encode(),
                  f'Content-Disposition: form-data; name="csv_file"; filename="{fname}"'.encode(),
                  b"Content-Type: text/csv", b"", content.encode(),
                  f"--{BOUNDARY}--".encode()]
    body = b"\r\n".join(lines)
    req = urllib.request.Request(BASE + path, data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={BOUNDARY}")
    with opener.open(req, timeout=60) as r:
        return r.read().decode(), r.url

def post_form(path, fields):
    req = urllib.request.Request(BASE + path,
        data=urllib.parse.urlencode(fields).encode(), method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with opener.open(req, timeout=60) as r:
        return r.read().decode()

# 空文件
body, _ = post_multipart("/datasets/new", {"name": "空文件"}, "empty.csv", "")
assert "CSV 内容为空" in body
print("ok - 空文件被拒绝并提示")

# 5x5 超限
big = "\n".join(",".join("1" for _ in range(5)) for _ in range(5))
body, _ = post_multipart("/datasets/new", {"name": "5x5"}, "big.csv", big)
assert "超过上限 4" in body
print("ok - 5x5 被拒绝")

# 1x1 全流程
body, url = post_multipart("/datasets/new", {"name": "1x1"}, "one.csv", "500\n")
assert "/datasets/1" in url
post_form("/datasets/1/margins", {"margin_0": "501", "margin_1": "501"})
body = post_form("/datasets/1/analyze",
                 {"target_row": "1", "target_col": "1", "proposed": "501"})
assert "证明成立" in body and "（1 条）" in body
print("ok - 1x1 单条合计证明")

# 拟改值越界
body = post_form("/datasets/1/analyze",
                 {"target_row": "1", "target_col": "1", "proposed": "10000"})
assert "超过四位" in body
print("ok - 拟改值 10000 被拒绝")

# 目标格越界
body = post_form("/datasets/1/analyze",
                 {"target_row": "2", "target_col": "1", "proposed": "501"})
assert "目标格下标越界" in body
print("ok - 目标格越界被拒绝")
print("全部边界检查通过")
