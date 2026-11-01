"""
Step 2c: CNN-BiLSTM-Attention — 充电需求车辆数
"""
import pandas as pd, numpy as np, time, joblib, json
from sklearn.preprocessing import LabelEncoder
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score
import torch, torch.nn as nn, torch.nn.functional as F
from torch.utils.data import DataLoader, TensorDataset
import warnings
warnings.filterwarnings('ignore')

print("=" * 60)
print("  CNN-BiLSTM-Attention: 充电需求车辆数")
print("=" * 60)

df = pd.read_csv(r'D:\荷的预测\ml_feature_dataset.csv')
exclude_cols = ['timestamp','target_output_15min','target_util_15min','target_ev_flow_15min',
                'target_queue_15min','target_charge_ratio_15min','target_ev_need_charge_15min']
for c in ['holiday_type','price_period']:
    df[c] = LabelEncoder().fit_transform(df[c].astype(str))
df = df.fillna(method='ffill').fillna(method='bfill').fillna(0)
feature_cols = [c for c in df.columns if c not in exclude_cols]

n = len(df); train_end = int(n*0.8); val_end = int(n*0.9)
y_full = df['target_ev_need_charge_15min'].values.astype(float)
y_clean = np.nan_to_num(y_full, 0.0)

X_all = df[feature_cols].values.astype(np.float32)
device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

sX = joblib.load('scalerX_ev_need_charge.pkl')
sy = joblib.load('scalerY_ev_need_charge.pkl')
X_all_s = sX.transform(X_all)
y_all_s = sy.transform(y_clean.reshape(-1,1)).flatten()

LOOKBACK = 48
def create_sequences(X, y, lb):
    xs, ys = [], []
    for i in range(lb, len(X)):
        xs.append(X[i-lb:i]); ys.append(y[i])
    return np.array(xs), np.array(ys)

train_seq_start = max(0, train_end - 8000)
X_tr, y_tr = create_sequences(X_all_s[train_seq_start:train_end], y_all_s[train_seq_start:train_end], LOOKBACK)
if len(X_tr) > 5000:
    idx = np.random.RandomState(42).choice(len(X_tr), 5000, replace=False)
    X_tr, y_tr = X_tr[idx], y_tr[idx]

val_start = train_end - LOOKBACK
X_va, y_va = create_sequences(X_all_s[val_start:val_end], y_all_s[val_start:val_end], LOOKBACK)
test_start = val_end - LOOKBACK
X_te, y_te = create_sequences(X_all_s[test_start:], y_all_s[test_start:], LOOKBACK)

print(f"Sequences: train={X_tr.shape}, val={X_va.shape}, test={X_te.shape}")

class AttnLayer(nn.Module):
    def __init__(self, hdim):
        super().__init__()
        self.attn = nn.Sequential(nn.Linear(hdim*2, hdim), nn.Tanh(), nn.Linear(hdim, 1))
    def forward(self, x):
        w = F.softmax(self.attn(x), dim=1)
        return (w*x).sum(1), w

class CNN_BiLSTM_Attn(nn.Module):
    def __init__(self, in_dim, hid=64, cnn_f=32, drop=0.2):
        super().__init__()
        self.conv1 = nn.Conv1d(in_dim, cnn_f, 3, padding=1)
        self.conv2 = nn.Conv1d(in_dim, cnn_f, 5, padding=2)
        self.conv3 = nn.Conv1d(in_dim, cnn_f, 7, padding=3)
        self.bn1 = nn.BatchNorm1d(cnn_f)
        self.bn2 = nn.BatchNorm1d(cnn_f)
        self.bn3 = nn.BatchNorm1d(cnn_f)
        self.dc = nn.Dropout(drop)
        self.lstm = nn.LSTM(cnn_f, hid, 2, batch_first=True, bidirectional=True, dropout=drop)
        self.attn = AttnLayer(hid)
        self.fc = nn.Sequential(nn.Linear(hid*2, 64), nn.ReLU(), nn.Dropout(drop),
                                nn.Linear(64, 32), nn.ReLU(), nn.Linear(32, 1))
    def forward(self, x):
        xc = x.permute(0,2,1)
        c = F.relu(self.bn1(self.conv1(xc))) + F.relu(self.bn2(self.conv2(xc))) + F.relu(self.bn3(self.conv3(xc)))
        c = self.dc(c).permute(0,2,1)
        out, _ = self.lstm(c)
        ctx, _ = self.attn(out)
        return self.fc(ctx).squeeze(-1)

model = CNN_BiLSTM_Attn(X_tr.shape[2], hid=64, cnn_f=32).to(device)
print(f"Parameters: {sum(p.numel() for p in model.parameters()):,}")

bs = 64
tr_ds = TensorDataset(torch.FloatTensor(X_tr), torch.FloatTensor(y_tr))
va_ds = TensorDataset(torch.FloatTensor(X_va), torch.FloatTensor(y_va))
tr_dl = DataLoader(tr_ds, batch_size=bs, shuffle=True)
va_dl = DataLoader(va_ds, batch_size=bs, shuffle=False)

opt = torch.optim.Adam(model.parameters(), lr=0.001, weight_decay=1e-5)
sch = torch.optim.lr_scheduler.ReduceLROnPlateau(opt, patience=10, factor=0.5)
crit = nn.MSELoss()

best_val = float('inf'); pc = 0
t0 = time.time()

for ep in range(80):
    model.train()
    for xb, yb in tr_dl:
        xb, yb = xb.to(device), yb.to(device)
        opt.zero_grad()
        loss = crit(model(xb), yb)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        opt.step()
    model.eval()
    vl = sum(crit(model(xb.to(device)), yb.to(device)).item() for xb, yb in va_dl) / len(va_dl)
    sch.step(vl)
    if (ep+1) % 10 == 0:
        print(f"  Epoch {ep+1:3d}/80 | val_loss={vl:.6f} | best={best_val:.6f}")
    if vl < best_val:
        best_val = vl; pc = 0
        torch.save(model.state_dict(), 'best_dl_t3.pth')
    else:
        pc += 1
        if pc >= 12:
            print(f"  Early stopping at epoch {ep+1}")
            break

train_t = time.time() - t0
print(f"Training: {train_t:.1f}s")

model.load_state_dict(torch.load('best_dl_t3.pth'))
model.eval()
t0 = time.time()
with torch.no_grad():
    pred_s = model(torch.FloatTensor(X_te).to(device)).cpu().numpy()
infer_t = time.time() - t0

pred = sy.inverse_transform(pred_s.reshape(-1,1)).flatten()
valid = ~np.isnan(y_full[val_end:])
y_tv = y_clean[val_end:][valid]
pv = pred[:len(y_tv)]

rmse = np.sqrt(mean_squared_error(y_tv, pv))
mae  = mean_absolute_error(y_tv, pv)
r2   = r2_score(y_tv, pv)
mape = np.mean(np.abs((y_tv-pv)/np.maximum(y_tv,1e-8)))*100

print(f"Results: RMSE={rmse:.2f}, MAE={mae:.2f}, MAPE={mape:.2f}%, R²={r2:.4f}")
print(f"Inference: {infer_t:.4f}s")

result = {'target': '充电需求车辆数', 'rmse': rmse, 'mae': mae, 'mape': mape, 'r2': r2,
          'train_time': train_t, 'infer_time': infer_t}
with open('dl_result_t3.json', 'w', encoding='utf-8') as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
print("Done!")
