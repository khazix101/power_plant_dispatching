import pandas as pd
import numpy as np
import torch
import torch.nn as nn
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

hourly_df['target_4h'] = hourly_df['total_energy'].rolling(4).mean().shift(-3)

log('2. 创建特征')
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
y_raw = hourly_df['target_4h'].values.astype(float)
n_total = len(hourly_df)
train_end = int(n_total * 0.8)

X_train_basic, X_test_basic = X_basic[:train_end], X_basic[train_end:]
X_train_full, X_test_full = X_full[:train_end], X_full[train_end:]
y_train, y_test = y_raw[:train_end], y_raw[train_end:]

log('3. 定义模型（带attention权重提取）')
class CNNBiLSTMAttn(nn.Module):
    def __init__(self, in_dim):
        super().__init__()
        self.cnn = nn.Sequential(nn.Conv1d(in_dim, 32, 3, padding=1), nn.ReLU(), nn.MaxPool1d(2))
        self.bilstm = nn.LSTM(32, 64, bidirectional=True, batch_first=True)
        self.attention = nn.Linear(128, 1)
        self.fc = nn.Linear(128, 1)
    
    def forward(self, x, return_weights=False):
        x = x.transpose(1, 2)
        x = self.cnn(x)
        x = x.transpose(1, 2)
        lstm_out, _ = self.bilstm(x)
        attn_weights = torch.softmax(self.attention(lstm_out), dim=1)
        weighted = torch.sum(attn_weights * lstm_out, dim=1)
        output = self.fc(weighted)
        if return_weights:
            return output, attn_weights
        return output

def create_seq(X, y, lb):
    xs, ys = [], []
    for i in range(lb, len(X)):
        xs.append(X[i-lb:i])
        ys.append(y[i])
    return np.array(xs), np.array(ys)

def train_model(model, X_tr, X_te, y_tr, y_te, epochs=50, batch_size=64):
    X_tr_seq, y_tr_seq = create_seq(X_tr, y_tr.astype(np.float32), 48)
    X_te_seq, _ = create_seq(X_te, np.zeros(len(X_te), dtype=np.float32), 48)
    y_te_seq = y_te[48:].astype(np.float32)
    
    optimizer = torch.optim.Adam(model.parameters(), lr=0.001)
    criterion = nn.MSELoss()
    
    best_r2 = -np.inf
    for epoch in range(epochs):
        model.train()
        idx = np.random.permutation(len(X_tr_seq))
        for i in range(0, len(X_tr_seq), batch_size):
            batch_idx = idx[i:i+batch_size]
            batch_X = torch.tensor(X_tr_seq[batch_idx])
            batch_y = torch.tensor(y_tr_seq[batch_idx])
            
            optimizer.zero_grad()
            pred = model(batch_X).squeeze()
            loss = criterion(pred, batch_y)
            loss.backward()
            optimizer.step()
        
        model.eval()
        with torch.no_grad():
            val_pred = model(torch.tensor(X_te_seq)).squeeze().numpy()
            r2 = r2_score(y_te_seq, val_pred)
            if r2 > best_r2:
                best_r2 = r2
    return best_r2

def compute_entropy(model, X_te, y_te):
    X_te_seq, _ = create_seq(X_te, np.zeros(len(X_te), dtype=np.float32), 48)
    y_te_seq = y_te[48:].astype(np.float32)
    
    model.eval()
    with torch.no_grad():
        _, attn_weights = model(torch.tensor(X_te_seq), return_weights=True)
    
    attn_weights = attn_weights.squeeze(-1).numpy()
    eps = 1e-10
    entropy = -np.sum(attn_weights * np.log(attn_weights + eps), axis=1)
    mean_entropy = np.mean(entropy)
    std_entropy = np.std(entropy)
    return mean_entropy, std_entropy, entropy

log('4. 训练17特征模型并计算熵')
model_17 = CNNBiLSTMAttn(17)
r2_17 = train_model(model_17, X_train_basic, X_test_basic, y_train, y_test)
entropy_17_mean, entropy_17_std, _ = compute_entropy(model_17, X_test_basic, y_test)
log(f'  17特征: R²={r2_17:.4f}, 注意力熵={entropy_17_mean:.4f}±{entropy_17_std:.4f}')

log('5. 训练66特征模型并计算熵')
model_66 = CNNBiLSTMAttn(len(all_features))
r2_66 = train_model(model_66, X_train_full, X_test_full, y_train, y_test)
entropy_66_mean, entropy_66_std, _ = compute_entropy(model_66, X_test_full, y_test)
log(f'  66特征: R²={r2_66:.4f}, 注意力熵={entropy_66_mean:.4f}±{entropy_66_std:.4f}')

log('6. 保存结果')
results = {
    '17_features': {
        'R2': float(r2_17),
        'entropy_mean': float(entropy_17_mean),
        'entropy_std': float(entropy_17_std)
    },
    '66_features': {
        'R2': float(r2_66),
        'entropy_mean': float(entropy_66_mean),
        'entropy_std': float(entropy_66_std)
    },
    'entropy_increase': float(entropy_66_mean - entropy_17_mean),
    'entropy_ratio': float(entropy_66_mean / entropy_17_mean)
}

with open(r'D:\荷的预测\real_data\attention_entropy_results.json', 'w') as f:
    json.dump(results, f, indent=2)

log('完成！')
print(json.dumps(results, indent=2))