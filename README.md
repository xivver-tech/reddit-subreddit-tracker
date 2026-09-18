# Reddit Subreddit Tracker

A **full local app** to track subreddits — no Reddit API key required.

## Features
- Track multiple subreddits
- Sync **hot / new / top / rising** listings
- SQLite database
- Search titles & post body
- Top posts by score
- Unread tracking
- Stats per subreddit
- Export JSON / CSV

## Usage
```bash
# Track subreddits
python reddit_tracker.py add python
python reddit_tracker.py add machinelearning

# Sync posts
python reddit_tracker.py sync
python reddit_tracker.py sync new 40
python reddit_tracker.py sync top 25

# Browse
python reddit_tracker.py subs
python reddit_tracker.py posts 20
python reddit_tracker.py posts 15 --sub python --unread
python reddit_tracker.py top 10
python reddit_tracker.py search "fastapi"
python reddit_tracker.py stats

# Read state + export
python reddit_tracker.py read --all
python reddit_tracker.py export --csv

# Remove
python reddit_tracker.py remove python
```

Data is stored in `reddit.db`.

Uses Reddit’s public `.json` endpoints with a polite delay between requests.
