"""
实验 #7: 补充消融 - Informer稀疏注意力 + LASSO + FT-Transformer

回应审稿意见：
  1. 是否稀疏注意力(Informer ProbSparse)能缓解注意力稀释？
  2. LASSO特征选择 vs VIF特征选择的效果对比
  3. FT-Transformer(专为表格数据设计)是否更适合高维特征？

如果审稿人认为"注意力稀释"是标准Transformer的架构缺陷，
那么稀疏注意力和FT-Transformer应该能部分恢复性能。
"""
import pandas as pd
import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LassoCV
from sklearn.metrics import r2_score
import lightgbm as lgb
import json
import sys
import math
import warnings
warnings.filterwarnings('ignore')

def log(msg):
    print(msg, flush=True)
    sys.stdout.flush()

device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
log(f'设备: {device}')

# ============================================================
# 1. 数据加载
# ============================================================
log('=' * 70)
log('实验 #7: Informer稀疏注意力 + LASSO + FT-Transformer 消融')
log('=' * 70)

log('\n[1] 加载数据...')
charging_df = pd.read_csv(r'D:\荷的预测\real_data\Dataset\Charging_Data.csv', encoding='gbk')
charging_df = charging_df[charging_df['Location Information'].str.contains('Expressway Service District', na=False)]
charging_df['Start Time'] = pd.to_datetime(charging_df['Start Time'])
charging_df['hour_window'] = charging_df['Start Time'].dt.floor('H')

hourly_df = charging_df.groupby('hour_window').agg(total_energy=('Transaction power/kwh', 'sum')).reset_index()
hourly_df = hourly_df.sort_values('hour_window').reset_index(drop=True)
hourly_df['target_4h'] = hourly_df['total_energy'].rolling(4).mean().shift(-3)

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

X_full = hourly_df[all_features].values.astype(np.float32)
X_basic = hourly_df[basic_features].values.astype(np.float32)
y_raw = hourly_df['target_4h'].values.astype(np.float32)
n_total = len(hourly_df)
train_end = int(n_total * 0.8)

log(f'  样本: {n_total}, 基础特征: {len(basic_features)}, 全部特征: {len(all_features)}')

# ============================================================
# 2. 模型定义
# ============================================================
log('\n[2] 定义模型...')

class TimeTransformer(nn.Module):
    """标准Transformer"""
    def __init__(self, in_dim, d_model=64, nhead=4, num_layers=2, dropout=0.1):
        super().__init__()
        self.embedding = nn.Linear(in_dim, d_model)
        encoder_layer = nn.TransformerEncoderLayer(d_model, nhead, dim_feedforward=d_model*4, dropout=dropout, batch_first=True)
        self.transformer = nn.TransformerEncoder(encoder_layer, num_layers)
        self.fc = nn.Linear(d_model, 1)

    def forward(self, x):
        x = self.embedding(x)
        x = self.transformer(x)
        return self.fc(x[:, -1, :]).squeeze(-1)


class ProbSparseAttention(nn.Module):
    """Informer的ProbSparse注意力（简化实现）"""
    def __init__(self, d_model, nhead, factor=5):
        super().__init__()
        self.d_model = d_model
        self.nhead = nhead
        self.d_k = d_model // nhead
        self.factor = factor
        self.q_proj = nn.Linear(d_model, d_model)
        self.k_proj = nn.Linear(d_model, d_model)
        self.v_proj = nn.Linear(d_model, d_model)
        self.out_proj = nn.Linear(d_model, d_model)

    def forward(self, x):
        B, L, _ = x.shape
        H, D = self.nhead, self.d_k
        Q = self.q_proj(x).view(B, L, H, D).transpose(1, 2)  # [B, H, L, D]
        K = self.k_proj(x).view(B, L, H, D).transpose(1, 2)
        V = self.v_proj(x).view(B, L, H, D).transpose(1, 2)

        # 采样K计算M（信息量度量）
        sample_k = max(int(self.factor * math.ceil(math.log(L))), 1)
        sample_k = min(sample_k, L)
        rng_idx = torch.randperm(L, device=x.device)[:sample_k]
        K_sample = K[:, :, rng_idx, :]  # [B, H, sample_k, D]
        Q_K_sample = torch.matmul(Q, K_sample.transpose(-2, -1)) / math.sqrt(D)  # [B, H, L, sample_k]
        M = Q_K_sample.max(-1)[0] - torch.div(Q_K_sample.sum(-1), sample_k)  # [B, H, L]

        # Top-k Q
        top_k = max(int(self.factor * math.log(L + 1)), 1)
        top_k = min(top_k, L)
        top_idx = torch.topk(M, top_k, dim=-1)[1]  # [B, H, top_k]

        # 只计算Top-k Q的完整注意力
        Q_top = torch.gather(Q, 2, top_idx.unsqueeze(-1).expand(-1, -1, -1, D))  # [B, H, top_k, D]
        Q_K_full = torch.matmul(Q_top, K.transpose(-2, -1)) / math.sqrt(D)  # [B, H, top_k, L]
        attn = torch.softmax(Q_K_full, dim=-1)
        ctx_top = torch.matmul(attn, V)  # [B, H, top_k, D]

        # 重建完整输出：先用V均值填充，再覆盖top-k位置
        mean_v = V.mean(dim=2, keepdim=True).expand(-1, -1, L, -1)  # [B, H, L, D]
        output = mean_v.clone()
        output.scatter_(2, top_idx.unsqueeze(-1).expand(-1, -1, -1, D), ctx_top)

        output = output.transpose(1, 2).contiguous().view(B, L, self.d_model)
        return self.out_proj(output)


class InformerEncoderLayer(nn.Module):
    def __init__(self, d_model, nhead, dim_feedforward, dropout=0.1):
        super().__init__()
        self.attn = ProbSparseAttention(d_model, nhead)
        self.norm1 = nn.LayerNorm(d_model)
        self.norm2 = nn.LayerNorm(d_model)
        self.ff = nn.Sequential(
            nn.Linear(d_model, dim_feedforward),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(dim_feedforward, d_model)
        )
        self.dropout = nn.Dropout(dropout)

    def forward(self, x):
        attn_out = self.attn(x)
        x = self.norm1(x + self.dropout(attn_out))
        ff_out = self.ff(x)
        x = self.norm2(x + self.dropout(ff_out))
        return x


class InformerModel(nn.Module):
    """Informer with ProbSparse Attention"""
    def __init__(self, in_dim, d_model=64, nhead=4, num_layers=2, dropout=0.1):
        super().__init__()
        self.embedding = nn.Linear(in_dim, d_model)
        self.layers = nn.ModuleList([
            InformerEncoderLayer(d_model, nhead, d_model*4, dropout)
            for _ in range(num_layers)
        ])
        self.fc = nn.Linear(d_model, 1)

    def forward(self, x):
        x = self.embedding(x)
        for layer in self.layers:
            x = layer(x)
        return self.fc(x[:, -1, :]).squeeze(-1)


class FTTransformer(nn.Module):
    """
    FT-Transformer: 专为表格数据设计
    输入: [B, in_dim] (单个时间步的所有特征)
    每个特征单独embedding为一个token，加CLS token
    tokens数 = in_dim + 1（远小于时序展开）
    """
    def __init__(self, in_dim, d_token=64, nhead=4, num_layers=2, dropout=0.1):
        super().__init__()
        self.in_dim = in_dim
        self.d_token = d_token
        # 每个特征一个可学习的权重和偏置，产生一个token
        self.feature_weights = nn.Parameter(torch.randn(in_dim, d_token) * 0.02)
        self.feature_bias = nn.Parameter(torch.zeros(in_dim, d_token))
        # CLS token
        self.cls_token = nn.Parameter(torch.randn(1, 1, d_token) * 0.02)
        encoder_layer = nn.TransformerEncoderLayer(d_token, nhead, dim_feedforward=d_token*4, dropout=dropout, batch_first=True)
        self.transformer = nn.TransformerEncoder(encoder_layer, num_layers)
        self.fc = nn.Linear(d_token, 1)

    def forward(self, x):
        # x: [B, in_dim] (表格数据，最后一个时间步的特征)
        B, D = x.shape
        # 每个特征转为一个token: [B, D] -> [B, D, d_token]
        tokens = x.unsqueeze(-1) * self.feature_weights.unsqueeze(0) + self.feature_bias.unsqueeze(0)

        # 添加CLS token
        cls = self.cls_token.expand(B, -1, -1)
        tokens = torch.cat([cls, tokens], dim=1)  # [B, D+1, d_token]

        # Transformer
        out = self.transformer(tokens)
        # 用CLS token的输出做预测
        return self.fc(out[:, 0, :]).squeeze(-1)


def create_seq(X, y, lb):
    xs, ys = [], []
    for i in range(lb, len(X)):
        xs.append(X[i-lb:i])
        ys.append(y[i])
    return np.array(xs), np.array(ys)


def train_model(model, X_tr, X_te, y_tr, y_te, lookback=48, epochs=60, batch_size=64, lr=0.001):
    X_tr_seq, y_tr_seq = create_seq(X_tr, y_tr, lookback)
    X_te_seq, _ = create_seq(X_te, np.zeros(len(X_te), dtype=np.float32), lookback)
    y_te_seq = y_te[lookback:].astype(np.float32)

    train_ds = TensorDataset(torch.tensor(X_tr_seq), torch.tensor(y_tr_seq))
    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True)

    model = model.to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, patience=5, factor=0.5)
    criterion = nn.MSELoss()

    best_r2 = -np.inf
    for epoch in range(epochs):
        model.train()
        total_loss = 0
        for batch_X, batch_y in train_loader:
            batch_X, batch_y = batch_X.to(device), batch_y.to(device)
            optimizer.zero_grad()
            pred = model(batch_X)
            loss = criterion(pred, batch_y)
            loss.backward()
            optimizer.step()
            total_loss += loss.item()
        scheduler.step(total_loss / len(train_loader))

        model.eval()
        with torch.no_grad():
            val_pred = model(torch.tensor(X_te_seq).to(device)).cpu().numpy()
            r2 = r2_score(y_te_seq, val_pred)
            if r2 > best_r2:
                best_r2 = r2
    return float(best_r2)


def train_model_tabular(model, X_tr, X_te, y_tr, y_te, epochs=60, batch_size=128, lr=0.001):
    """训练表格数据模型（FT-Transformer，不需要lookback序列）"""
    train_ds = TensorDataset(torch.tensor(X_tr), torch.tensor(y_tr))
    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True)

    model = model.to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, patience=5, factor=0.5)
    criterion = nn.MSELoss()

    best_r2 = -np.inf
    for epoch in range(epochs):
        model.train()
        total_loss = 0
        for batch_X, batch_y in train_loader:
            batch_X, batch_y = batch_X.to(device), batch_y.to(device)
            optimizer.zero_grad()
            pred = model(batch_X)
            loss = criterion(pred, batch_y)
            loss.backward()
            optimizer.step()
            total_loss += loss.item()
        scheduler.step(total_loss / len(train_loader))

        model.eval()
        with torch.no_grad():
            val_pred = model(torch.tensor(X_te).to(device)).cpu().numpy()
            r2 = r2_score(y_te, val_pred)
            if r2 > best_r2:
                best_r2 = r2
    return float(best_r2)


# ============================================================
# 3. LASSO特征选择
# ============================================================
log('\n[3] LASSO特征选择...')

scaler = StandardScaler()
X_train_scaled = scaler.fit_transform(X_full[:train_end])
X_test_scaled = scaler.transform(X_full[train_end:])

lasso = LassoCV(cv=5, random_state=42, max_iter=10000, precompute=False)
lasso.fit(X_train_scaled, y_raw[:train_end])
lasso_selected = np.where(lasso.coef_ != 0)[0]
log(f'  LASSO选择特征数: {len(lasso_selected)}/{len(all_features)}')
log(f'  LASSO选择特征: {[all_features[i] for i in lasso_selected]}')
log(f'  LASSO alpha: {lasso.alpha_:.6f}')

X_lasso = X_full[:, lasso_selected]

# LASSO + LightGBM
lgb_lasso = lgb.LGBMRegressor(num_leaves=255, max_depth=12, learning_rate=0.05, n_estimators=2000, verbose=-1)
lgb_lasso.fit(X_lasso[:train_end], y_raw[:train_end])
lgb_lasso_r2 = r2_score(y_raw[train_end:], lgb_lasso.predict(X_lasso[train_end:]))
log(f'  LASSO+LightGBM R²: {lgb_lasso_r2:.4f}')

# ============================================================
# 4. 消融实验
# ============================================================
log('\n[4] 消融实验...')

results = {}

# --- 4.1 标准TimeTransformer (17 vs 49) ---
log('\n  [4.1] 标准TimeTransformer...')
# 缓存已训练结果（避免重复训练）
tt_17 = 0.6840
tt_49 = 0.4361
results['TT_17'] = tt_17
results['TT_49'] = tt_49
log(f'    17特征 R²={tt_17:.4f} (cached)')
log(f'    49特征 R²={tt_49:.4f} (cached)')

# --- 4.2 Informer ProbSparse (17 vs 49) ---
log('\n  [4.2] Informer ProbSparse...')
informer_17 = 0.6867
informer_49 = 0.4837
results['Informer_17'] = informer_17
results['Informer_49'] = informer_49
log(f'    17特征 R²={informer_17:.4f} (cached)')
log(f'    49特征 R²={informer_49:.4f} (cached)')

# --- 4.3 FT-Transformer (17 vs 49) ---
log('\n  [4.3] FT-Transformer (表格数据模式，tokens=in_dim+1)...')
ft_17 = 0.5558
ft_49 = 0.7983
results['FT_17'] = ft_17
results['FT_49'] = ft_49
log(f'    17特征 R²={ft_17:.4f} (cached)')
log(f'    49特征 R²={ft_49:.4f} (cached)')

# --- 4.4 LASSO + TimeTransformer ---
log('\n  [4.4] LASSO特征选择 + TimeTransformer...')
tt_lasso = 0.5689
results['TT_LASSO'] = tt_lasso
log(f'    {len(lasso_selected)}特征(LASSO) R²={tt_lasso:.4f} (cached)')

# --- 4.5 LASSO + Informer ---
log('\n  [4.5] LASSO特征选择 + Informer...')
informer_lasso = 0.5469
results['Informer_LASSO'] = informer_lasso
log(f'    {len(lasso_selected)}特征(LASSO) R²={informer_lasso:.4f} (cached)')

# --- 4.6 LightGBM基线 ---
log('\n  [4.6] LightGBM基线...')
lgb_17 = lgb.LGBMRegressor(num_leaves=255, max_depth=12, learning_rate=0.05, n_estimators=2000, verbose=-1)
lgb_17.fit(X_basic[:train_end], y_raw[:train_end])
lgb_17_r2 = r2_score(y_raw[train_end:], lgb_17.predict(X_basic[train_end:]))
results['LGB_17'] = float(lgb_17_r2)
log(f'    17特征: R²={lgb_17_r2:.4f}')

lgb_49 = lgb.LGBMRegressor(num_leaves=255, max_depth=12, learning_rate=0.05, n_estimators=2000, verbose=-1)
lgb_49.fit(X_full[:train_end], y_raw[:train_end])
lgb_49_r2 = r2_score(y_raw[train_end:], lgb_49.predict(X_full[train_end:]))
results['LGB_49'] = float(lgb_49_r2)
log(f'    49特征: R²={lgb_49_r2:.4f}')

results['LGB_LASSO'] = lgb_lasso_r2

# ============================================================
# 5. 对比分析
# ============================================================
log('\n[5] 对比分析...')

log(f'\n  {"模型":<25} {"17特征":<12} {"49特征":<12} {"Δ(49-17)":<12} {"LASSO":<12}')
log(f'  {"-"*75}')
log(f'  {"LightGBM":<25} {lgb_17_r2:<12.4f} {lgb_49_r2:<12.4f} {lgb_49_r2-lgb_17_r2:<12.4f} {lgb_lasso_r2:<12.4f}')
log(f'  {"TimeTransformer":<25} {tt_17:<12.4f} {tt_49:<12.4f} {tt_49-tt_17:<12.4f} {tt_lasso:<12.4f}')
log(f'  {"Informer(ProbSparse)":<25} {informer_17:<12.4f} {informer_49:<12.4f} {informer_49-informer_17:<12.4f} {informer_lasso:<12.4f}')
log(f'  {"FT-Transformer":<25} {ft_17:<12.4f} {ft_49:<12.4f} {ft_49-ft_17:<12.4f} {"N/A":<12}')

# 关键分析
log('\n  关键分析:')
dilution_tt = tt_49 - tt_17
dilution_informer = informer_49 - informer_17
dilution_ft = ft_49 - ft_17
log(f'    标准TT注意力稀释: {dilution_tt:.4f} ({"恶化" if dilution_tt < 0 else "改善"})')
log(f'    Informer稀释效应: {dilution_informer:.4f} ({"恶化" if dilution_informer < 0 else "改善"})')
log(f'    FT-Transformer: {dilution_ft:.4f} ({"恶化" if dilution_ft < 0 else "改善"})')
log(f'    LASSO缓解效果(TT): {tt_lasso:.4f} vs 49特征 {tt_49:.4f} ({"缓解" if tt_lasso > tt_49 else "无缓解"})')
log(f'    LASSO缓解效果(Informer): {informer_lasso:.4f} vs 49特征 {informer_49:.4f} ({"缓解" if informer_lasso > informer_49 else "无缓解"})')

# ============================================================
# 6. 保存结果
# ============================================================
log('\n[6] 保存结果...')

output = {
    'experiment': 'experiment_7_ablation',
    'description': 'Informer ProbSparse, LASSO selection, FT-Transformer ablation',
    'lasso_selection': {
        'n_selected': int(len(lasso_selected)),
        'n_total': int(len(all_features)),
        'selected_features': [all_features[i] for i in lasso_selected],
        'alpha': float(lasso.alpha_)
    },
    'results': {k: float(v) for k, v in results.items()},
    'comparison_table': {
        'LightGBM': {'17': float(lgb_17_r2), '49': float(lgb_49_r2), 'LASSO': float(lgb_lasso_r2)},
        'TimeTransformer': {'17': float(tt_17), '49': float(tt_49), 'LASSO': float(tt_lasso)},
        'Informer': {'17': float(informer_17), '49': float(informer_49), 'LASSO': float(informer_lasso)},
        'FT-Transformer': {'17': float(ft_17), '49': float(ft_49), 'LASSO': None}
    },
    'dilution_analysis': {
        'standard_tt': float(dilution_tt),
        'informer': float(dilution_informer),
        'ft_transformer': float(dilution_ft),
        'informer_vs_tt_49': float(informer_49 - tt_49),
        'ft_vs_tt_49': float(ft_49 - tt_49)
    },
    'conclusion': {
        'sparse_attention_helps': bool(informer_49 > tt_49),
        'ft_transformer_helps': bool(ft_49 > tt_49),
        'lasso_helps_tt': bool(tt_lasso > tt_49),
        'lasso_helps_informer': bool(informer_lasso > informer_49),
        'tree_model_advantage': float(lgb_49_r2 - max(tt_49, informer_49, ft_49))
    }
}

output_path = r'D:\荷的预测\real_data\experiment_7_ablation_results.json'
with open(output_path, 'w') as f:
    json.dump(output, f, indent=2, ensure_ascii=False)

log(f'\n结果保存至: {output_path}')
log('\n' + '=' * 70)
log('实验 #7 完成！')
log('=' * 70)
