# D Data Quality Report

## Scope and conclusion

The checked-in `data/` sample set contains no station-keyed historical measurements. It contains four 24-row, hourly, timezone-naive example profiles dated 2024-01-01 and a 30-row holiday calendar. The audit found zero missing cells and zero duplicate time keys in these five files. The available profiles cannot directly enter A's multi-site contract, which requires 2–4 site IDs, IANA timezones, and 30-minute timestamps. Current coverage is therefore **0 verified sites / 0 measured site traces**.

This report was generated without reading `Dataset/`. That private/raw root is excluded by the audit's explicit five-file allowlist. No privacy classification or equipment metadata is available for the sample profiles.

## File findings

| File | Rows | Coverage / cadence | Missing | Duplicate keys | Time zone | Classification |
|---|---:|---|---:|---:|---|---|
| `A.csv` | 24 | 2024-01-01 00:00–23:00, 1 hour | 0 | 0 | Unspecified; naive | Forecast/example wind power, kW |
| `B.csv` | 24 | 2024-01-01 00:00–23:00, 1 hour | 0 | 0 | Unspecified; naive | Forecast/example PV power, kW |
| `C.csv` | 24 | 2024-01-01 00:00–23:00, 1 hour | 0 | 0 | Unspecified; naive | Forecast/example `power_kW`; semantic conflict below |
| `D.csv` | 24 | 2024-01-01 00:00–23:00, 1 hour | 0 | 0 | Unspecified; naive | Forecast/example temperature °C and weather category |
| `E.csv` | 30 | Calendar dates, not a regular telemetry series | 0 | 0 | Not applicable | Holiday example calendar |

`src/data/loader.py` names C as a load profile. `src/game/forecast_provider.py` then supplies C to the EV base-load field, while documenting that it is treated as base EV demand. Confirm whether C represents EV-only demand or total site load before any operational conclusion. The holiday calendar's locale/source is likewise unspecified.

## Equipment parameter provenance

No site-specific equipment values were found in `data/`. Existing Python defaults are generic simulation settings, not verified physical parameters: `src/game/dc_microgrid.py` declares a 2,000 kWh / 800 kW battery, SOC minimum/reserve/maximum 0.10/0.20/0.90, charge/discharge efficiency 0.95, and a default EV service ceiling of 8 × 120 kW. `src/game/forecast_provider.py` documents 400 kW PV and 100 kW wind typical profiles and a 60 kW non-EV load as assumptions in one provider. These defaults must not be attributed to any site without an owner-provided source.

## Blocking data gaps

- Supply a de-identified site ID and approved privacy class for every trace.
- Supply timezone-aware half-hour timestamps and station-specific coverage, units, source, and forecast/observed status.
- Resolve C.csv load semantics and the locality/source of E.csv.
- Provide traceable equipment ratings, battery initial SOC/SOH, efficiencies, capacity fade parameters, transformer limits, and tie-line limits.
- Provide forecast vintages and corresponding measured outcomes separately to prevent target leakage.

Until those items are supplied, results from the paired runner are synthetic diagnostics only; they do not establish measured multi-site performance or physical network feasibility.

Reproduce the machine-readable report with:

```powershell
D:\miniconda3\python.exe scripts/audit_multisite_data.py --output local/results/d_validation/data_quality_audit.json
```
