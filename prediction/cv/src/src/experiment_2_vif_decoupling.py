"""
实验 #2: VIF特征筛选 + 三重去污解耦
回应审稿意见：PCA控制实验"不纯净"（改变物理意义/量纲/分布）

核心策略：
  方案A: VIF筛选 — 只删高共线性特征，不做任何线性变换，物理意义/量纲/分布完全保留
  方案B: 标准化对照 — 只做StandardScaler，不删特征，验证"尺度"vs"冗余"
  方案C: PCA去冗余 — 原始方案（做对照基准）

如果VIF筛选（保物理意义）也能恢复TimeTransformer性能
  → 强证据：是冗余而非分布/量纲导致的问题
如果标准化对照也能恢复
  → 审稿人对尺度问题的批评成立，需要修正论文表述
"""
import pandas as pd
import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset
import lightgbm as lgb
from sklearn.preprocessing import StandardScaler
from sklearn.decomposition import PCA
from sklearn.metrics import r2_score
from sklearn.linear_model import LinearRegression
import json
import sys
import time
import os

def log(msg):
    print(msg, flush=True)
    sys.stdout.flush()

# ============================================================
# 1. 数据加载
# ============================================================
log('=' * 70)
log('实验 #2: VIF特征筛选 + 三重去污解耦')
log('=' * 70)

log('\n[1] 加载数据...')
charging_df = pd.read_csv(r'D:\荷的预测\real_data\Dataset\Charging_Data.csv', encoding='gbk')
charging_df = charging_df[charging_df['Location Information'].str.contains('Expressway Service District', na=False)]
charging_df['Start Time'] = pd.to_datetime(charging_df['Start Time'])
charging_df['hour_window'] = charging_df['Start Time'].dt.floor('H')

hourly_df = charging_df.groupby('hour_window').agg(total_energy=('Transaction power/kwh', 'sum')).reset_index()
hourly_df = hourly_df.sort_values('hour_window').reset_index(drop=True)
hourly_df['target_4h'] = hourly_df['total_energy'].rolling(4).mean().shift(-3)

# 特征工程（与现有脚本一致）
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
y_raw = hourly_df['target_4h'].values.astype(float)
n_total = len(hourly_df)
train_end = int(n_total * 0.8)

y_train, y_test = y_raw[:train_end], y_raw[train_end:]

log(f'  总样本: {n_total}, 训练: {train_end}, 测试: {n_total - train_end}')
log(f'  基础特征: {len(basic_features)}个, 全部特征: {len(all_features)}个')

# ============================================================
# 2. VIF特征筛选（保物理意义，只删高共线性）
# ============================================================
log('\n[2] VIF特征筛选...')

def compute_vif(X, idx):
    """手动计算VIF: 1 / (1 - R²), R²来自用其他特征回归该特征"""
    n = X.shape[1]
    if n <= 1:
        return 1.0
    y_i = X[:, idx]
    other_idx = [j for j in range(n) if j != idx]
    X_others = X[:, other_idx]
    reg = LinearRegression().fit(X_others, y_i)
    y_pred = reg.predict(X_others)
    ss_res = np.sum((y_i - y_pred) ** 2)
    ss_tot = np.sum((y_i - np.mean(y_i)) ** 2)
    if ss_tot < 1e-12:
        return 999.0
    r2 = 1 - ss_res / ss_tot
    if r2 >= 0.999:
        return 999.0
    return 1.0 / (1.0 - r2)

def vif_select_features(X, feature_names, vif_threshold=10.0, min_features=17):
    """
    基于VIF递归删除高共线性特征
    - 不做任何线性变换，保留原始物理意义、量纲和分布
    - VIF > threshold 的特征被逐步删除（每次删最大的）
    """
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    selected = list(range(X.shape[1]))
    removed = []

    while True:
        X_sel = X_scaled[:, selected]
        n_sel = len(selected)
        if n_sel <= min_features:
            break

        vifs = []
        for i in range(n_sel):
            try:
                vif_val = compute_vif(X_sel, i)
                if np.isnan(vif_val) or np.isinf(vif_val):
                    vif_val = 999.0
            except:
                vif_val = 999.0
            vifs.append(vif_val)

        max_vif = max(vifs)
        if max_vif < vif_threshold:
            break

        worst_idx = np.argmax(vifs)
        removed_feature = feature_names[selected[worst_idx]]
        removed.append({
            'feature': removed_feature,
            'vif': float(max_vif),
            'step': len(removed) + 1
        })
        del selected[worst_idx]

    selected_names = [feature_names[i] for i in selected]
    return selected, selected_names, removed

selected_idx, selected_names, removed_info = vif_select_features(
    X_full, all_features, vif_threshold=10.0, min_features=17
)

log(f'  VIF筛选结果: {len(selected_names)}个特征 (从{len(all_features)}个中保留)')
log(f'  删除了 {len(removed_info)} 个高VIF特征:')
for r in removed_info[:10]:
    log(f'    Step {r["step"]}: {r["feature"]} (VIF={r["vif"]:.1f})')
if len(removed_info) > 10:
    log(f'    ... 共{len(removed_info)}个')

X_vif = X_full[:, selected_idx].astype(np.float32)

# 计算各组的冗余度（平均绝对相关系数）
def compute_redundancy(X, name):
    corr = np.corrcoef(X.T)
    iu = np.triu_indices_from(corr, k=1)
    avg_corr = float(np.mean(np.abs(corr[iu])))
    max_corr = float(np.max(np.abs(corr[iu])))
    log(f'  [{name}] 特征数={X.shape[1]}, 平均绝对相关={avg_corr:.4f}, 最大相关={max_corr:.4f}')
    return avg_corr, max_corr

log('\n  各特征组冗余度对比:')
rd_basic = compute_redundancy(X_basic, '17_basic')
rd_full = compute_redundancy(X_full, '49_full')
rd_vif = compute_redundancy(X_vif, f'{len(selected_names)}_vif')

# 标准化版本
scaler_full = StandardScaler()
X_full_scaled = scaler_full.fit_transform(X_full[:train_end])
X_full_scaled = np.vstack([X_full_scaled, scaler_full.transform(X_full[train_end:])]).astype(np.float32)

scaler_vif = StandardScaler()
X_vif_scaled = scaler_vif.fit_transform(X_vif[:train_end])
X_vif_scaled = np.vstack([X_vif_scaled, scaler_vif.transform(X_vif[train_end:])]).astype(np.float32)

# PCA去冗余（做对照）
scaler_pca = StandardScaler()
X_for_pca = scaler_pca.fit_transform(X_full[:train_end])
X_for_pca = np.vstack([X_for_pca, scaler_pca.transform(X_full[train_end:])])
pca = PCA(n_components=len(all_features))
X_pca = pca.fit_transform(X_for_pca).astype(np.float32)
rd_pca = compute_redundancy(X_pca, f'{X_pca.shape[1]}_pca')

# ============================================================
# 3. 特征分布分析（验证VIF保留了原始分布）
# ============================================================
log('\n[3] 特征分布分析...')

def compute_distribution_stats(X, name, feature_names=None):
    """计算特征分布的统计量"""
    stats = {
        'name': name,
        'n_features': int(X.shape[1]),
        'mean_cv': float(np.mean(np.std(X, axis=0) / (np.mean(np.abs(X), axis=0) + 1e-8))),
        'avg_skew': float(np.mean([pd.Series(X[:, i]).skew() for i in range(min(X.shape[1], 20))])),
        'avg_kurt': float(np.mean([pd.Series(X[:, i]).kurt() for i in range(min(X.shape[1], 20))])),
    }
    log(f'  [{name}] 变异系数={stats["mean_cv"]:.4f}, 偏度={stats["avg_skew"]:.4f}, 峰度={stats["avg_kurt"]:.4f}')
    return stats

dist_full = compute_distribution_stats(X_full, '49_full')
dist_vif = compute_distribution_stats(X_vif, f'{len(selected_names)}_vif')
dist_pca = compute_distribution_stats(X_pca, '49_pca')
dist_scaled = compute_distribution_stats(X_full_scaled, '49_scaled')

# ============================================================
# 4. 模型定义和训练
# ============================================================
log('\n[4] 定义模型...')

class TimeTransformer(nn.Module):
    def __init__(self, in_dim, d_model=64, nhead=4, num_layers=2, dropout=0.1):
        super().__init__()
        self.embedding = nn.Linear(in_dim, d_model)
        encoder_layer = nn.TransformerEncoderLayer(d_model, nhead, dim_feedforward=d_model*4,
                                                   dropout=dropout, batch_first=True)
        self.transformer = nn.TransformerEncoder(encoder_layer, num_layers)
        self.fc = nn.Linear(d_model, 1)

    def forward(self, x):
        x = self.embedding(x)
        x = self.transformer(x)
        return self.fc(x[:, -1, :]).squeeze(-1)

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

    model = TimeTransformer(feature_dim)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, patience=5, factor=0.5)
    criterion = nn.MSELoss()

    best_r2 = -np.inf
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

        if (epoch + 1) % 20 == 0:
            log(f'    Epoch {epoch+1}/{epochs}, R²={r2:.4f}')

    log(f'    最佳 R²={best_r2:.4f}')
    return best_r2

# ============================================================
# 5. 五组对照实验
# ============================================================
log('\n[5] 五组对照实验:')
log('  A: 17基础特征 (原始)')
log('  B: 49全部特征 (原始, 未标准化)')
log('  C: 49全部特征 (标准化, 不去冗余) — 验证尺度假说')
log('  D: VIF筛选特征 (原始, 保物理意义) — 验证冗余假说')
log('  E: VIF筛选特征 (标准化)')
log('  F: PCA去冗余 (标准化) — 原始对照基准')

# 准备数据集
datasets = {
    'A_17_basic': (X_basic[:train_end], X_basic[train_end:], len(basic_features), '原始'),
    'B_49_raw': (X_full[:train_end], X_full[train_end:], len(all_features), '原始'),
    'C_49_scaled': (X_full_scaled[:train_end], X_full_scaled[train_end:], len(all_features), '标准化'),
    'D_vif_raw': (X_vif[:train_end], X_vif[train_end:], len(selected_names), '原始'),
    'E_vif_scaled': (X_vif_scaled[:train_end], X_vif_scaled[train_end:], len(selected_names), '标准化'),
    'F_pca': (X_pca[:train_end], X_pca[train_end:], len(all_features), 'PCA'),
}

results = {}

for name, (X_tr, X_te, n_feat, desc) in datasets.items():
    log(f'\n  --- {name} ({n_feat}特征, {desc}) ---')

    # LightGBM
    lgb_model = lgb.LGBMRegressor(num_leaves=255, max_depth=12, learning_rate=0.05,
                                   n_estimators=2000, verbose=-1)
    lgb_model.fit(X_tr, y_train)
    lgb_r2 = r2_score(y_test, lgb_model.predict(X_te))
    log(f'    LightGBM R²: {lgb_r2:.4f}')

    # TimeTransformer
    t0 = time.time()
    tt_r2 = train_transformer(X_tr, X_te, y_train, y_test, n_feat)
    elapsed = time.time() - t0
    log(f'    TimeTransformer R²: {tt_r2:.4f} (耗时 {elapsed:.1f}s)')

    results[name] = {
        'n_features': n_feat,
        'description': desc,
        'LightGBM_R2': float(lgb_r2),
        'TimeTransformer_R2': float(tt_r2),
        'TT_training_time': float(elapsed)
    }

# ============================================================
# 6. 结果分析
# ============================================================
log('\n[6] 结果分析:')
log('\n  对照表:')
log(f'  {"实验组":<20} {"特征数":<8} {"处理":<10} {"LGB R²":<10} {"TT R²":<10} {"TT恢复":<10}')
log(f'  {"-"*70}')

baseline_tt = results['B_49_raw']['TimeTransformer_R2']
for name, r in results.items():
    recovery = r['TimeTransformer_R2'] - baseline_tt
    recovery_str = f'{recovery:+.4f}' if name != 'B_49_raw' else 'baseline'
    log(f'  {name:<20} {r["n_features"]:<8} {r["description"]:<10} '
        f'{r["LightGBM_R2"]:<10.4f} {r["TimeTransformer_R2"]:<10.4f} {recovery_str:<10}')

# 关键对比
log('\n  关键对比:')
log(f'  1. 尺度假说验证 (B vs C):')
log(f'     原始49特征 R²={results["B_49_raw"]["TimeTransformer_R2"]:.4f} → '
    f'标准化49特征 R²={results["C_49_scaled"]["TimeTransformer_R2"]:.4f} '
    f'(Δ={results["C_49_scaled"]["TimeTransformer_R2"]-results["B_49_raw"]["TimeTransformer_R2"]:+.4f})')
log(f'  2. 冗余假说验证 (B vs D):')
log(f'     原始49特征 R²={results["B_49_raw"]["TimeTransformer_R2"]:.4f} → '
    f'VIF筛选{results["D_vif_raw"]["n_features"]}特征 R²={results["D_vif_raw"]["TimeTransformer_R2"]:.4f} '
    f'(Δ={results["D_vif_raw"]["TimeTransformer_R2"]-results["B_49_raw"]["TimeTransformer_R2"]:+.4f})')
log(f'  3. PCA对照 (B vs F):')
log(f'     原始49特征 R²={results["B_49_raw"]["TimeTransformer_R2"]:.4f} → '
    f'PCA去冗余 R²={results["F_pca"]["TimeTransformer_R2"]:.4f} '
    f'(Δ={results["F_pca"]["TimeTransformer_R2"]-results["B_49_raw"]["TimeTransformer_R2"]:+.4f})')
log(f'  4. LightGBM影响 (B vs D):')
log(f'     原始49特征 LGB R²={results["B_49_raw"]["LightGBM_R2"]:.4f} → '
    f'VIF筛选 LGB R²={results["D_vif_raw"]["LightGBM_R2"]:.4f} '
    f'(Δ={results["D_vif_raw"]["LightGBM_R2"]-results["B_49_raw"]["LightGBM_R2"]:+.4f})')

# ============================================================
# 7. 保存结果
# ============================================================
log('\n[7] 保存结果...')

output = {
    'experiment': 'experiment_2_vif_decoupling',
    'description': 'VIF feature selection + three-way decoupling of redundancy vs scale vs distribution',
    'feature_info': {
        'basic_count': len(basic_features),
        'full_count': len(all_features),
        'vif_selected_count': len(selected_names),
        'vif_selected_features': selected_names,
        'vif_removed': removed_info,
        'redundancy': {
            '17_basic': {'avg_corr': rd_basic[0], 'max_corr': rd_basic[1]},
            '49_full': {'avg_corr': rd_full[0], 'max_corr': rd_full[1]},
            'vif_selected': {'avg_corr': rd_vif[0], 'max_corr': rd_vif[1]},
            'pca': {'avg_corr': rd_pca[0], 'max_corr': rd_pca[1]}
        },
        'distribution': {
            '49_full': dist_full,
            'vif_selected': dist_vif,
            'pca': dist_pca,
            '49_scaled': dist_scaled
        }
    },
    'results': results,
    'key_comparisons': {
        'scale_hypothesis': {
            'B_49_raw_TT_R2': results['B_49_raw']['TimeTransformer_R2'],
            'C_49_scaled_TT_R2': results['C_49_scaled']['TimeTransformer_R2'],
            'delta': results['C_49_scaled']['TimeTransformer_R2'] - results['B_49_raw']['TimeTransformer_R2'],
            'interpretation': 'If C >> B, scaling matters; if C ≈ B, scaling does not explain the gap'
        },
        'redundancy_hypothesis': {
            'B_49_raw_TT_R2': results['B_49_raw']['TimeTransformer_R2'],
            'D_vif_raw_TT_R2': results['D_vif_raw']['TimeTransformer_R2'],
            'delta': results['D_vif_raw']['TimeTransformer_R2'] - results['B_49_raw']['TimeTransformer_R2'],
            'interpretation': 'If D >> B, redundancy matters even without changing distribution/scale'
        },
        'pca_baseline': {
            'B_49_raw_TT_R2': results['B_49_raw']['TimeTransformer_R2'],
            'F_pca_TT_R2': results['F_pca']['TimeTransformer_R2'],
            'delta': results['F_pca']['TimeTransformer_R2'] - results['B_49_raw']['TimeTransformer_R2'],
            'interpretation': 'PCA removes both redundancy and changes distribution'
        },
        'lgb_impact': {
            'B_49_raw_LGB_R2': results['B_49_raw']['LightGBM_R2'],
            'D_vif_raw_LGB_R2': results['D_vif_raw']['LightGBM_R2'],
            'delta': results['D_vif_raw']['LightGBM_R2'] - results['B_49_raw']['LightGBM_R2'],
            'interpretation': 'If LGB drops, VIF removed useful features; if LGB stays, VIF only removed redundant ones'
        }
    }
}

output_path = r'D:\荷的预测\real_data\experiment_2_vif_results.json'
with open(output_path, 'w') as f:
    json.dump(output, f, indent=2, ensure_ascii=False)

log(f'\n结果保存至: {output_path}')
log('\n' + '=' * 70)
log('实验 #2 完成！')
log('=' * 70)
