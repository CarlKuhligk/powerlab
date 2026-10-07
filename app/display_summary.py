"""Mergeable display statistics, computed before discarding acquisition samples.

All averages use received sample counts (never log-transformed values). Missing
device ticks remain separate segments. Display summaries do not affect charge.
"""
from __future__ import annotations

import math
from typing import Any


def merge_summary(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    count = left["sample_count"] + right["sample_count"]
    total = left["sum_ua"] + right["sum_ua"]
    return {
        **left,
        "end_sample": right["end_sample"],
        "last_ua": right["last_ua"],
        "sample_count": count,
        "sum_ua": total,
        "current_ua": total / count,
        "min_ua": min(left["min_ua"], right["min_ua"]),
        "max_ua": max(left["max_ua"], right["max_ua"]),
    }


def compact_summaries(points: list[dict[str, Any]], max_points: int) -> list[dict[str, Any]]:
    """Use aligned time buckets, retaining segment breaks and weighted statistics.

    A compacted block is indivisible: zoom never pretends to recover its samples.
    If gaps alone exceed the budget, return isolated representative blocks rather
    than fabricate continuity across omitted intervals.
    """
    if len(points) <= max_points:
        return points
    width = max(1, math.ceil((points[-1]["end_sample"] - points[0]["sample_index"] + 1) / max_points))
    while True:
        result = []
        for point in points:
            if (result and result[-1]["line_key"] == point["line_key"]
                    and result[-1]["end_sample"] + 1 == point["sample_index"]
                    and result[-1]["sample_index"] // width == point["sample_index"] // width):
                result[-1] = merge_summary(result[-1], point)
            else:
                result.append(point)
        if len(result) <= max_points:
            return result
        if width > points[-1]["end_sample"] + 1:
            # More disjoint segments than display slots. Keep global extrema too.
            selected = {0, len(result) - 1,
                        min(range(len(result)), key=lambda i: result[i]["min_ua"]),
                        max(range(len(result)), key=lambda i: result[i]["max_ua"])}
            for slot in range(max_points):
                if len(selected) >= max_points:
                    break
                selected.add(round(slot * (len(result) - 1) / max(1, max_points - 1)))
            return [{**result[i], "line_key": f"{result[i]['line_key']}-isolated-{i}"}
                    for i in sorted(selected)]
        width *= 2
