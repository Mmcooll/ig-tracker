from datetime import datetime, timedelta

from . import db, stats

CHECKPOINTS = (6, 12, 24, 48, 168)
ALERT_CHECKPOINT = 12
METRICS = ("views", "reach", "er_reach")
ALERT_METRICS = ("views", "er_reach")


def to_post(m):
    """API media dict -> posts row, or None for stories / unknown types."""
    if m.get("media_product_type") == "STORY":
        return None
    fmt = "REEL" if m.get("media_product_type") == "REELS" else {
        "CAROUSEL_ALBUM": "CAROUSEL", "IMAGE": "IMAGE", "VIDEO": "REEL"}.get(m.get("media_type"))
    if fmt is None:
        return None
    return dict(id=m["id"], format=fmt, posted_at=datetime.fromisoformat(m["timestamp"]),
                caption=m.get("caption"), permalink=m.get("permalink"))


def is_late(age_h, checkpoint_h):
    return age_h - checkpoint_h > max(1.0, 0.1 * checkpoint_h)


def metric_value(snap, metric):
    return stats.er(snap) if metric == "er_reach" else snap[metric]


def compare_snapshot(conn, post, snap, metric):
    cp = snap["checkpoint_h"]
    scope, base = "format", db.baseline(conn, post, cp, post["format"])
    if len(base) < stats.MIN_N:
        scope, base = "account", db.baseline(conn, post, cp, None)
    values = [v for b in base if (v := metric_value(b, metric)) is not None]
    c = stats.compare(metric_value(snap, metric), values)
    return dict(post_id=post["id"], checkpoint_h=cp, metric=metric, scope=scope, **c)


def run(conn, source, now, notify):
    """One tracker pass. Returns the number of snapshots taken. Safe to rerun."""
    db.upsert_posts(conn, filter(None, map(to_post, source.list_media(now))), now)
    followers = None
    taken, failed = 0, []
    for post in db.posts_since(conn, now - timedelta(hours=CHECKPOINTS[-1] + 6)):
        age = (now - post["posted_at"]).total_seconds() / 3600
        done = db.done_checkpoints(conn, post["id"])
        due = [cp for cp in CHECKPOINTS if cp <= age and cp not in done]
        if not due:
            continue
        cp = max(due)  # older missed checkpoints would just get today's numbers, so they stay empty
        if followers is None:
            followers = source.followers(now)
        ins = source.insights(post["id"], now)
        snap = dict(post_id=post["id"], checkpoint_h=cp, taken_at=now, age_h=age, late=is_late(age, cp),
                    views=ins["views"], reach=ins["reach"], likes=ins["likes"], comments=ins["comments"],
                    shares=ins["shares"], saves=ins["saved"], followers=followers)
        reasons = []
        with conn.transaction():  # one transaction per post
            if not db.insert_snapshot(conn, snap):
                continue
            comps = {m: compare_snapshot(conn, post, snap, m) for m in METRICS}
            for c in comps.values():
                db.insert_comparison(conn, c)
            if cp == ALERT_CHECKPOINT and not snap["late"]:
                # Only alert against the same format: a normal image looks "way below" next to Reels.
                reasons = [(m, comps[m], d) for m in ALERT_METRICS
                           if comps[m]["scope"] == "format" and (d := stats.direction(comps[m]))]
                alert = dict(post_id=post["id"], checkpoint_h=cp, sent_at=now,
                             direction=",".join(sorted({d for _, _, d in reasons})))
                if not (reasons and db.insert_alert(conn, alert)):
                    reasons = []
        taken += 1
        # Email after the commit, so a mail failure neither loses the data nor stops the other posts.
        if reasons:
            try:
                notify(post, reasons, db.report_rows(conn, post["id"]))
            except Exception as e:  
                failed.append(f"{post['id']}: {e!r}")
    if failed:  # fail the run so GitHub emails the repo owner
        raise RuntimeError(f"{taken} snapshot(s) saved, but alert email failed for " + "; ".join(failed))
    return taken


def simulate(conn, source, start, end, notify):
    """Replay history: run only at the moments a checkpoint falls due (7 min past, like the cron)."""
    posts = filter(None, map(to_post, source.list_media(end, limit=None)))
    times = sorted({t for p in posts for cp in CHECKPOINTS
                    if start <= (t := p["posted_at"] + timedelta(hours=cp, minutes=7)) <= end})
    return sum(run(conn, source, t, notify) for t in times)
