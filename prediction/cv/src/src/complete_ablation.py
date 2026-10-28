import pandas as pd
import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset
import lightgbm as lgb
import xgboost as xgb
from catboost import CatBoostRegressor
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import r2_score, mean_absolute_error, mean_squared_error
from scipy import stats
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
        x = x.mean(dim=1)
        x = self.fc(x)
        return x

class BiLSTMModel(nn.Module):
    def __init__(self, in_dim, hidden_dim=64):
        super().__init__()
        self.lstm = nn.LSTM(in_dim, hidden_dim, batch_first=True, bidirectional=True)
        self.fc = nn.Sequential(nn.Linear(hidden_dim*2, 128), nn.ReLU(),
                               nn.Linear(128, 64), nn.ReLU(),
                               nn.Linear(64, 32), nn.ReLU(),
                               nn.Linear(32, 1))
    
    def forward(self, x):
        _, (h, _) = self.lstm(x)
        x = torch.cat([h[0], h[1]], dim=1)
        x = self.fc(x)
        return x

class CNNBiLSTMModel(nn.Module):
    def __init__(self, in_dim):
        super().__init__()
        self.cnn = nn.Sequential(nn.Conv1d(in_dim, 32, kernel_size=3, padding=1),
                                nn.BatchNorm1d(32), nn.ReLU(),
                                nn.Conv1d(32, 32, kernel_size=5, padding=2),
                                nn.BatchNorm1d(32), nn.ReLU())
        self.lstm = nn.LSTM(32, 64, batch_first=True, bidirectional=True)
        self.fc = nn.Linear(64*2, 1)
    
    def forward(self, x):
        x = x.transpose(1, 2)
        x = self.cnn(x)
        x = x.transpose(1, 2)
        _, (h, _) = self.lstm(x)
        x = torch.cat([h[0], h[1]], dim=1)
        x = self.fc(x)
        return x

def create_sequences(X, y, window=48, horizon=4):
    X_seq, y_seq = [], []
    for i in range(window, len(X) - horizon):
        X_seq.append(X[i-window:i])
        y_seq.append(y[i+horizon])
    return np.array(X_seq), np.array(y_seq)

def train_torch_model(model, X_train_seq, y_train_seq, X_val_seq, y_val_seq, epochs=80, lr=0.001):
    model = model.to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, 'min', patience=10, factor=0.5)
    criterion = nn.MSELoss()
    
    train_dataset = TensorDataset(torch.tensor(X_train_seq).float().to(device), 
                                 torch.tensor(y_train_seq).float().to(device))
    val_dataset = TensorDataset(torch.tensor(X_val_seq).float().to(device), 
                               torch.tensor(y_val_seq).float().to(device))
    
    train_loader = DataLoader(train_dataset, batch_size=64, shuffle=True)
    val_loader = DataLoader(val_dataset, batch_size=64, shuffle=False)
    
    best_val_loss = float('inf')
    for epoch in range(epochs):
        model.train()
        train_loss = 0
        for batch_X, batch_y in train_loader:
            optimizer.zero_grad()
            pred = model(batch_X).squeeze()
            loss = criterion(pred, batch_y)
            loss.backward()
            optimizer.step()
            train_loss += loss.item() * len(batch_X)
        
        model.eval()
        val_loss = 0
        with torch.no_grad():
            for batch_X, batch_y in val_loader:
                pred = model(batch_X).squeeze()
                loss = criterion(pred, batch_y)
                val_loss += loss.item() * len(batch_X)
        
        train_loss /= len(train_dataset)
        val_loss /= len(val_dataset)
        scheduler.step(val_loss)
        
        if val_loss < best_val_loss:
            best_val_loss = val_loss
            best_state = model.state_dict().copy()
        
        if (epoch + 1) % 20 == 0:
            log(f'    Epoch {epoch+1}/{epochs}: train_loss={train_loss:.4f}, val_loss={val_loss:.4f}')
    
    model.load_state_dict(best_state)
    return model

def compute_bias_variance(model_func, X_train, X_test, y_train, y_test, n_bootstraps=50):
    predictions = []
    for _ in range(n_bootstraps):
        idx = np.random.choice(len(X_train), len(X_train), replace=True)
        X_boot, y_boot = X_train[idx], y_train[idx]
        
        if 'lgb' in str(model_func.__name__).lower() or 'xgb' in str(model_func.__name__).lower():
            model = model_func(X_boot, y_boot, X_test, y_test)
            pred = model.predict(X_test)
        else:
            model = model_func(X_boot, y_boot, X_test, y_test)
            pred = model.predict(X_test)
        
        predictions.append(pred)
    
    predictions = np.array(predictions)
    mean_pred = predictions.mean(axis=0)
    mse = ((mean_pred - y_test)**2).mean()
    bias_sq = ((mean_pred - y_test)**2).mean()
    variance = ((predictions - mean_pred)**2).mean()
    
    return {'mse': float(mse), 'bias_sq': float(bias_sq), 'variance': float(variance)}

def diebold_mariano_test(y_true, pred1, pred2, h=1):
    e1 = y_true - pred1
    e2 = y_true - pred2
    d = e1**2 - e2**2
    n = len(d)
    
    autocov = []
    for lag in range(min(h, n-1)):
        autocov.append(np.mean(d[:n-lag] * d[lag:]))
    
    var_d = autocov[0] + 2 * sum(autocov[1:]) / n
    dm_stat = np.mean(d) / np.sqrt(var_d / n)
    p_value = 2 * (1 - stats.norm.cdf(np.abs(dm_stat)))
    
    return {'dm_stat': float(dm_stat), 'p_value': float(p_value)}

def run_lgb(X_train, y_train, X_test, y_test):
    model = lgb.LGBMRegressor(num_leaves=255, max_depth=12, learning_rate=0.05, 
                              n_estimators=2000, subsample=0.8, colsample_bytree=0.8,
                              reg_alpha=0.1, reg_lambda=0.1, verbose=-1)
    model.fit(X_train, y_train, eval_set=[(X_test, y_test)], eval_metric='mse', 
              early_stopping_rounds=100, verbose=False)
    return model

def run_xgb(X_train, y_train, X_test, y_test):
    model = xgb.XGBRegressor(n_estimators=2000, max_depth=12, learning_rate=0.05,
                             subsample=0.8, colsample_bytree=0.8, reg_alpha=0.1, 
                             reg_lambda=0.1, verbosity=0)
    model.fit(X_train, y_train, eval_set=[(X_test, y_test)], eval_metric='rmse',
              early_stopping_rounds=100, verbose=False)
    return model

def run_catboost(X_train, y_train, X_test, y_test):
    model = CatBoostRegressor(iterations=2000, depth=12, learning_rate=0.05,
                              verbose=False, early_stopping_rounds=100)
    model.fit(X_train, y_train, eval_set=[(X_test, y_test)], use_best_model=True)
    return model

log('11. 划分数据集')
results = {}

for feature_set_name, X_data in [('17_features', X_old), ('66_features', X_new)]:
    log(f'=== {feature_set_name} ===')
    results[feature_set_name] = {}
    
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X_data)
    
    for horizon in [1, 4, 8, 12, 16]:
        log(f'  预测窗口: {horizon}h')
        results[feature_set_name][f'{horizon}h'] = {}
        
        y = np.zeros(len(y_raw))
        for i in range(len(y_raw) - horizon):
            y[i] = np.mean(y_raw[i:i+horizon])
        
        X_seq, y_seq = create_sequences(X_scaled, y, window=48, horizon=horizon)
        X_train_seq, X_test_seq = X_seq[:train_end], X_seq[train_end:]
        y_train_seq, y_test_seq = y_seq[:train_end], y_seq[train_end:]
        X_train_flat = X_scaled[:train_end]
        X_test_flat = X_scaled[train_end:]
        y_train = y[:train_end]
        y_test = y[train_end:]
        
        log('    训练LightGBM...')
        start = time.time()
        lgb_model = run_lgb(X_train_flat, y_train, X_test_flat, y_test)
        lgb_pred = lgb_model.predict(X_test_flat)
        lgb_time = time.time() - start
        lgb_r2 = r2_score(y_test, lgb_pred)
        results[feature_set_name][f'{horizon}h']['LightGBM'] = {
            'r2': float(lgb_r2), 'rmse': float(np.sqrt(mean_squared_error(y_test, lgb_pred))),
            'mae': float(mean_absolute_error(y_test, lgb_pred)), 'time': float(lgb_time)
        }
        log(f'      LightGBM R²={lgb_r2:.4f} ({lgb_time:.1f}s)')
        
        log('    训练XGBoost...')
        start = time.time()
        xgb_model = run_xgb(X_train_flat, y_train, X_test_flat, y_test)
        xgb_pred = xgb_model.predict(X_test_flat)
        xgb_time = time.time() - start
        xgb_r2 = r2_score(y_test, xgb_pred)
        results[feature_set_name][f'{horizon}h']['XGBoost'] = {
            'r2': float(xgb_r2), 'rmse': float(np.sqrt(mean_squared_error(y_test, xgb_pred))),
            'mae': float(mean_absolute_error(y_test, xgb_pred)), 'time': float(xgb_time)
        }
        log(f'      XGBoost R²={xgb_r2:.4f} ({xgb_time:.1f}s)')
        
        log('    训练CatBoost...')
        start = time.time()
        cat_model = run_catboost(X_train_flat, y_train, X_test_flat, y_test)
        cat_pred = cat_model.predict(X_test_flat)
        cat_time = time.time() - start
        cat_r2 = r2_score(y_test, cat_pred)
        results[feature_set_name][f'{horizon}h']['CatBoost'] = {
            'r2': float(cat_r2), 'rmse': float(np.sqrt(mean_squared_error(y_test, cat_pred))),
            'mae': float(mean_absolute_error(y_test, cat_pred)), 'time': float(cat_time)
        }
        log(f'      CatBoost R²={cat_r2:.4f} ({cat_time:.1f}s)')
        
        log('    训练BiLSTM...')
        start = time.time()
        bilstm = BiLSTMModel(X_scaled.shape[1])
        bilstm = train_torch_model(bilstm, X_train_seq, y_train_seq, X_test_seq, y_test_seq)
        bilstm.eval()
        with torch.no_grad():
            bilstm_pred = bilstm(torch.tensor(X_test_seq).float().to(device)).cpu().numpy().flatten()
        bilstm_time = time.time() - start
        bilstm_r2 = r2_score(y_test_seq, bilstm_pred)
        results[feature_set_name][f'{horizon}h']['BiLSTM'] = {
            'r2': float(bilstm_r2), 'rmse': float(np.sqrt(mean_squared_error(y_test_seq, bilstm_pred))),
            'mae': float(mean_absolute_error(y_test_seq, bilstm_pred)), 'time': float(bilstm_time)
        }
        log(f'      BiLSTM R²={bilstm_r2:.4f} ({bilstm_time:.1f}s)')
        
        log('    训练CNN-BiLSTM...')
        start = time.time()
        cnn_bilstm = CNNBiLSTMModel(X_scaled.shape[1])
        cnn_bilstm = train_torch_model(cnn_bilstm, X_train_seq, y_train_seq, X_test_seq, y_test_seq)
        cnn_bilstm.eval()
        with torch.no_grad():
            cnn_bilstm_pred = cnn_bilstm(torch.tensor(X_test_seq).float().to(device)).cpu().numpy().flatten()
        cnn_bilstm_time = time.time() - start
        cnn_bilstm_r2 = r2_score(y_test_seq, cnn_bilstm_pred)
        results[feature_set_name][f'{horizon}h']['CNN-BiLSTM'] = {
            'r2': float(cnn_bilstm_r2), 'rmse': float(np.sqrt(mean_squared_error(y_test_seq, cnn_bilstm_pred))),
            'mae': float(mean_absolute_error(y_test_seq, cnn_bilstm_pred)), 'time': float(cnn_bilstm_time)
        }
        log(f'      CNN-BiLSTM R²={cnn_bilstm_r2:.4f} ({cnn_bilstm_time:.1f}s)')
        
        log('    训练TimeTransformer...')
        start = time.time()
        tt = TimeTransformer(X_scaled.shape[1])
        tt = train_torch_model(tt, X_train_seq, y_train_seq, X_test_seq, y_test_seq)
        tt.eval()
        with torch.no_grad():
            tt_pred = tt(torch.tensor(X_test_seq).float().to(device)).cpu().numpy().flatten()
        tt_time = time.time() - start
        tt_r2 = r2_score(y_test_seq, tt_pred)
        results[feature_set_name][f'{horizon}h']['TimeTransformer'] = {
            'r2': float(tt_r2), 'rmse': float(np.sqrt(mean_squared_error(y_test_seq, tt_pred))),
            'mae': float(mean_absolute_error(y_test_seq, tt_pred)), 'time': float(tt_time)
        }
        log(f'      TimeTransformer R²={tt_r2:.4f} ({tt_time:.1f}s)')

log('12. 计算Bias-Variance分解')
results['bias_variance'] = {}

for feature_set_name, X_data in [('17_features', X_old), ('66_features', X_new)]:
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X_data)
    
    y = np.zeros(len(y_raw))
    for i in range(len(y_raw) - 4):
        y[i] = np.mean(y_raw[i:i+4])
    
    X_train, X_test = X_scaled[:train_end], X_scaled[train_end:]
    y_train, y_test = y[:train_end], y[train_end:]
    
    results['bias_variance'][feature_set_name] = {}
    
    log(f'  B-V分解: {feature_set_name} LightGBM...')
    bv_lgb = compute_bias_variance(run_lgb, X_train, X_test, y_train, y_test, n_bootstraps=30)
    results['bias_variance'][feature_set_name]['LightGBM'] = bv_lgb
    log(f'    LightGBM: MSE={bv_lgb["mse"]:.2f}, Bias²={bv_lgb["bias_sq"]:.2f}, Var={bv_lgb["variance"]:.2f}')
    
    log(f'  B-V分解: {feature_set_name} TimeTransformer...')
    bv_tt = compute_bias_variance(lambda X_tr, y_tr, X_te, y_te: train_torch_model(
        TimeTransformer(X_scaled.shape[1]), 
        create_sequences(X_tr, y_tr, window=48, horizon=4)[0],
        create_sequences(X_tr, y_tr, window=48, horizon=4)[1],
        create_sequences(X_te, y_te, window=48, horizon=4)[0],
        create_sequences(X_te, y_te, window=48, horizon=4)[1]),
        X_train, X_test, y_train, y_test, n_bootstraps=30)
    results['bias_variance'][feature_set_name]['TimeTransformer'] = bv_tt
    log(f'    TimeTransformer: MSE={bv_tt["mse"]:.2f}, Bias²={bv_tt["bias_sq"]:.2f}, Var={bv_tt["variance"]:.2f}')

log('13. 计算Diebold-Mariano检验')
results['dm_tests'] = {}

for feature_set_name, X_data in [('17_features', X_old), ('66_features', X_new)]:
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X_data)
    
    y = np.zeros(len(y_raw))
    for i in range(len(y_raw) - 4):
        y[i] = np.mean(y_raw[i:i+4])
    
    X_train, X_test = X_scaled[:train_end], X_scaled[train_end:]
    y_train, y_test = y[:train_end], y[train_end:]
    
    results['dm_tests'][feature_set_name] = {}
    
    lgb_model = run_lgb(X_train, y_train, X_test, y_test)
    lgb_pred = lgb_model.predict(X_test)
    
    tt = TimeTransformer(X_scaled.shape[1])
    X_seq_train, y_seq_train = create_sequences(X_train, y_train, window=48, horizon=4)
    X_seq_test, y_seq_test = create_sequences(X_test, y_test, window=48, horizon=4)
    tt = train_torch_model(tt, X_seq_train, y_seq_train, X_seq_test, y_seq_test)
    tt.eval()
    with torch.no_grad():
        tt_pred = tt(torch.tensor(X_seq_test).float().to(device)).cpu().numpy().flatten()
    
    dm = diebold_mariano_test(y_seq_test, lgb_pred, tt_pred, h=4)
    results['dm_tests'][feature_set_name]['LightGBM_vs_TimeTransformer'] = dm
    log(f'  DM检验 {feature_set_name}: stat={dm["dm_stat"]:.2f}, p={dm["p_value"]:.4f}')

log('14. 保存结果')
with open(r'D:\荷的预测\real_data\complete_ablation_results.json', 'w', encoding='utf-8') as f:
    json.dump(results, f, indent=2, ensure_ascii=False)

log('完成!')