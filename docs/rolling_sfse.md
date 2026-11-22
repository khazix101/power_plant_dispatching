# Rolling SFSE Interface

## Purpose

`ForecastBundle` is the contract between the forecasting module and the rolling scheduling module. It contains:

- current `SOC` and `SOH`;
- current realized EV charging power;
- the latest history window supplied by the forecasting service;
- 24-hour (or configurable horizon) renewable supply forecasts;
- 24-hour base EV-load and non-EV-load forecasts;
- EV and non-EV forecasts used by the DC network controller;
- optional prediction intervals.

The base EV-load forecast must not already include the price response that the Stackelberg follower model will calculate. Otherwise price response is counted twice.

## Rolling loop

```text
read current state and forecast bundle
        |
solve 24-hour SFSE / BRI horizon
        |
project dispatch to the physical safe set
        |
execute only the first-hour price and storage action
        |
run the DC network snapshot and update SOC/SOH
        |
advance one hour and repeat
```

Example:

```python
from datetime import datetime

from src.game.forecast import ForecastBundle
from src.game.rolling import RollingSFSEController

bundle = ForecastBundle(
    timestamp=datetime.now(),
    horizon_hours=24,
    pv_forecast_kw=pv_forecast,
    wind_forecast_kw=wind_forecast,
    base_ev_load_forecast_kw=ev_load_forecast,
    non_ev_load_forecast_kw=non_ev_load_forecast,
    current_ev_load_kw=current_ev_kw,
    soc_current=current_soc,
    soh_current=current_soh,
    reserve_kwh=reserve_kwh,
    history=history,
)

controller = RollingSFSEController(method="bri", flexibility=0.2)
decision = controller.step(bundle)
```

`current_ev_load_kw` is treated as already realized and is not shifted by the follower. The remaining forecast demand is flexible within the configured `flexibility` ratio. The controller now applies decisions to the DC network snapshot.

## DC execution layer

The rolling controller returns the current-hour price, EV service, and BESS
power. The dynamic DC branch executes those values through
`DynamicDCSFSEController` and `DCMicrogridPowerFlow`; it does not depend on an
external grid model.

## Runtime forecast provider

`src.game.forecast_provider.DailyCsvForecastProvider` converts the validated
`data/A.csv`, `data/B.csv`, and `data/C.csv` daily files into rolling
`ForecastBundle` windows. `CallableForecastProvider` is the adapter for a
trained LightGBM or deep-learning inference function. The complete 24-hour
experiment is launched with:

```powershell
D:\miniconda3\python.exe scripts/run_24h_dc_experiment.py
```

The experiment records EV service, shedding, renewable curtailment, voltage,
line current, SOC, SOH degradation, and two baselines under `local/results/`:
the primary `fixed_price_plus_optimal_bess` baseline (fixed price 1.2 with the
same BESS optimization) and the `no_storage` ablation. The fixed-price
baseline keeps both planned MILP profit and the realized DC execution metrics;
they must not be conflated when the simplified planning model and the DC
network have different loss/feasibility details.

The DC execution path uses 0.5-hour dispatch intervals. `current_ev_load_kw`
is the average power measured over the elapsed half-hour; the controller
subtracts that measured energy from the current hourly forecast and dispatches
the remaining energy. Backlog may cross midnight, while backlog above the
configured six-hour/480-kW limit is reported as `overflow_unserved_kwh`.
