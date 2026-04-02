#!/usr/bin/env Rscript

parse_args <- function(argv, default_root) {
  out <- list(root = default_root, fail_fast = TRUE)
  i <- 1L
  while (i <= length(argv)) {
    key <- argv[[i]]
    if (key == "--root" && i < length(argv)) {
      out$root <- argv[[i + 1L]]
      i <- i + 2L
      next
    }
    if (key == "--no-fail-fast") {
      out$fail_fast <- FALSE
      i <- i + 1L
      next
    }
    stop(sprintf("Unknown/invalid argument: %s", key), call. = FALSE)
  }
  out$root <- normalizePath(out$root, winslash = "/", mustWork = TRUE)
  out
}

has_prefix <- function(x, prefix) {
  if (length(x) < length(prefix)) return(FALSE)
  identical(as.integer(x[seq_along(prefix)]), as.integer(prefix))
}

binary_reason <- function(path) {
  info <- file.info(path)
  if (!is.finite(info$size) || info$size <= 0L) return(NULL)
  raw <- readBin(path, what = "raw", n = info$size)
  if (length(raw) == 0L) return(NULL)

  if (any(raw == as.raw(0x00))) return("contains NUL byte(s)")

  sigs <- list(
    list(name = "ELF", bytes = as.raw(c(0x7F, 0x45, 0x4C, 0x46))),
    list(name = "Mach-O", bytes = as.raw(c(0xCF, 0xFA, 0xED, 0xFE))),
    list(name = "Mach-O", bytes = as.raw(c(0xFE, 0xED, 0xFA, 0xCF))),
    list(name = "Mach-O", bytes = as.raw(c(0xCE, 0xFA, 0xED, 0xFE))),
    list(name = "Mach-O", bytes = as.raw(c(0xFE, 0xED, 0xFA, 0xCE))),
    list(name = "ZIP", bytes = as.raw(c(0x50, 0x4B, 0x03, 0x04))),
    list(name = "PNG", bytes = as.raw(c(0x89, 0x50, 0x4E, 0x47))),
    list(name = "PDF", bytes = as.raw(c(0x25, 0x50, 0x44, 0x46))),
    list(name = "GZIP", bytes = as.raw(c(0x1F, 0x8B)))
  )
  for (sig in sigs) {
    if (has_prefix(raw, sig$bytes)) {
      return(sprintf("binary signature detected (%s)", sig$name))
    }
  }

  ints <- as.integer(raw)
  printable <- ints %in% c(9L, 10L, 13L) | (ints >= 32L & ints <= 126L) | ints >= 128L
  non_text_ratio <- mean(!printable)
  if (is.finite(non_text_ratio) && non_text_ratio > 0.30) {
    return(sprintf("suspicious non-text byte ratio=%.2f", non_text_ratio))
  }

  NULL
}

main <- function() {
  self <- normalizePath(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE)[1L]), winslash = "/", mustWork = TRUE)
  default_root <- dirname(dirname(self))
  args <- parse_args(commandArgs(trailingOnly = TRUE), default_root = default_root)

  files <- list.files(args$root, pattern = "\\.R$", recursive = TRUE, full.names = TRUE, include.dirs = FALSE)
  files <- files[!grepl("/out/|/\\.git/", files)]
  files <- sort(unique(normalizePath(files, winslash = "/", mustWork = FALSE)))

  message(sprintf("[preflight] root=%s", args$root))
  message(sprintf("[preflight] files=%d", length(files)))

  findings <- list()
  for (f in files) {
    reason <- binary_reason(f)
    if (is.null(reason)) next
    findings[[length(findings) + 1L]] <- list(path = f, reason = reason)
    message(sprintf("[FAIL] parse_preflight path=%s reason=%s", f, reason))
    if (isTRUE(args$fail_fast)) {
      message("[preflight] aborting on first failure (fail-fast)")
      quit(status = 1L)
    }
  }

  if (length(findings) > 0L) {
    message(sprintf("[preflight] failures=%d", length(findings)))
    quit(status = 1L)
  }

  message("[PASS] parse preflight clean")
}

if (sys.nframe() == 0L) main()
