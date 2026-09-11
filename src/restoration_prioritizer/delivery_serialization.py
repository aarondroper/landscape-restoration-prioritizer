"""Small deterministic serializers shared by static web-delivery artifacts."""

from __future__ import annotations

import json
import math
from typing import Any

import numpy as np

GEOMETRY_DECIMALS = 6


def round_number(value: float | int, decimals: int) -> float:
    rounded = float(f"{float(value):.{decimals}f}")
    return 0.0 if rounded == 0 else rounded


def round_coordinates(value: Any, decimals: int) -> Any:
    if isinstance(value, dict):
        return {key: round_coordinates(item, decimals) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [round_coordinates(item, decimals) for item in value]
    if isinstance(value, (float, int, np.floating, np.integer)):
        return round_number(value, decimals)
    return value


def serialize_feature_collection(features: list[dict[str, Any]]) -> bytes:
    """Serialize a compact deterministic GeoJSON FeatureCollection."""

    try:
        return json.dumps(
            {"type": "FeatureCollection", "features": features},
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("GeoJSON contains a non-serializable or non-finite value") from exc


def finite_coordinates(value: Any) -> bool:
    """Return whether every numeric value in a coordinate structure is finite."""

    if isinstance(value, dict):
        return all(finite_coordinates(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return all(finite_coordinates(item) for item in value)
    if isinstance(value, (float, int, np.floating, np.integer)):
        return math.isfinite(float(value))
    return True
