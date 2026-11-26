"""抽测包：带标准答案的错例包生成。

业务规则（与任务约定一一对应）
==============================

1. 至多选择 :data:`MAX_TABLES` 张现有数据表作为**真值表**；
2. 每张真值表分别申请“错一格”（tier=1）与“错两格”（tier=2）各若干份，
   每档名额 :data:`TIER_MIN` .. :data:`TIER_MAX`（0..24）；
3. 每个错格严格只发生**一位十进制数字误识**，沿用
   :func:`errata_app.solver.digit_neighbors`；
4. 模拟识别表按 ``(行数, 列数, 整表格值签名)`` 做**整包去重**——去重集合
   跨所有底表、所有档位共享；
5. 只有各档名额全部凑齐，应用层才允许下载 JSON；
6. 每例给出：来源表号、模拟识别表、真值表行列合计，以及每个错格的坐标
   （同时给 1 基与 0 基）、原值（真值）与误识值。

内存约束
--------

4×4 “错两格”的全量候选约 14.7 万张（见 solver 模块说明），不能像单表分析
那样整体物化。本模块：

- 用组合公式直接算每档“不重复模拟表”的理论容量（:func:`tier_capacity`）；
- 候选错例通过 :func:`mutation_stream` **逐张流式生成、用完即弃**，
  分配器（:func:`allocate_slots`）不缓存候选表；
- 最终在内存中保留的只有**入选错例**，数量不超过所填名额总和
  （≤ 4 表 × 2 档 × 24 = 192），且每条只存去重键与 ≤2 个错格描述，
  表格在构造 JSON 时才按真值+错格描述还原。

去重键使用 solver 的 10000 进制整表签名；该签名对固定格数是单射，
再附带行列数即可避免不同尺寸之间的压缩碰撞。
"""

from __future__ import annotations

from itertools import combinations
from typing import Callable, Iterable

from .solver import _pack, digit_neighbors

MAX_TABLES = 4
TIER_MIN = 0
TIER_MAX = 24
TIERS = (1, 2)
TIER_LABELS = {1: "错一格", 2: "错两格"}
TIER_API = {1: "one_error", 2: "two_errors"}


# --------------------------------------------------------------------------- #
# 容量：某张真值表在某一档下能产生多少张互不相同的模拟识别表
# --------------------------------------------------------------------------- #
def neighbor_count(value: int) -> int:
    """``value`` 的一位数字误识结果个数（等价于 len(digit_neighbors(value))）。

    - 一位数：其余 9 个数字均可（含 0），共 9 个；
    - 多位数：首位有 8 个合法替换（1..9 中除本位，且不能改 0），
      其余每个数位各 9 个替换。
    """
    digits = len(str(value))
    if digits == 1:
        return 9
    return 8 + 9 * (digits - 1)


def tier_capacity(grid: list[list[int]], tier: int) -> int:
    """真值表 ``grid`` 在指定档位下可生成的互不相同模拟表总数。

    唯一性论证：一次 t 格误识得到的模拟表与真值恰好有 t 个不同格，
    因此不同错格集合不可能产出同一张表；同一错格集合内各格的邻值
    组合也是笛卡尔积、互不重复。故：

    - 错一格：Σ n(v)；
    - 错两格：Σ_{格对 a<b} n(v_a)·n(v_b)。
    """
    counts = [neighbor_count(v) for row in grid for v in row]
    if tier == 1:
        return sum(counts)
    if tier == 2:
        return sum(a * b for a, b in combinations(counts, 2))
    raise ValueError(f"不支持的档位：{tier}")


# --------------------------------------------------------------------------- #
# 去重键 / 合计
# --------------------------------------------------------------------------- #
def table_key(grid) -> tuple[int, int, int]:
    """按“尺寸 + 格值”生成整包去重键 ``(行数, 列数, 整表签名)``。"""
    r = len(grid)
    c = len(grid[0]) if r else 0
    base = tuple(tuple(row) for row in grid)
    return (r, c, _pack(base))


def truth_totals(grid: list[list[int]]):
    """返回真值表的 ``(行合计列表, 列合计列表)``。"""
    r, c = len(grid), len(grid[0])
    row_totals = [sum(row) for row in grid]
    col_totals = [sum(grid[i][j] for i in range(r)) for j in range(c)]
    return row_totals, col_totals


# --------------------------------------------------------------------------- #
# 流式候选生成
# --------------------------------------------------------------------------- #
ErrorSpec = tuple[int, int, int, int]  # (行0基, 列0基, 原值/真值, 误识值)
Candidate = tuple[tuple[int, int, int], tuple[ErrorSpec, ...]]


def mutation_stream(grid: list[list[int]], tier: int) -> Iterable[Candidate]:
    """按稳定顺序逐张产生“恰好错 tier 格、每格一位数字误识”的模拟表。

    每次 yield ``(key, errors)``：

    - ``key`` 为 :func:`table_key` 去重键；
    - ``errors`` 为错格描述元组（坐标 0 基 + 原值 + 误识值）。

    生成器不保存已生成的表，调用方按需迭代、用完即弃。
    """
    base = tuple(tuple(row) for row in grid)
    r, c = len(base), len(base[0])
    positions = [(i, j) for i in range(r) for j in range(c)]

    if tier == 1:
        for i, j in positions:
            original = base[i][j]
            for nv in digit_neighbors(original):
                g = [list(row) for row in base]
                g[i][j] = nv
                gt = tuple(tuple(row) for row in g)
                yield (r, c, _pack(gt)), ((i, j, original, nv),)
        return

    if tier == 2:
        for (i1, j1), (i2, j2) in combinations(positions, 2):
            o1, o2 = base[i1][j1], base[i2][j2]
            for nv1 in digit_neighbors(o1):
                for nv2 in digit_neighbors(o2):
                    g = [list(row) for row in base]
                    g[i1][j1] = nv1
                    g[i2][j2] = nv2
                    gt = tuple(tuple(row) for row in g)
                    errors = (
                        (i1, j1, o1, nv1),
                        (i2, j2, o2, nv2),
                    )
                    yield (r, c, _pack(gt)), errors
        return

    raise ValueError(f"不支持的档位：{tier}")


# --------------------------------------------------------------------------- #
# 整包去重分配器（二分图最大匹配 / Kuhn 增广）
# --------------------------------------------------------------------------- #
# 左侧“名额座位”：每个档位要 q 份就有 q 个座位；右侧是模拟表去重键。
# 同一把键整包只能分给一个座位。座位按轮询（round-robin）方式创建，
# 使相同底表被重复选择等竞争场景下各档尽量均匀覆盖，而不是被第一个档占满。
# 增广链允许把已分配的座位搬迁到其它候选，从而避免“先到先得”的贪心陷阱：
# 只要名额联合可行，就一定能找到全部匹配（Kuhn 算法给出最大匹配）。
# --------------------------------------------------------------------------- #

CandidateFactory = Callable[[], Iterable[tuple]]


class CachingCandidates:
    """每档一个的轻量候选缓存。

    Kuhn 增广会反复重放候选序列；若每次都重新构造整张网格，4×4 下会产生
    数千次重复计算。这里每个候选只缓存 ``(key, errors)``（整数键 + 至多
    两个错格描述），**不缓存表格本身**；同时候选只从底层生成器拉取一次。
    真正的网格只在最终定选后由 :func:`_build_case` 按真值+错格描述还原，
    因此内存中保留的具体候选表数量始终不超过所填名额。
    """

    def __init__(self, grid, tier: int):
        self._grid = grid
        self._tier = tier
        self.cache: list = []
        self._seen: set = set()
        self._stream = None
        self.exhausted = False
        self.generated = 0

    def __call__(self):
        for item in self.cache:
            yield item
        if self.exhausted:
            return
        if self._stream is None:
            self._stream = mutation_stream(self._grid, self._tier)
        while True:
            try:
                item = next(self._stream)
            except StopIteration:
                self.exhausted = True
                return
            self.generated += 1
            key = item[0]
            if key in self._seen:
                continue
            self._seen.add(key)
            self.cache.append(item)
            yield item


def allocate_slots(slots: list[dict]):
    """给各档分配整包唯一的候选键。

    参数 ``slots``：每项为 ``{"demand": int, "candidates": 零参可调用}``，
    调用 ``slots[i]["candidates"]()`` 返回该档候选的可迭代对象，
    元素为 ``(key, payload)``；每次调用都应得到一个**全新的迭代器**
    （确定性重放即可），分配器不长期保存候选。

    返回 ``(results, total_matched)``：

    - ``results[i]`` 为该档最终分到的 ``[(key, payload), ...]``；
    - ``total_matched`` 为全部分到的座位数。
    """
    seat_slot: list[int] = []
    max_demand = max((int(s["demand"]) for s in slots), default=0)
    for round_index in range(max_demand):
        for si, slot in enumerate(slots):
            if round_index < int(slot["demand"]):
                seat_slot.append(si)

    n_seats = len(seat_slot)
    seat_key: list = [None] * n_seats
    seat_payload: list = [None] * n_seats
    key_owner: dict = {}

    def assign(seat: int, key, payload) -> None:
        old = seat_key[seat]
        if old is not None and old != key and key_owner.get(old) == seat:
            del key_owner[old]
        seat_key[seat] = key
        seat_payload[seat] = payload
        key_owner[key] = seat

    def augment(seat: int, visited: set) -> bool:
        si = seat_slot[seat]
        factory: CandidateFactory = slots[si]["candidates"]
        for key, payload in factory():
            if key in visited:
                continue
            visited.add(key)
            owner = key_owner.get(key)
            if owner is None:
                assign(seat, key, payload)
                return True
            if owner == seat:
                continue
            if augment(owner, visited):
                assign(seat, key, payload)
                return True
        return False

    total_matched = 0
    for seat in range(n_seats):
        if augment(seat, set()):
            total_matched += 1

    results: list[list] = [[] for _ in slots]
    for seat, si in enumerate(seat_slot):
        key = seat_key[seat]
        if key is not None:
            results[si].append((key, seat_payload[seat]))
    return results, total_matched


# --------------------------------------------------------------------------- #
# 错例 JSON 构造
# --------------------------------------------------------------------------- #
def _error_cell_dict(spec: ErrorSpec) -> dict:
    i, j, original, misrecognized = spec
    return {
        # 1 基坐标（与网页表单/“第 x 行第 y 列”的习惯一致）
        "row": i + 1,
        "column": j + 1,
        "coordinate": [i + 1, j + 1],
        # 0 基下标（与 solver 内部一致，方便程序化试跑）
        "row_index": i,
        "column_index": j,
        # 任务口径：“原值”= 原书真值；同时给无歧义别名
        "original_value": original,
        "truth_value": original,
        "correct_value": original,
        # “误识值”= OCR 模拟识别出来的错值
        "misrecognized_value": misrecognized,
        "recognized_value": misrecognized,
        "ocr_value": misrecognized,
        "digit_errors": 1,
        # 中文键，直接对应需求措辞
        "坐标": [i + 1, j + 1],
        "原值": original,
        "误识值": misrecognized,
    }


def _build_case(case_no: int, request: dict, tier: int,
                key, errors: tuple[ErrorSpec, ...]) -> dict:
    grid = request["grid"]
    r, c = len(grid), len(grid[0])
    simulated = [list(row) for row in grid]
    for i, j, _original, nv in errors:
        simulated[i][j] = nv

    # 自检：还原出的模拟表去重键必须与分配到的键一致
    assert table_key(simulated) == key, "错格描述还原结果与候选键不一致"

    row_totals, col_totals = truth_totals(grid)
    dataset_id = request["dataset_id"]
    error_cells = [_error_cell_dict(spec) for spec in errors]

    case = {
        "case_no": case_no,
        # 来源
        "source_dataset_id": dataset_id,
        "source_table_no": dataset_id,
        "来源表号": dataset_id,
        "source_table_name": request.get("name", ""),
        # 档位
        "error_count": tier,
        "tier": TIER_API[tier],
        "tier_label": TIER_LABELS[tier],
        "档位": TIER_LABELS[tier],
        "dimensions": {"rows": r, "columns": c},
        # 表体
        "truth_grid": grid,
        "simulated_recognition_grid": simulated,
        "simulated_ocr_grid": simulated,
        "模拟识别表": simulated,
        # 真值行列合计（由真值表直接求和）
        "row_totals": row_totals,
        "column_totals": col_totals,
        "truth_row_totals": row_totals,
        "truth_column_totals": col_totals,
        "行合计": row_totals,
        "列合计": col_totals,
        "行列合计": {"行合计": row_totals, "列合计": col_totals},
        # 错格答案
        "error_cells": error_cells,
        "错格答案": error_cells,
    }
    return case


def build_package(requests: list[dict]) -> dict:
    """根据表单名额构造整包结果文档（纯 dict，可直接 JSON 序列化）。

    ``requests`` 每项形如::

        {
          "dataset_id": 3,
          "name": "……",
          "grid": [[...]],
          "quotas": {1: 24, 2: 12},
        }
    """
    slot_records: list[dict] = []
    alloc_slots: list[dict] = []
    candidate_sources: list[CachingCandidates] = []
    # slot_records 下标 -> alloc_slots 下标（quota=0 时为 None）
    alloc_index: list[int | None] = []

    for request in requests:
        grid = request["grid"]
        dataset_id = request["dataset_id"]
        for tier in TIERS:
            quota = int(request.get("quotas", {}).get(tier, 0) or 0)
            capacity = tier_capacity(grid, tier)
            record = {
                "dataset_id": dataset_id,
                "source_table_no": dataset_id,
                "name": request.get("name", ""),
                "rows": len(grid),
                "cols": len(grid[0]),
                "tier": tier,
                "tier_label": TIER_LABELS[tier],
                "quota": quota,
                "capacity": capacity,
                # “各档名额限制内最多能生成多少份”的单表口径
                "max_within_quota": min(quota, capacity),
                "generated": 0,
                "complete": quota == 0,
                "case_keys": [],
            }
            idx: int | None = None
            if quota > 0:
                idx = len(alloc_slots)
                source = CachingCandidates(grid, tier)
                candidate_sources.append(source)
                alloc_slots.append({
                    "demand": quota,
                    "candidates": source,
                })
            alloc_index.append(idx)
            slot_records.append(record)

    results, total_matched = allocate_slots(alloc_slots)

    cases: list[dict] = []
    case_no = 0
    total_quota = sum(int(r["quota"]) for r in slot_records)

    for record, ai in zip(slot_records, alloc_index):
        if ai is None:
            continue
        assigned = sorted(results[ai], key=lambda item: item[0])
        record["generated"] = len(assigned)
        record["complete"] = len(assigned) == record["quota"]
        record["case_keys"] = [item[0] for item in assigned]
        request = next(
            req for req in requests
            if req["dataset_id"] == record["dataset_id"]
        )
        for key, errors in assigned:
            case_no += 1
            cases.append(_build_case(
                case_no, request, record["tier"], key, errors))

    complete = all(r["complete"] for r in slot_records)
    # 模板不需要内部键元组，summary 对外只给计数；case_keys 保留也无害，
    # 但键里没有额外业务信息，移除以保持 JSON 整洁。
    summary = []
    for record in slot_records:
        rec = {k: v for k, v in record.items() if k != "case_keys"}
        summary.append(rec)

    # 整包去重自检：尺寸+格值键必须与 case 数一致
    seen_keys = {table_key(c["simulated_recognition_grid"]) for c in cases}
    assert len(seen_keys) == len(cases), "整包去重失败：存在重复模拟表"

    return {
        "schema": "errata-sampling-package/v1",
        "kind": "sampling_package",
        "complete": complete,
        "凑齐": complete,
        "selected_table_count": len(requests),
        "max_tables": MAX_TABLES,
        "total_quota": total_quota,
        "case_count": len(cases),
        "total_matched": total_matched,
        "limits": {
            "max_tables": MAX_TABLES,
            "tier_min": TIER_MIN,
            "tier_max": TIER_MAX,
        },
        "dedupe": {
            "package_wide": True,
            "key": ["rows", "columns", "cell_values"],
            "description": "模拟识别表按行数、列数与全部格值整包去重",
        },
        "memory": {
            "retained_cases": len(cases),
            "bound_by_total_quota": total_quota,
            "max_lightweight_cache": max(
                (len(src.cache) for src in candidate_sources), default=0),
            "candidates_generated": sum(
                src.generated for src in candidate_sources),
            "note": "候选表流式生成、不整体物化；仅保留入选错例，"
                    "数量不超过所填名额总和；增广缓存只存去重键与错格描述",
        },
        "summary": summary,
        "cases": cases,
        "错例清单": cases,
    }
