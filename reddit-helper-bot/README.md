# Reddit helper bot

A bot that watches subreddits for people asking questions in topics you choose, drafts a short, plain-spoken reply with Claude, and posts it from a Reddit account you control. It ships configured for four niches (pest control, selling property, food hygiene, holidays), each with its own subreddits, keywords, persona, Reddit account and style samples. By default nothing is posted until you approve it.

It is a standalone project. Copy this folder anywhere and it works on its own.

## How it works

1. `scan` pulls the newest posts from every niche's subreddits, keeps the ones that look like a question for that niche (keyword match, text post, recent, not already busy), and asks Claude for a reply. A subreddit shared by two niches is fetched once; the first niche whose keywords match takes the post.
2. Claude gets the niche's persona (what it knows, what it must never say) plus a shared set of voice rules: write like a regular on the subreddit, a few sentences, answer only what was asked, no lists, no headers, no "Great question". It also sees real upvoted replies from those subreddits as examples of the register (see `study`). If a draft still reads like an AI essay it is sent back once for a rewrite; if it still fails, the post is skipped.
3. Drafts sit in a local SQLite database tagged with their niche and account. `review` lets you approve, edit, or reject each one. `post` publishes approved drafts from the right account, spaced out per account to stay under Reddit's radar.
4. Every comment ends with a short disclosure line saying it is an automated account. Reddit's rules and most subreddits' rules require that. Each niche can override the wording.

The bot never includes links, never names a business or brand as a recommendation, and each persona carries hard limits for its topic: no pesticide dosages, no legal or tax advice on property, no medical advice on food, no guarantees on visas.

## Setup

You need Python 3.10 or newer.

```bash
cd reddit-helper-bot
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env
```

### Reddit accounts

Each niche names the account that answers for it (`account: pest` in `config.yaml`). Use dedicated accounts for the bot, not your personal one, and put a line in each account's profile saying it is an automated helper. Moderators look for that.

For each account:

1. Log in as that account and open https://www.reddit.com/prefs/apps
2. Click "create another app", choose type **script**, give it a name, and set the redirect URI to `http://localhost:8080` (it is not used).
3. Copy the client ID (the string under the app name), the secret, and the account's username and password into `.env` under that account's prefix. Account `pest` reads `REDDIT_PEST_CLIENT_ID`, `REDDIT_PEST_CLIENT_SECRET`, `REDDIT_PEST_USERNAME`, `REDDIT_PEST_PASSWORD`. A niche with no `account` line uses the plain `REDDIT_*` variables.

Several niches can share one account. Rate limits are tracked per account, so a busy niche does not throttle the others.

Running several accounts is allowed by Reddit as long as each is disclosed and they never interact with each other's posts. Do not have them upvote or reply to one another, and do not try to hide that they are related. That is what Reddit treats as manipulation, and it gets every account removed at once.

### Anthropic API key

Create a key at https://console.anthropic.com/ and put it in `.env` as `ANTHROPIC_API_KEY`.

### Check everything

```bash
helper-bot check
```

This logs into every account, confirms the key is set, and lists each niche with its subreddit, keyword and sample counts. Add `--niche holidays` to check just one.

## Step one: the one-time study

Before the first scan, run:

```bash
helper-bot study
```

For each niche this pulls the top posts of the past year from its subreddits, finds the ones that were questions, and saves the most upvoted human replies to `samples/<niche>.yaml`. The bot shows those to Claude as examples of how people actually write there. It also prints a few stats per niche (median length, how often people use lists, how often they start lowercase).

You only need to run it once. The files are reused by every scan after that. Re-run it every few months if you like, or restrict it:

```bash
helper-bot --niche food_hygiene study --posts 60
helper-bot --niche holidays study --subreddits travel,AskReddit
```

The repo ships with a few hand-written seed examples per niche so the bot has something to go on. The study replaces them with real ones.

## Day-to-day use

```bash
helper-bot scan              # fetch new posts for every niche, draft replies, store as pending
helper-bot review            # approve / edit / reject each draft
helper-bot post              # publish approved drafts from each niche's account (rate limited)
helper-bot status            # counts by niche and status
helper-bot show <post_id>    # see one record
```

Every command takes `--niche <name>` (repeatable) to work on a subset:

```bash
helper-bot --niche pest_control --niche holidays scan
helper-bot --niche property_selling review
```

Dry-run mode never posts. Use it to see what the bot would say and from which account:

```bash
helper-bot scan --dry-run
helper-bot post --dry-run
```

To keep drafting in the background:

```bash
helper-bot run --interval 600
```

In approval mode `run` only drafts. You still review and post by hand.

## Going automatic

Once you trust the drafts, set `approval_mode: false` in `config.yaml`. Then `scan` and `run` post immediately, subject to the per-account rate limits (`max_comments_per_hour`, `min_seconds_between_comments`). Keep those low. A new account posting more than a few comments an hour gets shadowbanned.

## Adding a niche

Add a block under `niches:` in `config.yaml` with `subreddits`, `keywords`, `persona`, and optionally `account`, `exclude_keywords`, `disclosure_footer`, and `enabled: false` to pause it. The persona should say who the bot is for that topic and, in a "Never" sentence, what it must not do. Then run `helper-bot --niche <name> study` once.

## Configuration

Everything except credentials lives in `config.yaml`. The global knobs:

| Key | What it does |
|---|---|
| `approval_mode` | `true` holds drafts for review. `false` posts automatically. |
| `max_post_age_hours`, `skip_if_comments_at_least` | Skip old or already-busy threads. |
| `max_comments_per_hour`, `min_seconds_between_comments` | Posting rate limits, per account. |
| `disclosure_footer` | The line appended to every comment. `{username}` is replaced. Niches can override. |
| `model`, `effort`, `max_tokens` | Claude settings. |
| `niches` | One block per topic. See above. |

## Tests

```bash
pytest
```

The tests use fakes for Reddit and Claude, so they run offline.

## Things to know

- Reddit's API terms require bots to identify themselves in the user agent and to respect rate limits. The bot does both.
- Subreddit moderators can and do ban automated accounts, disclosed or not. Watch each account's inbox for modmail, and remove any subreddit that asks you to stop.
- Claude can be wrong. The approval step exists so a person reads every reply before it goes live. Advice about food safety, property sales and pests has real consequences when it is wrong.
- The disclosure footer is on by default. You can blank it, but doing so breaks Reddit's bot policy and most subreddit rules, and it is the fastest way to get the accounts banned.
