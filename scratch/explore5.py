import json, sys
sys.path.insert(0, "/workspace")
from errata_app import solver

ocr = [
    [100, 100, 100],
    [100, 100, 100],
    [100, 100, 100],
]
# 真表：四格各 +100（一位误识），其余不变
true = [row[:] for row in ocr]
for (i, j) in [(0, 0), (0, 1), (1, 0), (2, 2)]:
    true[i][j] = 200
r = c = 3
m = [sum(row) for row in true] + [sum(true[i][j] for i in range(r)) for j in range(c)]
print("margins:", m)
res = solver.analyze(ocr, m, 0, 0, 200)
print(json.dumps(res, ensure_ascii=False, indent=1))
