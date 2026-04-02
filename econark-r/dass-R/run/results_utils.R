infer_family <- function(outcome) {
  o <- tolower(as.character(outcome))
  if (grepl("spread|yield|credit", o)) return("credit_spreads")
  if (grepl("m1|m2|money|liquidity", o)) return("money")
  if (grepl("cpi|pce|infl", o)) return("inflation")
  if (grepl("crowd|investment|private", o)) return("crowding_out")
  "other"
}
