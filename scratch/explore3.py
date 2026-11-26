import json
import resource
import sys
import time

sys.path.insert(0, "/workspace")
from errata_app import solver

# 4x4 上限规模
grid4 = [
    [1234, 2345, 3456, 4567],
    [2143, 3254, 4365, 5476],
    [1357, 2468, 3579, 4680],
    [9876, 8765, 7654, 6543],
]
r = c = 4
true4 = [row[:] for row in grid4]
true4[0][0] = 1284  # 一位误识 3->8
margins4 = [sum(row) for row in true4] + [
    sum(true4[i][j] for i in range(r)) for j in range(c)
]
t0 = time.perf_counter()
res = solver.analyze(grid4, margins4, 0, 0, 1284)
dt = time.perf_counter() - t0
mem = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
print(f"4x4 elapsed={dt:.2f}s peak_rss={mem / 1024:.1f}MB status={res['status']}")
print(json.dumps(res, ensure_ascii=False, indent=1)[:1200])

# 第三类矛盾：一般整数表可行，但超出“两格各一位”模型
grid2 = [[100, 200], [300, 400]]
# 目标表三格各错一位：100->200, 200->300, 300->400
m = [500, 800, 600, 700]  # 行:200+300, 400+400; 列:200+400, 300+400
t0 = time.perf_counter()
res2 = solver.analyze(grid2, m, 0, 0, 200)
print("2x2 model-external:", json.dumps(res2, ensure_ascii=False, indent=1))
print(f"elapsed {time.perf_counter() - t0:.2f}s")
