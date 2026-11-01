import pandas as pd
import numpy as np
import json
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import r2_score, classification_report, roc_auc_score, accuracy_score, precision_score, recall_score, f1_score
import lightgbm as lgb

print('[1/4] 数据加载...', flush=True)
charging_df = pd.read_csv(r'D:\荷的预测\real_data\Dataset\Charging_Data.csv', encoding='gbk')
charging_df = charging_df[charging_df['Location Information'].str.contains('Expressway Service District', na=False)]
charging_df['Start Time'] = pd.to_datetime(charging_df['Start Time'])
charging_df['hour_window'] = charging_df['Start Time'].dt.floor('H')

hourly_df = charging_df.groupby('hour_window').agg(
    total_energy=('Transaction power/kwh', 'sum'),
    charging_count=('Transaction power/kwh', 'count')
).reset_index()
hourly_df = hourly_df.sort_values('hour_window').reset_index(drop=True)

hourly_df['hour'] = hourly_df['hour_window'].dt.hour
hourly_df['day_of_week'] = hourly_df['hour_window'].dt.dayofweek
hourly_df['month'] = hourly_df['hour_window'].dt.month
hourly_df['is_weekend'] = (hourly_df['day_of_week'] >= 5).astype(int)
hourly_df['hour_sin'] = np.sin(2 * np.pi * hourly_df['hour'] / 24)
hourly_df['hour_cos'] = np.cos(2 * np.pi * hourly_df['hour'] / 24)

for i in [1, 4, 8, 12, 24]:
    hourly_df[f'lag{i}_energy'] = hourly_df['total_energy'].shift(i)

hourly_df['rolling_mean_4'] = hourly_df['total_energy'].rolling(4, min_periods=1).mean().shift(1)
hourly_df['rolling_std_4'] = hourly_df['total_energy'].rolling(4, min_periods=1).std().shift(1)

weather_df = pd.read_csv(r'D:\荷的预测\real_data\Dataset\Weather_Data.csv', encoding='gbk')
weather_df['Date'] = pd.to_datetime(weather_df['Date'], format='%Y%m%d')
weather_df = weather_df.rename(columns={'Temperature(℃)': 'temperature', 'Relative Humidity(%)': 'humidity', 'Precipitation(mm)': 'precipitation'})
weather_df = weather_df.drop_duplicates(subset=['Date'], keep='first')

def get_price_period(hour):
    if 0 <= hour < 8: return 0
    elif 8 <= hour < 11: return 1
    elif 11 <= hour < 13: return 0
    elif 13 <= hour < 19: return 1
    elif 19 <= hour < 21: return 2
    elif 21 <= hour < 22: return 1
    else: return 0

hourly_df['price_period'] = hourly_df['hour'].apply(get_price_period)
hourly_df['date'] = hourly_df['hour_window'].dt.date
weather_df['date'] = weather_df['Date'].dt.date
hourly_df = hourly_df.merge(weather_df[['date', 'temperature', 'humidity', 'precipitation']], on='date', how='left')
hourly_df = hourly_df.fillna(method='ffill').fillna(method='bfill').fillna(0)

feature_cols = [
    'hour', 'day_of_week', 'month', 'is_weekend',
    'hour_sin', 'hour_cos',
    'lag1_energy', 'lag4_energy', 'lag8_energy', 'lag12_energy', 'lag24_energy',
    'rolling_mean_4', 'rolling_std_4',
    'temperature', 'humidity', 'precipitation',
    'price_period'
]

X_all = hourly_df[feature_cols].values.astype(np.float32)
y_raw = hourly_df['total_energy'].values.astype(float)
n_total = len(hourly_df)
train_end = int(n_total * 0.8)

print(f'数据量: {n_total}, 特征数: {len(feature_cols)}', flush=True)

y_4h = pd.Series(y_raw).rolling(4, min_periods=1).mean().shift(-3).values
y_4h = np.nan_to_num(y_4h, 0.0)

X_tr, X_te = X_all[:train_end], X_all[train_end:]
y_tr, y_te = y_4h[:train_end], y_4h[train_end:]

sX = StandardScaler()
X_tr_s = sX.fit_transform(X_tr)
X_te_s = sX.transform(X_te)

def train_lgbm_reg(X_tr, y_tr, X_te, y_te):
    params = {'objective': 'regression', 'metric': 'rmse', 'num_leaves': 127, 'max_depth': 8,
              'learning_rate': 0.05, 'n_estimators': 500, 'verbose': 0, 'n_jobs': 1,
              'random_state': 42, 'force_row_wise': True}
    model = lgb.LGBMRegressor(**params)
    model.fit(X_tr, y_tr, eval_set=[(X_te, y_te)], eval_metric='rmse',
              callbacks=[lgb.early_stopping(50), lgb.log_evaluation(0)])
    return model.predict(X_te)

def train_lgbm_clf(X_tr, y_tr, X_te, y_te):
    params = {'objective': 'binary', 'metric': 'binary_logloss', 'num_leaves': 127, 'max_depth': 8,
              'learning_rate': 0.05, 'n_estimators': 500, 'verbose': 0, 'n_jobs': 1,
              'random_state': 42, 'force_row_wise': True}
    model = lgb.LGBMClassifier(**params)
    model.fit(X_tr, y_tr, eval_set=[(X_te, y_te)], eval_metric='binary_logloss',
              callbacks=[lgb.early_stopping(50), lgb.log_evaluation(0)])
    return model.predict(X_te), model.predict_proba(X_te)[:, 1]

print('[2/4] 实验1: 昼夜鸿沟验证...', flush=True)

def get_time_period(hour):
    if 0 <= hour < 7: return '深夜(0-6h)'
    elif 7 <= hour < 13: return '上午(7-12h)'
    elif 13 <= hour < 19: return '下午(13-18h)'
    else: return '晚上(19-23h)'

hourly_df['time_period'] = hourly_df['hour'].apply(get_time_period)

scenario_results = []
for period in ['深夜(0-6h)', '上午(7-12h)', '下午(13-18h)', '晚上(19-23h)']:
    for is_weekend_val in [0, 1]:
        mask = (hourly_df['time_period'] == period) & (hourly_df['is_weekend'] == is_weekend_val)
        scenario_name = f'{period}_{"周末" if is_weekend_val else "工作日"}'
        sample_size = mask.sum()
        
        if sample_size < 100:
            scenario_results.append({
                'scenario': scenario_name,
                'time_period': period,
                'is_weekend': is_weekend_val,
                'sample_size': int(sample_size),
                'r2': None,
                'status': '样本不足'
            })
            continue
        
        idx = np.where(mask)[0]
        train_mask = idx[idx < train_end]
        test_mask = idx[idx >= train_end]
        
        if len(test_mask) < 20:
            scenario_results.append({
                'scenario': scenario_name,
                'time_period': period,
                'is_weekend': is_weekend_val,
                'sample_size': int(sample_size),
                'r2': None,
                'status': '测试样本不足'
            })
            continue
        
        X_tr_sc = sX.transform(X_all[train_mask])
        X_te_sc = sX.transform(X_all[test_mask])
        y_tr_sc = y_4h[train_mask]
        y_te_sc = y_4h[test_mask]
        
        pred = train_lgbm_reg(X_tr_sc, y_tr_sc, X_te_sc, y_te_sc)
        r2 = r2_score(y_te_sc[y_te_sc > 0], pred[y_te_sc > 0])
        
        scenario_results.append({
            'scenario': scenario_name,
            'time_period': period,
            'is_weekend': is_weekend_val,
            'sample_size': int(sample_size),
            'r2': r2
        })
        print(f'  {scenario_name}: R²={r2:.4f}, 样本={sample_size}', flush=True)

with open(r'D:\荷的预测\real_data\real_scenario_results.json', 'w', encoding='utf-8') as f:
    json.dump(scenario_results, f, indent=2, ensure_ascii=False)

print('[3/4] 实验2: 分类指标...', flush=True)

y_4h_nonzero = y_4h[y_4h > 0]
threshold = np.median(y_4h_nonzero)
y_clf = (y_4h > threshold).astype(int)

X_tr_clf, X_te_clf = X_all[:train_end], X_all[train_end:]
y_tr_clf, y_te_clf = y_clf[:train_end], y_clf[train_end:]

X_tr_clf_s = sX.transform(X_tr_clf)
X_te_clf_s = sX.transform(X_te_clf)

pred_clf, pred_prob = train_lgbm_clf(X_tr_clf_s, y_tr_clf, X_te_clf_s, y_te_clf)

classification_results = {
    'threshold': float(threshold),
    'positive_ratio': float(y_clf.mean()),
    'accuracy': accuracy_score(y_te_clf, pred_clf),
    'precision': precision_score(y_te_clf, pred_clf, zero_division=0),
    'recall': recall_score(y_te_clf, pred_clf, zero_division=0),
    'f1': f1_score(y_te_clf, pred_clf, zero_division=0),
    'auc_roc': roc_auc_score(y_te_clf, pred_prob),
    'classification_report': classification_report(y_te_clf, pred_clf, output_dict=True),
    'synthetic_auc_reference': 0.964
}

print(f'  阈值={threshold:.2f}, 正样本比例={y_clf.mean():.4f}', flush=True)
print(f'  Accuracy={classification_results["accuracy"]:.4f}', flush=True)
print(f'  Precision={classification_results["precision"]:.4f}', flush=True)
print(f'  Recall={classification_results["recall"]:.4f}', flush=True)
print(f'  F1={classification_results["f1"]:.4f}', flush=True)
print(f'  AUC-ROC={classification_results["auc_roc"]:.4f} (合成数据参考: 0.964)', flush=True)

with open(r'D:\荷的预测\real_data\real_classification_results.json', 'w', encoding='utf-8') as f:
    json.dump(classification_results, f, indent=2, ensure_ascii=False)

print('[4/4] 实验3: 自适应切换收益...', flush=True)

previous_results = [
    {'window': 4, 'cv': 53.4, 'LightGBM': 0.603, 'BiLSTM': 0.563, 'TimeTransformer': 0.647},
    {'window': 8, 'cv': 44.0, 'LightGBM': 0.295, 'BiLSTM': 0.509, 'TimeTransformer': 0.609},
    {'window': 12, 'cv': 33.6, 'LightGBM': -0.215, 'BiLSTM': 0.390, 'TimeTransformer': 0.226},
    {'window': 16, 'cv': 23.6, 'LightGBM': -1.244, 'BiLSTM': -0.112, 'TimeTransformer': -0.089}
]

strategies = {
    '全程LightGBM': [r['LightGBM'] for r in previous_results],
    '全程BiLSTM': [r['BiLSTM'] for r in previous_results],
    '全程TimeTransformer': [r['TimeTransformer'] for r in previous_results],
    'CV>34%→LGB, CV≤34%→BiLSTM': [
        r['LightGBM'] if r['cv'] > 34 else r['BiLSTM'] for r in previous_results
    ]
}

cv_values = [r['cv'] for r in previous_results]
sample_ratios = [0.25, 0.25, 0.25, 0.25]

switch_results = {}
for name, r2_values in strategies.items():
    weighted_r2 = sum(r2 * ratio for r2, ratio in zip(r2_values, sample_ratios))
    switch_results[name] = {
        'window_r2': {f'{r["window"]}h': r2 for r, r2 in zip(previous_results, r2_values)},
        'weighted_r2': weighted_r2
    }

baseline_r2 = switch_results['全程LightGBM']['weighted_r2']
for name, result in switch_results.items():
    improvement = (result['weighted_r2'] - baseline_r2) / abs(baseline_r2) * 100 if baseline_r2 != 0 else 0
    result['improvement_over_lgb'] = improvement
    print(f'  {name}: 加权R²={result["weighted_r2"]:.4f}, 相对提升={improvement:.2f}%', flush=True)

with open(r'D:\荷的预测\real_data\real_switch_results.json', 'w', encoding='utf-8') as f:
    json.dump(switch_results, f, indent=2, ensure_ascii=False)

print('\n【结论】', flush=True)

night_r2 = [r['r2'] for r in scenario_results if r['time_period'] == '深夜(0-6h)' and r['r2'] is not None]
day_r2 = [r['r2'] for r in scenario_results if r['time_period'] != '深夜(0-6h)' and r['r2'] is not None]

if night_r2 and day_r2:
    night_avg = np.mean(night_r2)
    day_avg = np.mean(day_r2)
    print(f'实验1: 昼夜鸿沟 - 深夜平均R²={night_avg:.4f}, 白天平均R²={day_avg:.4f}')
    if night_avg < day_avg - 0.2:
        print('  ✅ 真实数据也"白天能测、黑夜瞎"！')
    else:
        print('  ⚠️ 昼夜差距不明显')

print(f'实验2: 分类AUC={classification_results["auc_roc"]:.4f} vs 合成数据0.964')
if classification_results['auc_roc'] > 0.85:
    print('  ✅ 分类指标在真实数据上依然有效！')
else:
    print('  ⚠️ 真实数据分类效果不如合成数据')

best_strategy = max(switch_results.items(), key=lambda x: x[1]['weighted_r2'])
print(f'实验3: 最优策略={best_strategy[0]}, 加权R²={best_strategy[1]["weighted_r2"]:.4f}')
if best_strategy[0] == 'CV>34%→LGB, CV≤34%→BiLSTM':
    print('  ✅ 自适应切换策略有效！')
else:
    print('  ⚠️ 自适应切换未取得最佳效果')

print('\n结果已保存到三个JSON文件', flush=True)