"""
Stage-3 组件三: 安全策略梯度强化学习。

学习一个场景 -> 电价的策略 pi_theta(p | s), 用策略梯度 (REINFORCE +
基线 + 熵正则) 训练。策略输出的电价送入带安全层的定价环境, 因此
训练与部署全过程严格可行。

优势: 训练一次后, 对新场景仅需一次前向推理 (毫秒级), 无需重复求解
MILP, 实现"场景到电价"的摊销式加速。
"""

import logging

import numpy as np

logger = logging.getLogger(__name__)

try:
    import torch
    import torch.nn as nn
    _HAS_TORCH = True
except Exception:  # noqa: BLE001
    _HAS_TORCH = False


class PolicyNet(nn.Module if _HAS_TORCH else object):
    """高斯策略: 场景特征 -> 电价分布。"""

    def __init__(self, n_features, T, p_lo, p_hi, hidden=128, seed=0):
        if not _HAS_TORCH:
            raise RuntimeError("未安装 PyTorch, 无法使用策略网络")
        super().__init__()
        torch.manual_seed(seed)
        self.T = T
        self.p_lo = float(p_lo)
        self.p_hi = float(p_hi)
        self.body = nn.Sequential(
            nn.Linear(n_features, hidden), nn.ReLU(),
            nn.Linear(hidden, hidden), nn.ReLU(),
        )
        self.mean_head = nn.Linear(hidden, T)
        self.logstd = nn.Parameter(torch.full((T,), -1.0))

    def forward(self, feat):
        h = self.body(feat)
        mean = self.p_lo + (self.p_hi - self.p_lo) * torch.sigmoid(self.mean_head(h))
        std = torch.nn.functional.softplus(self.logstd) + 1e-3
        return mean, std

    def sample(self, feat):
        mean, std = self.forward(feat)
        eps = torch.randn_like(mean)
        raw = mean + std * eps
        logp = (-0.5 * ((raw - mean) / std) ** 2 - torch.log(std)
                - 0.5 * np.log(2 * np.pi)).sum(dim=-1)
        return raw, logp, mean, std

    def act_deterministic(self, feat):
        mean, _ = self.forward(feat)
        return mean.detach().numpy()


class ValueNet(nn.Module if _HAS_TORCH else object):
    """状态价值网络, 作为策略梯度的基线 (actor-critic)。"""

    def __init__(self, n_features, hidden=128, seed=0):
        if not _HAS_TORCH:
            raise RuntimeError("未安装 PyTorch")
        super().__init__()
        torch.manual_seed(seed)
        self.net = nn.Sequential(
            nn.Linear(n_features, hidden), nn.ReLU(),
            nn.Linear(hidden, hidden), nn.ReLU(),
            nn.Linear(hidden, 1),
        )

    def forward(self, feat):
        return self.net(feat).squeeze(-1)


def pretrain_policy(policy, X, Y, epochs=600, lr=2e-3, batch=64, verbose=False):
    """用 MILP 示范数据行为克隆预训练策略均值 (learning-to-optimize)。"""
    if not _HAS_TORCH:
        raise RuntimeError("未安装 PyTorch")
    Xt = torch.tensor(np.asarray(X, dtype=np.float32))
    Yt = torch.tensor(np.asarray(Y, dtype=np.float32))
    opt = torch.optim.Adam(policy.parameters(), lr=lr)
    lossf = nn.MSELoss()
    n = len(Xt)
    for ep in range(epochs):
        perm = torch.randperm(n)
        tot = 0.0
        for s in range(0, n, batch):
            idx = perm[s:s + batch]
            opt.zero_grad()
            mean, _ = policy.forward(Xt[idx])
            loss = lossf(mean, Yt[idx])
            loss.backward()
            opt.step()
            tot += loss.item() * len(idx)
        if verbose and (ep + 1) % 200 == 0:
            logger.info("pretrain epoch %d: mse=%.5f", ep + 1, tot / n)
    return policy


def train_policy(env, episodes=800, lr=3e-3, value_lr=1e-3,
                 entropy_coef=0.005, seed=0, verbose=True, policy=None):
    """Actor-Critic 策略梯度训练 (A2C)。可传入预训练策略做微调。"""
    if not _HAS_TORCH:
        raise RuntimeError("未安装 PyTorch")
    torch.manual_seed(seed)
    rng = np.random.default_rng(seed)
    if policy is None:
        policy = PolicyNet(env.n_features, env.base.T,
                           min(env.base.price_levels), max(env.base.price_levels),
                           hidden=256, seed=seed)
    value = ValueNet(env.n_features, hidden=256, seed=seed)
    opt = torch.optim.Adam(policy.parameters(), lr=lr)
    vopt = torch.optim.Adam(value.parameters(), lr=value_lr)

    history = []
    for ep in range(episodes):
        scen = env.sample_scenario(rng)
        feat = torch.tensor(scen.features, dtype=torch.float32)
        raw, logp, mean, std = policy.sample(feat)
        prices = raw.detach().numpy()
        reward, feasible, _ = env.reward(prices, scen)

        r = torch.tensor(float(reward), dtype=torch.float32)
        v = value(feat)
        adv = (r - v).detach()

        entropy = (torch.log(std) + 0.5 * (1 + np.log(2 * np.pi))).sum()
        policy_loss = -logp * adv - entropy_coef * entropy
        value_loss = 0.5 * (r - v) ** 2

        opt.zero_grad()
        policy_loss.backward()
        opt.step()
        vopt.zero_grad()
        value_loss.backward()
        vopt.step()

        history.append(reward)
        if verbose and (ep + 1) % 200 == 0:
            logger.info("RL episode %d: 平均奖励=%.2f", ep + 1,
                        float(np.mean(history[-200:])))
    return policy, history


def evaluate_policy(env, policy, n=20, seed=123):
    """在测试场景上评估策略 (确定性电价)。"""
    rng = np.random.default_rng(seed)
    profits = []
    feasible_cnt = 0
    for _ in range(n):
        scen = env.sample_scenario(rng)
        feat = torch.tensor(scen.features, dtype=torch.float32)
        prices = policy.act_deterministic(feat)
        profit, feasible, _ = env.reward(prices, scen)
        profits.append(profit)
        feasible_cnt += int(feasible)
    return {"mean_profit": float(np.mean(profits)),
            "std_profit": float(np.std(profits)),
            "feasibility_rate": feasible_cnt / n,
            "profits": profits}
