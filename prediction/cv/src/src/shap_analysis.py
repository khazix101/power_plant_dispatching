import pandas as pd
import numpy as np
import lightgbm as lgb
import shap
import matplotlib.pyplot as plt
import seaborn as sns
import time
import sys

plt.rcParams['font.sans-serif'] = ['SimHei', 'Arial Unicode MS', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

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

log('2. 创建特征')
hourly_df['hour'] = hourly_df['hour_window'].dt.hour
hourly_df['day_of_week'] = hourly_df['hour_window'].dt.dayofweek
hourly_df['month'] = hourly_df['hour_window'].dt.month
hourly_df['day_of_month'] = hourly_df['hour_window'].dt.day
hourly_df['is_weekend'] = (hourly_df['day_of_week'] >= 5).astype(int)
hourly_df['is_workday'] = 1 - hourly_df['is_weekend']

hourly_df['hour_sin'] = np.sin(2 * np.pi * hourly_df['hour'] / 24)
hourly_df['hour_cos'] = np.cos(2 * np.pi * hourly_df['hour'] / 24)
hourly_df['dow_sin'] = np.sin(2 * np.pi * hourly_df['day_of_week'] / 7)
hourly_df['dow_cos'] = np.cos(2 * np.pi * hourly_df['day_of_week'] / 7)
hourly_df['month_sin'] = np.sin(2 * np.pi * hourly_df['month'] / 12)
hourly_df['month_cos'] = np.cos(2 * np.pi * hourly_df['month'] / 12)

lags = [1, 2, 3, 4, 6, 8, 12, 18, 24, 36, 48]
for i in lags:
    hourly_df[f'lag{i}_energy'] = hourly_df['total_energy'].shift(i)

for window in [4, 8, 12, 24, 48]:
    hourly_df[f'rolling_mean_{window}'] = hourly_df['total_energy'].rolling(window, min_periods=1).mean().shift(1)
    hourly_df[f'rolling_std_{window}'] = hourly_df['total_energy'].rolling(window, min_periods=1).std().shift(1)
    hourly_df[f'rolling_max_{window}'] = hourly_df['total_energy'].rolling(window, min_periods=1).max().shift(1)
    hourly_df[f'rolling_min_{window}'] = hourly_df['total_energy'].rolling(window, min_periods=1).min().shift(1)
    hourly_df[f'rolling_q25_{window}'] = hourly_df['total_energy'].rolling(window, min_periods=1).quantile(0.25).shift(1)
    hourly_df[f'rolling_q75_{window}'] = hourly_df['total_energy'].rolling(window, min_periods=1).quantile(0.75).shift(1)

hourly_df['diff_1'] = hourly_df['total_energy'] - hourly_df['total_energy'].shift(1)
hourly_df['diff_4'] = hourly_df['total_energy'] - hourly_df['total_energy'].shift(4)
hourly_df['diff_8'] = hourly_df['total_energy'] - hourly_df['total_energy'].shift(8)
hourly_df['diff_12'] = hourly_df['total_energy'] - hourly_df['total_energy'].shift(12)
hourly_df['diff_24'] = hourly_df['total_energy'] - hourly_df['total_energy'].shift(24)

hourly_df['hour_x_weekend'] = hourly_df['hour'] * hourly_df['is_weekend']
hourly_df['hour_x_workday'] = hourly_df['hour'] * hourly_df['is_workday']
hourly_df['dow_x_weekend'] = hourly_df['day_of_week'] * hourly_df['is_weekend']

hourly_df['lag1_ratio'] = hourly_df['total_energy'] / (hourly_df['total_energy'].shift(1) + 1e-8)
hourly_df['lag24_ratio'] = hourly_df['total_energy'] / (hourly_df['total_energy'].shift(24) + 1e-8)

hourly_df['ema_4'] = hourly_df['total_energy'].ewm(span=4).mean().shift(1)
hourly_df['ema_8'] = hourly_df['total_energy'].ewm(span=8).mean().shift(1)
hourly_df['ema_24'] = hourly_df['total_energy'].ewm(span=24).mean().shift(1)

hourly_df = hourly_df.fillna(method='ffill').fillna(method='bfill').fillna(0)

all_features = [col for col in hourly_df.columns if col not in ['hour_window', 'total_energy']]
X = hourly_df[all_features].values.astype(np.float32)
y = hourly_df['total_energy'].values.astype(float)
n_total = len(hourly_df)
train_end = int(n_total * 0.8)

X_train, X_test = X[:train_end], X[train_end:]
y_train, y_test = y[:train_end], y[train_end:]

log('3. 训练 LightGBM')
params = {
    'objective': 'regression',
    'metric': 'mse',
    'num_leaves': 255,
    'max_depth': 12,
    'learning_rate': 0.05,
    'n_estimators': 2000,
    'verbose': -1,
    'random_state': 42
}

start_time = time.time()
model = lgb.LGBMRegressor(**params)
model.fit(X_train, y_train)
train_time = time.time() - start_time
log(f'  训练时间: {train_time:.2f}s')

log('4. SHAP 分析')
explainer = shap.TreeExplainer(model)
shap_values = explainer.shap_values(X_test)

plt.figure(figsize=(12, 8))
shap.summary_plot(shap_values, X_test, feature_names=all_features, show=False)
plt.savefig(r'D:\荷的预测\shap_summary.png', dpi=300, bbox_inches='tight')
log('  SHAP summary plot saved')

plt.figure(figsize=(10, 6))
shap.summary_plot(shap_values, X_test, feature_names=all_features, plot_type='bar', show=False)
plt.savefig(r'D:\荷的预测\shap_bar.png', dpi=300, bbox_inches='tight')
log('  SHAP bar plot saved')

log('5. 特征相关性热力图')
corr_matrix = hourly_df[all_features].corr()
plt.figure(figsize=(20, 16))
sns.heatmap(corr_matrix, cmap='coolwarm', center=0, vmin=-1, vmax=1, linewidths=0.5)
plt.savefig(r'D:\荷的预测\feature_correlation.png', dpi=300, bbox_inches='tight')
log('  Correlation heatmap saved')

log('6. 推理效率测试')
n_samples = 10000
inference_times = []
for _ in range(n_samples):
    idx = np.random.randint(len(X_test))
    start = time.time()
    _ = model.predict(X_test[idx:idx+1])
    inference_times.append(time.time() - start)

mean_inf_time = np.mean(inference_times) * 1000
std_inf_time = np.std(inference_times) * 1000
log(f'  平均推理时间: {mean_inf_time:.4f} ms')
log(f'  推理时间标准差: {std_inf_time:.4f} ms')

log('7. 保存特征重要性')
feature_importance = pd.DataFrame({
    'feature': all_features,
    'importance': model.feature_importances_
}).sort_values('importance', ascending=False)

feature_importance.to_csv(r'D:\荷的预测\feature_importance.csv', index=False)
log('  Feature importance saved')

log('8. SHAP 最重要特征分析')
shap_importance = np.abs(shap_values).mean(axis=0)
shap_feature_importance = pd.DataFrame({
    'feature': all_features,
    'shap_importance': shap_importance
}).sort_values('shap_importance', ascending=False)

shap_feature_importance.to_csv(r'D:\荷的预测\shap_feature_importance.csv', index=False)
log('  SHAP importance saved')
log('  Top 10 SHAP features:')
print(shap_feature_importance.head(10).to_string())

log('完成！')