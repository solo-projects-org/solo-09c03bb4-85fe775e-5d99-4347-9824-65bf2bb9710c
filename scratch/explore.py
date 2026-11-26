import json
import sys

sys.path.insert(0, "/workspace")

from errata_app import solver

# 3x3 试验：真表里 (0,0)=1200，OCR 误识为 1300（一位：百位 3→2）
true_grid = [
    [1200, 340, 260],
    [560, 780, 410],
    [190, 620, 330],
]
ocr = [row[:] for row in true_grid]
ocr[0][0] = 1300

r, c = 3, 3
true_margins = [sum(row) for row in true_grid] + [
    sum(true_grid[i][j] for i in range(r)) for j in range(c)
]
print("true margins:", true_margins)

t0 = __import__("time").perf_counter()
res = solver.analyze(ocr, true_margins, 0, 0, 1200)
t1 = __import__("time").perf_counter()
print(json.dumps(res, ensure_ascii=False, indent=1))
print(f"elapsed {t1 - t0:.2f}s")
