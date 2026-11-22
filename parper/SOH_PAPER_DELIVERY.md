# SOH-aware charging microgrid paper

## Manuscripts

- English: `SOH_game_dispatch_en.md` and `SOH_game_dispatch_en.pdf`.
- Chinese: `SOH_game_dispatch_zh.md` and `SOH_game_dispatch_zh.pdf`.
- Both manuscripts use the same English figures in `soh_paper_figures/`.

## Reproduction

Run from the repository root:

1. `D:/miniconda3/python.exe scripts/prepare_soh_paper.py`
2. Run `parper/build_soh_paper_pdf.py en` and `parper/build_soh_paper_pdf.py zh` with the bundled Python containing ReportLab, Pillow and pypdf.

The PDF builder shares the existing language-specific typography engines. Its output uses formal paper titles and neutral running headers. Markdown is the editable manuscript source; equations are rendered from that source.

## Evidence boundary

The paper focuses on SOH capacity/cost interfaces, independent-day component ablations, 72 matched consecutive days, common energy/revenue accounting and conditional cost sensitivity. Equipment remains PV 400 kW, wind 100 kW, BESS 2000 kWh/800 kW and EV 8 x 60 kW. EV input demand scale is 1.0.

The archived experiment records retain excluded cases and stopping-limit records. The manuscript states these inclusion limits briefly. Unchanged EV delivery and higher aging cost remain reported because they affect interpretation. No calibrated battery-lifetime gain, responsive-game equilibrium, thermal safety or real-time performance is claimed.

## Verification

Both PDFs were rendered in full and inspected using page contact sheets and detailed equation/table pages. Structure: 2 figures, 3 tables, 11 equations and 10 references per language. Figures use English labels. Numerical tables derive from `soh_feasibility_audit_20261118.json`.
