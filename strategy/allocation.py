"""Allocation and plan helpers for LongBridgePlatform."""

import os

from strategy_loader import load_allocation_module

_ALLOCATION_MODULE = load_allocation_module(os.getenv("STRATEGY_PROFILE"))

build_rebalance_plan = _ALLOCATION_MODULE.build_rebalance_plan
get_dynamic_allocation = _ALLOCATION_MODULE.get_dynamic_allocation
get_income_layer_ratio = _ALLOCATION_MODULE.get_income_layer_ratio

__all__ = [
    "build_rebalance_plan",
    "get_dynamic_allocation",
    "get_income_layer_ratio",
]
