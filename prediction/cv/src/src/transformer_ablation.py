"""TimeTransformer 消融实验
测试不同配置下的性能表现，为论文提供实验证据
配置1: 原始配置（大模型+手工特征）→ 当前过拟合版本
配置2: 减小模型容量（d_model=32, 1层, dropout=0.3）
配置3: 只用原始特征（8个，不加手工滞后特征），让Transformer自己学习
配置4: 组合优化（小模型+原始特征+大LOOKBACK）
"""
import pandas as pd
import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset
import lightgbm as lgb
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import r2_score, mean_absolute_error, mean_squared_error
import json
import time
import sys

def log(msg):
    print(msg, flush=True)
    sys.stdout.flush()

log('=' * 80)
log('TimeTransformer 消融实验')
log('=' * 80)

# 加载数据
charging_df = pd.read_csv(r'D:\荷的预测\real_data\Dataset\Charging_Data.csv', encoding='gbk')
charging_df = charging_df[charging_df['Location Information'].str.contains('Expressway Service District', na=False)]
charging_df['Start Time'] = pd.to_datetime(charging_df['Start Time'])
charging_df['hour_window'] = charging_df['Start Time'].dt.floor('H')
hourly_df = charging_df.groupby('hour_window').agg(total_energy=('Transaction power/kwh', 'sum')).reset_index()
hourly_df = hourly_df.sort_values('hour_window').reset_index(drop=True)

# 基础特征
hourly_df['hour'] = hourly_df['hour_window'].dt.hour
hourly_df['day_of_week'] = hourly_df['hour_window'].dt.dayofweek
hourly_df['month'] = hourly_df['hour_window'].dt.month
hourly_df['is_weekend'] = (hourly_df['day_of_week'] >= 5).astype(int)
hourly_df['hour_sin'] = np.sin(2 * np.pi * hourly_df['hour'] / 24)
hourly_df['hour_cos'] = np.cos(2 * np.pi * hourly_df['hour'] / 24)
hourly_df['dow_sin'] = np.sin(2 * np.pi * hourly_df['day_of_week'] / 7)
hourly_df['dow_cos'] = np.cos(2 * np.pi * hourly_df['day_of_week'] / 7)

# 扩展特征（手工滞后等）
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
hourly_df['diff_24'] = hourly_df['total_energy'] - hourly_df['total_energy'].shift(24)
hourly_df['hour_x_weekend'] = hourly_df['hour'] * hourly_df['is_weekend']

hourly_df = hourly_df.fillna(method='ffill').fillna(method='bfill').fillna(0)

# 定义特征集
FEATURE_SETS = {
    'raw': ['hour', 'day_of_week', 'month', 'is_weekend', 'hour_sin', 'hour_cos', 'dow_sin', 'dow_cos'],
    'full': [col for col in hourly_df.columns if col not in ['hour_window', 'total_energy']]
}

# 定义模型配置
MODEL_CONFIGS = {
    'config1': {'d_model': 64, 'num_layers': 2, 'nhead': 4, 'dropout': 0.1, 'lookback': 48, 'label': '原始配置'},
    'config2': {'d_model': 32, 'num_layers': 1, 'nhead': 2, 'dropout': 0.3, 'lookback': 48, 'label': '小模型'},
    'config3': {'d_model': 64, 'num_layers': 2, 'nhead': 4, 'dropout': 0.1, 'lookback': 96, 'label': '大窗口'},
    'config4': {'d_model': 32, 'num_layers': 1, 'nhead': 2, 'dropout': 0.3, 'lookback': 96, 'label': '组合优化'},
}

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
        xs.append(X[i-lb:i])
        ys.append(y[i])
    return np.array(xs), np.array(ys)

def train_and_eval(X, y, features, model_cfg, w, n_total, train_end):
    y_target = pd.Series(y).rolling(w, min_periods=1).mean().shift(-(w-1)).values
    y_clean = np.nan_to_num(y_target, 0.0)
    
    y_tr = y_clean[:train_end]
    y_te = y_clean[train_end:]
    
    sX = StandardScaler()
    X_tr = sX.fit_transform(X[:train_end]).astype(np.float32)
    X_te = sX.transform(X[train_end:]).astype(np.float32)
    
    sy = StandardScaler()
    y_tr_s = sy.fit_transform(y_tr.reshape(-1,1)).flatten().astype(np.float32)
    
    lookback = model_cfg['lookback']
    X_tr_seq, y_tr_seq = create_seq(X_tr, y_tr_s, lookback)
    X_te_seq, _ = create_seq(X_te, np.zeros(len(X_te), dtype=np.float32), lookback)
    
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    model = TimeTransformer(X_tr_seq.shape[2], 
                            d_model=model_cfg['d_model'],
                            nhead=model_cfg['nhead'],
                            num_layers=model_cfg['num_layers'],
                            dropout=model_cfg['dropout']).to(device)
    
    optimizer = torch.optim.Adam(model.parameters(), lr=0.001)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, patience=5, factor=0.5)
    criterion = nn.MSELoss()
    
    train_ds = TensorDataset(torch.tensor(X_tr_seq), torch.tensor(y_tr_seq))
    train_loader = DataLoader(train_ds, batch_size=64, shuffle=True)
    
    best_val = float('inf')
    patience = 0
    
    for epoch in range(80):
        model.train()
        tr_loss = 0
        n = 0
        for bx, by in train_loader:
            bx, by = bx.to(device), by.to(device)
            optimizer.zero_grad()
            pred = model(bx)
            loss = criterion(pred, by)
            loss.backward()
            optimizer.step()
            tr_loss += loss.item() * len(bx)
            n += len(bx)
        
        model.eval()
        with torch.no_grad():
            val_pred = model(torch.tensor(X_te_seq).to(device))
            y_te_scaled = sy.transform(y_te[lookback:].reshape(-1,1)).flatten()
            val_loss = criterion(val_pred, torch.tensor(y_te_scaled).to(device)).item()
        
        scheduler.step(val_loss)
        
        if val_loss < best_val:
            best_val = val_loss
            patience = 0
        else:
            patience += 1
            if patience >= 15:
                break
    
    model.eval()
    with torch.no_grad():
        pred_s = model(torch.tensor(X_te_seq).to(device)).cpu().numpy()
    pred = sy.inverse_transform(pred_s.reshape(-1,1)).flatten()
    
    y_te_valid = y_te[lookback:]
    mask = y_te_valid > 0
    
    r2 = r2_score(y_te_valid[mask], pred[:len(y_te_valid)][mask])
    mae = mean_absolute_error(y_te_valid[mask], pred[:len(y_te_valid)][mask])
    rmse = np.sqrt(mean_squared_error(y_te_valid[mask], pred[:len(y_te_valid)][mask]))
    
    return {'r2': r2, 'mae': mae, 'rmse': rmse, 'params': sum(p.numel() for p in model.parameters())}

# 主实验
y_raw = hourly_df['total_energy'].values.astype(float)
n_total = len(hourly_df)
train_end = int(n_total * 0.8)

results = {}

for w in [4, 8]:
    results[w] = {}
    log(f'\n--- w={w}h ---')
    log(f'{"配置":<12} | {"特征集":<8} | {"参数":<8} | {"R²":<8} | {"MAE":<8} | {"RMSE":<8}')
    log('-' * 60)
    
    for cfg_name, cfg in MODEL_CONFIGS.items():
        for feat_name, features in FEATURE_SETS.items():
            X = hourly_df[features].values.astype(np.float32)
            key = f'{cfg_name}_{feat_name}'
            
            t0 = time.time()
            res = train_and_eval(X, y_raw, features, cfg, w, n_total, train_end)
            elapsed = time.time() - t0
            
            results[w][key] = {
                'config': cfg['label'],
                'features': feat_name,
                'params': res['params'],
                'r2': res['r2'],
                'mae': res['mae'],
                'rmse': res['rmse'],
                'time': elapsed
            }
            
            log(f'{cfg["label"]:<12} | {feat_name:<8} | {res["params"]:<8} | {res["r2"]:<8.4f} | {res["mae"]:<8.2f} | {res["rmse"]:<8.2f}')

# LightGBM baseline
log(f'\n--- LightGBM Baseline ---')
lgb_params = {
    'objective': 'regression', 'metric': 'rmse',
    'num_leaves': 255, 'max_depth': 12, 'learning_rate': 0.05,
    'n_estimators': 2000, 'verbose': 0, 'n_jobs': -1,
    'random_state': 42, 'force_row_wise': True
}

for w in [4, 8]:
    y_target = pd.Series(y_raw).rolling(w, min_periods=1).mean().shift(-(w-1)).values
    y_clean = np.nan_to_num(y_target, 0.0)
    y_tr = y_clean[:train_end]
    y_te = y_clean[train_end:]
    
    for feat_name, features in FEATURE_SETS.items():
        X = hourly_df[features].values.astype(np.float32)
        sX = StandardScaler()
        X_tr = sX.fit_transform(X[:train_end])
        X_te = sX.transform(X[train_end:])
        
        model = lgb.LGBMRegressor(**lgb_params)
        model.fit(X_tr, y_tr, eval_set=[(X_te, y_te)], eval_metric='rmse',
                  callbacks=[lgb.early_stopping(30), lgb.log_evaluation(0)])
        
        pred = model.predict(X_te)
        mask = y_te > 0
        
        r2 = r2_score(y_te[mask], pred[mask])
        mae = mean_absolute_error(y_te[mask], pred[mask])
        rmse = np.sqrt(mean_squared_error(y_te[mask], pred[mask]))
        
        results[w][f'lgb_{feat_name}'] = {
            'config': 'LightGBM',
            'features': feat_name,
            'params': '~255 leaves',
            'r2': r2,
            'mae': mae,
            'rmse': rmse
        }
        
        log(f'LightGBM        | {feat_name:<8} | 255 leaves | {r2:<8.4f} | {mae:<8.2f} | {rmse:<8.2f}')

# 保存结果
with open(r'D:\荷的预测\real_data\ablation_results.json', 'w', encoding='utf-8') as f:
    json.dump(results, f, indent=2, ensure_ascii=False)

log('\n消融实验完成，结果已保存到 ablation_results.json')

# 总结分析
log('\n[分析总结]')
log('=' * 80)

for w in [4, 8]:
    log(f'\nw={w}h:')
    
    # 找最佳Transformer配置
    best_tt = None
    best_r2 = -float('inf')
    for key, val in results[w].items():
        if 'lgb' not in key and val['r2'] > best_r2:
            best_r2 = val['r2']
            best_tt = key
    
    # LightGBM最佳
    lgb_full = results[w]['lgb_full']
    
    log(f'  最佳Transformer: {results[w][best_tt]["config"]} + {results[w][best_tt]["features"]} → R²={best_r2:.4f}')
    log(f'  LightGBM(full): R²={lgb_full["r2"]:.4f}')
    log(f'  差距: {best_r2 - lgb_full["r2"]:+.4f}')
    
    # 对比config1(原始) vs config4(组合优化)
    if 'config1_full' in results[w] and 'config4_raw' in results[w]:
        orig = results[w]['config1_full']
        opt = results[w]['config4_raw']
        log(f'\n  原始配置(full): R²={orig["r2"]:.4f}, 参数={orig["params"]}')
        log(f'  组合优化(raw): R²={opt["r2"]:.4f}, 参数={opt["params"]}')
        log(f'  改进: {(opt["r2"] - orig["r2"]) / abs(orig["r2"]) * 100:+.1f}%')
