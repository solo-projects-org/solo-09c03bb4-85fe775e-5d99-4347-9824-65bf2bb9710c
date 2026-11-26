"""勘误凭据求解器。

问题模型
========

- 原表为 r×c（1 ≤ r, c ≤ 4）的非负整数格，每格取值 0..9999（至多四位）。
- OCR 表（上传 CSV）与“原书核准”的原表至多有两个格不同，且每个不同的格
  恰好只误识了 *一位* 十进制数字（位数不变；多位数首位不能变成 0）。
- 编辑录入原书核准的行合计、列合计（合计本身不参与 OCR 误识模型，以录入
  值为准；凭据引用的合计被修改后凭据在应用层立即失效）。
- 编辑指定一格 (i, j) 及拟改值 v。

本模块负责：
  1. 枚举出所有符合“≤2 格、每格一位数字误识”模型的候选原表；
  2. 在给定合计上判断可行候选是否存在；
  3. 找出条目数最少的一组合计，使 *每张* 满足该组合计的候选原表在 (i, j)
     上都等于 v（即该组合计可证明拟改值，且不存在反例）；
  4. 全份合计矛盾、或无法钉住拟改值（仍有反例）时，给出原因与反例。
"""

from __future__ import annotations

from itertools import combinations
from typing import Optional

MAX_CELL = 9999
MAX_ERROR_CELLS = 2
_BASE = MAX_CELL + 1  # 10000，用于把整张表压成一个整数签名


# --------------------------------------------------------------------------- #
# 一位十进制数字误识的邻域
# --------------------------------------------------------------------------- #
def digit_neighbors(value: int) -> list[int]:
    """返回与 value 恰好相差一位十进制数字、位数不变的所有取值。

    规则：
      - 0 为一位数，可误识为 1..9；
      - 多位数首位不能变成 0（否则位数改变，属于多位错误）；
      - 其余位可替换为任意不同数字（含 0）。
    """
    if value < 0 or value > MAX_CELL:
        raise ValueError("格值必须位于 0..9999")
    s = str(value)
    out: list[int] = []
    for pos, ch in enumerate(s):
        for d in "0123456789":
            if d == ch:
                continue
            if len(s) > 1 and pos == 0 and d == "0":
                continue
            out.append(int(s[:pos] + d + s[pos + 1:]))
    return out


def digit_distance(a: int, b: int) -> Optional[int]:
    """两值之间的“误识位数”：位数相同且恰有 n 位不同则返回 n，否则 None。"""
    sa, sb = str(a), str(b)
    if len(sa) != len(sb):
        return None
    diffs = sum(1 for x, y in zip(sa, sb) if x != y)
    return diffs


# --------------------------------------------------------------------------- #
# 候选原表枚举
# --------------------------------------------------------------------------- #
def _pack(grid: tuple[tuple[int, ...], ...]) -> int:
    sig = 0
    for row in grid:
        for x in row:
            sig = sig * _BASE + x
    return sig


def _unpack(sig: int, r: int, c: int) -> tuple[tuple[int, ...], ...]:
    vals: list[int] = []
    n = r * c
    for _ in range(n):
        vals.append(sig % _BASE)
        sig //= _BASE
    vals.reverse()
    return tuple(tuple(vals[i * c:(i + 1) * c]) for i in range(r))


def enumerate_candidates(grid: list[list[int]]) -> list[tuple[tuple[int, ...], ...]]:
    """枚举所有符合误识模型的候选原表（含与 OCR 表完全相同的无错情形）。

    返回去重后的完整表格列表，第 0 个恒为 OCR 表本身。
    """
    r, c = len(grid), len(grid[0])
    base = tuple(tuple(row) for row in grid)
    seen: set[int] = {_pack(base)}
    out: list[tuple[tuple[int, ...], ...]] = [base]
    positions = [(i, j) for i in range(r) for j in range(c)]

    def with_change(pos, new_val):
        i, j = pos
        g = [list(row) for row in base]
        g[i][j] = new_val
        return tuple(tuple(row) for row in g)

    # 一格误识
    for pos in positions:
        i, j = pos
        for nv in digit_neighbors(grid[i][j]):
            g = with_change(pos, nv)
            sig = _pack(g)
            if sig not in seen:
                seen.add(sig)
                out.append(g)

    # 两格各误识一位
    for p1, p2 in combinations(positions, 2):
        i1, j1 = p1
        i2, j2 = p2
        for nv1 in digit_neighbors(grid[i1][j1]):
            for nv2 in digit_neighbors(grid[i2][j2]):
                g = [list(row) for row in base]
                g[i1][j1] = nv1
                g[i2][j2] = nv2
                g = tuple(tuple(row) for row in g)
                sig = _pack(g)
                if sig not in seen:
                    seen.add(sig)
                    out.append(g)
    return out


# --------------------------------------------------------------------------- #
# 合计索引：合计条目 k（前 r 个为行合计，后 c 个为列合计）-> 取值 -> 候选集合
# --------------------------------------------------------------------------- #
def build_margin_index(candidates, r: int, c: int):
    index: list[dict[int, set[int]]] = [dict() for _ in range(r + c)]
    for cid, table in enumerate(candidates):
        for i, row in enumerate(table):
            index[i].setdefault(sum(row), set()).add(cid)
        for j in range(c):
            index[r + j].setdefault(sum(table[i][j] for i in range(r)), set()).add(cid)
    return index


def _feasible_set(margins, index, all_ids: set[int]) -> set[int]:
    """满足全部已录入合计的候选 id 集合。"""
    feasible = set(all_ids)
    for k, val in enumerate(margins):
        if val is None:
            continue
        feasible &= index[k].get(val, frozenset())
        if not feasible:
            return set()
    return feasible


# --------------------------------------------------------------------------- #
# 无 OCR 模型限制时，带上下界 0..9999 的行列合计是否存在可行整数表（最大流）
# --------------------------------------------------------------------------- #
def margins_bounded_feasible(margins, r: int, c: int) -> bool:
    """判断是否存在取值 0..9999 的整数表满足全部已知行列合计。

    未知条目不施加总量约束：对应源/汇边容量取“已知侧总量”这一宽松上界
    （任何可行解都不可能超过它），目标流量即为已知合计总量。
    """
    row_sums = margins[:r]
    col_sums = margins[r:]
    if any(v is not None and v < 0 for v in margins):
        return False
    known_rows = [v for v in row_sums if v is not None]
    known_cols = [v for v in col_sums if v is not None]
    # 两侧都有条目时，总和必须相等；只录一侧时以该侧总量为目标
    if known_rows and known_cols and sum(known_rows) != sum(known_cols):
        return False
    total_known = max(sum(known_rows), sum(known_cols))
    # 没有任何已知合计，或已知合计总量为 0：零表（或忽略未知条目）即可满足
    if total_known == 0:
        return True
    # 单格上限决定的总量硬上界；未知条目用它做宽松容量
    loose = max(total_known, MAX_CELL)
    row_cap = [v if v is not None else loose for v in row_sums]
    col_cap = [v if v is not None else loose for v in col_sums]
    n = 1 + r + c + 1
    src, sink = 0, n - 1
    graph = [[] for _ in range(n)]

    def add_edge(u, v, cap):
        graph[u].append([v, cap, len(graph[v])])
        graph[v].append([u, 0, len(graph[u]) - 1])

    for i in range(r):
        add_edge(src, 1 + i, row_cap[i])
    for i in range(r):
        for j in range(c):
            add_edge(1 + i, 1 + r + j, MAX_CELL)
    for j in range(c):
        add_edge(1 + r + j, sink, col_cap[j])

    def bfs_augment():
        """Edmonds–Karp：BFS 找一条增广路并返回可增广流量，无路返回 0。"""
        parent = [None] * n  # (上游节点, 边在上游邻接表中的下标)
        parent[src] = (src, -1)
        queue = [src]
        for u in queue:
            for ei, (v, cap, _rev) in enumerate(graph[u]):
                if cap > 0 and parent[v] is None:
                    parent[v] = (u, ei)
                    if v == sink:
                        queue = []
                        break
                    queue.append(v)
        if parent[sink] is None:
            return 0
        pushed = loose + 1
        v = sink
        while v != src:
            u, ei = parent[v]
            pushed = min(pushed, graph[u][ei][1])
            v = u
        v = sink
        while v != src:
            u, ei = parent[v]
            edge = graph[u][ei]
            rev = edge[2]
            edge[1] -= pushed
            graph[v][rev][1] += pushed
            v = u
        return pushed

    flow = 0
    while flow < total_known:
        pushed = bfs_augment()
        if not pushed:
            break
        flow += pushed
    return flow >= total_known


# --------------------------------------------------------------------------- #
# 主分析入口
# --------------------------------------------------------------------------- #
def analyze(grid, margins, target_row: int, target_col: int, proposed: int):
    """分析拟改值能否由最少条合计证明。

    参数
    ----
    grid : r×c 的 OCR 表（list[list[int]]，值 0..9999）
    margins : 长度 r+c 的列表，前 r 项为行合计、后 c 项为列合计；
              未录入条目用 None 表示。
    target_row, target_col : 拟改格的下标（0 起）。
    proposed : 拟改值（0..9999）。

    返回 dict，status 取值：
      - "proven"      : 找到最少条合计，所有满足它们的候选表在该格均为拟改值；
      - "counterexample" : 任何合计子集都无法钉住拟改值（附反例候选）；
      - "contradiction"  : 全份合计互相矛盾或超出 OCR 误识模型（附原因）。
    """
    r, c = len(grid), len(grid[0])
    if not (0 <= target_row < r and 0 <= target_col < c):
        raise ValueError("目标格下标越界")
    if not (0 <= proposed <= MAX_CELL):
        raise ValueError("拟改值必须是 0..9999 的非负整数")

    candidates = enumerate_candidates(grid)
    index = build_margin_index(candidates, r, c)
    all_ids = set(range(len(candidates)))
    entry_kind = (["row"] * r) + (["col"] * c)
    entry_index = list(range(r)) + list(range(c))

    original_value = grid[target_row][target_col]
    same_as_original = original_value == proposed

    full_feasible = _feasible_set(margins, index, all_ids)

    # --------------------------------------------------------------- #
    # 1. 全份合计矛盾：没有任何候选表满足
    # --------------------------------------------------------------- #
    if not full_feasible:
        reason = _contradiction_reason(grid, margins, candidates, index, r, c)
        return {
            "status": "contradiction",
            "reason": reason,
            "original_value": original_value,
            "proposed": proposed,
            "candidate_count": len(candidates),
        }

    # --------------------------------------------------------------- #
    # 2. 枚举合计子集，由小到大，找条目最少的证明
    # --------------------------------------------------------------- #
    entered = [k for k, v in enumerate(margins) if v is not None]
    for size in range(1, len(entered) + 1):
        for subset in combinations(entered, size):
            feasible = set(all_ids)
            for k in subset:
                feasible &= index[k][margins[k]]
                if not feasible:
                    break
            if not feasible:
                continue
            if all(candidates[cid][target_row][target_col] == proposed
                   for cid in feasible):
                return {
                    "status": "proven",
                    "cited_entries": [
                        {
                            "entry": int(k),
                            "kind": entry_kind[k],
                            "index": entry_index[k],
                            "value": margins[k],
                        }
                        for k in subset
                    ],
                    "subset_size": size,
                    "candidate_count": len(candidates),
                    "feasible_under_full_margins": len(full_feasible),
                    "feasible_under_cited": len(feasible),
                    "original_value": original_value,
                    "proposed": proposed,
                    "same_as_original": same_as_original,
                }

    # --------------------------------------------------------------- #
    # 3. 无法钉住：取全份合计下的一个反例候选
    # --------------------------------------------------------------- #
    counter_id = next(
        cid for cid in full_feasible
        if candidates[cid][target_row][target_col] != proposed
    )
    counter_table = candidates[counter_id]
    dist = _table_error_distance(grid, counter_table)
    return {
        "status": "counterexample",
        "reason": (
            "任何已录入合计的子集都无法排除该格取其他值的候选原表；"
            "下表同时满足全部已录入合计，却在该格不取拟改值。"
        ),
        "counterexample": [list(row) for row in counter_table],
        "counterexample_target": counter_table[target_row][target_col],
        "counterexample_error_cells": dist,
        "full_feasible_count": len(full_feasible),
        "candidate_count": len(candidates),
        "original_value": original_value,
        "proposed": proposed,
    }


def _table_error_distance(grid, table) -> list[dict]:
    """两张表逐格对比，列出不同格及其误识位数。"""
    out = []
    for i, row in enumerate(grid):
        for j, val in enumerate(row):
            if table[i][j] != val:
                out.append({
                    "row": i,
                    "col": j,
                    "ocr": val,
                    "candidate": table[i][j],
                    "digit_errors": digit_distance(val, table[i][j]),
                })
    return out


def _contradiction_reason(grid, margins, candidates, index, r: int, c: int) -> str:
    """全份合计无解时给出人类可读的原因。"""
    row_sums, col_sums = margins[:r], margins[r:]
    known_rows = [v for v in row_sums if v is not None]
    known_cols = [v for v in col_sums if v is not None]
    if known_rows and known_cols and sum(known_rows) != sum(known_cols):
        return (
            f"行合计之和（{sum(known_rows)}）与列合计之和（{sum(known_cols)}）"
            "不相等，任何二维表都不可能同时满足这些合计。"
        )
    if not margins_bounded_feasible(margins, r, c):
        return (
            "在每格取值 0..9999 的整数范围内，不存在任何满足这些行列合计的表"
            "（合计超出格值上界或结构不可行）。"
        )
    # 合计本身可实现，但落在“至多两格、每格一位数字误识”模型之外
    best = None
    best_satisfied = -1
    for cid, table in enumerate(candidates):
        satisfied = 0
        for k, val in enumerate(margins):
            if val is None:
                continue
            actual = sum(table[k]) if k < r else sum(table[i][k - r] for i in range(r))
            if actual == val:
                satisfied += 1
        if satisfied > best_satisfied:
            best_satisfied, best = satisfied, table
    entered = sum(1 for v in margins if v is not None)
    dist = _table_error_distance(grid, best)
    detail = "；".join(
        f"第{d['row'] + 1}行第{d['col'] + 1}列 {d['ocr']}→{d['candidate']}"
        f"（{d['digit_errors']} 位不同）"
        for d in dist
    ) or "无需改动"
    return (
        f"合计在一般整数表下可实现，但没有任何符合“至多两格、每格一位数字误识”"
        f"模型的候选表能同时满足全部 {entered} 条合计；最接近的候选表也只能满足 "
        f"{best_satisfied}/{entered} 条，其改动为：{detail}。"
    )
