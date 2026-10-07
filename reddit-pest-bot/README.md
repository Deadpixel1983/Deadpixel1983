# Reddit pest-control helper bot

A small bot that watches pest-control subreddits for people asking questions, drafts a short, plain-spoken reply with Claude, and posts it from a Reddit account you control. By default nothing is posted until you approve it.

It is a standalone project. Copy this folder anywhere and it works on its own.

## How it works

1. `scan` pulls the newest posts from the subreddits in `config.yaml`, keeps the ones that look like a pest question (keyword match, text post, recent, not already busy), and asks Claude for a reply.
2. Claude is told to write like a regular on the subreddit: a few sentences, answers only what was asked, no lists, no headers, no "Great question". It also sees a handful of real upvoted replies from your subreddits as examples of the register (see `study` below). If the draft still reads like an AI essay, it is sent back once for a rewrite; if it still fails, the post is skipped.
3. Drafts sit in a local SQLite database. `review` lets you approve, edit, or reject each one in the terminal. `post` publishes the approved ones, spaced out to stay under Reddit's radar.
4. Every comment ends with a short disclosure line saying it is an automated account. Reddit's rules and most subreddits' rules require that.

The bot never includes links, never names a business, never gives pesticide dosages, and tells people to call a professional for termites, wall nests, venomous things, or anything involving kids, pets, or health.

## Setup

You need Python 3.10 or newer.

```bash
cd reddit-pest-bot
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env
```

### Reddit credentials

Use a dedicated Reddit account for the bot, not your personal one. Automated accounts that aren't disclosed get banned, and a ban would take your main account with it.

1. Log in as the bot account and open https://www.reddit.com/prefs/apps
2. Click "create another app", choose type **script**, give it a name, and set the redirect URI to `http://localhost:8080` (it is not used).
3. Copy the client ID (the string under the app name) and the secret into `.env`, along with the bot account's username and password.

Put a line in the bot account's profile saying it is an automated helper. Moderators look for that.

### Anthropic API key

Create a key at https://console.anthropic.com/ and put it in `.env` as `ANTHROPIC_API_KEY`.

### Check everything

```bash
pest-bot check
```

This logs into Reddit, confirms the key is set, and prints what the bot will watch.

## Step one: the one-time study

Before the first scan, run:

```bash
pest-bot study
```

This pulls the top posts of the past year from your configured subreddits, finds the ones that were questions, and saves the most upvoted human replies to `samples.yaml`. The bot shows those replies to Claude as examples of how people actually write there. It also prints a few stats (median length, how often people use lists, how often they start lowercase) so you can see what "human" looks like in these subs.

You only need to run it once. The file is reused by every scan after that. Re-run it every few months if you like, or point it at different subreddits to learn a more general Reddit voice:

```bash
pest-bot study --subreddits pestcontrol,HomeImprovement,AskReddit --posts 60
```

The repo ships with a few hand-written seed examples in `samples.yaml` so the bot has something to go on. The study replaces them with real ones.

## Day-to-day use

```bash
pest-bot scan              # fetch new posts, draft replies, store them as pending
pest-bot review            # approve / edit / reject each draft
pest-bot post              # publish approved drafts (rate limited)
pest-bot status            # counts by status
pest-bot show <post_id>    # see one record
```

Dry-run mode never posts. Use it to see what the bot would say:

```bash
pest-bot scan --dry-run
pest-bot post --dry-run
```

To keep drafting in the background:

```bash
pest-bot run --interval 600
```

In approval mode `run` only drafts. You still review and post by hand.

## Going automatic

Once you trust the drafts, set `approval_mode: false` in `config.yaml`. Then `scan` and `run` post immediately, subject to the rate limits (`max_comments_per_hour`, `min_seconds_between_comments`). Keep those low. A new account posting more than a few comments an hour gets shadowbanned.

## Configuration

Everything except credentials lives in `config.yaml`. The main knobs:

| Key | What it does |
|---|---|
| `subreddits` | Which subreddits to watch. Read each one's rules on bots first. |
| `keywords` / `exclude_keywords` | What counts as a pest question, and what to skip. |
| `max_post_age_hours`, `skip_if_comments_at_least` | Skip old or already-busy threads. |
| `approval_mode` | `true` holds drafts for review. `false` posts automatically. |
| `max_comments_per_hour`, `min_seconds_between_comments` | Posting rate limits. |
| `disclosure_footer` | The line appended to every comment. `{username}` is replaced. |
| `model`, `effort`, `max_tokens` | Claude settings. |

## Tests

```bash
pytest
```

The tests use fakes for Reddit and Claude, so they run offline.

## Things to know

- Reddit's API terms require bots to identify themselves in the user agent and to respect rate limits. The bot does both.
- Subreddit moderators can and do ban automated accounts, disclosed or not. Watch the bot account's inbox for modmail, and remove any subreddit that asks you to stop.
- Claude can be wrong. The approval step exists so a person reads every reply before it goes live. Pest advice has real consequences when it is wrong.
- The disclosure footer is on by default. You can blank it in the config, but doing so breaks Reddit's bot policy and most subreddit rules, and it is the fastest way to get the account banned.
