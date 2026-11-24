# CTD bottle matching contract

Ocean-data technicians provide 1..100 casts. Each cast contains 2..10000 strictly increasing CTD samples with globally unique sample IDs, nonnegative decimal-string depth, temperature and practical salinity. Values are converted to exact rational numbers; this small baseline deliberately does not handle negative temperatures, pressure-to-depth conversion, sensor calibration or geographic metadata.

Each bottle references one cast and supplies its observed closure depth. A closure exactly on a CTD sample inherits that sample. A closure between samples receives linear temperature and salinity interpolation only when the bracketing depth gap is no larger than max_gap_m. Closures beyond the sampled profile and closures across excessive gaps remain explicit business statuses instead of being silently extrapolated.

The result preserves bottle identity, source CTD sample IDs, interpolation gap and exact rational values. It is a structured Python data API for cruise data preparation, not a dashboard, instrument controller or generic CSV tool. Future oxygen or nutrient laboratory results can join through bottle ID without changing the CTD-source evidence.

