import pandas as pd
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader, TensorDataset
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import r2_score
import json
import sys

def log(msg):
    print(msg, flush=True)
    sys.stdout.flush()

class iTransformer(nn.Module):
    def __init__(self, n_features, d_model=64, nhead=4, num_layers=2, dropout=0.1):
        super().__init__()
        self.n_features = n_features
        self.d_model = d_model
        
        self.input_proj = nn.Linear(1, d_model)
        
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=d_model,
            nhead=nhead,
            dim_feedforward=d_model * 4,
            dropout=dropout,
            batch_first=True
        )
        self.encoder = nn.TransformerEncoder(encoder_layer, num_layers=num_layers)
        
        self.output_proj = nn.Sequential(
            nn.Linear(d_model * n_features, d_model // 2),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(d_model // 2, 1)
        )
    
    def forward(self, x):
        batch_size, n_features = x.shape
        
        x = x.unsqueeze(-1)
        x = self.input_proj(x)
        
        x = self.encoder(x)
        
        x = x.reshape(batch_size, -1)
        x = self.output_proj(x)
        
        return x.squeeze(-1)

def train_itransformer(X_tr, X_te, y_tr, y_te, n_features, epochs=30):
    model = iTransformer(n_features, d_model=32, nhead=4, num_layers=1, dropout=0.1)
    optimizer = torch.optim.Adam(model.parameters(), lr=0.002)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, patience=3, factor=0.5)
    criterion = nn.MSELoss()
    
    X_tr_t = torch.tensor(X_tr, dtype=torch.float32)
    y_tr_t = torch.tensor(y_tr, dtype=torch.float32)
    X_te_t = torch.tensor(X_te, dtype=torch.float32)
    y_te_t = torch.tensor(y_te, dtype=torch.float32)
    
    train_ds = TensorDataset(X_tr_t, y_tr_t)
    train_loader = DataLoader(train_ds, batch_size=128, shuffle=True)
    
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
        
        avg_loss = total_loss / len(train_loader)
        scheduler.step(avg_loss)
        
        model.eval()
        with torch.no_grad():
            val_pred = model(X_te_t).numpy()
            r2 = r2_score(y_te_t.numpy(), val_pred)
            if r2 > best_r2:
                best_r2 = r2
    
    return best_r2

log('1. 加载数据')
charging_df = pd.read_csv(r'D:\荷的预测\real_data\Dataset\Charging_Data.csv', encoding='gbk')
charging_df = charging_df[charging_df['Location Information'].str.contains('Expressway Service District', na=False)]
charging_df['Start Time'] = pd.to_datetime(charging_df['Start Time'])
charging_df['hour_window'] = charging_df['Start Time'].dt.floor('H')

hourly_df = charging_df.groupby('hour_window').agg(total_energy=('Transaction power/kwh', 'sum')).reset_index()
hourly_df = hourly_df.sort_values('hour_window').reset_index(drop=True)

log('2. 创建预测目标')
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

X_basic = hourly_df[basic_features].values.astype(np.float32)
X_full = hourly_df[all_features].values.astype(np.float32)
y = hourly_df['target_4h'].values.astype(np.float32)

scaler = StandardScaler()
X_basic_scaled = scaler.fit_transform(X_basic)
X_full_scaled = scaler.fit_transform(X_full)

n_total = len(y)
train_end = int(n_total * 0.8)

X_tr_17, X_te_17 = X_basic_scaled[:train_end], X_basic_scaled[train_end:]
X_tr_66, X_te_66 = X_full_scaled[:train_end], X_full_scaled[train_end:]
y_tr, y_te = y[:train_end], y[train_end:]

log(f'训练集: {train_end}个样本, 测试集: {n_total - train_end}个样本')

log('4. 运行iTransformer实验...')
results = {}

n_17 = X_tr_17.shape[1]
n_full = X_tr_66.shape[1]
log(f'  实际特征数: 基础={n_17}, 全部={n_full}')

log(f'  iTransformer {n_17} features...')
r2_17 = train_itransformer(X_tr_17, X_te_17, y_tr, y_te, n_17)
results[f'{n_17}_features'] = {'R2': float(r2_17)}
log(f'    R² = {r2_17:.4f}')

log(f'  iTransformer {n_full} features...')
r2_full = train_itransformer(X_tr_66, X_te_66, y_tr, y_te, n_full)
results[f'{n_full}_features'] = {'R2': float(r2_full)}
log(f'    R² = {r2_full:.4f}')

log('5. 保存结果')
with open(r'D:\荷的预测\real_data\itransformer_results.json', 'w') as f:
    json.dump(results, f, indent=2)

log('完成！')
log(json.dumps(results, indent=2))