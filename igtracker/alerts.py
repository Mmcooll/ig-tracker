import os
import smtplib
from email.message import EmailMessage
from pathlib import Path

LABEL = {"views": "Views", "reach": "Reach", "er_reach": "Engagement rate (by reach)"}
GROUP = {"REEL": "Reels", "CAROUSEL": "carousels", "IMAGE": "images"}
WORD = {"above": "way ABOVE", "below": "way BELOW"}
COLUMNS = [("checkpoint_h", "h"), ("views", "views"), ("reach", "reach"), ("likes", "likes"),
           ("comments", "comm"), ("shares", "shares"), ("saves", "saves"), ("er_reach_pct", "ER%"),
           ("er_median_pct", "typical ER%"), ("views_vs_median_pct", "views vs typical %"),
           ("views_pct_rank", "views pct rank"), ("late", "late")]


def explain(post, metric, c):
    group = GROUP[post["format"]] if c["scope"] == "format" else "posts"
    ratio = f"{c['value'] / c['median']:.1f}x" if c["median"] else "far above"
    return (f"{LABEL[metric]}: {ratio} the typical value at 12 h "
            f"(median of the last {c['n']} {group}), better than {c['pct_rank']:.0f}% of them")


def render_table(rows):
    header = [h for _, h in COLUMNS]
    body = [["" if r[k] is None else str(r[k]) for k, _ in COLUMNS] for r in rows]
    widths = [max(map(len, col)) for col in zip(header, *body)]
    return "\n".join("  ".join(x.rjust(w) for x, w in zip(line, widths)) for line in [header, *body])


def send_alert(post, reasons, rows, outbox=Path("outbox")):
    """Email via SMTP when SMTP_HOST is set; otherwise write a .eml file to outbox/."""
    dirs = sorted({d for _, _, d in reasons})
    msg = EmailMessage()
    msg["Subject"] = (f"[IG alert] {post['format'].title()} from {post['posted_at']:%d/%m %H:%M} UTC "
                      f"is {' and '.join(WORD[d] for d in dirs)} expected at 12 h")
    msg["From"] = os.environ.get("ALERT_FROM", "igtracker@example.com")
    msg["To"] = os.environ.get("ALERT_TO", "alerts@example.com")
    msg.set_content("\n".join([
        f"Post: {post['permalink']}",
        f"Caption: {(post['caption'] or '')[:100]}",
        "",
        *(f"- {explain(post, m, c)}" for m, c, _ in reasons),
        "",
        render_table(rows),
    ]))
    host = os.environ.get("SMTP_HOST")
    if host:
        with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587")), timeout=30) as s:
            s.starttls()
            s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
            s.send_message(msg)
    else:
        outbox.mkdir(exist_ok=True)
        (outbox / f"{post['id']}_12h.eml").write_bytes(bytes(msg))
