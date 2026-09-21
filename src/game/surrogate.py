"""
Stage-3 组件一: 可微最佳反应代理 (Best-Response Surrogate)。

用神经网络学习下层 EV 用户的响应映射
    p (电价向量, T)  ->  E (总充电负荷曲线, T)
训练数据由精确跟随者 LP 在随机电价下生成。

用途:
  1. 在 BRI/优化内循环中以毫秒级前向推理替代下层 LP, 加速求解;
  2. 提供关于电价的梯度, 支撑端到端可微优化。

实现基于 PyTorch。若环境无 torch, 相关函数会给出明确报错。
"""

import logging

import numpy as np

from src.game.case import GameCase
from src.game.follower import solve_follower_response

logger = logging.getLogger(__name__)

try:
    import torch
    import torch.nn as nn
    _HAS_TORCH = True
except Exception:  # noqa: BLE001
    _HAS_TORCH = False


def sample_prices(case: GameCase, n, seed=0, continuous=True):
    """随机采样电价向量 (连续均匀 / 离散档位)。"""
    rng = np.random.default_rng(seed)
    lo, hi = min(case.price_levels), max(case.price_levels)
    if continuous:
        return rng.uniform(lo, hi, size=(n, case.T))
    levels = np.array(case.price_levels)
    idx = rng.integers(0, len(levels), size=(n, case.T))
    return levels[idx]


def build_dataset(case: GameCase, n=1500, seed=0, continuous=True):
    """生成 (电价, 总负荷) 数据集。"""
    P = sample_prices(case, n, seed=seed, continuous=continuous)
    Y = np.zeros((n, case.T))
    for i in range(n):
        resp = solve_follower_response(case, list(P[i]))
        Y[i] = [sum(resp[u.name][t] for u in case.users) for t in range(case.T)]
    logger.info("数据集生成完成: %d 样本", n)
    return P.astype(np.float32), Y.astype(np.float32)


class BestResponseSurrogate:
    """p -> E 的 MLP 代理模型。"""

    def __init__(self, T, hidden=128, seed=0):
        if not _HAS_TORCH:
            raise RuntimeError("未安装 PyTorch, 无法使用代理模型")
        torch.manual_seed(seed)
        self.T = T
        self.net = nn.Sequential(
            nn.Linear(T, hidden), nn.ReLU(),
            nn.Linear(hidden, hidden), nn.ReLU(),
            nn.Linear(hidden, T),
        )
        self.p_mean = None
        self.p_std = None
        self.y_scale = None

    def fit(self, P, Y, epochs=400, lr=1e-3, batch=128, verbose=False):
        """标准化 + 训练。"""
        self.p_mean = P.mean(axis=0, keepdims=True)
        self.p_std = P.std(axis=0, keepdims=True) + 1e-6
        self.y_scale = Y.std() + 1e-6
        Pn = (P - self.p_mean) / self.p_std
        Yn = Y / self.y_scale

        pt = torch.tensor(Pn, dtype=torch.float32)
        yt = torch.tensor(Yn, dtype=torch.float32)
        opt = torch.optim.Adam(self.net.parameters(), lr=lr)
        lossf = nn.MSELoss()
        n = len(pt)
        for ep in range(epochs):
            perm = torch.randperm(n)
            tot = 0.0
            for s in range(0, n, batch):
                idx = perm[s:s + batch]
                opt.zero_grad()
                out = self.net(pt[idx])
                loss = lossf(out, yt[idx])
                loss.backward()
                opt.step()
                tot += loss.item() * len(idx)
            if verbose and (ep + 1) % 100 == 0:
                logger.info("surrogate epoch %d: loss=%.5f", ep + 1, tot / n)
        return self

    def predict(self, prices):
        """输入单个电价向量, 返回预测总负荷 (numpy, 长度 T)。"""
        p = np.asarray(prices, dtype=np.float32).reshape(1, -1)
        pn = (p - self.p_mean) / self.p_std
        with torch.no_grad():
            out = self.net(torch.tensor(pn)).numpy()[0]
        return np.maximum(out * self.y_scale, 0.0)

    def predict_batch(self, P):
        pn = (np.asarray(P, dtype=np.float32) - self.p_mean) / self.p_std
        with torch.no_grad():
            out = self.net(torch.tensor(pn)).numpy()
        return np.maximum(out * self.y_scale, 0.0)


def evaluate_surrogate(model, case, n=200, seed=99):
    """在测试集上评估代理精度 (MAE / 相对误差)。"""
    P, Y = build_dataset(case, n=n, seed=seed)
    Yp = model.predict_batch(P)
    mae = float(np.mean(np.abs(Yp - Y)))
    denom = float(np.mean(np.abs(Y))) + 1e-6
    rel = mae / denom
    logger.info("代理精度: MAE=%.3f kWh, 相对误差=%.2f%%", mae, 100 * rel)
    return {"mae_kwh": mae, "rel_error": rel}
