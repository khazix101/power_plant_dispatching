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

log('1. 加载数据')
charging_df = pd.read_csv(r'D:\荷的预测\real_data\Dataset\Charging_Data.csv', encoding='gbk')
charging_df = charging_df[charging_df['Location Information'].str.contains('Expressway Service District', na=False)]
charging_df['Start Time'] = pd.to_datetime(charging_df['Start Time'])
charging_df['hour_window'] = charging_df['Start Time'].dt.floor('H')

hourly_df = charging_df.groupby('hour_window').agg(total_energy=('Transaction power/kwh', 'sum')).reset_index()
hourly_df = hourly_df.sort_values('hour_window').reset_index(drop=True)

log('2. 创建基础特征')
hourly_df['hour'] = hourly_df['hour_window'].dt.hour
hourly_df['day_of_week'] = hourly_df['hour_window'].dt.dayofweek
hourly_df['month'] = hourly_df['hour_window'].dt.month
hourly_df['day_of_month'] = hourly_df['hour_window'].dt.day
hourly_df['is_weekend'] = (hourly_df['day_of_week'] >= 5).astype(int)
hourly_df['is_workday'] = 1 - hourly_df['is_weekend']

log('3. 周期性编码')
hourly_df['hour_sin'] = np.sin(2 * np.pi * hourly_df['hour'] / 24)
hourly_df['hour_cos'] = np.cos(2 * np.pi * hourly_df['hour'] / 24)
hourly_df['dow_sin'] = np.sin(2 * np.pi * hourly_df['day_of_week'] / 7)
hourly_df['dow_cos'] = np.cos(2 * np.pi * hourly_df['day_of_week'] / 7)
hourly_df['month_sin'] = np.sin(2 * np.pi * hourly_df['month'] / 12)
hourly_df['month_cos'] = np.cos(2 * np.pi * hourly_df['month'] / 12)

log('4. 滞后特征')
lags = [1, 2, 3, 4, 6, 8, 12, 18, 24, 36, 48]
for i in lags:
    hourly_df[f'lag{i}_energy'] = hourly_df['total_energy'].shift(i)

log('5. 滚动统计特征')
for window in [4, 8, 12, 24, 48]:
    hourly_df[f'rolling_mean_{window}'] = hourly_df['total_energy'].rolling(window, min_periods=1).mean().shift(1)
    hourly_df[f'rolling_std_{window}'] = hourly_df['total_energy'].rolling(window, min_periods=1).std().shift(1)
    hourly_df[f'rolling_max_{window}'] = hourly_df['total_energy'].rolling(window, min_periods=1).max().shift(1)
    hourly_df[f'rolling_min_{window}'] = hourly_df['total_energy'].rolling(window, min_periods=1).min().shift(1)
    hourly_df[f'rolling_q25_{window}'] = hourly_df['total_energy'].rolling(window, min_periods=1).quantile(0.25).shift(1)
    hourly_df[f'rolling_q75_{window}'] = hourly_df['total_energy'].rolling(window, min_periods=1).quantile(0.75).shift(1)

log('6. 差分特征')
hourly_df['diff_1'] = hourly_df['total_energy'] - hourly_df['total_energy'].shift(1)
hourly_df['diff_4'] = hourly_df['total_energy'] - hourly_df['total_energy'].shift(4)
hourly_df['diff_8'] = hourly_df['total_energy'] - hourly_df['total_energy'].shift(8)
hourly_df['diff_12'] = hourly_df['total_energy'] - hourly_df['total_energy'].shift(12)
hourly_df['diff_24'] = hourly_df['total_energy'] - hourly_df['total_energy'].shift(24)

log('7. 交互特征')
hourly_df['hour_x_weekend'] = hourly_df['hour'] * hourly_df['is_weekend']
hourly_df['hour_x_workday'] = hourly_df['hour'] * hourly_df['is_workday']
hourly_df['dow_x_weekend'] = hourly_df['day_of_week'] * hourly_df['is_weekend']

log('8. 比率特征')
hourly_df['lag1_ratio'] = hourly_df['total_energy'] / (hourly_df['total_energy'].shift(1) + 1e-8)
hourly_df['lag24_ratio'] = hourly_df['total_energy'] / (hourly_df['total_energy'].shift(24) + 1e-8)

log('9. 指数移动平均')
hourly_df['ema_4'] = hourly_df['total_energy'].ewm(span=4).mean().shift(1)
hourly_df['ema_8'] = hourly_df['total_energy'].ewm(span=8).mean().shift(1)
hourly_df['ema_24'] = hourly_df['total_energy'].ewm(span=24).mean().shift(1)

log('10. 填充缺失值')
hourly_df = hourly_df.fillna(method='ffill').fillna(method='bfill').fillna(0)

old_features = ['hour', 'day_of_week', 'month', 'is_weekend', 'hour_sin', 'hour_cos', 'dow_sin', 'dow_cos',
                'lag1_energy', 'lag2_energy', 'lag3_energy', 'lag4_energy', 'lag8_energy', 'lag12_energy', 'lag24_energy',
                'rolling_mean_4', 'rolling_std_4']

all_features = [col for col in hourly_df.columns if col not in ['hour_window', 'total_energy']]

log(f'  原始特征数: {len(old_features)}')
log(f'  扩充后特征数: {len(all_features)}')

X_old = hourly_df[old_features].values.astype(np.float32)
X_new = hourly_df[all_features].values.astype(np.float32)
y_raw = hourly_df['total_energy'].values.astype(float)
n_total = len(hourly_df)
train_end = int(n_total * 0.8)

device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
log(f'  设备: {device}')

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

def train_lgbm(X_tr, y_tr, X_te, y_te, params):
    model = lgb.LGBMRegressor(**params)
    model.fit(X_tr, y_tr, eval_set=[(X_te, y_te)], eval_metric='rmse',
              callbacks=[lgb.early_stopping(30), lgb.log_evaluation(0)])
    return model

def train_transformer(X_tr_s, y_tr, X_te_s, y_te, feature_dim, lookback=48, epochs=80):
    sy = StandardScaler()
    y_tr_s = sy.fit_transform(y_tr.reshape(-1,1)).flatten().astype(np.float32)
    
    X_tr_seq, y_tr_seq = create_seq(X_tr_s.astype(np.float32), y_tr_s, lookback)
    X_te_seq, _ = create_seq(X_te_s.astype(np.float32), np.zeros(len(X_te_s), dtype=np.float32), lookback)
    
    train_ds = TensorDataset(torch.tensor(X_tr_seq), torch.tensor(y_tr_seq))
    train_loader = DataLoader(train_ds, batch_size=64, shuffle=True)
    
    model = TimeTransformer(feature_dim, d_model=64, nhead=4, num_layers=2).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=0.001)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, patience=5, factor=0.5)
    criterion = nn.MSELoss()
    
    best_val_loss = float('inf')
    patience = 0
    
    for epoch in range(epochs):
        model.train()
        tr_loss = 0
        for bx, by in train_loader:
            bx, by = bx.to(device), by.to(device)
            optimizer.zero_grad()
            pred = model(bx)
            loss = criterion(pred, by)
            loss.backward()
            optimizer.step()
            tr_loss += loss.item() * len(bx)
        
        model.eval()
        with torch.no_grad():
            val_pred = model(torch.tensor(X_te_seq).to(device))
            val_loss = criterion(val_pred, torch.tensor(sy.transform(y_te[lookback:].reshape(-1,1)).flatten()).to(device)).item()
        
        scheduler.step(val_loss)
        
        if val_loss < best_val_loss:
            best_val_loss = val_loss
            patience = 0
        else:
            patience += 1
            if patience >= 15:
                break
    
    model.eval()
    with torch.no_grad():
        pred_s = model(torch.tensor(X_te_seq).to(device)).cpu().numpy()
    return sy.inverse_transform(pred_s.reshape(-1,1)).flatten(), sy

lgb_params = {
    'objective': 'regression', 'metric': 'rmse',
    'num_leaves': 255, 'max_depth': 12, 'learning_rate': 0.05,
    'n_estimators': 2000, 'verbose': 0, 'n_jobs': -1,
    'random_state': 42, 'force_row_wise': True
}

log('')
log('=' * 90)
log('| 窗口 | CV值  | LGB(旧) | LGB(新) | TimeT(旧) | TimeT(新) |')
log('|------|-------|---------|---------|-----------|-----------|')

results = []

for w in [1, 2, 4, 8, 12, 16]:
    y_target = pd.Series(y_raw).rolling(w, min_periods=1).mean().shift(-(w-1)).values
    y_clean = np.nan_to_num(y_target, 0.0)
    y_te_segment = y_clean[train_end:]
    y_tv = y_te_segment[y_te_segment > 0]
    cv = np.std(y_tv) / np.mean(y_tv) * 100 if len(y_tv) > 0 else 0
    
    y_tr = y_clean[:train_end]
    y_te = y_clean[train_end:]
    
    sX_old = StandardScaler()
    X_tr_old = sX_old.fit_transform(X_old[:train_end])
    X_te_old = sX_old.transform(X_old[train_end:])
    
    sX_new = StandardScaler()
    X_tr_new = sX_new.fit_transform(X_new[:train_end])
    X_te_new = sX_new.transform(X_new[train_end:])
    
    log(f'')
    log(f'--- w={w}h (CV={cv:.2f}%) ---')
    
    log(f'  LightGBM (旧特征)...')
    t0 = time.time()
    lgb_old = train_lgbm(X_tr_old, y_tr, X_te_old, y_te, lgb_params)
    lgb_pred_old = lgb_old.predict(X_te_old)
    lgb_r2_old = r2_score(y_te[y_te>0], lgb_pred_old[y_te>0])
    lgb_mae_old = mean_absolute_error(y_te[y_te>0], lgb_pred_old[y_te>0])
    lgb_rmse_old = np.sqrt(mean_squared_error(y_te[y_te>0], lgb_pred_old[y_te>0]))
    lgb_mape_old = np.mean(np.abs((y_te[y_te>0] - lgb_pred_old[y_te>0]) / y_te[y_te>0])) * 100
    log(f'  LightGBM (旧): R²={lgb_r2_old:.4f}, MAE={lgb_mae_old:.2f}, RMSE={lgb_rmse_old:.2f}, MAPE={lgb_mape_old:.2f}%, Time={time.time()-t0:.1f}s')
    
    log(f'  LightGBM (新特征)...')
    t0 = time.time()
    lgb_new = train_lgbm(X_tr_new, y_tr, X_te_new, y_te, lgb_params)
    lgb_pred_new = lgb_new.predict(X_te_new)
    lgb_r2_new = r2_score(y_te[y_te>0], lgb_pred_new[y_te>0])
    lgb_mae_new = mean_absolute_error(y_te[y_te>0], lgb_pred_new[y_te>0])
    lgb_rmse_new = np.sqrt(mean_squared_error(y_te[y_te>0], lgb_pred_new[y_te>0]))
    lgb_mape_new = np.mean(np.abs((y_te[y_te>0] - lgb_pred_new[y_te>0]) / y_te[y_te>0])) * 100
    log(f'  LightGBM (新): R²={lgb_r2_new:.4f}, MAE={lgb_mae_new:.2f}, RMSE={lgb_rmse_new:.2f}, MAPE={lgb_mape_new:.2f}%, Time={time.time()-t0:.1f}s')
    
    log(f'  TimeTransformer (旧特征)...')
    t0 = time.time()
    tt_pred_old, _ = train_transformer(X_tr_old, y_tr, X_te_old, y_te, X_old.shape[1])
    y_te_tt = y_te[48:]
    tt_pred_old_valid = tt_pred_old[:len(y_te_tt)]
    tt_r2_old = r2_score(y_te_tt[y_te_tt>0], tt_pred_old_valid[y_te_tt>0])
    tt_mae_old = mean_absolute_error(y_te_tt[y_te_tt>0], tt_pred_old_valid[y_te_tt>0])
    tt_rmse_old = np.sqrt(mean_squared_error(y_te_tt[y_te_tt>0], tt_pred_old_valid[y_te_tt>0]))
    tt_mape_old = np.mean(np.abs((y_te_tt[y_te_tt>0] - tt_pred_old_valid[y_te_tt>0]) / y_te_tt[y_te_tt>0])) * 100
    log(f'  TimeTransformer (旧): R²={tt_r2_old:.4f}, MAE={tt_mae_old:.2f}, RMSE={tt_rmse_old:.2f}, MAPE={tt_mape_old:.2f}%, Time={time.time()-t0:.1f}s')
    
    log(f'  TimeTransformer (新特征)...')
    t0 = time.time()
    tt_pred_new, _ = train_transformer(X_tr_new, y_tr, X_te_new, y_te, X_new.shape[1])
    tt_pred_new_valid = tt_pred_new[:len(y_te_tt)]
    tt_r2_new = r2_score(y_te_tt[y_te_tt>0], tt_pred_new_valid[y_te_tt>0])
    tt_mae_new = mean_absolute_error(y_te_tt[y_te_tt>0], tt_pred_new_valid[y_te_tt>0])
    tt_rmse_new = np.sqrt(mean_squared_error(y_te_tt[y_te_tt>0], tt_pred_new_valid[y_te_tt>0]))
    tt_mape_new = np.mean(np.abs((y_te_tt[y_te_tt>0] - tt_pred_new_valid[y_te_tt>0]) / y_te_tt[y_te_tt>0])) * 100
    log(f'  TimeTransformer (新): R²={tt_r2_new:.4f}, MAE={tt_mae_new:.2f}, RMSE={tt_rmse_new:.2f}, MAPE={tt_mape_new:.2f}%, Time={time.time()-t0:.1f}s')
    
    lgb_winner_new = 'LightGBM' if lgb_r2_new > tt_r2_new else 'TimeTransformer'
    
    log(f'| {w:3d}h | {cv:5.1f}% | {lgb_r2_old:9.4f} | {lgb_r2_new:9.4f} | {tt_r2_old:11.4f} | {tt_r2_new:11.4f} |')
    
    results.append({
        'window': w, 'cv': cv,
        'lgb_old_r2': lgb_r2_old, 'lgb_new_r2': lgb_r2_new,
        'lgb_old_mae': lgb_mae_old, 'lgb_new_mae': lgb_mae_new,
        'lgb_old_rmse': lgb_rmse_old, 'lgb_new_rmse': lgb_rmse_new,
        'lgb_old_mape': lgb_mape_old, 'lgb_new_mape': lgb_mape_new,
        'tt_old_r2': tt_r2_old, 'tt_new_r2': tt_r2_new,
        'tt_old_mae': tt_mae_old, 'tt_new_mae': tt_mae_new,
        'tt_old_rmse': tt_rmse_old, 'tt_new_rmse': tt_rmse_new,
        'tt_old_mape': tt_mape_old, 'tt_new_mape': tt_mape_new,
        'winner_new': lgb_winner_new,
        'lgb_improvement': ((lgb_r2_new - lgb_r2_old) / abs(lgb_r2_old) * 100) if lgb_r2_old != 0 else 0,
        'tt_improvement': ((tt_r2_new - tt_r2_old) / abs(tt_r2_old) * 100) if tt_r2_old != 0 else 0
    })

log('')
log('=' * 90)

output = {
    'old_feature_count': len(old_features),
    'new_feature_count': len(all_features),
    'features': {
        'old': old_features,
        'new': all_features
    },
    'results': results
}

with open(r'D:\荷的预测\real_data\feature_expansion_results.json', 'w', encoding='utf-8') as f:
    json.dump(output, f, indent=2, ensure_ascii=False)

log('')
log('结果已保存到 feature_expansion_results.json')

log('')
log('【结论】')
log('-' * 50)
for r in results:
    log(f'w={r["window"]}h: LightGBM {r["lgb_old_r2"]:.4f}→{r["lgb_new_r2"]:.4f} ({r["lgb_improvement"]:+.1f}%)')
    log(f'       TimeTransformer {r["tt_old_r2"]:.4f}→{r["tt_new_r2"]:.4f} ({r["tt_improvement"]:+.1f}%)')
    log(f'       胜者: {r["winner_new"]}')