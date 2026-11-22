"""Imitation-trained price warm starts; exact BRI remains the decision maker.

This is a supervised retrieval agent, not reinforcement learning. It never
outputs storage power or bypasses the follower/leader/safety constraints.
"""
from __future__ import annotations

import numpy as np


def case_features(case):
    """Per-slot observable features, using fixed engineering scales."""
    rows = []
    for t in range(case.T):
        dt = float(case.interval_hours[t])
        eligible = sum(u.energy_max_kwh / max(len(u.available), 1)
                       for u in case.users if t in u.available)
        rows.append([case.pv[t] / 400, case.wind[t] / 100,
                     case.base_load[t] / 500, eligible / 500,
                     case.battery.soc0, case.battery.capacity_kwh / 2000,
                     case.battery.deg_cost_per_kwh, dt,
                     t / max(case.T - 1, 1)])
    return rows


class PriceWarmStartAgent:
    def __init__(self, examples, neighbors=1):
        self.examples = examples
        self.neighbors = int(neighbors)
        if not examples or self.neighbors < 1:
            raise ValueError("nonempty training examples and positive neighbors required")
        self.x = np.asarray([r['features'] for r in examples], dtype=float)
        self.y = np.asarray([r['price'] for r in examples], dtype=float)

    def predict(self, case):
        prices = []
        for features in case_features(case):
            distances = np.sum((self.x - np.asarray(features)) ** 2, axis=1)
            nearest = np.argsort(distances, kind='stable')[:self.neighbors]
            prediction = float(np.mean(self.y[nearest]))
            prices.append(min(case.price_levels, key=lambda p: abs(p - prediction)))
        return prices

    def solve(self, case, solver, **kwargs):
        kwargs['init_prices'] = self.predict(case)
        result = solver(case, **kwargs)
        result['warm_start_agent'] = 'supervised_knn_price_initialization'
        return result
