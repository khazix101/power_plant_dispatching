"""Replace superseded pre-alignment claims in the review draft."""
from pathlib import Path

path = Path.cwd() / "parper/WEVJ_second_draft.md"
source = path.read_text(encoding="utf-8")

start = source.index("## Abstract\n")
end = source.index("**Keywords:**", start)
abstract = """## Abstract

This second review draft separates the roles of battery state of health (SOH), a rolling leader-follower charging-price game, and an optional solver-assisting Agent for an islanded highway electric vehicle (EV) station. The corrected main configuration fixes 400-kW photovoltaic generation, 100-kW wind generation, a 2000-kWh/800-kW battery and eight 60-kW charging posts. EV revenue is calculated from delivered energy, and EV and non-EV shortages are accounted separately. ERA5 reanalysis is matched by local date and by its hour-ending solar-radiation accumulation interval; conversion to available PV and wind power remains an engineering assumption rather than measured station generation. A corrected two-hour common-request diagnostic passes the energy-identity gate and gives identical physical delivery in the three executable arms. Earlier full-day and three-date results used a one-hour weather misalignment and are retained only as superseded audit evidence. A full SOH-by-demand matrix and a genuinely trained Agent warm start are in progress; neither provides a validated comparative result in this review copy. Historical repeated-profile experiments exposed curtailment-to-battery-loss substitution and a negative storage-only learning result, but cannot establish superiority of the corrected SOH-aware game. The manuscript therefore reports feasibility, accounting limits and pending tests without presuming a positive SOH or Agent effect.

"""
source = source[:start] + abstract + source[end:]

start = source.index("### 7.0. Date-matched short-window accounting check (new evidence)")
end = source.index("### 7.1. Representative-day SOH effects", start)
section = """### 7.0. Corrected date-matched short-window check and experiment status

Raw ERA5 `ssrd` messages have a one-hour `stepRange` and use the hour-ending validity timestamp. The first date-matched runner erroneously selected the same-hour validity value for the following dispatch hour. Its previous 24-hour and three-date files are preserved for traceability but are superseded as weather-driven results. The aligned runner now uses the solar-radiation value valid at the end of the dispatch hour and approximates mean wind speed by averaging the two hourly boundary samples. Both half-hour actions within an hour share the hour's solar mean. The ERA5 grid point is a spatial proxy because the public station record does not establish a verified location coordinate.

The aligned four-slot check uses Site A on 4 May 2021, 10:00-12:00 China Standard Time, at initial SOC 0.50 and SOH 0.90. The fixed-price+BESS, dynamic-price+fixed-cost and SOH-aware dynamic-game arms share 124.4754 kWh physical EV requests and each delivers all 124.4754 kWh, with zero EV shedding and curtailment. Modeled renewable input is 78.5340 kWh; useful EV plus non-EV delivery is 244.4754 kWh; battery conversion loss is 8.7866 kWh, network loss 1.0049 kWh and stored-energy change -175.7329 kWh. The energy-identity residual is 8.53e-14 kWh. The dynamic fixed-cost and SOH-aware paths are identical because the initial marginal coefficient is matched and SOH changes only at the daily boundary. A fixed-tariff revenue of 186.7131 CNY versus 311.1885 CNY at the dynamic tariff reflects price arithmetic on the same rigid requests, not an identified pricing benefit. Agent assistance remains pending a trained, locked solver warm-start policy.

The planned SOH-by-demand matrix is being run from the aligned trace in separate checkpointed files. Its cases are independent daily resets, so they will diagnose initial-SOH capacity effects but cannot by themselves establish a continuous multi-day lifetime benefit. Failed energy, convergence or data gates will remain visible and be excluded from pass-only summaries. No confidence interval or policy ranking is assigned to the superseded three-date files.

"""
source = source[:start] + section + source[end:]
source = source.replace(
    "A corrected single-day, three-arm common-request diagnostic shows zero paired service and modeled-value differences at a supply-constrained date, with much longer dynamic computation. The Agent-assisted arm and a full multi-day SOH feedback test remain pending; no comparative advantage is asserted from the historical tables or the one-day diagnostic.",
    "The aligned two-hour common-request diagnostic shows identical physical delivery in the three executable arms; older full-day weather-driven files have been superseded after an hour-ending radiation alignment correction. The Agent-assisted arm and a full multi-day SOH feedback test remain pending; no comparative advantage is asserted from these diagnostics."
)
path.write_text(source, encoding="utf-8")
print(path)
