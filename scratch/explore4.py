import json, sys, time
sys.path.insert(0, "/workspace")
from errata_app import solver

# OCR: 100 200 / 300 400；真表三格各错一位：200,300 / 400,500
# 行合计 500, 900；列合计 600, 800
grid2 = [[100, 200], [300, 400]]
m = [500, 900, 600, 800]
t0 = time.perf_counter()
res = solver.analyze(grid2, m, 0, 0, 200)
print(json.dumps(res, ensure_ascii=False, indent=1))
print("elapsed", round(time.perf_counter() - t0, 2))

# 再验证 maxflow 可行性判断本身
print("bounded feasible (should be True):",
      solver.margins_bounded_feasible(m, 2, 2))
