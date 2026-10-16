# Rolling SFSE Interface

## Purpose

`ForecastBundle` is the contract between the forecasting module and the rolling scheduling module. It contains:

- current `SOC` and `SOH`;
- current realized EV charging power;
- the latest history window supplied by the forecasting service;
- 24-hour (or configurable horizon) renewable supply forecasts;
- 24-hour base EV-load and non-EV-load forecasts;
- EV and non-EV reactive-load forecasts (`kvar`), required by the GFM QSTS adapter;
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
run OpenDSS snapshot and update SOC/SOH
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

`current_ev_load_kw` is treated as already realized and is not shifted by the follower. The remaining forecast demand is flexible within the configured `flexibility` ratio. The legacy `apply_to_opendss` method uses the original slack-source snapshot path; strict islanded operation must use `apply_to_gfm` with an `IslandedGFMQSTS` controller.

## DC execution layer

The rolling controller returns the current-hour price, EV service, and BESS
power. The dynamic DC branch executes those values through
`DynamicDCSFSEController` and `DCMicrogridPowerFlow`; it does not depend on an
AC OpenDSS model or an external slack source.
