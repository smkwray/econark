# CoFlow System Architecture: Mixed-Frequency Stacked VAR

This file is a technical deep dive on stacked-system architecture.
For current operational behavior (launchers, runtime policy), use `ORCHESTRATION_GUIDE.md`.
For current methodology and scoring/FDR semantics, use `overview.md`.

## 1. Overview
The CoFlow system implements a **Stacked U-MIDAS (Unrestricted Mixed Data Sampling) Vector Autoregression (VAR)** to analyze the relationships between high-frequency (monthly) drivers and lower-frequency (quarterly) economic variables. 

Instead of aggregating monthly data into quarterly averages (which destroys information) or using polynomial constraints (which imposes bias), CoFlow uses a **"Stacking" approach**. This transforms the temporal dimension of high-frequency data into the cross-sectional dimension, allowing for a rigorous multivariate system estimation.

---

## 2. Core Methodology: The Stacked Approach

### 2.1 The Concept
In a standard quarterly model, a variable $X$ has one value per quarter: $X_t$.
In the real world, $X$ evolves over the months of the quarter: $X_{m1}, X_{m2}, X_{m3}$.

CoFlow "stacks" these intra-quarter observations into a single vector at the quarter-end timestamp ($t$):

$$
\mathbf{X}_t = \begin{bmatrix} X_{t, m1} \\ X_{t, m2} \\ X_{t, m3} \end{bmatrix}
$$

This allows the model to see the **shape** of the quarter (e.g., front-loaded vs. back-loaded activity) and estimate separate impacts for shocks occurring in different months.

### 2.2 Implementation (`data_loader.py`)
The stacking logic is handled dynamically during data loading:

1.  **Pivot Transformation**: Monthly time series are pivoted so that months $\{1, 2, 3\}$ of quarter $t$ become columns `_m1`, `_m2`, `_m3` indexed at $t$.
2.  **Selective Stacking**: Typically, flow variables (like `Fed_Funds`, `CPI`) are stacked to preserve dynamics. Stock variables might be taken at period-end if desired, but "stacking all" is the default to maximize information.
3.  **Result**: A DataFrame where every row is a Quarter-End, but columns represent the full monthly history.

---

## 3. Handling Exogenous Controls: Stacked PCA

A major challenge of U-MIDAS is the **Curse of Dimensionality**. If we control for 10 macro variables, and each is stacked (3 columns), we would have 30 control regressors. This would consume all degrees of freedom in a rolling window.

CoFlow solves this with **Principal Component Analysis (PCA)** (`engine.py`):

1.  **Resolution**: All control variables are "resolved" into their stacked components (e.g., `GDP_m1...m3`, `VIX_m1...m3`).
2.  **Global Factor Extraction**:
    *   The system feeds the **entire set** of stacked controls (often 40+ columns) into a PCA algorithm.
    *   It extracts the top $K$ Principal Components (typically capturing >85% of variance).
3.  **Parsimonious Estimation**:
    *   Instead of regressing on 40 controls, the VAR regresses on just the first ~2-5 Principal Components ($PC_1, \dots, PC_K$).
    *   **Interpretation**: These PCs represent the "Global Macro Factor," "Monetary Policy Factor," etc., leveraging the monthly variances without paying the parameter cost.

---

## 4. Inference Engine: Block-Wise Causality

Since variables are now vectors (blocks of columns), standard scalar t-tests are insufficient. We cannot simply ask "Is $X_{m1}$ significant?" because $X_{m2}$ and $X_{m3}$ are highly correlated controls.

### 4.1 Joint Wald Test (`driver_response.py`)
To determine if Driver $X$ causes Responder $Y$, the system performs a **Block Granger Causality Test** (Wald Test).

**The Hypothesis:**
*   $H_0$: The coefficients of *all* lags of *all* components of $\mathbf{X}$ (m1, m2, m3) are simultaneously zero in *all* equations for $\mathbf{Y}$ (m1, m2, m3).

$$
\text{Test Statistic} \xrightarrow{d} \chi^2(p \times k^2)
$$

This provides a single, rigorous P-value for the relationship between the **concept** $X$ and the **concept** $Y$, robust to the internal correlation structure.

### 4.2 Total Multiplier
To report a human-readable "coefficient" or "elasticity," the system calculates the **Total Multiplier**:

$$
\beta_{total} = \sum_{l=1}^{L} \sum_{i=1}^{3} \sum_{j=1}^{3} \beta_{ji}^{(l)}
$$

This sums the impulse response across all months, effectively answering: *"If the driver increases by 1 unit sustained across the quarter, what is the total cumulative impact on the responder?"*

---

## 5. Technical Summary

| Component | Strategy | Benefit |
| :--- | :--- | :--- |
| **Data Structure** | **Stacked U-MIDAS** | Preserves high-frequency information without bias. |
| **Controls** | **PCA on Stacked Vectors** | Massive dimensionality reduction; controls for "shape" of macro environment. |
| **Inference** | **Joint Wald Tests** | rigorous statistical validity for vector-valued variables. |
| **Estimation** | **VAR / VECM** | Captures bidirectional feedback loops typically missed by single-equation regressions. |

This architecture represents a state-of-the-art approach to multivariate mixed-frequency analysis, balancing the need for granular data with the constraints of finite samples.
