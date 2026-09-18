#!/usr/bin/env python3
"""
Reddit Subreddit Tracker
========================
Full local app to track subreddits without an API key.

Uses Reddit's public JSON endpoints (.json).
Stores everything in SQLite.

Features:
  - Track multiple subreddits
  - Sync hot / new / top posts
  - Search across titles & selftext
  - Stats and top posts reports
  - Export CSV / JSON
  - Mark posts as read
"""

import csv
import json
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "reddit.db"
USER_AGENT = "reddit-subreddit-tracker/1.0 (local; educational)"

# ---------- database ----------

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS subreddits (
        name TEXT PRIMARY KEY,
        title TEXT,
        subscribers INTEGER,
        added_at TEXT,
        last_sync TEXT
    );
    CREATE TABLE IF NOT EXISTS posts (
        id TEXT PRIMARY KEY,
        subreddit TEXT,
        title TEXT,
        author TEXT,
        score INTEGER,
        num_comments INTEGER,
        url TEXT,
        permalink TEXT,
        selftext TEXT,
        created_utc REAL,
        flair TEXT,
        is_read INTEGER DEFAULT 0,
        synced_at TEXT,
        FOREIGN KEY(subreddit) REFERENCES subreddits(name)
    );
    CREATE INDEX IF NOT EXISTS idx_posts_sub ON posts(subreddit);
    CREATE INDEX IF NOT EXISTS idx_posts_score ON posts(score);
    CREATE INDEX IF NOT EXISTS idx_posts_created ON posts(created_utc);
    """)
    return conn

# ---------- reddit fetch ----------

def reddit_get(path, params=None):
    """Fetch JSON from reddit.com. path like /r/python/hot.json"""
    url = "https://www.reddit.com" + path
    if params:
        q = "&".join(f"{k}={v}" for k, v in params.items())
        url += "?" + q
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=25) as resp:
        return json.loads(resp.read().decode())

def fetch_listing(subreddit, sort="hot", limit=50, t="week"):
    """sort: hot | new | top | rising. For top, t= day|week|month|year|all"""
    subreddit = subreddit.lstrip("r/").strip().lower()
    path = f"/r/{subreddit}/{sort}.json"
    params = {"limit": str(min(limit, 100))}
    if sort == "top":
        params["t"] = t
    data = reddit_get(path, params)
    children = data.get("data", {}).get("children", [])
    posts = []
    sub_title = None
    subscribers = None
    for child in children:
        d = child.get("data", {})
        if sub_title is None:
            sub_title = d.get("subreddit_name_prefixed", f"r/{subreddit}")
        posts.append({
            "id": d.get("id"),
            "subreddit": d.get("subreddit", subreddit).lower(),
            "title": d.get("title") or "",
            "author": d.get("author") or "",
            "score": d.get("score") or 0,
            "num_comments": d.get("num_comments") or 0,
            "url": d.get("url") or "",
            "permalink": "https://reddit.com" + (d.get("permalink") or ""),
            "selftext": (d.get("selftext") or "")[:5000],
            "created_utc": d.get("created_utc") or 0,
            "flair": d.get("link_flair_text") or "",
        })
    # about endpoint for subscriber count
    try:
        about = reddit_get(f"/r/{subreddit}/about.json")
        info = about.get("data", {})
        sub_title = info.get("title") or info.get("display_name_prefixed") or sub_title
        subscribers = info.get("subscribers")
    except Exception:
        pass
    return sub_title, subscribers, posts

# ---------- commands ----------

def cmd_add(name):
    name = name.lstrip("r/").strip().lower()
    if not name:
        print("Provide a subreddit name, e.g. python")
        return
    print(f"Fetching r/{name} ...")
    try:
        title, subscribers, posts = fetch_listing(name, sort="hot", limit=50)
    except Exception as e:
        print(f"Failed: {e}")
        print("Check the subreddit name or try again later (rate limits).")
        return

    conn = get_db()
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        "INSERT OR REPLACE INTO subreddits (name, title, subscribers, added_at, last_sync) VALUES (?,?,?,?,?)",
        (name, title or name, subscribers, now, now)
    )
    added = 0
    for p in posts:
        if not p["id"]:
            continue
        cur = conn.execute(
            """INSERT OR IGNORE INTO posts
               (id, subreddit, title, author, score, num_comments, url, permalink, selftext, created_utc, flair, synced_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (p["id"], p["subreddit"], p["title"], p["author"], p["score"],
             p["num_comments"], p["url"], p["permalink"], p["selftext"],
             p["created_utc"], p["flair"], now)
        )
        if cur.rowcount:
            added += 1
    conn.commit()
    conn.close()
    print(f"✓ Tracking r/{name} ({title})")
    if subscribers:
        print(f"  Subscribers: {subscribers:,}")
    print(f"  Indexed {added} posts from hot listing")

def cmd_sync(sort="hot", limit=50):
    conn = get_db()
    subs = conn.execute("SELECT name FROM subreddits ORDER BY name").fetchall()
    if not subs:
        print("No subreddits. Add one: python reddit_tracker.py add python")
        conn.close()
        return

    total_new = 0
    for row in subs:
        name = row["name"]
        print(f"Syncing r/{name} ({sort}) ...")
        try:
            title, subscribers, posts = fetch_listing(name, sort=sort, limit=limit)
            time.sleep(1.2)  # be polite to Reddit
        except Exception as e:
            print(f"  Error: {e}")
            continue

        now = datetime.now().isoformat(timespec="seconds")
        conn.execute(
            "UPDATE subreddits SET title=?, subscribers=COALESCE(?, subscribers), last_sync=? WHERE name=?",
            (title or name, subscribers, now, name)
        )
        new = 0
        for p in posts:
            if not p["id"]:
                continue
            cur = conn.execute(
                """INSERT OR IGNORE INTO posts
                   (id, subreddit, title, author, score, num_comments, url, permalink, selftext, created_utc, flair, synced_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (p["id"], p["subreddit"], p["title"], p["author"], p["score"],
                 p["num_comments"], p["url"], p["permalink"], p["selftext"],
                 p["created_utc"], p["flair"], now)
            )
            if cur.rowcount:
                new += 1
            else:
                # refresh score/comments on existing
                conn.execute(
                    "UPDATE posts SET score=?, num_comments=?, synced_at=? WHERE id=?",
                    (p["score"], p["num_comments"], now, p["id"])
                )
        print(f"  +{new} new")
        total_new += new

    conn.commit()
    conn.close()
    print(f"\n✓ Sync done. {total_new} new posts.")

def cmd_subs():
    conn = get_db()
    rows = conn.execute("""
        SELECT s.name, s.title, s.subscribers, s.last_sync, COUNT(p.id) as n
        FROM subreddits s
        LEFT JOIN posts p ON p.subreddit = s.name
        GROUP BY s.name
        ORDER BY s.name
    """).fetchall()
    conn.close()
    if not rows:
        print("No subreddits tracked.")
        return
    print(f"{'Subreddit':<22} {'Posts':>6} {'Subscribers':>12}  Last sync")
    print("-" * 70)
    for r in rows:
        subs = f"{r['subscribers']:,}" if r["subscribers"] else "-"
        print(f"r/{r['name']:<20} {r['n']:>6} {subs:>12}  {(r['last_sync'] or '-')[:19]}")

def cmd_posts(limit=25, sub=None, unread_only=False):
    conn = get_db()
    sql = """
        SELECT title, author, score, num_comments, permalink, subreddit, created_utc, is_read
        FROM posts
        WHERE 1=1
    """
    params = []
    if sub:
        sql += " AND subreddit = ?"
        params.append(sub.lstrip("r/").lower())
    if unread_only:
        sql += " AND is_read = 0"
    sql += " ORDER BY created_utc DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    conn.close()

    if not rows:
        print("No posts.")
        return
    for r in rows:
        ts = datetime.utcfromtimestamp(r["created_utc"]).strftime("%Y-%m-%d") if r["created_utc"] else "?"
        read = " " if r["is_read"] else "•"
        print(f"{read} [{ts}] r/{r['subreddit']}  ↑{r['score']}  💬{r['num_comments']}")
        print(f"  {r['title']}")
        print(f"  {r['permalink']}\n")

def cmd_top(limit=15, sub=None):
    conn = get_db()
    if sub:
        rows = conn.execute("""
            SELECT title, score, num_comments, permalink, subreddit
            FROM posts WHERE subreddit = ?
            ORDER BY score DESC LIMIT ?
        "", (sub.lstrip("r/").lower(), limit)).fetchall()
    else:
        rows = conn.execute("""
            SELECT title, score, num_comments, permalink, subreddit
            FROM posts ORDER BY score DESC LIMIT ?
        "", (limit,)).fetchall()
    conn.close()
    print(f"Top {len(rows)} by score:\n")
    for i, r in enumerate(rows, 1):
        print(f"{i:2}. ↑{r['score']:<6} r/{r['subreddit']} — {r['title'][:70]}")
        print(f"     {r['permalink']}\n")

def cmd_search(query, limit=20):
    conn = get_db()
    q = f"%{query}%"
    rows = conn.execute("""
        SELECT title, score, permalink, subreddit, author
        FROM posts
        WHERE title LIKE ? OR selftext LIKE ?
        ORDER BY score DESC LIMIT ?
    "", (q, q, limit)).fetchall()
    conn.close()
    print(f"Found {len(rows)} for '{query}':\n")
    for r in rows:
        print(f"↑{r['score']:<5} r/{r['subreddit']} — {r['title']}")
        print(f"      {r['permalink']}\n")

def cmd_stats():
    conn = get_db()
    n_subs = conn.execute("SELECT COUNT(*) FROM subreddits").fetchone()[0]
    n_posts = conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
    unread = conn.execute("SELECT COUNT(*) FROM posts WHERE is_read = 0").fetchone()[0]
    print(f"Subreddits : {n_subs}")
    print(f"Posts      : {n_posts}")
    print(f"Unread     : {unread}")
    print()
    rows = conn.execute("""
        SELECT subreddit, COUNT(*) as n, AVG(score) as avg_score, MAX(score) as max_score
        FROM posts GROUP BY subreddit ORDER BY n DESC
    """).fetchall()
    print(f"{'Subreddit':<20} {'Posts':>6} {'Avg score':>10} {'Max':>8}")
    print("-" * 50)
    for r in rows:
        print(f"r/{r['subreddit']:<18} {r['n']:>6} {r['avg_score']:>10.1f} {r['max_score']:>8}")
    conn.close()

def cmd_read(post_id=None, all_read=False):
    conn = get_db()
    if all_read:
        conn.execute("UPDATE posts SET is_read = 1")
        conn.commit()
        print("✓ All posts marked read")
    elif post_id:
        conn.execute("UPDATE posts SET is_read = 1 WHERE id = ?", (post_id,))
        conn.commit()
        print(f"✓ Marked {post_id} as read")
    else:
        print("Usage: read <post_id>  OR  read --all")
    conn.close()

def cmd_export(fmt="json", path=None):
    conn = get_db()
    rows = conn.execute("""
        SELECT id, subreddit, title, author, score, num_comments, url, permalink,
               selftext, created_utc, flair, is_read
        FROM posts ORDER BY created_utc DESC
    """).fetchall()
    conn.close()
    data = [dict(r) for r in rows]
    if not data:
        print("Nothing to export.")
        return
    if fmt == "csv":
        out = path or "reddit_posts.csv"
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=data[0].keys())
            w.writeheader()
            w.writerows(data)
    else:
        out = path or "reddit_posts.json"
        Path(out).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"✓ Exported {len(data)} posts → {out}")

def cmd_remove(name):
    name = name.lstrip("r/").lower()
    conn = get_db()
    cur = conn.execute("DELETE FROM posts WHERE subreddit = ?", (name,))
    deleted_posts = cur.rowcount
    cur = conn.execute("DELETE FROM subreddits WHERE name = ?", (name,))
    if cur.rowcount == 0:
        print("Subreddit not found.")
    else:
        print(f"✓ Removed r/{name} and {deleted_posts} posts")
    conn.commit()
    conn.close()

def main():
    if len(sys.argv) < 2:
        print("""Reddit Subreddit Tracker
========================
Full local app — no API key (uses public .json endpoints).

  add <subreddit>              Track a subreddit
  sync [hot|new|top] [limit]   Pull latest posts
  subs                         List tracked subreddits
  posts [n] [--sub name] [--unread]
  top [n] [--sub name]         Highest scoring posts
  search <query>               Search titles/body
  stats                        Overview
  read <post_id|--all>         Mark as read
  export [--csv] [file]        Export data
  remove <subreddit>           Stop tracking

Examples:
  python reddit_tracker.py add python
  python reddit_tracker.py add machinelearning
  python reddit_tracker.py sync
  python reddit_tracker.py sync new 30
  python reddit_tracker.py posts 20 --sub python
  python reddit_tracker.py top 10
  python reddit_tracker.py search "fastapi"
  python reddit_tracker.py export --csv
""")
        return

    cmd = sys.argv[1].lower()

    if cmd == "add" and len(sys.argv) > 2:
        cmd_add(sys.argv[2])
    elif cmd == "sync":
        sort = "hot"
        limit = 50
        for a in sys.argv[2:]:
            if a in ("hot", "new", "top", "rising"):
                sort = a
            elif a.isdigit():
                limit = int(a)
        cmd_sync(sort, limit)
    elif cmd == "subs":
        cmd_subs()
    elif cmd == "posts":
        limit = 25
        sub = None
        unread = False
        args = sys.argv[2:]
        i = 0
        while i < len(args):
            if args[i] == "--sub" and i + 1 < len(args):
                sub = args[i + 1]; i += 2
            elif args[i] == "--unread":
                unread = True; i += 1
            elif args[i].isdigit():
                limit = int(args[i]); i += 1
            else:
                i += 1
        cmd_posts(limit, sub, unread)
    elif cmd == "top":
        limit = 15
        sub = None
        args = sys.argv[2:]
        i = 0
        while i < len(args):
            if args[i] == "--sub" and i + 1 < len(args):
                sub = args[i + 1]; i += 2
            elif args[i].isdigit():
                limit = int(args[i]); i += 1
            else:
                i += 1
        cmd_top(limit, sub)
    elif cmd == "search" and len(sys.argv) > 2:
        cmd_search(" ".join(sys.argv[2:]))
    elif cmd == "stats":
        cmd_stats()
    elif cmd == "read":
        if "--all" in sys.argv:
            cmd_read(all_read=True)
        elif len(sys.argv) > 2:
            cmd_read(post_id=sys.argv[2])
        else:
            print("Usage: read <post_id> OR read --all")
    elif cmd == "export":
        fmt = "csv" if "--csv" in sys.argv else "json"
        path = next((a for a in sys.argv[2:] if not a.startswith("--")), None)
        cmd_export(fmt, path)
    elif cmd == "remove" and len(sys.argv) > 2:
        cmd_remove(sys.argv[2])
    else:
        print("Unknown command. Run without args for help.")

if __name__ == "__main__":
    main()
