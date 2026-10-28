"""
实验 #1: 互信息(MI) + 信号-冗余比(SRR)分析
回应审稿意见：因果归因缺少信息瓶颈/互信息等量化理论支撑

目标：
1. 计算每个特征与目标Y的互信息 I(X_i; Y) —— 信号量
2. 计算特征间的平均互信息 I(X_i; X_j) —— 冗余度
3. 定义信号-冗余比 SRR = mean(I(X;Y)) / mean(I(X_i;X_j))
4. 对比17特征 vs 49特征 vs VIF筛选 vs PCA的SRR
5. 额外：特征-目标条件互信息（控制其他特征后的增量信息）
"""
import pandas as pd
import numpy as np
from sklearn.feature_selection import mutual_info_regression
from sklearn.preprocessing import StandardScaler
from sklearn.decomposition import PCA
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
log('实验 #1: 互信息(MI) + 信号-冗余比(SRR)分析')
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

X_full = hourly_df[all_features].values.astype(np.float64)
X_basic = hourly_df[basic_features].values.astype(np.float64)
y = hourly_df['target_4h'].values.astype(np.float64)
n_total = len(hourly_df)
train_end = int(n_total * 0.8)

X_train_full = X_full[:train_end]
X_train_basic = X_basic[:train_end]
y_train = y[:train_end]

log(f'  样本数: {train_end} (训练集), 特征数: 基础{len(basic_features)} / 全部{len(all_features)}')

# VIF筛选（复用实验#2的逻辑）
def compute_vif(X, idx):
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

def vif_select(X, names, threshold=10.0, min_feat=17):
    scaler = StandardScaler()
    X_s = scaler.fit_transform(X)
    selected = list(range(X.shape[1]))
    while True:
        X_sel = X_s[:, selected]
        if len(selected) <= min_feat:
            break
        vifs = [compute_vif(X_sel, i) for i in range(len(selected))]
        max_vif = max(vifs)
        if max_vif < threshold:
            break
        del selected[np.argmax(vifs)]
    return selected, [names[i] for i in selected]

log('\n[2] VIF特征筛选...')
vif_idx, vif_names = vif_select(X_train_full, all_features)
X_train_vif = X_train_full[:, vif_idx]
log(f'  VIF保留: {len(vif_names)}个特征')

# PCA
log('\n[3] PCA变换...')
scaler_pca = StandardScaler()
X_scaled = scaler_pca.fit_transform(X_train_full)
pca = PCA(n_components=len(all_features))
X_train_pca = pca.fit_transform(X_scaled)
log(f'  PCA成分数: {X_train_pca.shape[1]}')

# ============================================================
# 2. 互信息计算
# ============================================================
log('\n[4] 计算互信息...')

def compute_mi_analysis(X, y, feature_names, group_name, max_inter_pairs=15):
    """
    计算完整的MI分析：
    1. 每个特征与目标的MI: I(X_i; Y)
    2. 特征间互信息: I(X_i; X_j) （采样加速）
    3. 信号-冗余比: SRR = mean(I(X;Y)) / mean(I(X_i;X_j))
    4. 冗余分数: 每个特征与其他特征的平均MI
    """
    n_feat = X.shape[1]
    t0 = time.time()

    # 1. 特征-目标互信息
    mi_to_y = mutual_info_regression(X, y, random_state=42, n_neighbors=3)

    # 2. 特征间互信息（采样以加速）
    # 如果特征数>max_inter_pairs，随机采样
    if n_feat > max_inter_pairs:
        rng = np.random.RandomState(42)
        sample_idx = rng.choice(n_feat, max_inter_pairs, replace=False)
    else:
        sample_idx = np.arange(n_feat)

    inter_mi = []
    inter_mi_matrix = np.zeros((n_feat, n_feat))
    for i in sample_idx:
        for j in range(n_feat):
            if i >= j:
                continue
            mi_ij = mutual_info_regression(
                X[:, [i]], X[:, j], random_state=42, n_neighbors=3
            )[0]
            inter_mi.append(mi_ij)
            inter_mi_matrix[i, j] = mi_ij
            inter_mi_matrix[j, i] = mi_ij

    avg_inter_mi = np.mean(inter_mi) if inter_mi else 0
    max_inter_mi = np.max(inter_mi) if inter_mi else 0

    # 3. 信号-冗余比
    srr = np.mean(mi_to_y) / (avg_inter_mi + 1e-10)

    # 4. 每个特征的冗余分数（与其他特征的平均MI）
    redundancy_scores = np.zeros(n_feat)
    for i in range(n_feat):
        if np.any(inter_mi_matrix[i] > 0):
            redundancy_scores[i] = np.mean(inter_mi_matrix[i][inter_mi_matrix[i] > 0])
        else:
            redundancy_scores[i] = 0

    # 5. 信息效率 = I(X_i; Y) / (I(X_i; Y) + mean(I(X_i; X_j)))
    info_efficiency = mi_to_y / (mi_to_y + redundancy_scores + 1e-10)

    elapsed = time.time() - t0

    result = {
        'group': group_name,
        'n_features': n_feat,
        'mi_to_y': {
            'mean': float(np.mean(mi_to_y)),
            'std': float(np.std(mi_to_y)),
            'max': float(np.max(mi_to_y)),
            'min': float(np.min(mi_to_y)),
            'per_feature': mi_to_y.tolist()
        },
        'inter_feature_mi': {
            'mean': float(avg_inter_mi),
            'max': float(max_inter_mi),
            'n_pairs_computed': len(inter_mi)
        },
        'signal_redundancy_ratio': float(srr),
        'info_efficiency': {
            'mean': float(np.mean(info_efficiency)),
            'std': float(np.std(info_efficiency)),
            'per_feature': info_efficiency.tolist()
        },
        'top_signal_features': [
            {'feature': feature_names[i], 'mi_to_y': float(mi_to_y[i]),
             'redundancy': float(redundancy_scores[i]),
             'efficiency': float(info_efficiency[i])}
            for i in np.argsort(mi_to_y)[-10:][::-1]
        ],
        'most_redundant_features': [
            {'feature': feature_names[i], 'mi_to_y': float(mi_to_y[i]),
             'redundancy': float(redundancy_scores[i]),
             'efficiency': float(info_efficiency[i])}
            for i in np.argsort(redundancy_scores)[-10:][::-1]
        ],
        'computation_time': float(elapsed)
    }

    log(f'  [{group_name}] {n_feat}特征, 耗时{elapsed:.1f}s')
    log(f'    I(X;Y)均值={result["mi_to_y"]["mean"]:.4f}, '
        f'特征间MI均值={result["inter_feature_mi"]["mean"]:.4f}')
    log(f'    SRR={result["signal_redundancy_ratio"]:.4f}, '
        f'信息效率={result["info_efficiency"]["mean"]:.4f}')

    return result

# 对4组特征分别计算
log('\n  --- 17基础特征 ---')
mi_basic = compute_mi_analysis(X_train_basic, y_train, basic_features, '17_basic')

log('\n  --- 49全部特征 ---')
mi_full = compute_mi_analysis(X_train_full, y_train, all_features, '49_full')

log('\n  --- VIF筛选特征 ---')
mi_vif = compute_mi_analysis(X_train_vif, y_train, vif_names, f'{len(vif_names)}_vif')

log('\n  --- PCA特征 ---')
pca_names = [f'PC{i+1}' for i in range(X_train_pca.shape[1])]
mi_pca = compute_mi_analysis(X_train_pca, y_train, pca_names, '49_pca')

# ============================================================
# 3. 条件互信息分析（增量信息量）
# ============================================================
log('\n[5] 条件互信息分析（增量信息量）...')

def compute_incremental_mi(X, y, feature_names, group_name, order=None):
    """
    按给定顺序逐步加入特征，计算每个特征的增量MI：
    I(X_i; Y | X_{already_selected})
    使用差分近似：ΔI = I(X_{1..i}; Y) - I(X_{1..i-1}; Y)
    """
    n_feat = X.shape[1]
    if order is None:
        order = list(range(n_feat))

    cumulative_mi = []
    prev_mi = 0

    # 分批计算（每5个特征一批，减少计算量）
    batch_size = 5
    for batch_start in range(0, n_feat, batch_size):
        batch_end = min(batch_start + batch_size, n_feat)
        batch_idx = order[:batch_end]
        X_subset = X[:, batch_idx]
        mi_subset = mutual_info_regression(X_subset, y, random_state=42, n_neighbors=3)
        # 总MI用多变量MI近似（取最大单变量MI的加权）
        total_mi = np.mean(mi_subset) * np.sqrt(batch_end)  # 近似
        incremental = total_mi - prev_mi
        cumulative_mi.append({
            'n_features': batch_end,
            'cumulative_mi': float(total_mi),
            'incremental_mi': float(incremental)
        })
        prev_mi = total_mi

    log(f'  [{group_name}] 增量MI: {cumulative_mi[-1]["cumulative_mi"]:.4f} '
        f'(从{cumulative_mi[0]["n_features"]}到{cumulative_mi[-1]["n_features"]}特征)')
    return cumulative_mi

# 对17和49特征做增量MI分析
incr_basic = compute_incremental_mi(X_train_basic, y_train, basic_features, '17_basic')
incr_full = compute_incremental_mi(X_train_full, y_train, all_features, '49_full')

# ============================================================
# 4. 信息瓶颈分析
# ============================================================
log('\n[6] 信息瓶颈分析...')

def information_bottleneck_analysis(X, y, feature_names, group_name):
    """
    信息瓶颈框架分析：
    I(X; Y) = I(X; T) + I(T; Y) - I(X; T; Y)
    简化：用PCA压缩后的表示T，计算 I(T;Y) / I(X;Y)
    如果比值低，说明特征中存在大量与Y无关的冗余信息
    """
    n_feat = X.shape[1]

    # 原始特征与Y的MI（信号上界）
    mi_original = np.mean(mutual_info_regression(X, y, random_state=42, n_neighbors=3))

    # 用PCA压缩到不同维度，看信息保留率
    compression_ratios = [0.2, 0.4, 0.6, 0.8, 1.0]
    ib_results = []

    for ratio in compression_ratios:
        n_components = max(1, int(n_feat * ratio))
        if n_components >= n_feat:
            mi_compressed = mi_original
        else:
            scaler = StandardScaler()
            X_s = scaler.fit_transform(X)
            pca = PCA(n_components=n_components)
            T = pca.fit_transform(X_s)
            mi_compressed = np.mean(mutual_info_regression(T, y, random_state=42, n_neighbors=3))

        info_retention = mi_compressed / (mi_original + 1e-10)
        ib_results.append({
            'compression_ratio': float(ratio),
            'n_components': n_components,
            'mi_compressed': float(mi_compressed),
            'info_retention': float(info_retention)
        })

    log(f'  [{group_name}] 信息瓶颈:')
    for r in ib_results:
        log(f'    压缩到{r["n_components"]}维 ({r["compression_ratio"]*100:.0f}%): '
            f'MI={r["mi_compressed"]:.4f}, 保留率={r["info_retention"]:.4f}')

    return ib_results

ib_basic = information_bottleneck_analysis(X_train_basic, y_train, basic_features, '17_basic')
ib_full = information_bottleneck_analysis(X_train_full, y_train, all_features, '49_full')
ib_vif = information_bottleneck_analysis(X_train_vif, y_train, vif_names, f'{len(vif_names)}_vif')

# ============================================================
# 5. 综合对比和总结
# ============================================================
log('\n[7] 综合对比:')

log('\n  信号-冗余比(SRR)对比:')
log(f'    17基础特征: SRR={mi_basic["signal_redundancy_ratio"]:.4f}')
log(f'    49全部特征: SRR={mi_full["signal_redundancy_ratio"]:.4f}')
log(f'    VIF筛选特征: SRR={mi_vif["signal_redundancy_ratio"]:.4f}')
log(f'    PCA特征:     SRR={mi_pca["signal_redundancy_ratio"]:.4f}')

log('\n  信息效率对比:')
log(f'    17基础特征: η={mi_basic["info_efficiency"]["mean"]:.4f}')
log(f'    49全部特征: η={mi_full["info_efficiency"]["mean"]:.4f}')
log(f'    VIF筛选特征: η={mi_vif["info_efficiency"]["mean"]:.4f}')
log(f'    PCA特征:     η={mi_pca["info_efficiency"]["mean"]:.4f}')

log('\n  Top-5 信号特征 (49特征组):')
for i, f in enumerate(mi_full['top_signal_features'][:5]):
    log(f'    {i+1}. {f["feature"]}: I(X;Y)={f["mi_to_y"]:.4f}, '
        f'冗余={f["redundancy"]:.4f}, 效率={f["efficiency"]:.4f}')

log('\n  Top-5 最冗余特征 (49特征组):')
for i, f in enumerate(mi_full['most_redundant_features'][:5]):
    log(f'    {i+1}. {f["feature"]}: I(X;Y)={f["mi_to_y"]:.4f}, '
        f'冗余={f["redundancy"]:.4f}, 效率={f["efficiency"]:.4f}')

# ============================================================
# 6. 保存结果
# ============================================================
log('\n[8] 保存结果...')

output = {
    'experiment': 'experiment_1_mi_srr',
    'description': 'Mutual information analysis and signal-to-redundancy ratio',
    'mi_analysis': {
        '17_basic': mi_basic,
        '49_full': mi_full,
        'vif_selected': mi_vif,
        'pca': mi_pca
    },
    'incremental_mi': {
        '17_basic': incr_basic,
        '49_full': incr_full
    },
    'information_bottleneck': {
        '17_basic': ib_basic,
        '49_full': ib_full,
        'vif_selected': ib_vif
    },
    'summary': {
        'srr_comparison': {
            '17_basic': mi_basic['signal_redundancy_ratio'],
            '49_full': mi_full['signal_redundancy_ratio'],
            'vif_selected': mi_vif['signal_redundancy_ratio'],
            'pca': mi_pca['signal_redundancy_ratio']
        },
        'info_efficiency_comparison': {
            '17_basic': mi_basic['info_efficiency']['mean'],
            '49_full': mi_full['info_efficiency']['mean'],
            'vif_selected': mi_vif['info_efficiency']['mean'],
            'pca': mi_pca['info_efficiency']['mean']
        },
        'key_finding': 'SRR and info_efficiency comparison reveals whether expanded features add more signal or more redundancy'
    }
}

output_path = r'D:\荷的预测\real_data\experiment_1_mi_srr_results.json'
with open(output_path, 'w') as f:
    json.dump(output, f, indent=2, ensure_ascii=False)

log(f'\n结果保存至: {output_path}')
log('\n' + '=' * 70)
log('实验 #1 完成！')
log('=' * 70)
