"""Pure statistics: median/MAD on a log scale, because Instagram metrics are right-skewed."""
import math
from statistics import median

MIN_N = 5          # fewer comparable posts than this: report numbers, never alert
Z_ALERT = 2.0      # |robust z| at or above this is "way above/below"
MAD_TO_SD = 1.4826  # makes MAD comparable to a standard deviation for normal data
EPS = 1e-4         # floor so log(0) never happens (0 views, 0 % ER)


def er(s):
    """Engagement rate by reach, as a fraction. None when reach is 0."""
    if not s["reach"]:
        return None
    return (s["likes"] + s["comments"] + s["shares"] + s["saves"]) / s["reach"]


def _log(x):
    return math.log(max(x, EPS))


def compare(value, baseline):
    """Where `value` sits within `baseline` (past values of the same metric)."""
    n = len(baseline)
    if value is None or n == 0:
        return dict(value=value, n=n, median=median(baseline) if n else None, mad=None, z=None, pct_rank=None)
    logs = [_log(x) for x in baseline]
    m = median(logs)
    mad = median(abs(x - m) for x in logs)
    z = (_log(value) - m) / (MAD_TO_SD * mad) if mad > 0 else None
    pct = 100 * (sum(x < value for x in baseline) + 0.5 * sum(x == value for x in baseline)) / n
    return dict(value=value, n=n, median=median(baseline), mad=mad, z=z, pct_rank=pct)


def direction(c):
    """'above' / 'below' / None for a compare() result."""
    if c["n"] < MIN_N or c["value"] is None or c["median"] is None:
        return None
    if c["z"] is not None:
        return "above" if c["z"] >= Z_ALERT else "below" if c["z"] <= -Z_ALERT else None
    # MAD = 0 (all baseline values identical): fall back to a plain ratio.
    if c["median"] == 0:
        return "above" if c["value"] > 0 else None
    ratio = c["value"] / c["median"]
    return "above" if ratio >= 2 else "below" if ratio <= 0.5 else None
