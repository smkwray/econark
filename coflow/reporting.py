import seaborn as sns
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.dates as mdates
import matplotlib.gridspec as gridspec
import pandas as pd
import numpy as np
import warnings
import scipy.stats as stats
from statsmodels.tsa.api import VAR
from sklearn.preprocessing import StandardScaler
from statsmodels.tools.sm_exceptions import ValueWarning
import matplotlib.colors as mcolors
from matplotlib.patches import Patch
import matplotlib.transforms as transforms
from matplotlib.colors import to_rgb

def _threshold_to_p_value(threshold):
    """Support legacy t-stat thresholds (>1) and direct p-value thresholds ([0,1])."""
    if threshold is None or pd.isna(threshold):
        return 0.05
    value = float(threshold)
    # Preserve explicit 0 cutoffs from FDR (no rejections) rather than widening to p<=1.
    if value <= 0:
        return 0.0
    if value <= 1.0:
        return value
    return float(stats.norm.sf(abs(value)) * 2)

def _get_mapped_name(name, config):
    """Resolve display name with exact match first, then optional legacy suffix fallback."""
    if name in config.NAME_MAP:
        return config.NAME_MAP[name]
    base_name = name.removesuffix('_wam')
    return config.NAME_MAP.get(base_name, name)

def _get_mode_description(config, analysis_mode):
    mode_details = {
        config.AnalysisMode.NEGATIVE_CORRELATION: {"desc": "Top candidates ranked by significance score for **negative correlation**."},
        config.AnalysisMode.POSITIVE_CORRELATION: {"desc": "Top candidates ranked by significance score for **positive correlation**."}
    }
    return mode_details.get(analysis_mode, {"desc": ""})["desc"]

def _get_fdr_hypothesis_level(config, candidate_data=None):
    if candidate_data and candidate_data.get("fdr_hypothesis_level") is not None:
        raw_level = candidate_data.get("fdr_hypothesis_level")
    else:
        raw_level = getattr(config, "FDR_HYPOTHESIS_LEVEL", "window")
    level = str(raw_level).strip().lower()
    return "pair" if level == "pair" else "window"

def _get_window_diagnostic_threshold(config, candidate_data=None):
    if candidate_data and candidate_data.get("window_diagnostic_p_threshold") is not None:
        raw = candidate_data.get("window_diagnostic_p_threshold")
    else:
        raw = getattr(config, "PAIR_WINDOW_DIAGNOSTIC_P_THRESHOLD", getattr(config, "SCORING_T_STAT_THRESHOLD", 1.28))
    return _threshold_to_p_value(raw)

def _select_significant_periods(rolling_df, config, significance_threshold, candidate_data=None):
    if config.SIGNIFICANCE_METHOD != config.SignificanceMethod.FDR:
        return rolling_df[rolling_df['p_val_C_on_T'] <= significance_threshold], significance_threshold, "pval"

    if _get_fdr_hypothesis_level(config, candidate_data) == "pair":
        diag_threshold = _get_window_diagnostic_threshold(config, candidate_data)
        return rolling_df[rolling_df['p_val_C_on_T'] <= diag_threshold], diag_threshold, "pval"

    return rolling_df[rolling_df['q_value'] <= config.FDR_ALPHA], config.FDR_ALPHA, "qval"

def _build_summary_table_lines(summary_stats, config, analysis_mode):
    table_header = "| Rank | Counterparty | Score | VECM Periods | Avg. Alpha | Avg. Beta | C -> T (%) | T -> C (%) | Avg. Corr. |"
    table_divider = "|:----:|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|"
    lines = [table_header, table_divider]

    def format_stat(stat_name, fmt_str, stats_dict):
        val = stats_dict.get(stat_name)
        return f"{val:{fmt_str}}" if val is not None and not np.isnan(val) else "---"

    def format_percentage(num_key, den_key, stats_dict):
        num, den = stats_dict.get(num_key, 0), stats_dict.get(den_key, 0)
        return f"{num / den:.1%}" if den > 0 else "0.0%"

    sorted_candidates = sorted(summary_stats.items(), key=lambda item: item[1]['score'], reverse=True)
    for i, (candidate, stats_dict) in enumerate(sorted_candidates[:config.TOP_N_CANDIDATES_FOR_SUMMARY]):
        rank, mapped_candidate_name = i + 1, _get_mapped_name(candidate, config)
        score_str = format_stat('score', '.2f', stats_dict)
        avg_alpha_str = format_stat('avg_alpha', '.3f', stats_dict)
        avg_beta_str = format_stat('avg_beta', '.3f', stats_dict)
        avg_corr_str = format_stat('avg_corr', '.3f', stats_dict)
        vecm_pct_str = format_percentage('vecm_periods_count', 'total_windows_count', stats_dict)
        c_on_t_str = format_percentage('sig_C_on_T_count', 'var_periods_count', stats_dict)
        t_on_c_str = format_percentage('sig_T_on_C_count', 'var_periods_count', stats_dict)
        lines.append(f"| {rank} | {mapped_candidate_name} | {score_str} | {vecm_pct_str} | {avg_alpha_str} | {avg_beta_str} | {c_on_t_str} | {t_on_c_str} | {avg_corr_str} |")

    return lines, sorted_candidates

def build_summary_table_lines(summary_stats, config, analysis_mode):
    table_lines, _ = _build_summary_table_lines(summary_stats, config, analysis_mode)
    return table_lines


def _format_score_pair(mean_score, median_score):
    mean_val = 0.0 if mean_score is None or pd.isna(mean_score) else float(mean_score)
    med_val = 0.0 if median_score is None or pd.isna(median_score) else float(median_score)
    return f"{mean_val:.2f} / {med_val:.2f}"


def generate_mf_track_comparison_block(track_rows, include_short_interpretation=True):
    if not track_rows:
        return ""

    ordered_rows = sorted(track_rows, key=lambda row: row.get("track_code", "Z"))
    lines = [
        "#### Mixed-Frequency 3-Track Comparison",
        "| Track | System | VECM Share | Total Windows | VECM Windows | Valid C -> T p | Pair Rejections | Mean / Median Score | Non-zero Candidates | Interpretation |",
        "|:---:|:---|:---:|---:|---:|---:|---:|:---:|:---:|:---|",
    ]

    row_by_label = {}
    for row in ordered_rows:
        interpretation = str(row.get("interpretation_label", "")).strip().lower()
        row_by_label[interpretation] = row
        vecm_share = row.get("vecm_share")
        vecm_str = "n/a" if vecm_share is None or pd.isna(vecm_share) else f"{float(vecm_share):.1%}"
        total_windows = int(row.get("total_windows", 0) or 0)
        vecm_windows = int(row.get("vecm_windows", 0) or 0)
        valid_p_windows = int(row.get("valid_p_windows", 0) or 0)
        pair_rejections_raw = row.get("pair_rejection_count")
        pair_rejections = "n/a" if pair_rejections_raw is None else str(int(pair_rejections_raw))
        score_pair = _format_score_pair(row.get("mean_score"), row.get("median_score"))
        non_zero = int(row.get("non_zero_count", 0))
        candidate_count = max(1, int(row.get("candidate_count", 0)))
        lines.append(
            f"| {row.get('track_code', '?')} | `{row.get('system', 'unknown')}` | {vecm_str} | "
            f"{total_windows} | {vecm_windows} | {valid_p_windows} | {pair_rejections} | "
            f"{score_pair} | {non_zero}/{candidate_count} | `{interpretation or 'unknown'}` |"
        )

    lines.extend(
        [
            "",
            "_Diagnostics: totals are summed across all target-candidate windows in this scenario._",
        ]
    )

    confirm_row = row_by_label.get("confirmatory", {})
    robust_row = row_by_label.get("robustness", {})
    exploratory_row = row_by_label.get("exploratory", {})

    confirm_non_zero = int(confirm_row.get("non_zero_count", 0) or 0)
    robust_non_zero = int(robust_row.get("non_zero_count", 0) or 0)
    exploratory_non_zero = int(exploratory_row.get("non_zero_count", 0) or 0)
    confirm_pair_rejections_raw = confirm_row.get("pair_rejection_count")
    confirm_pair_rejections = (
        None if confirm_pair_rejections_raw is None else int(confirm_pair_rejections_raw)
    )
    confirm_valid_p = int(confirm_row.get("valid_p_windows", 0) or 0)
    confirm_hypothesis_level = str(confirm_row.get("fdr_hypothesis_level", "window")).strip().lower()
    confirm_vecm_share = confirm_row.get("vecm_share")
    robust_vecm_share = robust_row.get("vecm_share")

    if (
        confirm_vecm_share is not None
        and robust_vecm_share is not None
        and not pd.isna(confirm_vecm_share)
        and not pd.isna(robust_vecm_share)
        and float(robust_vecm_share) > float(confirm_vecm_share) + 0.10
    ):
        vecm_gap_line = (
            f"Track B's VECM share ({float(robust_vecm_share):.1%}) is above Track A "
            f"({float(confirm_vecm_share):.1%}); full-stacked systems usually select VECM more often because "
            "higher dimensional blocks can satisfy Johansen rank criteria in more windows."
        )
    else:
        vecm_gap_line = (
            "VECM shares are not materially higher in Track B for this scenario, so the stacked-system selection "
            "gap is limited here."
        )

    if confirm_non_zero == 0 and (robust_non_zero > 0 or exploratory_non_zero > 0):
        bug_vs_method_line = (
            "Pattern check: signals appear in robustness/exploratory tracks but not in Track A, "
            "which points to confirmatory strictness rather than an implementation bug."
        )
    elif confirm_non_zero == 0 and robust_non_zero == 0 and exploratory_non_zero == 0:
        bug_vs_method_line = (
            "Pattern check: all tracks are near-null; this suggests weak available signal under the current sample/spec, "
            "not a specific track-implementation failure."
        )
    else:
        bug_vs_method_line = (
            "Pattern check: Track A and at least one sensitivity track show non-zero signal, "
            "so results are not consistent with an all-null implementation bug."
        )

    if confirm_hypothesis_level == "pair":
        if confirm_pair_rejections is None or confirm_pair_rejections <= 0:
            strictness_line = (
                f"Track A has zero pair-level rejections while only {confirm_valid_p} windows produced valid C -> T p-values; "
                "pair-level confirmatory gating plus the window-level causality filter drives near-zero confirmatory scores."
            )
        else:
            strictness_line = (
                f"Track A has {confirm_pair_rejections} pair-level rejections; confirmatory scores stay non-zero where pair "
                "tests pass and are attenuated where they do not."
            )
    else:
        strictness_line = (
            f"Track A uses window-level FDR; near-zero confirmatory scores occur when few windows pass both the FDR-adjusted "
            f"window cutoff and the C -> T causality filter (valid C -> T windows: {confirm_valid_p})."
        )

    if include_short_interpretation:
        lines.extend(
            [
                "",
                "Short Interpretation:",
                f"1. {vecm_gap_line}",
                f"2. {strictness_line}",
                f"3. {bug_vs_method_line}",
            ]
        )
    return "\n".join(lines)

def create_aggregated_irf_plot(rolling_results_dict, target_variable, scenario_name, output_path, config):
    """Creates a paired plot showing aggregated Impulse Response Functions and their occurrence over time."""
    num_candidates = len(rolling_results_dict)
    if num_candidates == 0:
        return
        
    fig = plt.figure(figsize=(18, 5 * num_candidates))
    gs = gridspec.GridSpec(2 * num_candidates, 1, height_ratios=np.tile([10, 1], num_candidates))

    # === LAYOUT CONTROL PANEL ===
    # Adjust these values to control plot spacing
    layout_params = {
        "main_title_y": 1.12,  # Vertical position of the main title.
        "subtitle_y": 1.06,   # Vertical position of the subtitle.
        "top_margin": 0.94,   # Position of the top of the plots (closer to 1.0 = less space for titles)
        "plot_hspace": 0.45   # Vertical gap BETWEEN candidate plots.
    }
    # ============================

    title = f'Aggregated Impulse Response to Shock in {_get_mapped_name(target_variable, config)}'
    subtitle = f'Scenario: {scenario_name} | Regimes defined by significant VAR residual correlation'
    # Anchor titles to the first subplot for stable placement
    ax_for_title = fig.add_subplot(gs[0, 0])
    ax_for_title.text(0.5, layout_params["main_title_y"], title, ha='center', va='center', transform=ax_for_title.transAxes, fontsize=20, weight='bold')
    ax_for_title.text(0.5, layout_params["subtitle_y"], subtitle, ha='center', va='center', transform=ax_for_title.transAxes, fontsize=12, style='italic')
    ax_for_title.set_axis_off()

    for i, (candidate_name, data) in enumerate(rolling_results_dict.items()):
        ax_irf = fig.add_subplot(gs[2*i, 0])
        ax_timeline = fig.add_subplot(gs[2*i+1, 0])
        
        # Logic for plotting IRFs (unchanged)
        rolling_df = data['df']
        mapped_candidate_name = _get_mapped_name(candidate_name, config)
        # ... (rest of the plotting logic is the same as before) ...
        significance_threshold = _threshold_to_p_value(
            data.get('significance_threshold', stats.norm.sf(abs(config.SCORING_T_STAT_THRESHOLD)) * 2)
        )
        significant_periods, _, _ = _select_significant_periods(
            rolling_df,
            config,
            significance_threshold,
            data,
        )
        
        # --- NEW LOGIC: Include VECMs in IRF aggregation ---
        # Co-movement (Positive): Stable VECM VECM beta > 0 OR VAR resid > 0
        vecm_pos = significant_periods[(significant_periods['model_type'] == 'VECM') & 
                                       (significant_periods['target_alpha'] < 0) & 
                                       (significant_periods['beta_coeff'] > 0)]
        var_pos = significant_periods[(significant_periods['model_type'] == 'VAR') & 
                                      (significant_periods['residual_corr'] > 0)]
        positive_periods = pd.concat([vecm_pos, var_pos]).sort_index()

        # Counterparty (Negative): Stable VECM beta < 0 OR VAR resid <= 0
        vecm_neg = significant_periods[(significant_periods['model_type'] == 'VECM') & 
                                       (significant_periods['target_alpha'] < 0) & 
                                       (significant_periods['beta_coeff'] <= 0)]
        var_neg = significant_periods[(significant_periods['model_type'] == 'VAR') & 
                                      (significant_periods['residual_corr'] <= 0)]
        negative_periods = pd.concat([vecm_neg, var_neg]).sort_index()
        
        regime_data = {'Co-movement (Positive Corr)': {'df': positive_periods, 'color': '#e0cc84'}, 'Counterparty (Negative Corr)': {'df': negative_periods, 'color': '#a5bac9'}}
        has_data = False
        for label, regime in regime_data.items():
            irf_series = regime['df']['irf_response'].dropna()
            if not irf_series.empty:
                has_data = True
                irf_matrix = np.vstack(irf_series.values)
                mean_irf = np.mean(irf_matrix, axis=0)
                q1, q3 = np.percentile(irf_matrix, [25, 75], axis=0)
                ax_irf.plot(range(len(mean_irf)), mean_irf, label=f"{label} ({len(irf_series)} windows)", color=regime['color'], lw=2.5)
                ax_irf.fill_between(range(len(mean_irf)), q1, q3, color=regime['color'], alpha=0.3, ec='none')
                ax_timeline.bar(regime['df'].index, height=1, width=31, color=regime['color'], alpha=0.8, edgecolor='none')
        if not has_data:
            ax_irf.text(0.5, 0.5, 'Insufficient Significant VAR Data for IRF', ha='center', va='center', transform=ax_irf.transAxes, fontsize=14, style='italic', color='gray')
        
        # Axis and label settings
        ax_irf.set_title(f'Candidate: {mapped_candidate_name}', fontsize=14, pad=10)
        ax_irf.set_ylabel('Response of Candidate')
        ax_irf.grid(True, which='both', linestyle=':', alpha=0.6)
        handles, labels = ax_irf.get_legend_handles_labels()
        if handles:
            ax_irf.legend(loc='best')
        ax_irf.set_xlabel('Months After Shock')
        ax_irf.set_xticks(np.arange(0, config.IRF_PERIODS + 1, 2))
        ax_irf.set_xlim(0, config.IRF_PERIODS)
        ax_timeline.set_yticks([])
        ax_timeline.set_ylabel('Events', rotation=0, ha='right', va='center', fontsize=9)
        ax_timeline.xaxis.set_major_locator(mdates.YearLocator(2))
        ax_timeline.xaxis.set_major_formatter(mdates.DateFormatter('%Y'))
        if not rolling_df.empty:
            ax_timeline.set_xlim(rolling_df.index.min(), rolling_df.index.max())

    # Use subplots_adjust for direct, predictable control. NO tight_layout().
    fig.subplots_adjust(top=layout_params["top_margin"], hspace=layout_params["plot_hspace"]) 
    plt.savefig(output_path, dpi=config.GRAPH_DPI, bbox_inches='tight')
    plt.close(fig)
    print(f"✅ IRF plot saved to '{output_path}'")

def create_vecm_summary_plot(rolling_results_dict, target_variable, scenario_name, output_path, config):
    """Creates a paired plot showing VECM parameter distributions and their occurrence over time."""
    num_candidates = len(rolling_results_dict)
    if num_candidates == 0:
        return

    fig = plt.figure(figsize=(18, 5 * num_candidates))
    
    # === LAYOUT CONTROL PANEL ===
    # Adjust these values to control plot spacing
    layout_params = {
        "main_title_y": 1.25,  # Vertical position of the main title.
        "subtitle_y": 1.15,   # Vertical position of the subtitle.
        "top_margin": 0.90,   # Position of the top of the plots (closer to 1.0 = less space for titles)
        "plot_hspace": 0.42,  # Vertical gap BETWEEN candidate plots.
        "label_hspace": 0.50  # Vertical gap WITHIN a candidate's plots (to show x-axis labels).
    }
    # ============================

    title = f'VECM Long-Run Parameter Distributions for Target: {_get_mapped_name(target_variable, config)}'
    subtitle = f'Scenario: {scenario_name} | Based on significant, stable (α_target < 0) VECM periods'
    # Create a main GridSpec for the candidate rows
    outer_gs = gridspec.GridSpec(num_candidates, 1, figure=fig)
    
    # Anchor titles to the first subplot for stable placement
    ax_for_title = fig.add_subplot(outer_gs[0])
    ax_for_title.text(0.5, layout_params["main_title_y"], title, ha='center', va='center', transform=ax_for_title.transAxes, fontsize=20, weight='bold')
    ax_for_title.text(0.5, layout_params["subtitle_y"], subtitle, ha='center', va='center', transform=ax_for_title.transAxes, fontsize=12, style='italic')
    ax_for_title.set_axis_off()


    for i, (candidate_name, data) in enumerate(rolling_results_dict.items()):
        rolling_df = data['df']
        mapped_candidate_name = _get_mapped_name(candidate_name, config)
        
        # Use a nested GridSpec for each candidate's plots, now with configurable hspace
        inner_gs = gridspec.GridSpecFromSubplotSpec(2, 3, subplot_spec=outer_gs[i], 
                                                    height_ratios=[10, 1], wspace=0.3, hspace=layout_params["label_hspace"])
        
        ax_beta = fig.add_subplot(inner_gs[0, 0])
        ax_alpha_t = fig.add_subplot(inner_gs[0, 1])
        ax_alpha_c = fig.add_subplot(inner_gs[0, 2])
        ax_timeline = fig.add_subplot(inner_gs[1, :])
        
        significance_threshold = _threshold_to_p_value(
            data.get('significance_threshold', stats.norm.sf(abs(config.SCORING_T_STAT_THRESHOLD)) * 2)
        )
        significant_periods, _, _ = _select_significant_periods(
            rolling_df,
            config,
            significance_threshold,
            data,
        )
        vecm_periods = significant_periods[(significant_periods['model_type'] == 'VECM') & (significant_periods['target_alpha'] < 0)]

        ax_alpha_t.set_title(f'Candidate: {mapped_candidate_name}\n({len(vecm_periods)} windows)', fontsize=14, pad=20)

        # Logic for plotting violins (unchanged)
        if vecm_periods.empty or len(vecm_periods) < 5:
            for ax in [ax_beta, ax_alpha_t, ax_alpha_c]:
                ax.text(0.5, 0.5, 'Insufficient Data', ha='center', va='center', transform=ax.transAxes, fontsize=12, style='italic', color='gray')
                ax.set_xticks([]); ax.set_yticks([])
        else:
            # ... (rest of the plotting logic is the same as before) ...
            beta_vals = vecm_periods['beta_coeff'].dropna()
            sns.violinplot(y=beta_vals, ax=ax_beta, color='#440154', inner='quart', cut=0)
            ax_beta.set_xlabel(r'$\beta$ (Long-Run Coeff)'); ax_beta.set_ylabel('Coefficient')
            ax_beta.axhline(0, color='black', linestyle='--', lw=1)
            alpha_t_vals = vecm_periods['target_alpha'].dropna()
            alpha_c_vals = vecm_periods['candidate_alpha'].dropna()
            sns.violinplot(y=alpha_t_vals, ax=ax_alpha_t, color='#21918c', inner='quart', cut=0)
            ax_alpha_t.set_xlabel(r'$\alpha_{target}$ (Target Speed)'); ax_alpha_t.set_ylabel('')
            ax_alpha_t.axhline(0, color='black', linestyle='--', lw=1)
            sns.violinplot(y=alpha_c_vals, ax=ax_alpha_c, color='#fde725', inner='quart', cut=0)
            ax_alpha_c.set_xlabel(r'$\alpha_{candidate}$ (Cand. Speed)'); ax_alpha_c.set_ylabel('')
            ax_alpha_c.axhline(0, color='black', linestyle='--', lw=1)
            if not alpha_t_vals.empty and not alpha_c_vals.empty:
                min_alpha = min(alpha_t_vals.min(), alpha_c_vals.min())
                max_alpha = max(alpha_t_vals.max(), alpha_c_vals.max())
                padding = abs(max_alpha - min_alpha) * 0.1 if max_alpha > min_alpha else 0.1
                ax_alpha_t.set_ylim(min_alpha - padding, max_alpha + padding)
                ax_alpha_c.set_ylim(min_alpha - padding, max_alpha + padding)

        # Timeline logic (unchanged)
        positive_beta_periods = vecm_periods[vecm_periods['beta_coeff'] > 0]
        negative_beta_periods = vecm_periods[vecm_periods['beta_coeff'] <= 0]
        ax_timeline.bar(positive_beta_periods.index, height=1, width=31, color='#e0cc84', alpha=0.8, edgecolor='none')
        ax_timeline.bar(negative_beta_periods.index, height=1, width=31, color='#a5bac9', alpha=0.8, edgecolor='none')
        ax_timeline.set_yticks([])
        ax_timeline.xaxis.set_major_locator(mdates.YearLocator(2))
        ax_timeline.xaxis.set_major_formatter(mdates.DateFormatter('%Y'))
        if not rolling_df.empty:
            ax_timeline.set_xlim(rolling_df.index.min(), rolling_df.index.max())

    # Use subplots_adjust for direct, predictable control. NO tight_layout().
    fig.subplots_adjust(top=layout_params["top_margin"], hspace=layout_params["plot_hspace"])
    plt.savefig(output_path, dpi=config.GRAPH_DPI, bbox_inches='tight')
    plt.close(fig)
    print(f"✅ VECM summary plot saved to '{output_path}'")

def create_combined_rolling_plot(rolling_results_dict, target_variable, scenario_name, output_path, config, analysis_mode):
    num_plots = len(rolling_results_dict)
    if num_plots == 0:
        print(f"No results to plot for {scenario_name}.")
        return
        
    fig, axes = plt.subplots(num_plots, 1, figsize=(18, 7 * num_plots), sharex=False, squeeze=False)
    axes = axes.flatten()

    # === LAYOUT CONTROL PANEL ===
    # This is the ONLY place you need to edit to control spacing.
    layout_params = {
        "title_y": 1.20,      # Vertical position of the main title.
        "subtitle_y": 1.13,   # MOVED DOWN: New, lower position for the subtitle.
        "legend_y": 1.12,     # MOVED UP: New, higher position for the legend.
        "top_margin": 0.88    # This can stay the same for now.
    }
    # =======================================
    
    mapped_target_name = _get_mapped_name(target_variable, config)
    first_candidate_data = next(iter(rolling_results_dict.values()))
    significance_threshold = _threshold_to_p_value(
        first_candidate_data.get('significance_threshold', stats.norm.sf(abs(config.SCORING_T_STAT_THRESHOLD)) * 2)
    )
    fdr_mode = str(first_candidate_data.get("fdr_mode", getattr(config, "FDR_MODE", "bh"))).upper()
    fdr_hypothesis_level = _get_fdr_hypothesis_level(config, first_candidate_data)
    pair_window_diag_threshold = _get_window_diagnostic_threshold(config, first_candidate_data)

    title = f'Time-Varying VECM & VAR Coefficients for {scenario_name}'
        
    if config.SIGNIFICANCE_METHOD == config.SignificanceMethod.FDR:
        if fdr_hypothesis_level == "pair":
            subtitle = (
                f'Confirmatory FDR ({fdr_mode}) is applied at the pair level (α={config.FDR_ALPHA:.2f}) '
                f'with a combined p-value cutoff of {significance_threshold:.3f}. '
                f'Window shading is descriptive (window p-value <= {pair_window_diag_threshold:.3f}).'
            )
        else:
            subtitle = (
                f'Significance by window-level FDR ({fdr_mode}, α={config.FDR_ALPHA:.2f}), '
                f'producing a causality p-value cutoff of {significance_threshold:.3f}. '
                'Shading intensity reflects statistical strength (q-value).'
            )
    else:
        subtitle = (f'Significance by fixed causality p-value cutoff of {significance_threshold:.3f}. '
                    'Shading intensity reflects statistical strength.')

    # Place title and subtitle relative to the TOP subplot
    axes[0].text(0.5, layout_params["title_y"], title, ha='center', va='center', transform=axes[0].transAxes, fontsize=20, weight='bold')
    axes[0].text(0.5, layout_params["subtitle_y"], subtitle, ha='center', va='center', transform=axes[0].transAxes, fontsize=12, style='italic')

    p1, p2, p3, p4 = (None, None, None, None)
    # --- MODIFIED BLOCK: Consistent Color Definitions ---
    # Yellowish color will now ALWAYS represent a positive relationship.
    # Blue-greyish color will now ALWAYS represent a negative relationship.
    POSITIVE_SHADING_COLOR, NEGATIVE_SHADING_COLOR = '#e0cc84', '#a5bac9'
    # --- END OF MODIFIED BLOCK ---
    MIN_ALPHA, MAX_ALPHA = 0.20, 0.50

    for i, (candidate_name, data) in enumerate(rolling_results_dict.items()):
        # This loop is unchanged
        rolling_df = data['df']
        avg_lags = data['avg_lags']
        ax1 = axes[i]
        mapped_candidate_name = _get_mapped_name(candidate_name, config)

        significant_periods, strength_threshold, strength_basis = _select_significant_periods(
            rolling_df,
            config,
            significance_threshold,
            data,
        )
        candidate_effective_threshold = _threshold_to_p_value(
            data.get('significance_threshold', significance_threshold)
        )
        if (
            config.SIGNIFICANCE_METHOD == config.SignificanceMethod.FDR
            and _get_fdr_hypothesis_level(config, data) == "window"
        ):
            # Keep directional causality ticks on the same effective cutoff used by confirmatory windows.
            candidate_causality_threshold = candidate_effective_threshold
        else:
            candidate_causality_threshold = strength_threshold
        
        # --- MODIFIED BLOCK: Unified Shading Logic ---
        # This logic now defines positive and negative periods independently of the analysis mode.
        # This ensures the color meanings are stable across all plots.
        
        # A "positive period" is a stable VECM with beta > 0 OR a VAR with residual_corr > 0.
        vecm_positive = significant_periods[
            (significant_periods['model_type'] == 'VECM') & 
            (significant_periods['target_alpha'] < 0) & 
            (significant_periods['beta_coeff'] > 0)
        ]
        var_positive = significant_periods[(significant_periods['model_type'] == 'VAR') & (significant_periods['residual_corr'] > 0)]
        all_positive_periods = pd.concat([vecm_positive, var_positive])
        
        # A "negative period" is a stable VECM with beta < 0 OR a VAR with residual_corr <= 0.
        vecm_negative = significant_periods[
            (significant_periods['model_type'] == 'VECM') & 
            (significant_periods['target_alpha'] < 0) & 
            (significant_periods['beta_coeff'] < 0)
        ]
        var_negative = significant_periods[(significant_periods['model_type'] == 'VAR') & (significant_periods['residual_corr'] <= 0)]
        all_negative_periods = pd.concat([vecm_negative, var_negative])
        # --- END OF MODIFIED BLOCK ---

        is_significant = rolling_df.index.isin(significant_periods.index)

        corr_sig, corr_insig = rolling_df['residual_corr'].where(is_significant), rolling_df['residual_corr'].where(~is_significant)
        ax1.plot(rolling_df.index, corr_insig, color='orange', ls='-', lw=1.5, alpha=0.3)
        p3, = ax1.plot(rolling_df.index, corr_sig, color='orange', ls='-', lw=1.5, alpha=1.0, label='Pearson Corr.')
        
        if 'spearman_corr' in rolling_df.columns:
            spearman_sig, spearman_insig = rolling_df['spearman_corr'].where(is_significant), rolling_df['spearman_corr'].where(~is_significant)
            ax1.plot(rolling_df.index, spearman_insig, color='lightcoral', ls='-', lw=1.5, alpha=0.4)
            p4, = ax1.plot(rolling_df.index, spearman_sig, color='lightcoral', ls='-', lw=1.5, alpha=1.0, label='Spearman Corr.')
        
        ax1.set_ylabel('Residual Correlation', color='black', fontsize=12)
        ax1.tick_params(axis='y', labelcolor='black')
        ax1.axhline(0, color='red', linestyle=':', linewidth=1.5)
        ax1.grid(True, which='both', linestyle='--', alpha=0.5)
        ax2 = ax1.twinx()
        target_alpha_sig, target_alpha_insig = rolling_df['target_alpha'].where(is_significant), rolling_df['target_alpha'].where(~is_significant)
        ax2.plot(rolling_df.index, target_alpha_insig, color='cornflowerblue', lw=2.0, alpha=0.3)
        p1, = ax2.plot(rolling_df.index, target_alpha_sig, color='cornflowerblue', lw=2.0, alpha=1.0, label='VECM Alpha (Target)')
        
        if 'candidate_alpha' in rolling_df.columns:
            candidate_alpha_sig, candidate_alpha_insig = rolling_df['candidate_alpha'].where(is_significant), rolling_df['candidate_alpha'].where(~is_significant)
            ax2.plot(rolling_df.index, candidate_alpha_insig, color='green', lw=2.0, alpha=0.3)
            p2, = ax2.plot(rolling_df.index, candidate_alpha_sig, color='green', lw=2.0, alpha=1.0, label='VECM Alpha (Candidate)')
            
        ax2.set_ylabel('VECM Alpha Coefficient (Levels)', color='black', fontsize=12)
        ax2.tick_params(axis='y', labelcolor='black')
        ax2.axhline(0, color='teal', linestyle='dotted', linewidth=1.5)
        
        def draw_shading(periods, color):
            for idx, row in periods.iterrows():
                loc = rolling_df.index.get_loc(idx)
                start_date = rolling_df.index[loc - 1] if loc > 0 else idx
                if config.SIGNIFICANCE_METHOD == config.SignificanceMethod.FDR and strength_basis == "qval":
                    norm_strength = 1 - (row.get('q_value', config.FDR_ALPHA) / (config.FDR_ALPHA + 1e-9))
                else:
                    p_val = row.get('p_val_C_on_T', candidate_causality_threshold)
                    norm_strength = 1 - (p_val / (candidate_causality_threshold + 1e-9))
                
                final_alpha = MIN_ALPHA + norm_strength * (MAX_ALPHA - MIN_ALPHA)
                final_alpha = max(MIN_ALPHA, min(final_alpha, MAX_ALPHA))
                ax1.axvspan(start_date, idx, facecolor=color, alpha=final_alpha, ec='none')
        
        # --- MODIFIED BLOCK: Draw shading with consistent colors ---
        draw_shading(all_positive_periods, POSITIVE_SHADING_COLOR)
        draw_shading(all_negative_periods, NEGATIVE_SHADING_COLOR)
        # --- END OF MODIFIED BLOCK ---

        # --- MODIFIED BLOCK FOR CAUSALITY SHADING ---
        trans = transforms.blended_transform_factory(ax1.transData, ax1.transAxes)
        causality_y_pos, causality_height = 0.0, 0.045
        MIN_CAUSALITY_ALPHA, MAX_CAUSALITY_ALPHA = 0.5, 1.0
        
        for idx, row in rolling_df.iterrows():
            # Use the dynamic p-value threshold calculated earlier
            sig_c_on_t = row.get('p_val_C_on_T', 1.0) < candidate_causality_threshold
            sig_t_on_c = row.get('p_val_T_on_C', 1.0) < candidate_causality_threshold
            
            if not (sig_c_on_t or sig_t_on_c): continue
            
            # NEW: Calculate alpha based on the causality p-value's own strength
            causality_p_val = min(row.get('p_val_C_on_T', 1.0), row.get('p_val_T_on_C', 1.0))
            
            # Normalize strength: 1.0 for p=0, ~0.0 for p near the threshold
            norm_strength = 1 - (causality_p_val / (candidate_causality_threshold + 1e-9))
            norm_strength = max(0, min(norm_strength, 1)) # Clamp between 0 and 1
            
            final_alpha = MIN_CAUSALITY_ALPHA + norm_strength * (MAX_CAUSALITY_ALPHA - MIN_CAUSALITY_ALPHA)
            
            color = 'darkviolet' if sig_c_on_t and sig_t_on_c else ('crimson' if sig_c_on_t else 'royalblue')
            
            loc = rolling_df.index.get_loc(idx)
            start_date = rolling_df.index[loc - 1] if loc > 0 else idx
            duration = idx - start_date
            ax1.barh(causality_y_pos, duration, left=start_date, height=causality_height,
                     facecolor=color, alpha=final_alpha, edgecolor='none', transform=trans)
        # --- END OF MODIFIED BLOCK ---
        
        # --- NEW: BETA VISUALIZATION TRACK (AT TOP OF GRAPH) ---
        beta_trans = transforms.blended_transform_factory(ax1.transData, ax1.transAxes)
        VISIBLE_HEIGHT = 0.015
        BETA_Y_POS, BETA_HEIGHT = 1.0 - (VISIBLE_HEIGHT / 2), VISIBLE_HEIGHT # <-- CORRECTED LINE

        vecm_periods = rolling_df[rolling_df['model_type'] == 'VECM']
        max_abs_beta = vecm_periods['beta_coeff'].abs().max() if not vecm_periods.empty else 1.0
        if max_abs_beta == 0: max_abs_beta = 1.0 # Avoid division by zero
        MIN_BETA_ALPHA, MAX_BETA_ALPHA = 0.40, 0.95


        for idx, row in rolling_df.iterrows():
            # Check if it's a VECM period and beta exists
            if row['model_type'] == 'VECM' and pd.notna(row.get('beta_coeff')):
                
                # Assign color based on the sign of beta
                color = 'MediumVioletRed' if row['beta_coeff'] > 0 else 'teal'
                
                norm_strength = (abs(row['beta_coeff']) / max_abs_beta)**2 # <-- UPDATED LINE
                final_alpha = MIN_BETA_ALPHA + norm_strength * (MAX_BETA_ALPHA - MIN_BETA_ALPHA)

                # Get the date range for the bar
                loc = rolling_df.index.get_loc(idx)
                start_date = rolling_df.index[loc - 1] if loc > 0 else idx
                duration = idx - start_date
                
                # Draw the horizontal bar
                ax1.barh(BETA_Y_POS, duration, left=start_date, height=BETA_HEIGHT,
                         facecolor=color, alpha=0.75, edgecolor='none', transform=beta_trans)
        # --- END OF NEW BETA TRACK ---

        title_text = f'Candidate: {mapped_candidate_name}'
        if avg_lags is not None and avg_lags > 0: title_text += f' (Avg. Lags: {avg_lags:.1f})'
        ax1.set_title(title_text, fontsize=14)
        ax1.xaxis.set_major_locator(mdates.YearLocator(2))
        ax1.xaxis.set_major_formatter(mdates.DateFormatter('%Y'))
        plt.setp(ax1.get_xticklabels(), rotation=30, ha="right")
        
        if not rolling_df.empty: ax1.set_xlim(rolling_df.index.min(), rolling_df.index.max())
    
    # --- MODIFIED BLOCK: Standardized Legend ---
    # The legend is now fixed and no longer changes based on the analysis mode.
    legend_handles = [h for h in [p3, p4, p1, p2] if h]
    legend_handles.append(mpatches.Patch(color=POSITIVE_SHADING_COLOR, alpha=0.6, label='Significant Positive Period'))
    legend_handles.append(mpatches.Patch(color=NEGATIVE_SHADING_COLOR, alpha=0.6, label='Significant Negative Period'))
    
    # Place legend above the top subplot using its coordinate system
    axes[0].legend(handles=legend_handles, loc='upper center',
                   bbox_to_anchor=(0.5, layout_params["legend_y"]),
                   ncol=len(legend_handles), frameon=False, fontsize=12)
    # --- END OF MODIFIED BLOCK ---

    # Legend 1: Causality Track (This is the original bottom legend)
    causality_patches = [
        Patch(color='crimson', label='C → T'), 
        Patch(color='royalblue', label='T → C'), 
        Patch(color='darkviolet', label='Feedback')
    ]
    causality_legend = axes[-1].legend(
        handles=causality_patches, 
        loc='upper center', 
        bbox_to_anchor=(0.5, -0.12), # Position for the first legend
        ncol=3, 
        frameon=False, 
        title='Direction of Significant Granger-Causality (shading indicates p-value strength)'
    )
    # Manually add the first legend to the plot
    axes[-1].add_artist(causality_legend)

    # Legend 2: Beta Track (This is the new legend you requested)
    beta_patches = [
        Patch(color='MediumVioletRed', label='Positive Beta'),
        Patch(color='teal', label='Negative Beta')
    ]
    axes[-1].legend(
        handles=beta_patches,
        loc='upper center',
        bbox_to_anchor=(0.5, -0.22), # Position it lower than the first legend
        ncol=2,
        frameon=False,
        title='Cointegration Direction (Johansen p < 0.05, shading by beta magnitude)' # <-- UPDATED TITLE
    )
    # --- END OF MODIFIED LEGEND BLOCK ---

    axes[-1].set_xlabel('Date (End of Rolling Window)', fontsize=12)
    
    # This ensures the layout is calculated before adjusting the top margin
    fig.tight_layout()
    # This final adjustment reserves space at the top for our titles and legend
    fig.subplots_adjust(top=layout_params["top_margin"])
    
    plt.savefig(output_path, dpi=config.GRAPH_DPI, bbox_inches='tight')
    plt.close(fig)
    print(f"✅ Plot saved to '{output_path}'")

def generate_consolidated_report(
    point_estimate_stats,
    point_estimate_dfs,
    qs_run_stats,
    target_variable,
    scenario_str,
    config,
    analysis_mode,
    report_prefix="",
    include_plots=True,
    track_heading=None,
    track_tag=None,
):
    mapped_target_name = _get_mapped_name(target_variable, config)
    
    significance_threshold = _threshold_to_p_value(config.SCORING_T_STAT_THRESHOLD)
    first_candidate_data = {}
    if point_estimate_dfs:
        first_candidate_data = next(iter(point_estimate_dfs.values()))
        significance_threshold = _threshold_to_p_value(first_candidate_data.get('significance_threshold', significance_threshold))

    if config.SIGNIFICANCE_METHOD == config.SignificanceMethod.FDR:
        fdr_mode = str(first_candidate_data.get("fdr_mode", getattr(config, "FDR_MODE", "bh"))).upper()
        fdr_hypothesis_level = _get_fdr_hypothesis_level(config, first_candidate_data)
        if fdr_hypothesis_level == "pair":
            combiner_name = first_candidate_data.get("pair_combiner", "brown_kost_overlap_ar1")
            diag_threshold = _get_window_diagnostic_threshold(config, first_candidate_data)
            pair_score_mode = str(
                first_candidate_data.get(
                    "pair_score_calibration",
                    getattr(config, "PAIR_SCORE_CALIBRATION", "soft_gate"),
                )
            ).strip().lower()
            pair_score_power = first_candidate_data.get(
                "pair_score_soft_power",
                getattr(config, "PAIR_SCORE_SOFT_POWER", 2.0),
            )
            try:
                pair_score_power = float(pair_score_power)
            except (TypeError, ValueError):
                pair_score_power = 2.0
            if pair_score_mode == "hard_gate":
                pair_score_note = (
                    "Scores are hard-gated to zero for non-rejected pairs."
                )
            else:
                pair_score_note = (
                    f"Scores for non-rejected pairs are softly attenuated by "
                    f"(alpha / max(pair q-value, alpha))^{pair_score_power:.2f}; "
                    "confirmatory discoveries still require pair q-value <= alpha."
                )
            if significance_threshold <= 0:
                methodology_str = (
                    f"_**Methodology**: Confirmatory hypotheses were defined at the **pair level**. "
                    f"Window p-values were combined per pair using **{combiner_name}**, then FDR ({fdr_mode}) "
                    f"was applied across pair-level p-values at **alpha={config.FDR_ALPHA:.2f}**. "
                    f"No pair-level hypotheses were rejected (effective combined p-value cutoff: **0.000**). "
                    f"{pair_score_note} "
                    f"Window-level shading remains descriptive (window p-value <= **{diag_threshold:.3f}**)._"
                )
            else:
                methodology_str = (
                    f"_**Methodology**: Confirmatory hypotheses were defined at the **pair level**. "
                    f"Window p-values were combined per pair using **{combiner_name}**, then FDR ({fdr_mode}) "
                    f"was applied across pair-level p-values at **alpha={config.FDR_ALPHA:.2f}**. "
                    f"The effective combined p-value cutoff was **{significance_threshold:.3f}**. "
                    f"{pair_score_note} "
                    f"Window-level shading remains descriptive (window p-value <= **{diag_threshold:.3f}**)._"
                )
        else:
            granger_thresh = getattr(config, "GRANGER_SIG_THRESHOLD", 0.05)
            if significance_threshold <= 0:
                methodology_str = (
                    f"_**Methodology**: Confirmatory hypotheses were defined at the **window level** and adjusted via "
                    f"FDR ({fdr_mode}) at **alpha={config.FDR_ALPHA:.2f}**. "
                    f"No hypotheses were rejected (effective block-causality p-value cutoff: **0.000**). "
                    f"Block-causality (Granger) sig. threshold: **{granger_thresh:.2f}** (VAR periods)._"
                )
            else:
                methodology_str = (
                    f"_**Methodology**: Confirmatory hypotheses were defined at the **window level** and adjusted via "
                    f"FDR ({fdr_mode}) at **alpha={config.FDR_ALPHA:.2f}**. "
                    f"The resulting significance cutoff was block-causality p-value **{significance_threshold:.3f}**. "
                    f"Block-causality (Granger) sig. threshold: **{granger_thresh:.2f}** (VAR periods)._"
                )
    else:
        granger_thresh = getattr(config, "GRANGER_SIG_THRESHOLD", 0.05)
        methodology_str = (f"_**Methodology**: Results were scored using a fixed block-causality p-value threshold of "
                           f"**{significance_threshold:.3f}**. "
                           f"Block-causality (Granger) sig. threshold: **{granger_thresh:.2f}** (VAR periods)._")

    mode_desc = _get_mode_description(config, analysis_mode)
    baseline_lines = []
    if track_heading:
        baseline_lines.append(f"### {track_heading}")
    baseline_lines.extend(
        [
            f"### Analysis Mode: `{analysis_mode.name}`",
            f"## Scenario: Target = {mapped_target_name} | Controls = {scenario_str}",
            methodology_str,
            f"#### Baseline (Point-Estimate) Results",
        ]
    )

    track_scope = str(first_candidate_data.get("mf_track_label", "")).strip().lower()
    if track_scope == "confirmatory":
        baseline_lines.append("_Interpretation scope: **Confirmatory claims**._")
    elif track_scope == "robustness":
        baseline_lines.append("_Interpretation scope: **Robustness/sensitivity only**._")
    elif track_scope == "exploratory":
        baseline_lines.append("_Interpretation scope: **Exploratory only (non-confirmatory)**._")
    if mode_desc:
        baseline_lines.append(mode_desc)

    table_lines, sorted_candidates = _build_summary_table_lines(point_estimate_stats, config, analysis_mode)
    baseline_lines.extend(table_lines)
    
    final_report_parts = ["\n".join(baseline_lines)]

    if include_plots and analysis_mode in [config.AnalysisMode.POSITIVE_CORRELATION, config.AnalysisMode.NEGATIVE_CORRELATION]:
        top_candidates_for_plot = [name for name, _ in sorted_candidates[:config.TOP_N_CANDIDATES_TO_PLOT]]
        dfs_to_plot = {c: point_estimate_dfs[c] for c in top_candidates_for_plot if c in point_estimate_dfs}
        if dfs_to_plot:
            mode_map = {config.AnalysisMode.NEGATIVE_CORRELATION: "neg", config.AnalysisMode.POSITIVE_CORRELATION: "pos"}
            mode_short_name = mode_map.get(analysis_mode, "analysis")
            clean_scenario = scenario_str.replace(" ", "_").replace("|", "")
            
            if config.SIGNIFICANCE_METHOD == config.SignificanceMethod.FDR:
                sig_prefix = f"a{int(config.FDR_ALPHA * 100)}"
                if _get_fdr_hypothesis_level(config, first_candidate_data) == "pair":
                    sig_prefix += "_hpair"
            else:
                p_val = stats.norm.sf(config.SCORING_T_STAT_THRESHOLD) * 2
                sig_prefix = f"t{int(p_val * 100)}"
            
            file_prefix = f"l{config.MAX_LAGS - 1}_{sig_prefix}"
            if "pca" in clean_scenario:
                file_prefix += f"_k{config.MAX_PCA_COMPONENTS}"

            base_filename = f"{report_prefix}{mode_short_name}_{file_prefix}_{target_variable}_{clean_scenario}"
            track_suffix = str(track_tag or "").strip().lower()
            if track_suffix == "factor_block":
                track_suffix = ""
            if track_suffix:
                base_filename = f"{base_filename}_{track_suffix}"
            
            # Generate main rolling plot
            plot_filename = f"{base_filename}.png"
            output_path = config.GRAPHS_DIR / plot_filename
            create_combined_rolling_plot(dfs_to_plot, target_variable, f"{mapped_target_name} ({scenario_str})", output_path, config, analysis_mode)

            # Keep only the regular rolling plot unless explicitly enabled.
            if getattr(config, "ENABLE_EXTRA_PLOTS", False):
                irf_output_path = config.GRAPHS_DIR / f"{base_filename}_IRF.png"
                create_aggregated_irf_plot(dfs_to_plot, target_variable, f"{mapped_target_name} ({scenario_str})", irf_output_path, config)

                vecm_output_path = config.GRAPHS_DIR / f"{base_filename}_VECM_summary.png"
                create_vecm_summary_plot(dfs_to_plot, target_variable, f"{mapped_target_name} ({scenario_str})", vecm_output_path, config)


    if qs_run_stats:
        table_header, table_divider = table_lines[0], table_lines[1]
        qs_summary_lines = ["\n#### Quantile-Sampled (QS) Run Range", "_Shows the [Min, Max] range for each metric across selected QS runs._", table_header, table_divider]
        for i, (candidate, _) in enumerate(sorted_candidates[:config.TOP_N_CANDIDATES_FOR_SUMMARY]):
            rank = i + 1
            mapped_candidate_name = _get_mapped_name(candidate, config)
            candidate_qs_stats = [run_stats.get(candidate, {}) for run_stats in qs_run_stats]
            def get_range_str(stat_name, fmt_str):
                vals = [s.get(stat_name) for s in candidate_qs_stats if s and s.get(stat_name) is not None and not np.isnan(s.get(stat_name))]
                if not vals: return "---"
                return f"[{np.min(vals):{fmt_str}}, {np.max(vals):{fmt_str}}]"
            def get_percentage_range_str(num_key, den_key):
                ratios = []
                for s in candidate_qs_stats:
                    if s:
                        num, den = s.get(num_key, 0), s.get(den_key, 0)
                        if den > 0: ratios.append(num / den)
                if not ratios: return "---"
                return f"[{np.min(ratios):.1%}, {np.max(ratios):.1%}]"
            score_range, vecm_pct_range = get_range_str('score', '.2f'), get_percentage_range_str('vecm_periods_count', 'total_windows_count')
            avg_alpha_range, std_alpha_range = get_range_str('avg_alpha', '.3f'), get_range_str('std_alpha', '.3f')
            c_on_t_range, t_on_c_range = get_percentage_range_str('sig_C_on_T_count', 'var_periods_count'), get_percentage_range_str('sig_T_on_C_count', 'var_periods_count')
            avg_corr_range = get_range_str('avg_corr', '.3f')
            qs_summary_lines.append(f"| {rank} | {mapped_candidate_name} | {score_range} | {vecm_pct_range} | {avg_alpha_range} | {std_alpha_range} | {c_on_t_range} | {t_on_c_range} | {avg_corr_range} |")
        final_report_parts.append("\n".join(qs_summary_lines))
    
    return "\n\n".join(final_report_parts)

def generate_robustness_section(strict_stats, target_variable, scenario_str, config, analysis_mode, strict_threshold):
    if not strict_stats:
        return ""

    p_val = stats.norm.sf(abs(strict_threshold)) * 2
    mode_desc = _get_mode_description(config, analysis_mode)
    section_title = f"#### Robustness Check (|t| ≥ {strict_threshold:.2f})"
    methodology_str = (f"_**Methodology**: Results were re-scored using a fixed t-statistic threshold of "
                       f"**{strict_threshold:.3f}** (corresponds to a p-value of {p_val:.3f})._")

    section_lines = [section_title, methodology_str]
    if mode_desc:
        section_lines.append(mode_desc)

    table_lines, _ = _build_summary_table_lines(strict_stats, config, analysis_mode)
    section_lines.extend(table_lines)

    return "\n".join(section_lines)


def generate_overall_least_correlated_report(baseline_scores, qs_scores, config, scenario_str):
    lines = [
        f"### Analysis Mode: `{config.AnalysisMode.LEAST_CORRELATED.name}`",
        f"## System-Wide Independence Ranking | Controls: {scenario_str}",
        "Sectors ranked by their average independence score against all other sectors. A higher score indicates greater independence. QS range shown in brackets.",
        "| Rank | Sector | Independence Score |",
        "|:----:|:---|:---:|",
    ]
    sorted_baseline = sorted(baseline_scores.items(), key=lambda item: item[1], reverse=True)

    for i, (sector, baseline_score) in enumerate(sorted_baseline):
        rank = i + 1
        mapped_name = _get_mapped_name(sector, config)
        qs_range_str = ""
        if qs_scores:
            sector_qs_vals = [run.get(sector) for run in qs_scores if run and run.get(sector) is not None]
            if sector_qs_vals:
                min_val, max_val = np.min(sector_qs_vals), np.max(sector_qs_vals)
                qs_range_str = f" [{min_val:.2f}, {max_val:.2f}]"
        
        lines.append(f"| {rank} | {mapped_name} | {baseline_score:.2f}{qs_range_str} |")

    return "\n".join(lines)


def generate_multivariate_fevd_report(baseline_fevd_results, qs_fevd_results, target_variable, top_candidates, config, scenario_str, analysis_mode, report_prefix=""):
    if baseline_fevd_results is None:
        return "Skipping multivariate FEVD: Baseline calculation failed or had insufficient data."
    
    mapped_target = _get_mapped_name(target_variable, config)
    
    # --- This plotting section was commented out in the original file, so it is left as such ---
    # fevd_data = []
    # model_vars = top_candidates + [target_variable]
    # for sector in model_vars:
    #     if sector in baseline_fevd_results:
    #          fevd_data.append({'sector': _get_mapped_name(sector, config),
    #                            'percentage': baseline_fevd_results[sector]})
    # df = pd.DataFrame(fevd_data).sort_values('percentage', ascending=True)
    # fig, ax = plt.subplots(figsize=(10, 6))
    # ax.barh(df['sector'], df['percentage'], color='cornflowerblue')
    # ax.set_xlabel('Percentage of Forecast Error Explained (%)')
    # title = f'FEVD for {mapped_target} (Baseline)'
    # ax.set_title(title)
    # ax.grid(True, axis='x', linestyle='--', alpha=0.6)
    
    # mode_map = {AnalysisMode.NEGATIVE_CORRELATION: "neg", AnalysisMode.POSITIVE_CORRELATION: "pos"}
    # mode_short_name = mode_map.get(analysis_mode, "analysis")
    # clean_scenario = scenario_str.replace(" ", "_").replace("|", "").replace(":", "")
    # output_path = config.GRAPHS_DIR / f"{report_prefix}{mode_short_name}_fevd_multivariate_{target_variable}_{clean_scenario}.png"

    # plt.savefig(output_path, dpi=config.GRAPH_DPI, bbox_inches='tight')
    # plt.close(fig)
    # print(f"   ✅ Multivariate FEVD plot saved to '{output_path}'")

    lines = [
        f"#### Multivariate FEVD: Explaining the Forecast Error of {mapped_target}",
        f"_Based on a standardized VAR model with the top {config.MULTIVARIATE_TOP_N} candidates. QS range shown in brackets._",
        "| Explanatory Sector | Contribution (%) |",
        "|:---|:---:|",
    ]
    model_vars = top_candidates + [target_variable]
    for sector in model_vars:
        mapped_name = _get_mapped_name(sector, config)
        baseline_pct = baseline_fevd_results.get(sector, np.nan)
        baseline_str = f"{baseline_pct:.2f}%" if pd.notna(baseline_pct) else "N/A"
        qs_range_str = ""
        if qs_fevd_results:
            qs_vals = [res.get(sector, np.nan) for res in qs_fevd_results]
            qs_vals = [v for v in qs_vals if pd.notna(v)]
            if qs_vals:
                min_val, max_val = np.min(qs_vals), np.max(qs_vals)
                qs_range_str = f" [{min_val:.2f}%, {max_val:.2f}%]"
        lines.append(f"| {mapped_name} | {baseline_str}{qs_range_str} |")
    return "\n".join(lines)

def generate_exog_sensitivity_report(pe_results, bs_results, target, candidate, config):
    mapped_target = _get_mapped_name(target, config)
    mapped_candidate = _get_mapped_name(candidate, config)
    lines = [
        f"#### Exogenous Variable Sensitivity: {mapped_target} vs. {mapped_candidate}",
        f"_This table shows exogenous variables ranked by their ability to reduce residual correlation. QS range [Min, Max] shown for significant variables._",
        "| Exogenous Variable | Residual Corr. | Change from Baseline | Granger p-val (C -> T) |",
        "|:---|:---:|:---:|:---:|"
    ]
    baseline_results = pe_results.get('Baseline (No Controls)')
    if not baseline_results: return ""
    base_corr = baseline_results.get('corr', 0)
    def format_sensitivity_stat(exog_name, stat_key, fmt_str):
        pe_val = pe_results.get(exog_name, {}).get(stat_key)
        pe_str = f"{pe_val:{fmt_str}}" if pe_val is not None and not np.isnan(pe_val) else "---"
        if not bs_results: return pe_str
        bs_vals = [run.get(exog_name, {}).get(stat_key) for run in bs_results.values() if run.get(exog_name, {}).get(stat_key) is not None]
        if not bs_vals: return pe_str
        bs_str = f"[{np.min(bs_vals):{fmt_str}}, {np.max(bs_vals):{fmt_str}}]" if len(bs_vals) > 1 else f"[{bs_vals[0]:{fmt_str}}]"
        return f"{pe_str} {bs_str}"
    all_rows = []
    for exog, values in pe_results.items():
        if exog == 'Baseline (No Controls)': continue
        if values: # Ensure there are results for this variable
            # Calculate the change in the *absolute* correlation
            change_in_corr = abs(values.get('corr', base_corr)) - abs(base_corr)
            all_rows.append({
                'name': _get_mapped_name(exog, config),
                'raw_name': exog,
                'change': change_in_corr
            })
    # Sort all variables by the change they produced, from most reductive to least.
    sorted_data = sorted(all_rows, key=lambda x: x['change'])
    for row in sorted_data:
        corr_str = format_sensitivity_stat(row['raw_name'], 'corr', '.3f')
        pval_str = format_sensitivity_stat(row['raw_name'], 'pval', '.3f')
        change_str = f"{row['change']:+.3f}"
        lines.append(f"| {row['name']} | {corr_str} | {change_str} | {pval_str} |")
    return "\n".join(lines)

def plot_driver_response_divergence(dr_results_pe, driver, responders, config, output_path):
    """
    Creates a multi-line time-series plot of coefficients (Driver -> Responder) for all responders.
    Significant periods are highlighted with thicker, solid lines; insignificant are thinner/transparent.
    """
    if not dr_results_pe:
        return

    fig, ax = plt.subplots(figsize=(18, 8))
    
    # Determine significance threshold based on config
    if config.SIGNIFICANCE_METHOD == config.SignificanceMethod.FDR:
        sig_threshold = config.FDR_ALPHA
        sig_type_str = f"FDR < {sig_threshold}"
    else:
        # Convert t-stat threshold back to p-value for the DR p-value column
        sig_threshold = stats.norm.sf(config.SCORING_T_STAT_THRESHOLD) * 2
        sig_type_str = f"p < {sig_threshold:.3f}"

    mapped_driver = _get_mapped_name(driver, config)
    colors = plt.cm.tab10(np.linspace(0, 1, len(responders)))

    for i, responder in enumerate(responders):
        df = dr_results_pe.get(responder)
        if df is None or df.empty:
            continue
            
        mapped_responder = _get_mapped_name(responder, config)
        color = colors[i]
        
        # Data - Use standardized coefficients for transform-invariant comparisons
        coefs = df['driver_to_responder_std'] if 'driver_to_responder_std' in df.columns else df['driver_to_responder_coef']
        pvals = df['driver_to_responder_pval']
        dates = df.index

        # Plot "Insignificant" background line (thin, transparent)
        ax.plot(dates, coefs, color=color, alpha=0.3, linewidth=1, label='_nolegend_')

        # Plot "Significant" segments (thick, solid)
        # We mask values where p-value > threshold to leave gaps, then plot over
        sig_coefs = coefs.copy()
        sig_coefs[pvals > sig_threshold] = np.nan
        
        ax.plot(dates, sig_coefs, color=color, alpha=1.0, linewidth=2.5, label=mapped_responder)

    ax.set_title(f'Driver-Response Divergence: Impact of {mapped_driver}', fontsize=16, weight='bold')
    ax.set_ylabel(f'Std. Coefficient ({mapped_driver} → Responder)')
    ax.set_xlabel('Window End Date')
    ax.axhline(0, color='black', linestyle='--', linewidth=1, alpha=0.5)
    
    # Legend and Formatting
    ax.legend(title="Responders (Bold = Significant)", bbox_to_anchor=(1.02, 1), loc='upper left')
    ax.grid(True, linestyle=':', alpha=0.6)
    ax.xaxis.set_major_locator(mdates.YearLocator(2))
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y'))
    
    plt.tight_layout()
    plt.savefig(output_path, dpi=config.GRAPH_DPI, bbox_inches='tight')
    plt.close(fig)
    print(f"✅ Divergence plot saved to '{output_path}'")


def plot_driver_response_fanchart(dr_results_pe, dr_results_qs, driver, responders, config, output_path):
    """
    Creates a grid of robustness fan charts (one subplot per responder) in a single file.
    Each subplot shows: Baseline coefficient vs QS Min/Max range.
    """
    # Filter for responders that actually have data
    valid_responders = [r for r in responders if dr_results_pe.get(r) is not None and not dr_results_pe.get(r).empty]
    if not valid_responders:
        return

    num_plots = len(valid_responders)
    
    # Determine Grid Size (2 columns)
    if num_plots == 1:
        cols = 1
        rows = 1
        fig_width = 10
    else:
        cols = 2
        rows = (num_plots + 1) // 2
        fig_width = 18

    fig, axes = plt.subplots(rows, cols, figsize=(fig_width, 4 * rows), sharex=True)
    
    # Normalize axes object to a flat list for easy iteration
    if num_plots == 1:
        axes = [axes]
    else:
        axes = axes.flatten()

    mapped_driver = _get_mapped_name(driver, config)
    
    # Hide unused subplots if total plots is odd and > 1
    for i in range(num_plots, len(axes)):
        axes[i].axis('off')

    for i, responder in enumerate(valid_responders):
        ax = axes[i]
        df_pe = dr_results_pe[responder]
        mapped_responder = _get_mapped_name(responder, config)

        # Use standardized coef if available, else fall back to raw (backwards compat)
        coef_col = 'driver_to_responder_std' if 'driver_to_responder_std' in df_pe.columns else 'driver_to_responder_coef'

        # 1. Prepare QS Data (Min/Max per date)
        qs_min = []
        qs_max = []
        valid_dates = []
        
        # Optimization: Pre-fetch relevant QS dataframes for this responder
        relevant_qs_dfs = [run[responder] for run in dr_results_qs if responder in run]

        for dt in df_pe.index:
            vals = []
            # Check this date in the pre-filtered QS frames
            for qs_df in relevant_qs_dfs:
                if dt in qs_df.index:
                    # Use same column logic for QS
                    qs_coef_col = 'driver_to_responder_std' if 'driver_to_responder_std' in qs_df.columns else 'driver_to_responder_coef'
                    vals.append(qs_df.loc[dt, qs_coef_col])
            
            if vals:
                qs_min.append(np.min(vals))
                qs_max.append(np.max(vals))
                valid_dates.append(dt)
            else:
                qs_min.append(np.nan)
                qs_max.append(np.nan)
                valid_dates.append(dt)

        # 2. Plot Fan (Range or Analytic CI)
        has_qs = (dr_results_qs and valid_dates and any(pd.notna(x) for x in qs_min))
        
        if has_qs:
            ax.fill_between(valid_dates, qs_min, qs_max, color='gray', alpha=0.2, label='QS Range [Min, Max]')
        else:
            # Fallback to Analytic CI if SE is available
            se_col = 'driver_to_responder_std_se'
            if se_col in df_pe.columns:
                coefs = df_pe[coef_col]
                ses = df_pe[se_col]
                # 95% CI = +/- 1.96 * SE
                ci_lower = coefs - 1.96 * ses
                ci_upper = coefs + 1.96 * ses
                
                # Filter out NaNs to avoid plotting issues
                valid_idx = df_pe.index[coefs.notna() & ses.notna()]
                if not valid_idx.empty:
                     ax.fill_between(valid_idx, ci_lower.loc[valid_idx], ci_upper.loc[valid_idx], 
                                     color='orange', alpha=0.15, label='Analytic 95% CI')

        # 3. Plot Baseline
        ax.plot(df_pe.index, df_pe[coef_col], color='#1f77b4', linewidth=2, label='Baseline (Std)')

        ax.set_title(f'{mapped_responder}', fontsize=12, weight='bold')
        ax.axhline(0, color='black', linestyle=':', linewidth=1)
        
        # Only show legend on the first plot to avoid clutter
        if i == 0:
            ax.legend(loc='upper left', fontsize=10, frameon=True)
            
        ax.grid(True, linestyle='--', alpha=0.5)
        ax.xaxis.set_major_locator(mdates.YearLocator(2))
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y'))

    fig.suptitle(f'Robustness Fan Charts: Response to {mapped_driver}', fontsize=16, weight='bold', y=1.00 + (0.02 if rows > 2 else 0.05))
    plt.tight_layout()
    plt.savefig(output_path, dpi=config.GRAPH_DPI, bbox_inches='tight')
    plt.close(fig)
    print(f"✅ Combined fan charts saved to '{output_path}'")

def plot_driver_response_coef_corr(dr_results_pe, driver, responders, config, output_path):
    """
    Creates a grid of plots (one per responder) in a single file.
    Each plot uses Dual Axes:
      - Left Axis: Residual & Spearman Correlations (Orange/Red Lines, Black Axis)
      - Right Axis: Driver -> Responder Coefficient (Blue Line, Black Axis)
    Shading indicates the significance of the Coefficient.
    """
    # Filter for responders that have data
    valid_responders = [r for r in responders if dr_results_pe.get(r) is not None and not dr_results_pe.get(r).empty]
    if not valid_responders:
        return

    num_plots = len(valid_responders)
    
    # Determine Grid Size (2 columns)
    if num_plots == 1:
        cols = 1
        rows = 1
        fig_width = 12
        fig_height = 6
    else:
        cols = 2
        rows = (num_plots + 1) // 2
        fig_width = 20
        fig_height = 5 * rows

    fig, axes = plt.subplots(rows, cols, figsize=(fig_width, fig_height), sharex=True)
    
    # Normalize axes object to a flat list
    if num_plots == 1:
        axes = [axes]
    else:
        axes = axes.flatten()

    # Hide unused subplots
    for i in range(num_plots, len(axes)):
        axes[i].axis('off')

    mapped_driver = _get_mapped_name(driver, config)
    
    # Threshold Logic
    if config.SIGNIFICANCE_METHOD == config.SignificanceMethod.FDR:
        sig_threshold = config.FDR_ALPHA
    else:
        sig_threshold = stats.norm.sf(config.SCORING_T_STAT_THRESHOLD) * 2

    # --- Colors ---
    POS_COLOR = '#e0cc84'      # Yellowish (Positive Sig Shading)
    NEG_COLOR = '#a5bac9'      # Blue-greyish (Negative Sig Shading)
    
    COEF_LINE_COLOR = 'dodgerblue'   # Changed to Blue
    PEARSON_LINE_COLOR = '#ff7f0e' # Orange
    SPEARMAN_LINE_COLOR = '#d62728' # Red/Pink
    
    AXIS_LABEL_COLOR = 'black' # All axes text/ticks in black

    for i, responder in enumerate(valid_responders):
        ax1 = axes[i]      # Left Axis (Correlation)
        ax2 = ax1.twinx()  # Right Axis (Coefficient)
        
        df = dr_results_pe[responder]
        mapped_responder = _get_mapped_name(responder, config)

        # --- Data (Use standardized for transform-invariant comparison) ---
        coefs = df['driver_to_responder_std'] if 'driver_to_responder_std' in df.columns else df['driver_to_responder_coef']
        pvals = df['driver_to_responder_pval']
        res_corr = df['residual_corr']
        spearman = df['spearman']

        # --- Shading Logic (Applied to ax1, covers whole background) ---
        is_sig = pvals < sig_threshold
        pos_sig = is_sig & (coefs > 0)
        neg_sig = is_sig & (coefs <= 0)

        for idx in df.index[pos_sig]:
            loc = df.index.get_loc(idx)
            start = df.index[loc-1] if loc > 0 else idx
            ax1.axvspan(start, idx, facecolor=POS_COLOR, alpha=0.4, edgecolor='none')
            
        for idx in df.index[neg_sig]:
            loc = df.index.get_loc(idx)
            start = df.index[loc-1] if loc > 0 else idx
            ax1.axvspan(start, idx, facecolor=NEG_COLOR, alpha=0.4, edgecolor='none')

        # --- Plotting ---
        
        # LEFT AXIS: Correlations (Lines = Orange/Red, Axis = Black)
        l1, = ax1.plot(df.index, res_corr, color=PEARSON_LINE_COLOR, linewidth=1.5, alpha=0.9, label='Resid. Corr (Left)')
        l2, = ax1.plot(df.index, spearman, color=SPEARMAN_LINE_COLOR, linewidth=1.5, linestyle='--', alpha=0.8, label='Spearman (Left)')
        
        ax1.set_ylabel('Correlation', color=AXIS_LABEL_COLOR, fontsize=10)
        ax1.tick_params(axis='y', labelcolor=AXIS_LABEL_COLOR)
        ax1.axhline(0, color=AXIS_LABEL_COLOR, linestyle=':', linewidth=0.8, alpha=0.5)
        ax1.set_ylim(-1.05, 1.05) # Correlation is bounded

        # RIGHT AXIS: Coefficient (Line = Blue, Axis = Black)
        l3, = ax2.plot(df.index, coefs, color=COEF_LINE_COLOR, linewidth=2.0, label='Coefficient (Right)')
        
        ax2.set_ylabel('Std. Coefficient', color=AXIS_LABEL_COLOR, fontsize=10, rotation=270, labelpad=15)
        ax2.tick_params(axis='y', labelcolor=AXIS_LABEL_COLOR)
        ax2.axhline(0, color=AXIS_LABEL_COLOR, linestyle='-', linewidth=1, alpha=0.3)

        # Title & Grid
        ax1.set_title(f'{mapped_driver} → {mapped_responder}', fontsize=12, weight='bold')
        ax1.grid(True, axis='x', linestyle='--', alpha=0.5)

        # Legend (Only on the first plot to avoid clutter)
        if i == 0:
            lines = [l1, l2, l3]
            labels = [l.get_label() for l in lines]
            # Add shading patches manually
            labels += ['Sig. Positive Coef', 'Sig. Negative Coef']
            lines += [Patch(facecolor=POS_COLOR, alpha=0.4), Patch(facecolor=NEG_COLOR, alpha=0.4)]
            
            ax1.legend(lines, labels, loc='upper left', fontsize=9, framealpha=0.9)

        # X-Axis formatting
        ax1.xaxis.set_major_locator(mdates.YearLocator(2))
        ax1.xaxis.set_major_formatter(mdates.DateFormatter('%Y'))

    # Global Title
    fig.suptitle(f'Driver-Response Dynamics: {mapped_driver}\n(Left Axis: Correlation | Right Axis: Coefficient)', 
                 fontsize=16, weight='bold', y=1.00 if rows > 1 else 1.05)
    
    plt.tight_layout()
    plt.savefig(output_path, dpi=config.GRAPH_DPI, bbox_inches='tight')
    plt.close(fig)
    print(f"✅ Combined Coef/Corr panel saved to '{output_path}'")
# R→D (RESPONDER → DRIVER) PLOT FUNCTIONS
# These show how each component predicts M2 changes.

def plot_responder_to_driver_divergence(dr_results_pe, driver, responders, config, output_path):
    """Creates time-series of R→D standardized coefficients."""
    if not dr_results_pe:
        return
    sample_df = next((df for df in dr_results_pe.values() if df is not None and not df.empty), None)
    if sample_df is None or 'responder_to_driver_std' not in sample_df.columns:
        print('Warning: R→D columns not found')
        return
    fig, ax = plt.subplots(figsize=(18, 8))
    if config.SIGNIFICANCE_METHOD == config.SignificanceMethod.FDR:
        sig_threshold = config.FDR_ALPHA
    else:
        sig_threshold = stats.norm.sf(config.SCORING_T_STAT_THRESHOLD) * 2
    mapped_driver = _get_mapped_name(driver, config)
    colors = plt.cm.tab10(np.linspace(0, 1, len(responders)))
    for i, responder in enumerate(responders):
        df = dr_results_pe.get(responder)
        if df is None or df.empty or 'responder_to_driver_std' not in df.columns:
            continue
        mapped_responder = _get_mapped_name(responder, config)
        coefs = df['responder_to_driver_std']
        pvals = df['responder_to_driver_pval']
        ax.plot(df.index, coefs, color=colors[i], alpha=0.3, linewidth=1)
        sig_coefs = coefs.copy()
        sig_coefs[pvals > sig_threshold] = np.nan
        ax.plot(df.index, sig_coefs, color=colors[i], alpha=1.0, linewidth=2.5, label=mapped_responder)
    ax.set_title(f'Responder → Driver Analysis: Component → {mapped_driver}', fontsize=16, weight='bold')
    ax.set_ylabel(f'Standardized Coef (Resp→Driver)')
    ax.axhline(0, color='black', linestyle='--', linewidth=1)
    ax.legend(bbox_to_anchor=(1.02, 1), loc='upper left')
    ax.grid(True, linestyle=':', alpha=0.6)
    ax.xaxis.set_major_locator(mdates.YearLocator(2))
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y'))
    plt.tight_layout()
    plt.savefig(output_path, dpi=config.GRAPH_DPI, bbox_inches='tight')
    plt.close(fig)
    print(f"R2D Divergence saved to '{output_path}'")


def plot_responder_to_driver_coef_corr(dr_results_pe, driver, responders, config, output_path):
    """Creates grid of R→D plots showing component → M2 relationship."""
    sample_df = next((df for df in dr_results_pe.values() if df is not None and not df.empty), None)
    if sample_df is None or 'responder_to_driver_std' not in sample_df.columns:
        print('Warning: R→D columns not found')
        return
    valid_responders = [r for r in responders if dr_results_pe.get(r) is not None and not dr_results_pe.get(r).empty]
    if not valid_responders:
        return
    num_plots = len(valid_responders)
    cols = 1 if num_plots == 1 else 2
    rows = 1 if num_plots == 1 else (num_plots + 1) // 2
    fig, axes = plt.subplots(rows, cols, figsize=(20 if cols>1 else 12, 5*rows), sharex=True)
    if num_plots == 1:
        axes = [axes]
    else:
        axes = axes.flatten()
    for i in range(num_plots, len(axes)):
        axes[i].axis('off')
    mapped_driver = _get_mapped_name(driver, config)
    sig_threshold = config.FDR_ALPHA if config.SIGNIFICANCE_METHOD == config.SignificanceMethod.FDR else stats.norm.sf(config.SCORING_T_STAT_THRESHOLD) * 2
    for i, responder in enumerate(valid_responders):
        ax1 = axes[i]
        ax2 = ax1.twinx()
        df = dr_results_pe[responder]
        mapped_responder = _get_mapped_name(responder, config)
        coefs = df['responder_to_driver_std']
        pvals = df['responder_to_driver_pval']
        is_sig = pvals < sig_threshold
        for idx in df.index[is_sig & (coefs > 0)]:
            loc = df.index.get_loc(idx)
            start = df.index[loc-1] if loc > 0 else idx
            ax1.axvspan(start, idx, facecolor='#90EE90', alpha=0.4)
        for idx in df.index[is_sig & (coefs <= 0)]:
            loc = df.index.get_loc(idx)
            start = df.index[loc-1] if loc > 0 else idx
            ax1.axvspan(start, idx, facecolor='#FFB6C1', alpha=0.4)
        ax1.plot(df.index, df['residual_corr'], color='#ff7f0e', linewidth=1.5, label='Resid Corr')
        ax1.plot(df.index, df['spearman'], color='#d62728', linewidth=1.5, linestyle='--', label='Spearman')
        ax1.set_ylabel('Correlation')
        ax1.set_ylim(-1.05, 1.05)
        ax2.plot(df.index, coefs, color='darkgreen', linewidth=2.0, label='R→D Coef')
        ax2.set_ylabel(f'{mapped_responder}→{mapped_driver}')
        ax2.axhline(0, color='gray', linestyle=':')
        ax1.set_title(f'{mapped_responder} → {mapped_driver}', fontsize=12, weight='bold')
        ax1.grid(True, axis='x', linestyle='--', alpha=0.5)
        if i == 0:
            ax1.legend(loc='upper left', fontsize=9)
        ax1.xaxis.set_major_locator(mdates.YearLocator(2))
        ax1.xaxis.set_major_formatter(mdates.DateFormatter('%Y'))
    fig.suptitle(f'Responder → Driver: Component → {mapped_driver} (Green=Pos. Impact, Pink=Neg. Impact)', fontsize=16, weight='bold', y=1.02)
    plt.tight_layout()
    plt.savefig(output_path, dpi=config.GRAPH_DPI, bbox_inches='tight')
    plt.close(fig)
    print(f"R2D Coef/Corr saved to '{output_path}'")
