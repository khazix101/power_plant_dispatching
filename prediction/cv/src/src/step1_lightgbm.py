"""
Step 1: LightGBM 三目标完整训练
"""
import pandas as pd, numpy as np
from sklearn.preprocessing import StandardScaler, LabelEncoder
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score
import time, json, lightgbm as lgb, warnings
warnings.filterwarnings('ignore')

print("=" * 60)
print("  Step 1: LightGBM 三目标训练")
print("=" * 60)

df = pd.read_csv(r'D:\荷的预测\ml_feature_dataset.csv')
exclude_cols = ['timestamp','target_output_15min','target_util_15min','target_ev_flow_15min',
                'target_queue_15min','target_charge_ratio_15min','target_ev_need_charge_15min']
for c in ['holiday_type','price_period']:
    df[c] = LabelEncoder().fit_transform(df[c].astype(str))
df = df.fillna(method='ffill').fillna(method='bfill').fillna(0)
feature_cols = [c for c in df.columns if c not in exclude_cols]

n = len(df); train_end = int(n*0.8); val_end = int(n*0.9)
y_raw = df['target_output_15min'].values.astype(float)
y_1h  = pd.Series(y_raw).rolling(4,min_periods=1).mean().shift(-3).values
y_need = df['target_ev_need_charge_15min'].values.astype(float)

targets = [
    ('15min_output',  '15分钟后输出功率(kW)',    y_raw),
    ('1h_avg_output', '未来1小时平均输出功率(kW)', y_1h),
    ('ev_need_charge','充电需求车辆数',          y_need),
]
X_all = df[feature_cols].values.astype(np.float32)

results = {}
for tag, name, y_full in targets:
    print(f"\n--- {name} ---")
    y_c = np.nan_to_num(y_full, 0.0)
    X_tr, y_tr = X_all[:train_end], y_c[:train_end]
    X_va, y_va = X_all[train_end:val_end], y_c[train_end:val_end]
    X_te, y_te = X_all[val_end:], y_c[val_end:]
    
    sX, sy = StandardScaler(), StandardScaler()
    X_tr_s = sX.fit_transform(X_tr); X_va_s = sX.transform(X_va); X_te_s = sX.transform(X_te)
    y_tr_s = sy.fit_transform(y_tr.reshape(-1,1)).flatten()
    y_va_s = sy.transform(y_va.reshape(-1,1)).flatten()
    
    t0 = time.time()
    m = lgb.LGBMRegressor(num_leaves=255, max_depth=12, learning_rate=0.05,
        n_estimators=2000, subsample=0.8, colsample_bytree=0.8,
        reg_alpha=0.1, reg_lambda=0.1, min_child_samples=50,
        random_state=42, verbose=-1, n_jobs=-1)
    m.fit(X_tr_s, y_tr_s, eval_set=[(X_va_s, y_va_s)], eval_metric='rmse',
          callbacks=[lgb.early_stopping(100), lgb.log_evaluation(50)])
    train_t = time.time() - t0
    
    t0 = time.time()
    pred_s = m.predict(X_te_s)
    infer_t = time.time() - t0
    pred = sy.inverse_transform(pred_s.reshape(-1,1)).flatten()
    
    valid = ~np.isnan(y_full[val_end:])
    y_tv, pv = y_te[valid], pred[valid]
    
    rmse = np.sqrt(mean_squared_error(y_tv, pv))
    mae  = mean_absolute_error(y_tv, pv)
    r2   = r2_score(y_tv, pv)
    mape = np.mean(np.abs((y_tv-pv)/np.maximum(y_tv,1e-8)))*100
    
    # 保存模型和scaler
    import joblib
    joblib.dump(m, f'lgb_{tag}.pkl')
    joblib.dump(sX, f'scalerX_{tag}.pkl')
    joblib.dump(sy, f'scalerY_{tag}.pkl')
    
    # Top10 特征
    imp = pd.DataFrame({'feature':feature_cols,'importance':m.feature_importances_}).sort_values('importance',ascending=False)
    
    results[tag] = {
        'name': name, 'rmse': rmse, 'mae': mae, 'mape': mape, 'r2': r2,
        'train_time': train_t, 'infer_time': infer_t,
        'test_n': len(y_tv), 'test_mean': np.mean(y_tv), 'test_std': np.std(y_tv),
        'top_features': imp.head(10)[['feature','importance']].to_dict('records')
    }
    
    print(f"  RMSE={rmse:.2f}, MAE={mae:.2f}, MAPE={mape:.2f}%, R²={r2:.4f}")
    print(f"  训练{train_t:.1f}s, 推理{infer_t:.4f}s")
    print(f"  Top3特征: {', '.join(imp['feature'].values[:3])}")

# 保存结果
with open('lgb_results.json', 'w', encoding='utf-8') as f:
    json.dump(results, f, ensure_ascii=False, indent=2)

print(f"\n{'='*60}")
print(f"  LightGBM 全部完成!")
print(f"{'='*60}")
print(f"  15min输出:       R²={results['15min_output']['r2']:.4f}")
print(f"  1h平均输出:      R²={results['1h_avg_output']['r2']:.4f}")
print(f"  充电需求车辆数:   R²={results['ev_need_charge']['r2']:.4f}")
print(f"\n  结果已保存至 lgb_results.json")
