# Walk-forward evaluation — synthetic panel

> **This is a harness demonstration on synthetic data, not a performance claim.**
> The exchange archives are unreachable from the build environment, so this report
> exercises the evaluation machinery end-to-end against a generated panel whose
> structure is known. Re-run `python -m bse_monitor.main model evaluate` against
> real ingested deals to get real numbers; the table format is identical.
>
> The synthetic generating process deliberately contains interactions an additive
> heuristic cannot express — marquee holders selling into *anchor* releases but not
> promoter lock-ins, and large stakes mattering only in illiquid names. Labels
> generated as a monotone function of the baseline's own inputs would make the
> baseline Bayes-optimal and the comparison meaningless.

- Panel rows: **19,800** across 180 weeks, 110 companies, 23 holders
- Base rate: **6.046%**
- Feature set: `v1` (20 features)
- Folds: walk-forward by year, 60-day embargo matching the label horizon

## Results

| Year | Test rows | Positives | Base rate | P@20 model | P@20 baseline | Lift | Median lead (d) | PR-AUC model | PR-AUC baseline |
|---|---|---|---|---|---|---|---|---|---|
| 2023 | 4,950 | 281 | 5.677% | 0.126 | 0.124 | 2.21x | 33.0 | 0.1353 | 0.1262 |
| 2024 | 4,950 | 303 | 6.121% | 0.144 | 0.128 | 2.36x | 30.5 | 0.1475 | 0.1301 |
| 2025 | 4,950 | 291 | 5.879% | 0.113 | 0.107 | 1.93x | 28.0 | 0.1214 | 0.0984 |

Model beats the baseline on PR-AUC in 3 of 3 folds.

## Sample explanations

| Company | Holder | Stake Rs Cr | Days to trigger | Top-3 reasons |
|---|---|---|---|---|
| Co 0 | Holder 0 | 10 | 12 | trigger in 12d (+1.16); marquee institutional holder (+0.77); stake ~Rs 10 Cr (-0.47) |
| Co 1 | Holder 1 | 5,000 | 45 | 6.0 days of ADV to exit (+0.94); stake ~Rs 5,000 Cr (+0.61); trigger in 45d (+0.26) |
| Co 2 | Holder 2 | 10 | 45 | stake ~Rs 10 Cr (-0.69); trigger in 45d (+0.52); 6.0 days of ADV to exit (+0.49) |
| Co 3 | Holder 3 | 60 | 3 | peak filing score 0 (-0.94); 0.5 days of ADV to exit (-0.35); stake ~Rs 60 Cr (-0.35) |
| Co 4 | Holder 4 | 300 | 80 | historic sell rate 0.00/wk (+0.36); last disclosed stake 1.18% (-0.35); 0.5 days of ADV to exit (-0.33) |
