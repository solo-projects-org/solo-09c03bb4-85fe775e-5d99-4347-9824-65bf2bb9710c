import http.cookiejar, re, sys, time, urllib.request, urllib.parse, uuid
BASE = "http://127.0.0.1:5058"
BOUNDARY = "----b" + uuid.uuid4().hex
cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

def post_multipart(path, fields, fname, content):
    lines = []
    for k, v in fields.items():
        lines += [f"--{BOUNDARY}".encode(),
                  f'Content-Disposition: form-data; name="{k}"'.encode(), b"",
                  str(v).encode()]
    lines += [f"--{BOUNDARY}".encode(),
              f'Content-Disposition: form-data; name="csv_file"; filename="{fname}"'.encode(),
              b"Content-Type: text/csv", b"", content.encode(),
              f"--{BOUNDARY}--".encode()]
    req = urllib.request.Request(BASE + path, data=b"\r\n".join(lines), method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={BOUNDARY}")
    with opener.open(req, timeout=180) as r:
        return r.read().decode(), r.url

def post_form(path, fields):
    req = urllib.request.Request(BASE + path,
        data=urllib.parse.urlencode(fields).encode(), method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with opener.open(req, timeout=180) as r:
        return r.read().decode()

csv_text = open("/workspace/errata_app/samples/sample4x4.csv", encoding="utf-8").read()
body, url = post_multipart("/datasets/new", {"name": "4x4上限样例"}, "g.csv", csv_text)
m = re.search(r"/datasets/(\d+)", url)
assert m, url
ds = m.group(1)
margins = [11652, 15238, 12084, 32838, 14660, 16832, 19054, 21266]
post_form(f"/datasets/{ds}/margins",
          {f"margin_{k}": str(v) for k, v in enumerate(margins)})
t0 = time.perf_counter()
body = post_form(f"/datasets/{ds}/analyze",
                 {"target_row": "1", "target_col": "1", "proposed": "1284"})
dt = time.perf_counter() - t0
assert "证明成立" in body, body[:500]
mv = re.search(r"凭据 #(\d+)", body)
assert mv, body[:500]
vid = mv.group(1)
assert "（3 条）" in body
assert "11652" in body and "14660" in body
print(f"4x4 HTTP 分析耗时 {dt:.2f}s，证明成立、3 条最少合计、凭据 #{vid} 已生成")
body = opener.open(BASE + f"/vouchers/{vid}", timeout=30).read().decode()
assert "有效" in body and "1234" in body and "1284" in body
print("凭据页校验通过")
