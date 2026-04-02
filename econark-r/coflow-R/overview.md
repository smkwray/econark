# coflow-R Overview

`coflow-R` ports the CoFlow methodology into R for macroeconomic time-series screening.

Pipeline:
1. Load `final_*` or `mixed_*` panels from `fetchr-R`.
2. Align dates and requested variables (optional quarter-end restriction for MF mode).
3. Run rolling pair analysis for each target-candidate pair:
   - residual correlation (stationary panel),
   - lag-exclusion block causality tests (`anova` on nested lag models),
   - Johansen-trace cointegration rank switching (fallback: Engle-Granger proxy).
4. Apply BH q-values across candidate-window tests (per target/window setup).
5. Score and rank candidates in three modes:
   - `positive`: strongest positive association,
   - `negative`: strongest negative association,
   - `least`: most independent.
6. Emit CSV artifacts and markdown summaries.
7. (Chunk-6 scaffold) Emit shortlist exports, publication gate reports, and advanced analytics metadata/artifacts.
8. (Chunk-7 gate) Run `run/parity_gate.R` to validate fetchr->coflow artifact and schema contracts.

Method note: outputs are exploratory reduced-form rankings, not identified causal effects.
