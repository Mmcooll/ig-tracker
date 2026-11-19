from pathlib import Path

import psycopg
from psycopg.rows import dict_row

BASELINE_SIZE = 20


def connect(url):
    return psycopg.connect(url, autocommit=True, row_factory=dict_row)


def init_db(conn):
    conn.execute((Path(__file__).parent / "schema.sql").read_text(encoding="utf-8"))


def upsert_posts(conn, posts, now):
    for p in posts:
        conn.execute(
            """INSERT INTO posts (id, format, posted_at, caption, permalink, first_seen_at)
               VALUES (%(id)s, %(format)s, %(posted_at)s, %(caption)s, %(permalink)s, %(now)s)
               ON CONFLICT (id) DO UPDATE SET caption = EXCLUDED.caption""",
            {**p, "now": now})


def posts_since(conn, since):
    return conn.execute("SELECT * FROM posts WHERE posted_at > %s ORDER BY posted_at", (since,)).fetchall()


def done_checkpoints(conn, post_id):
    rows = conn.execute("SELECT checkpoint_h FROM snapshots WHERE post_id = %s", (post_id,)).fetchall()
    return {r["checkpoint_h"] for r in rows}


def insert_snapshot(conn, s):
    return conn.execute(
        """INSERT INTO snapshots (post_id, checkpoint_h, taken_at, age_h, late,
                                  views, reach, likes, comments, shares, saves, followers)
           VALUES (%(post_id)s, %(checkpoint_h)s, %(taken_at)s, %(age_h)s, %(late)s, %(views)s, %(reach)s,
                   %(likes)s, %(comments)s, %(shares)s, %(saves)s, %(followers)s)
           ON CONFLICT DO NOTHING""", s).rowcount == 1


def baseline(conn, post, checkpoint_h, fmt):
    """On-time snapshots of the last 20 earlier posts (180 days max); fmt=None means all formats."""
    return conn.execute(
        """SELECT s.* FROM snapshots s JOIN posts p ON p.id = s.post_id
           WHERE s.checkpoint_h = %(cp)s AND NOT s.late
             AND p.posted_at < %(ts)s AND p.posted_at > %(ts)s - interval '180 days'
             AND (%(fmt)s::text IS NULL OR p.format = %(fmt)s)
           ORDER BY p.posted_at DESC LIMIT %(lim)s""",
        dict(cp=checkpoint_h, ts=post["posted_at"], fmt=fmt, lim=BASELINE_SIZE)).fetchall()


def insert_comparison(conn, c):
    conn.execute(
        """INSERT INTO comparisons (post_id, checkpoint_h, metric, value, scope, n, median, mad, z, pct_rank)
           VALUES (%(post_id)s, %(checkpoint_h)s, %(metric)s, %(value)s, %(scope)s, %(n)s,
                   %(median)s, %(mad)s, %(z)s, %(pct_rank)s)
           ON CONFLICT DO NOTHING""", c)


def insert_alert(conn, a):
    return conn.execute(
        """INSERT INTO alerts (post_id, checkpoint_h, direction, sent_at)
           VALUES (%(post_id)s, %(checkpoint_h)s, %(direction)s, %(sent_at)s)
           ON CONFLICT DO NOTHING""", a).rowcount == 1


def report_rows(conn, post_id):
    return conn.execute("SELECT * FROM v_post_report WHERE post_id = %s ORDER BY checkpoint_h", (post_id,)).fetchall()
