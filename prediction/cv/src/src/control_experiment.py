import pandas as pd
import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset
import lightgbm as lgb
from sklearn.preprocessing import StandardScaler
from sklearn.decomposition import PCA
from sklearn.metrics import r2_score
import json
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

log('2. 创建预测目标（4h滚动均值）')
hourly_df['target_4h'] = hourly_df['total_energy'].rolling(4).mean().shift(-3)

log('3. 创建特征')
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

basic_features = ['hour', 'day_of_week', 'month', 'is_weekend', 'hour_sin', 'hour_cos', 'dow_sin', 'dow_cos',
                  'lag1_energy', 'lag2_energy', 'lag3_energy', 'lag4_energy', 'lag8_energy', 'lag12_energy', 'lag24_energy',
                  'rolling_mean_4', 'rolling_std_4']

all_features = [col for col in hourly_df.columns if col not in ['hour_window', 'total_energy', 'target_4h']]
log(f'  基础特征: {len(basic_features)}个')
log(f'  全部特征: {len(all_features)}个')

X_basic = hourly_df[basic_features].values.astype(np.float32)
X_full = hourly_df[all_features].values.astype(np.float32)
y_raw = hourly_df['target_4h'].values.astype(float)
n_total = len(hourly_df)
train_end = int(n_total * 0.8)

log('4. 构造低冗余特征（PCA）')
scaler = StandardScaler()
X_scaled = scaler.fit_transform(X_full)
pca = PCA(n_components=len(all_features))
X_pca_full = pca.fit_transform(X_scaled).astype(np.float32)

corr_full = np.corrcoef(X_full.T)
corr_pca = np.corrcoef(X_pca_full.T)
log(f'  高冗余特征平均绝对相关: {np.mean(np.abs(corr_full)):.4f}')
log(f'  低冗余特征平均绝对相关: {np.mean(np.abs(corr_pca)):.4f}')

log('5. 特征选择（LightGBM top-20）')
X_train, X_test = X_full[:train_end], X_full[train_end:]
y_train, y_test = y_raw[:train_end], y_raw[train_end:]

lgb_model = lgb.LGBMRegressor(num_leaves=255, max_depth=12, learning_rate=0.05, n_estimators=2000, verbose=-1)
lgb_model.fit(X_train, y_train)
feature_importance = lgb_model.feature_importances_
top20_idx = np.argsort(feature_importance)[-20:]
X_top20 = X_full[:, top20_idx].astype(np.float32)

pca20 = PCA(n_components=20)
X_pca20 = pca20.fit_transform(X_scaled).astype(np.float32)

log('6. 定义模型')
class TimeTransformer(nn.Module):
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

def create_seq(X, y, lb):
    xs, ys = [], []
    for i in range(lb, len(X)):
        xs.append(X[i-lb:i])
        ys.append(y[i])
    return np.array(xs), np.array(ys)

def train_transformer(X_tr, X_te, y_tr, y_te, feature_dim, lookback=48, epochs=80):
    X_tr_seq, y_tr_seq = create_seq(X_tr, y_tr.astype(np.float32), lookback)
    X_te_seq, _ = create_seq(X_te, np.zeros(len(X_te), dtype=np.float32), lookback)
    y_te_seq = y_te[lookback:].astype(np.float32)
    
    train_ds = TensorDataset(torch.tensor(X_tr_seq), torch.tensor(y_tr_seq))
    train_loader = DataLoader(train_ds, batch_size=64, shuffle=True)
    
    model = TimeTransformer(feature_dim)
    optimizer = torch.optim.Adam(model.parameters(), lr=0.001)
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
            optimizer.step()
            total_loss += loss.item()
        
        scheduler.step(total_loss / len(train_loader))
        
        model.eval()
        with torch.no_grad():
            val_pred = model(torch.tensor(X_te_seq)).numpy()
            r2 = r2_score(y_te_seq, val_pred)
            if r2 > best_r2:
                best_r2 = r2
    return best_r2

log('7. 训练评估')
feature_sets = {
    '17_basic': X_basic,
    '66_full': X_full,
    '66_pca': X_pca_full,
    '20_top': X_top20,
    '20_pca': X_pca20
}

results = {}
for name, X in feature_sets.items():
    X_train, X_test = X[:train_end], X[train_end:]
    log(f'  特征集: {name} ({X.shape[1]}维)')
    
    lgb_model = lgb.LGBMRegressor(num_leaves=255, max_depth=12, learning_rate=0.05, n_estimators=2000, verbose=-1)
    lgb_model.fit(X_train, y_train)
    lgb_r2 = r2_score(y_test, lgb_model.predict(X_test))
    log(f'    LightGBM R²: {lgb_r2:.4f}')
    
    tt_r2 = train_transformer(X_train, X_test, y_train, y_test, X.shape[1])
    log(f'    TimeTransformer R²: {tt_r2:.4f}')
    
    results[name] = {'LightGBM': lgb_r2, 'TimeTransformer': tt_r2}

log('8. 保存结果')
with open(r'D:\荷的预测\real_data\control_experiment_results.json', 'w') as f:
    json.dump(results, f, indent=2)

log('完成！')
print(json.dumps(results, indent=2))