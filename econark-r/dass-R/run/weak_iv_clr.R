weak_iv_clr_proxy <- function(beta, se, first_stage_f, min_first_stage_f = 10) {
  min_f <- suppressWarnings(as.numeric(min_first_stage_f))
  if (!is.finite(min_f) || min_f <= 0) min_f <- 10

  weak_flag <- !is.finite(first_stage_f) || (as.numeric(first_stage_f) < min_f)
  inflate <- 1
  if (weak_flag) {
    if (is.finite(first_stage_f) && as.numeric(first_stage_f) > 0) {
      inflate <- sqrt(min_f / as.numeric(first_stage_f))
    } else {
      inflate <- 2
    }
  }

  se_adj <- as.numeric(se) * inflate
  z_adj <- if (is.finite(se_adj) && se_adj > 0) as.numeric(beta) / se_adj else NA_real_
  p_clr <- if (is.finite(z_adj)) 2 * stats::pnorm(abs(z_adj), lower.tail = FALSE) else NA_real_
  ci_low_clr <- as.numeric(beta) - 1.96 * se_adj
  ci_high_clr <- as.numeric(beta) + 1.96 * se_adj

  list(
    weak_iv_method = "se_inflation_proxy",
    weak_iv_flag = weak_flag,
    first_stage_f = as.numeric(first_stage_f),
    min_first_stage_f = min_f,
    proxy_se = se_adj,
    proxy_p = p_clr,
    proxy_ci_low = ci_low_clr,
    proxy_ci_high = ci_high_clr
  )
}
