import json
import time
import sys

sys.path.insert(0, "/workspace")
from errata_app import solver


def run(name, grid, margins, tr, tc, proposed):
    t0 = time.perf_counter()
    res = solver.analyze(grid, margins, tr, tc, proposed)
    dt = time.perf_counter() - t0
    print("=" * 70)
    print(name, f"({dt:.2f}s, candidates={res['candidate_count']})")
    print(json.dumps(res, ensure_ascii=False, indent=1))


# 1) 1x2：一条行合计即可钉住
run("1x2 单条证明", [[10, 20]], [30, None, None], 0, 0, 11)

# 2) 3x3 只录两条合计 -> 预期 counterexample
true_grid = [
    [1200, 340, 260],
    [560, 780, 410],
    [190, 620, 330],
]
ocr = [row[:] for row in true_grid]
ocr[0][0] = 1300
m2 = [1800, None, None, 1950, None, None]
run("3x3 仅两条合计->反例", ocr, m2, 0, 0, 1200)

# 3) 3x3 全份真合计 -> proven（3 条）
full = [sum(row) for row in true_grid] + [
    sum(true_grid[i][j] for i in range(3)) for j in range(3)
]
run("3x3 全份真合计->证明", ocr, full, 0, 0, 1200)

# 4) 矛盾：行和总和 != 列和总和
run("3x3 行和列和不相等", ocr, [1800, 1750, 1140, 1900, 1740, 1000], 0, 0, 1200)

# 5) 矛盾：合计超出 4 位格值上界（1x1，合计 10000 不可实现）
run("1x1 合计超上界", [[500]], [10000, 10000], 0, 0, 501)

# 6) 1x1 单格：模型下候选只有 OCR 值与其一位邻域
run("1x1 可行证明", [[500]], [501, 501], 0, 0, 501)

# 7) 拟改值=原值，无合计录入 -> 任何候选都有其他取值 -> 反例
run("3x3 无合计->反例", ocr, [None] * 6, 0, 0, 1300)
