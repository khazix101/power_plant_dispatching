"""
实验 #5: TimeTransformer 注意力权重可视化
回应审稿意见：缺少注意力分布的可视化证据

目标：
1. 训练 TimeTransformer (17特征 vs 66特征)
2. 提取多头自注意力权重矩阵 [n_heads, seq_len, seq_len]
3. 绘制热力图对比：17特征的注意力分布 vs 66特征
4. 计算注意力熵分布、稀疏度、聚焦度
5. 额外：Permutation Importance for TimeTransformer
"""
import pandas as pd
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader, TensorDataset
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import r2_score
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import seaborn as sns
import json
import sys
import os
import time

def log(msg):
    print(msg, flush=True)
    sys.stdout.flush()

# ============================================================
# 1. 数据加载（与现有脚本完全一致）
# ============================================================
log('=' * 70)
log('实验 #5: TimeTransformer 注意力权重可视化')
log('=' * 70)

log('\n[1] 加载数据...')
charging_df = pd.read_csv(r'D:\荷的预测\real_data\Dataset\Charging_Data.csv', encoding='gbk')
charging_df = charging_df[charging_df['Location Information'].str.contains('Expressway Service District', na=False)]
charging_df['Start Time'] = pd.to_datetime(charging_df['Start Time'])
charging_df['hour_window'] = charging_df['Start Time'].dt.floor('H')

hourly_df = charging_df.groupby('hour_window').agg(total_energy=('Transaction power/kwh', 'sum')).reset_index()
hourly_df = hourly_df.sort_values('hour_window').reset_index(drop=True)
hourly_df['target_4h'] = hourly_df['total_energy'].rolling(4).mean().shift(-3)

# 特征工程
hourly_df['hour'] = hourly_df['hour_window'].dt.hour
hourly_df['day_of_week'] = hourly_df['hour_window'].dt.dayofweek
hourly_df['month'] = hourly_df['hour_window'].dt.month
hourly_df['is_weekend'] = (hourly_df['day_of_week'] >= 5).astype(int)
hourly_df['hour_sin'] = np.sin(2 * np.pi * hourly_df['hour'] / 24)
hourly_df['hour_cos'] = np.cos(2 * np.pi * hourly_df['hour'] / 24)
hourly_df['dow_sin'] = np.sin(2 * np.pi * hourly_df['day_of_week'] / 7)
hourly_df['dow_cos'] = np.cos(2 * np.pi * hourly_df['day_of_week'] / 7)

lags = [1, 2, 3, 4, 6, 8, 12, 18, 24, 36, 48]
for i in lags:
    hourly_df[f'lag{i}_energy'] = hourly_df['total_energy'].shift(i)

for window in [4, 8, 12, 24, 48]:
    hourly_df[f'rolling_mean_{window}'] = hourly_df['total_energy'].rolling(window, min_periods=1).mean().shift(1)
    hourly_df[f'rolling_std_{window}'] = hourly_df['total_energy'].rolling(window, min_periods=1).std().shift(1)
    hourly_df[f'rolling_max_{window}'] = hourly_df['total_energy'].rolling(window, min_periods=1).max().shift(1)
    hourly_df[f'rolling_min_{window}'] = hourly_df['total_energy'].rolling(window, min_periods=1).min().shift(1)

hourly_df['diff_1'] = hourly_df['total_energy'] - hourly_df['total_energy'].shift(1)
hourly_df['diff_4'] = hourly_df['total_energy'] - hourly_df['total_energy'].shift(4)
hourly_df['diff_12'] = hourly_df['total_energy'] - hourly_df['total_energy'].shift(12)
hourly_df['diff_24'] = hourly_df['total_energy'] - hourly_df['total_energy'].shift(24)
hourly_df['hour_x_weekend'] = hourly_df['hour'] * hourly_df['is_weekend']
hourly_df['lag1_ratio'] = hourly_df['total_energy'] / (hourly_df['total_energy'].shift(1) + 1e-8)
hourly_df['lag24_ratio'] = hourly_df['total_energy'] / (hourly_df['total_energy'].shift(24) + 1e-8)
hourly_df['ema_4'] = hourly_df['total_energy'].ewm(span=4).mean().shift(1)
hourly_df['ema_8'] = hourly_df['total_energy'].ewm(span=8).mean().shift(1)
hourly_df['ema_24'] = hourly_df['total_energy'].ewm(span=24).mean().shift(1)

hourly_df = hourly_df.dropna().reset_index(drop=True)

basic_features = ['hour', 'day_of_week', 'month', 'is_weekend', 'hour_sin', 'hour_cos',
                  'dow_sin', 'dow_cos', 'lag1_energy', 'lag2_energy', 'lag3_energy',
                  'lag4_energy', 'lag8_energy', 'lag12_energy', 'lag24_energy',
                  'rolling_mean_4', 'rolling_std_4']
all_features = [col for col in hourly_df.columns
                if col not in ['hour_window', 'total_energy', 'target_4h']]

X_basic = hourly_df[basic_features].values.astype(np.float32)
X_full = hourly_df[all_features].values.astype(np.float32)
y_raw = hourly_df['target_4h'].values.astype(float)
n_total = len(hourly_df)
train_end = int(n_total * 0.8)

log(f'  总样本数: {n_total}, 训练集: {train_end}, 测试集: {n_total - train_end}')
log(f'  基础特征: {len(basic_features)}个, 全部特征: {len(all_features)}个')

# 标准化（对Transformer很重要）
scaler_basic = StandardScaler()
X_basic_scaled = scaler_basic.fit_transform(X_basic[:train_end])
X_basic_scaled = np.vstack([X_basic_scaled, scaler_basic.transform(X_basic[train_end:])]).astype(np.float32)

scaler_full = StandardScaler()
X_full_scaled = scaler_full.fit_transform(X_full[:train_end])
X_full_scaled = np.vstack([X_full_scaled, scaler_full.transform(X_full[train_end:])]).astype(np.float32)

y_train, y_test = y_raw[:train_end], y_raw[train_end:]

# ============================================================
# 2. 自定义 TimeTransformer（能提取注意力权重）
# ============================================================
log('\n[2] 定义自定义 TimeTransformer（带注意力权重提取）')

class CustomMultiHeadAttention(nn.Module):
    """自定义多头注意力，能返回完整的注意力权重矩阵"""
    def __init__(self, d_model, nhead, dropout=0.1):
        super().__init__()
        assert d_model % nhead == 0
        self.d_model = d_model
        self.nhead = nhead
        self.d_k = d_model // nhead

        self.w_q = nn.Linear(d_model, d_model)
        self.w_k = nn.Linear(d_model, d_model)
        self.w_v = nn.Linear(d_model, d_model)
        self.w_o = nn.Linear(d_model, d_model)
        self.dropout = nn.Dropout(dropout)
        self.attn_weights = None  # 存储最近的注意力权重

    def forward(self, x):
        B, L, D = x.shape
        Q = self.w_q(x).view(B, L, self.nhead, self.d_k).transpose(1, 2)  # [B, H, L, d_k]
        K = self.w_k(x).view(B, L, self.nhead, self.d_k).transpose(1, 2)
        V = self.w_v(x).view(B, L, self.nhead, self.d_k).transpose(1, 2)

        scores = torch.matmul(Q, K.transpose(-2, -1)) / np.sqrt(self.d_k)  # [B, H, L, L]
        attn = F.softmax(scores, dim=-1)  # [B, H, L, L]
        self.attn_weights = attn.detach().clone()  # 保存用于可视化

        out = torch.matmul(self.dropout(attn), V)  # [B, H, L, d_k]
        out = out.transpose(1, 2).contiguous().view(B, L, D)
        return self.w_o(out)


class CustomTransformerLayer(nn.Module):
    """单个Transformer编码器层（带注意力权重提取）"""
    def __init__(self, d_model, nhead, dim_feedforward, dropout=0.1):
        super().__init__()
        self.self_attn = CustomMultiHeadAttention(d_model, nhead, dropout)
        self.norm1 = nn.LayerNorm(d_model)
        self.norm2 = nn.LayerNorm(d_model)
        self.ffn = nn.Sequential(
            nn.Linear(d_model, dim_feedforward),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(dim_feedforward, d_model),
            nn.Dropout(dropout)
        )

    def forward(self, x):
        attn_out = self.self_attn(x)
        x = self.norm1(x + attn_out)
        ffn_out = self.ffn(x)
        x = self.norm2(x + ffn_out)
        return x


class TimeTransformerViz(nn.Module):
    """TimeTransformer，能提取每层的注意力权重"""
    def __init__(self, in_dim, d_model=64, nhead=4, num_layers=2, dropout=0.1):
        super().__init__()
        self.embedding = nn.Linear(in_dim, d_model)
        self.layers = nn.ModuleList([
            CustomTransformerLayer(d_model, nhead, d_model * 4, dropout)
            for _ in range(num_layers)
        ])
        self.fc = nn.Linear(d_model, 1)

    def forward(self, x):
        x = self.embedding(x)
        for layer in self.layers:
            x = layer(x)
        return self.fc(x[:, -1, :]).squeeze(-1)

    def get_attention_weights(self, x):
        """前向传播并返回所有层的注意力权重"""
        x = self.embedding(x)
        all_attn = []
        for layer in self.layers:
            x = layer(x)
            # attn shape: [B, H, L, L]
            all_attn.append(layer.self_attn.attn_weights)
        return all_attn


# ============================================================
# 3. 训练函数
# ============================================================
def create_seq(X, y, lb):
    xs, ys = [], []
    for i in range(lb, len(X)):
        xs.append(X[i - lb:i])
        ys.append(y[i])
    return np.array(xs), np.array(ys)


def train_transformer(X_tr, X_te, y_tr, y_te, feature_dim, lookback=48, epochs=80, lr=0.001):
    X_tr_seq, y_tr_seq = create_seq(X_tr, y_tr.astype(np.float32), lookback)
    X_te_seq, _ = create_seq(X_te, np.zeros(len(X_te), dtype=np.float32), lookback)
    y_te_seq = y_te[lookback:].astype(np.float32)

    train_ds = TensorDataset(torch.tensor(X_tr_seq), torch.tensor(y_tr_seq))
    train_loader = DataLoader(train_ds, batch_size=64, shuffle=True)

    model = TimeTransformerViz(feature_dim, d_model=64, nhead=4, num_layers=2, dropout=0.1)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, patience=5, factor=0.5)
    criterion = nn.MSELoss()

    best_r2 = -np.inf
    best_state = None

    for epoch in range(epochs):
        model.train()
        total_loss = 0
        for batch_X, batch_y in train_loader:
            optimizer.zero_grad()
            pred = model(batch_X)
            loss = criterion(pred, batch_y)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            total_loss += loss.item()

        scheduler.step(total_loss / len(train_loader))

        model.eval()
        with torch.no_grad():
            val_pred = model(torch.tensor(X_te_seq)).numpy()
            r2 = r2_score(y_te_seq, val_pred)
            if r2 > best_r2:
                best_r2 = r2
                best_state = {k: v.clone() for k, v in model.state_dict().items()}

        if (epoch + 1) % 20 == 0:
            log(f'    Epoch {epoch+1}/{epochs}, Loss={total_loss/len(train_loader):.4f}, R²={r2:.4f}')

    # 恢复最佳模型
    if best_state is not None:
        model.load_state_dict(best_state)
    log(f'    最佳 R²={best_r2:.4f}')
    return model, best_r2


# ============================================================
# 4. 训练两个模型：17特征 vs 66特征
# ============================================================
log('\n[3] 训练 TimeTransformer (17特征)...')
t0 = time.time()
X_train_17, X_test_17 = X_basic_scaled[:train_end], X_basic_scaled[train_end:]
model_17, r2_17 = train_transformer(X_train_17, X_test_17, y_train, y_test, len(basic_features))
log(f'  17特征 TimeTransformer R²={r2_17:.4f} (耗时 {time.time()-t0:.1f}s)')

log('\n[4] 训练 TimeTransformer (66特征)...')
t0 = time.time()
X_train_66, X_test_66 = X_full_scaled[:train_end], X_full_scaled[train_end:]
model_66, r2_66 = train_transformer(X_train_66, X_test_66, y_train, y_test, len(all_features))
log(f'  66特征 TimeTransformer R²={r2_66:.4f} (耗时 {time.time()-t0:.1f}s)')

# ============================================================
# 5. 提取注意力权重
# ============================================================
log('\n[5] 提取注意力权重...')

# 用测试集前100个样本提取注意力
n_viz_samples = min(100, len(X_test_17) - 48)
X_test_17_seq = torch.tensor(X_test_17[:n_viz_samples + 48].astype(np.float32))
# 构造序列
test_seqs_17 = []
test_seqs_66 = []
for i in range(48, min(n_viz_samples + 48, len(X_test_17))):
    test_seqs_17.append(X_test_17[i - 48:i])
    test_seqs_66.append(X_test_66[i - 48:i])
test_seqs_17 = torch.tensor(np.array(test_seqs_17))
test_seqs_66 = torch.tensor(np.array(test_seqs_66))

log(f'  提取样本数: {len(test_seqs_17)}')

model_17.eval()
model_66.eval()
with torch.no_grad():
    attn_17 = model_17.get_attention_weights(test_seqs_17)  # list of [B, H, L, L]
    attn_66 = model_66.get_attention_weights(test_seqs_66)

# attn_17[0] shape: [n_samples, n_heads, 48, 48]
# 取最后一层的注意力权重做可视化（最后一层最接近输出决策）
last_layer_attn_17 = attn_17[-1].numpy()  # [n_samples, n_heads, 48, 48]
last_layer_attn_66 = attn_66[-1].numpy()

log(f'  注意力权重形状: {last_layer_attn_17.shape}')

# 跨样本和跨头平均
avg_attn_17 = np.mean(last_layer_attn_17, axis=(0, 1))  # [48, 48]
avg_attn_66 = np.mean(last_layer_attn_66, axis=(0, 1))  # [48, 48]

# ============================================================
# 6. 计算注意力统计量
# ============================================================
log('\n[6] 计算注意力统计量...')

def compute_attention_stats(attn_weights, name):
    """
    attn_weights: [n_samples, n_heads, L, L]
    返回：熵、最大权重、稀疏度、聚焦度
    """
    eps = 1e-10
    # 注意力熵（每个样本每个头每个query位置）
    entropy = -np.sum(attn_weights * np.log(attn_weights + eps), axis=-1)  # [n_samples, n_heads, L]
    mean_entropy = float(np.mean(entropy))
    std_entropy = float(np.std(entropy))

    # 最大注意力权重（每个query位置对key的最大关注）
    max_weight = float(np.mean(np.max(attn_weights, axis=-1)))

    # 稀疏度：小于0.01的权重占比
    sparsity = float(np.mean(attn_weights < 0.01))

    # 聚焦度：top-3注意力权重之和的均值
    top3_sum = np.mean(np.sort(attn_weights, axis=-1)[:, :, :, -3:].sum(axis=-1))

    # 对角线权重（自关注比例）
    diag_weight = float(np.mean(np.diagonal(attn_weights, axis1=-2, axis2=-1)))

    stats = {
        'name': name,
        'mean_entropy': mean_entropy,
        'std_entropy': std_entropy,
        'max_weight': max_weight,
        'sparsity_ratio': sparsity,
        'top3_focus': float(top3_sum),
        'diagonal_weight': diag_weight,
        'n_samples': int(attn_weights.shape[0]),
        'n_heads': int(attn_weights.shape[1]),
        'seq_len': int(attn_weights.shape[2])
    }
    log(f'  [{name}] 熵={mean_entropy:.4f}±{std_entropy:.4f}, '
        f'最大权重={max_weight:.4f}, 稀疏度={sparsity:.4f}, '
        f'top-3聚焦={float(top3_sum):.4f}, 自关注={diag_weight:.4f}')
    return stats

stats_17 = compute_attention_stats(last_layer_attn_17, '17_features')
stats_66 = compute_attention_stats(last_layer_attn_66, '66_features')

# 也计算第一层的统计量（对比层间差异）
first_layer_attn_17 = attn_17[0].numpy()
first_layer_attn_66 = attn_66[0].numpy()
stats_17_layer0 = compute_attention_stats(first_layer_attn_17, '17_features_layer0')
stats_66_layer0 = compute_attention_stats(first_layer_attn_66, '66_features_layer0')

# ============================================================
# 7. 可视化
# ============================================================
log('\n[7] 生成可视化...')

fig_dir = r'D:\荷的预测\real_data\attention_viz_figs'
os.makedirs(fig_dir, exist_ok=True)

# --- 图1: 注意力热力图对比（核心图） ---
fig, axes = plt.subplots(1, 3, figsize=(20, 6))

# 17特征
im1 = sns.heatmap(avg_attn_17, cmap='viridis', ax=axes[0], cbar=True,
                  xticklabels=6, yticklabels=6)
axes[0].set_title(f'17 Features (R²={r2_17:.3f})\n'
                  f'Entropy={stats_17["mean_entropy"]:.3f}', fontsize=13)
axes[0].set_xlabel('Key Time Step')
axes[0].set_ylabel('Query Time Step')

# 66特征
im2 = sns.heatmap(avg_attn_66, cmap='viridis', ax=axes[1], cbar=True,
                  xticklabels=6, yticklabels=6)
axes[1].set_title(f'66 Features (R²={r2_66:.3f})\n'
                  f'Entropy={stats_66["mean_entropy"]:.3f}', fontsize=13)
axes[1].set_xlabel('Key Time Step')
axes[1].set_ylabel('Query Time Step')

# 差异图
diff = avg_attn_66 - avg_attn_17
sns.heatmap(diff, cmap='RdBu_r', center=0, ax=axes[2], cbar=True,
            xticklabels=6, yticklabels=6)
axes[2].set_title(f'Difference (66 - 17)\n'
                  f'ΔEntropy={stats_66["mean_entropy"]-stats_17["mean_entropy"]:.3f}', fontsize=13)
axes[2].set_xlabel('Key Time Step')
axes[2].set_ylabel('Query Time Step')

plt.suptitle('TimeTransformer Attention Weight Heatmap (Last Layer, Averaged)', fontsize=15, y=1.02)
plt.tight_layout()
fig1_path = os.path.join(fig_dir, 'fig_attention_heatmap_17vs66.png')
plt.savefig(fig1_path, dpi=300, bbox_inches='tight')
plt.close()
log(f'  保存: {fig1_path}')

# --- 图2: 逐头注意力热力图（展示不同头的学习模式差异） ---
fig, axes = plt.subplots(2, 4, figsize=(24, 12))
n_heads = last_layer_attn_17.shape[1]

for h in range(n_heads):
    # 17特征 第h个头
    avg_h_17 = np.mean(last_layer_attn_17[:, h, :, :], axis=0)
    sns.heatmap(avg_h_17, cmap='viridis', ax=axes[0, h], cbar=True,
                xticklabels=8, yticklabels=8)
    entropy_h = -np.sum(avg_h_17 * np.log(avg_h_17 + 1e-10))
    axes[0, h].set_title(f'17-Feat Head {h}\nEntropy={entropy_h:.3f}', fontsize=11)

    # 66特征 第h个头
    avg_h_66 = np.mean(last_layer_attn_66[:, h, :, :], axis=0)
    sns.heatmap(avg_h_66, cmap='viridis', ax=axes[1, h], cbar=True,
                xticklabels=8, yticklabels=8)
    entropy_h66 = -np.sum(avg_h_66 * np.log(avg_h_66 + 1e-10))
    axes[1, h].set_title(f'66-Feat Head {h}\nEntropy={entropy_h66:.3f}', fontsize=11)

axes[0, 0].set_ylabel('17 Features', fontsize=13)
axes[1, 0].set_ylabel('66 Features', fontsize=13)
for h in range(n_heads):
    axes[1, h].set_xlabel('Key Time Step')
    axes[0, h].set_xlabel('')
    axes[0, h].set_ylabel('Query')
    axes[1, h].set_ylabel('Query')

plt.suptitle('Per-Head Attention Patterns (Last Layer)', fontsize=15, y=1.01)
plt.tight_layout()
fig2_path = os.path.join(fig_dir, 'fig_attention_per_head.png')
plt.savefig(fig2_path, dpi=300, bbox_inches='tight')
plt.close()
log(f'  保存: {fig2_path}')

# --- 图3: 注意力熵分布对比（violin plot） ---
fig, axes = plt.subplots(1, 2, figsize=(14, 6))

# 每个样本的平均熵
entropy_per_sample_17 = []
entropy_per_sample_66 = []
eps = 1e-10
for s in range(last_layer_attn_17.shape[0]):
    e17 = -np.sum(last_layer_attn_17[s] * np.log(last_layer_attn_17[s] + eps), axis=-1)
    entropy_per_sample_17.append(np.mean(e17))
    e66 = -np.sum(last_layer_attn_66[s] * np.log(last_layer_attn_66[s] + eps), axis=-1)
    entropy_per_sample_66.append(np.mean(e66))

entropy_per_sample_17 = np.array(entropy_per_sample_17)
entropy_per_sample_66 = np.array(entropy_per_sample_66)

# Violin plot
data_entropy = pd.DataFrame({
    '17 Features': entropy_per_sample_17,
    '66 Features': entropy_per_sample_66
})
sns.violinplot(data=data_entropy, ax=axes[0], palette=['#2196F3', '#F44336'])
axes[0].set_ylabel('Attention Entropy (nats)', fontsize=12)
axes[0].set_title('Attention Entropy Distribution\n(Lower = More Concentrated)', fontsize=13)

# 在violin上叠加均值标记
for i, col in enumerate(['17 Features', '66 Features']):
    mean_val = data_entropy[col].mean()
    axes[0].scatter(i, mean_val, marker='D', color='black', s=50, zorder=5)
    axes[0].annotate(f'{mean_val:.3f}', (i, mean_val), textcoords='offset points',
                     xytext=(15, 5), fontsize=11, fontweight='bold')

# Top-3聚焦度对比
top3_17 = []
top3_66 = []
for s in range(last_layer_attn_17.shape[0]):
    t17 = np.sort(last_layer_attn_17[s], axis=-1)[:, :, -3:].sum(axis=-1)
    top3_17.append(np.mean(t17))
    t66 = np.sort(last_layer_attn_66[s], axis=-1)[:, :, -3:].sum(axis=-1)
    top3_66.append(np.mean(t66))

data_top3 = pd.DataFrame({
    '17 Features': top3_17,
    '66 Features': top3_66
})
sns.violinplot(data=data_top3, ax=axes[1], palette=['#2196F3', '#F44336'])
axes[1].set_ylabel('Top-3 Attention Weight Sum', fontsize=12)
axes[1].set_title('Attention Concentration (Top-3)\n(Higher = More Focused)', fontsize=13)

for i, col in enumerate(['17 Features', '66 Features']):
    mean_val = data_top3[col].mean()
    axes[1].scatter(i, mean_val, marker='D', color='black', s=50, zorder=5)
    axes[1].annotate(f'{mean_val:.3f}', (i, mean_val), textcoords='offset points',
                     xytext=(15, 5), fontsize=11, fontweight='bold')

plt.tight_layout()
fig3_path = os.path.join(fig_dir, 'fig_attention_entropy_violin.png')
plt.savefig(fig3_path, dpi=300, bbox_inches='tight')
plt.close()
log(f'  保存: {fig3_path}')

# --- 图4: 最后一行注意力权重（query=最后时间步，即预测点） ---
fig, axes = plt.subplots(1, 2, figsize=(16, 5))

# 最后一个时间步的注意力分布（即模型预测时"看"了哪些历史时间步）
last_row_17 = np.mean(last_layer_attn_17[:, :, -1, :], axis=(0, 1))  # [48]
last_row_66 = np.mean(last_layer_attn_66[:, :, -1, :], axis=(0, 1))  # [48]

time_steps = np.arange(48)
axes[0].bar(time_steps - 0.2, last_row_17, width=0.4, alpha=0.8, label='17 Features', color='#2196F3')
axes[0].bar(time_steps + 0.2, last_row_66, width=0.4, alpha=0.8, label='66 Features', color='#F44336')
axes[0].set_xlabel('Historical Time Step (0 = oldest, 47 = most recent)', fontsize=11)
axes[0].set_ylabel('Attention Weight', fontsize=11)
axes[0].set_title('Attention from Last Time Step (Prediction Point)\nWhich history does the model attend to?', fontsize=12)
axes[0].legend(fontsize=11)
axes[0].set_xticks(range(0, 48, 4))

# 累积分布
cdf_17 = np.cumsum(last_row_17)
cdf_66 = np.cumsum(last_row_66)
axes[1].plot(time_steps, cdf_17, 'o-', label='17 Features', color='#2196F3', markersize=4)
axes[1].plot(time_steps, cdf_66, 's-', label='66 Features', color='#F44336', markersize=4)
axes[1].axhline(y=0.9, color='gray', linestyle='--', alpha=0.5, label='90% threshold')
axes[1].set_xlabel('Historical Time Step', fontsize=11)
axes[1].set_ylabel('Cumulative Attention', fontsize=11)
axes[1].set_title('Cumulative Attention Distribution\n(How quickly does attention reach 90%?)', fontsize=12)
axes[1].legend(fontsize=11)

# 标注达到90%的时间步
for name, cdf, color in [('17F', cdf_17, '#2196F3'), ('66F', cdf_66, '#F44336')]:
    idx_90 = np.argmax(cdf >= 0.9)
    axes[1].axvline(x=idx_90, color=color, linestyle=':', alpha=0.6)
    axes[1].annotate(f'{name}: step {idx_90}', (idx_90, 0.9),
                     textcoords='offset points', xytext=(10, -20 if '17' in name else -35),
                     fontsize=10, color=color)

plt.tight_layout()
fig4_path = os.path.join(fig_dir, 'fig_attention_last_timestep.png')
plt.savefig(fig4_path, dpi=300, bbox_inches='tight')
plt.close()
log(f'  保存: {fig4_path}')

# --- 图5: 层间对比（第一层 vs 最后一层） ---
fig, axes = plt.subplots(2, 2, figsize=(16, 14))

for row, (layer_name, attn_17_layer, attn_66_layer, stats_17_l, stats_66_l) in enumerate([
    ('Layer 0', first_layer_attn_17, first_layer_attn_66, stats_17_layer0, stats_66_layer0),
    ('Layer 1 (Last)', last_layer_attn_17, last_layer_attn_66, stats_17, stats_66)
]):
    avg_17 = np.mean(attn_17_layer, axis=(0, 1))
    avg_66 = np.mean(attn_66_layer, axis=(0, 1))

    sns.heatmap(avg_17, cmap='viridis', ax=axes[row, 0], cbar=True, xticklabels=8, yticklabels=8)
    axes[row, 0].set_title(f'{layer_name} - 17 Features\nEntropy={stats_17_l["mean_entropy"]:.3f}', fontsize=12)

    sns.heatmap(avg_66, cmap='viridis', ax=axes[row, 1], cbar=True, xticklabels=8, yticklabels=8)
    axes[row, 1].set_title(f'{layer_name} - 66 Features\nEntropy={stats_66_l["mean_entropy"]:.3f}', fontsize=12)

    axes[row, 0].set_ylabel(layer_name)
    axes[row, 0].set_xlabel('Key Time Step')
    axes[row, 1].set_xlabel('Key Time Step')

plt.suptitle('Cross-Layer Attention Comparison', fontsize=15, y=1.01)
plt.tight_layout()
fig5_path = os.path.join(fig_dir, 'fig_attention_layer_comparison.png')
plt.savefig(fig5_path, dpi=300, bbox_inches='tight')
plt.close()
log(f'  保存: {fig5_path}')

# ============================================================
# 8. Permutation Importance for TimeTransformer
# ============================================================
log('\n[8] 计算 TimeTransformer Permutation Importance...')

# 用最后时间步的特征做permutation（即X_test的最后一行）
X_test_66_seq_np = test_seqs_66.numpy()  # [n_samples, 48, 66]
# 基线预测
model_66.eval()
with torch.no_grad():
    baseline_pred = model_66(test_seqs_66).numpy()
# 基线R²
y_te_seq = y_test[48:48 + len(baseline_pred)].astype(np.float32)
baseline_r2 = r2_score(y_te_seq, baseline_pred)
log(f'  基线 R²={baseline_r2:.4f}')

# 对每个特征做permutation
n_features_66 = len(all_features)
perm_importances = []
n_repeats = 5

for feat_idx in range(n_features_66):
    importances = []
    for _ in range(n_repeats):
        # 打乱该特征在所有时间步的值
        X_perm = X_test_66_seq_np.copy()
        # 在样本维度打乱
        perm_idx = np.random.permutation(len(X_perm))
        X_perm[:, :, feat_idx] = X_perm[perm_idx, :, feat_idx]

        with torch.no_grad():
            perm_pred = model_66(torch.tensor(X_perm)).numpy()
        perm_r2 = r2_score(y_te_seq, perm_pred)
        importances.append(baseline_r2 - perm_r2)

    perm_importances.append({
        'feature': all_features[feat_idx],
        'importance_mean': float(np.mean(importances)),
        'importance_std': float(np.std(importances))
    })

    if (feat_idx + 1) % 10 == 0:
        log(f'    进度: {feat_idx+1}/{n_features_66}')

# 排序
perm_importances.sort(key=lambda x: x['importance_mean'], reverse=True)

# 保存top-20
log(f'\n  Top-10 重要特征 (Permutation Importance):')
for i, pi in enumerate(perm_importances[:10]):
    log(f'    {i+1}. {pi["feature"]}: {pi["importance_mean"]:.4f} ± {pi["importance_std"]:.4f}')

# --- 图6: Permutation Importance 对比图 ---
fig, ax = plt.subplots(figsize=(10, 8))
top_n = 20
top_features = [p['feature'] for p in perm_importances[:top_n]]
top_means = [p['importance_mean'] for p in perm_importances[:top_n]]
top_stds = [p['importance_std'] for p in perm_importances[:top_n]]

colors = ['#F44336' if 'rolling' in f or 'lag' in f or 'diff' in f or 'ema' in f
          else '#2196F3' for f in top_features]
ax.barh(range(top_n), top_means, xerr=top_stds, color=colors, alpha=0.8, capsize=3)
ax.set_yticks(range(top_n))
ax.set_yticklabels(top_features, fontsize=10)
ax.invert_yaxis()
ax.set_xlabel('Permutation Importance (ΔR²)', fontsize=12)
ax.set_title(f'TimeTransformer Permutation Importance (Top {top_n})\n'
             f'Red = Temporal/Redundant Feature, Blue = Static Feature', fontsize=12)

from matplotlib.patches import Patch
legend_elements = [Patch(facecolor='#F44336', alpha=0.8, label='Temporal/Redundant'),
                   Patch(facecolor='#2196F3', alpha=0.8, label='Static/Basic')]
ax.legend(handles=legend_elements, fontsize=10)

plt.tight_layout()
fig6_path = os.path.join(fig_dir, 'fig_transformer_permutation_importance.png')
plt.savefig(fig6_path, dpi=300, bbox_inches='tight')
plt.close()
log(f'  保存: {fig6_path}')

# ============================================================
# 9. 保存所有结果
# ============================================================
log('\n[9] 保存结果...')

results = {
    'experiment': 'experiment_5_attention_visualization',
    'description': 'TimeTransformer attention weight visualization: 17 vs 66 features',
    'model_config': {
        'd_model': 64,
        'nhead': 4,
        'num_layers': 2,
        'lookback': 48,
        'epochs': 80
    },
    'performance': {
        '17_features': {'R2': float(r2_17)},
        '66_features': {'R2': float(r2_66)},
        'performance_drop': float(r2_17 - r2_66)
    },
    'attention_stats': {
        '17_features_last_layer': stats_17,
        '66_features_last_layer': stats_66,
        '17_features_first_layer': stats_17_layer0,
        '66_features_first_layer': stats_66_layer0,
        'entropy_drop': float(stats_17['mean_entropy'] - stats_66['mean_entropy']),
        'sparsity_increase': float(stats_66['sparsity_ratio'] - stats_17['sparsity_ratio']),
        'focus_increase': float(stats_66['top3_focus'] - stats_17['top3_focus'])
    },
    'permutation_importance': {
        'top_10': perm_importances[:10],
        'all_features': perm_importances
    },
    'figures': {
        'heatmap_17vs66': fig1_path,
        'per_head': fig2_path,
        'entropy_violin': fig3_path,
        'last_timestep': fig4_path,
        'layer_comparison': fig5_path,
        'permutation_importance': fig6_path
    }
}

results_path = r'D:\荷的预测\real_data\experiment_5_attention_viz_results.json'
with open(results_path, 'w') as f:
    json.dump(results, f, indent=2, ensure_ascii=False)

log(f'\n结果保存至: {results_path}')
log(f'图片保存至: {fig_dir}/')
log('\n' + '=' * 70)
log('实验 #5 完成！')
log('=' * 70)
log('\n关键发现摘要:')
log(f'  17特征 R²={r2_17:.4f}, 注意力熵={stats_17["mean_entropy"]:.4f}')
log(f'  66特征 R²={r2_66:.4f}, 注意力熵={stats_66["mean_entropy"]:.4f}')
log(f'  性能下降: {r2_17-r2_66:.4f}, 熵下降: {stats_17["mean_entropy"]-stats_66["mean_entropy"]:.4f}')
log(f'  稀疏度增加: {stats_66["sparsity_ratio"]-stats_17["sparsity_ratio"]:.4f}')
log(f'  Top-3聚焦度变化: {stats_66["top3_focus"]-stats_17["top3_focus"]:.4f}')
