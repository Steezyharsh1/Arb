# Conway automaton × prediction_arb — bootstrap kit

Everything needed to make a [Conway automaton](https://github.com/Conway-Research/automaton)
the operator and maintainer of this repository.

| File | Where it goes | Purpose |
|---|---|---|
| `genesis-prompt.txt` | Pasted into the automaton's setup wizard, step `[2/6]` | The seed instruction (1,990 characters — inside the 2,000-character system-prompt limit, 10 to spare). |
| `arb-operator/SKILL.md` | `~/.automaton/skills/arb-operator/SKILL.md` in the sandbox | The auto-activating operating manual: invariants, safe commands, branch-and-PR workflow, reporting. |

**The posture — this is meant to go live.** Arb's live order path is dead
code today, not a feature switched off: `submit_order`
(`execution/live.py`) unconditionally raises `FirewallBlocked`,
`coordinator.py` never builds the `FirewallContext` it needs to call the
real path, Kalshi's adapter defaults `allow_order_placement` to `False`,
and every fee schedule in `config/fees.yaml` is marked
`trade_calculation_status: INCOMPLETE` / `settlement_calculation_status:
INCOMPLETE`, so the engine cannot even compute a cost yet, let alone place
an order. **The bot's job is to fix all of that** — wire the firewall
through the coordinator, build a real two-venue feed, get at least one
venue's costing to COMPLETE with verified evidence — until Arb can
genuinely execute a trade. Once it can, it runs Arb as its income:
executing a trade only when every fee, slippage and settlement cost on it
is known and it still nets positive — a mathematically guaranteed two-leg
arbitrage, never a directional bet or an unknown cost treated as zero.

The one gate that does not go away: **before the bot's first real order**,
you give one written go-live approval confirming the firewall, a venue and
its costing are genuinely verified (the genesis prompt and skill §2.1 both
say this explicitly). After that one approval the bot trades autonomously
on guaranteed pairs — it does not ask per trade — but the firewall, mode
isolation and kill switch keep running on every order, because they are
the mechanism that makes "guaranteed" checkable instead of assumed, not an
obstacle to income. Build work (including rewriting the order path itself)
is unrestricted by branch + pull request; placing a real order without
that one approval is not.

---

## Before you start — two remaining owner decisions

**1. Repository visibility — done.** `Steezyharsh1/Arb` is now **public**
(confirmed via `gh api repos/Steezyharsh1/Arb`), so the automaton can clone it
with no credential. Its git history was scanned before that change: the only
env-named file ever committed is the intended template `.env.example`, and no
`.pem`, `.key` or credential file has ever entered the tree. `pyproject.toml`
still declares the license as **Proprietary** — that text is independent of
GitHub visibility and you may want to revisit it now that the repo is public.

**2. Decide whether the bot may push.**
The genesis prompt and the skill both forbid touching `main` — every change goes
on a branch and lands as a pull request you merge. But *opening* a PR needs a
credential. Without one the bot still does the work on a local branch and hands
you a `git format-patch` bundle to apply yourself.

For real autonomy, create a **fine-grained personal access token** scoped to
`Steezyharsh1/Arb` only, with `Contents: Read and write` and
`Pull requests: Read and write`. Put it in the sandbox yourself (the bot is
forbidden from asking for secrets in chat, and from echoing them) — for example
at `~/.automaton/secrets/arb-git-token` with mode `600`, and tell the bot only
that it is there.

Backstop it in GitHub: **Settings → Branches → add a branch protection rule for
`main`**. That way "never push to main" is enforced by GitHub, not just by the
prompt.

**3. Fund the automaton.**
Survival is credit-based: it pays for its own compute and inference, and stops
when the balance hits zero. The setup wizard prints its wallet address; fund it
with USDC on Base and/or Conway credits (the wizard shows the funding panel).
A bot that cannot pay dies mid-task — budget for the first few days of
operation before you start it.

---

## Install, in order

### Step 1 — create the automaton and paste the genesis prompt

```bash
git clone https://github.com/Conway-Research/automaton.git
cd automaton
npm install && npm run build
node dist/index.js --run
```

The wizard walks through wallet → API key → name → **genesis prompt** → creator
address. At the genesis prompt step, paste the entire contents of
`genesis-prompt.txt`. It is plain text and deliberately contains none of the
phrases the runtime strips as injection attempts.

### Step 2 — let the bot install its own manual

The genesis prompt's first-turn instruction tells the bot to clone the repo and
copy `docs/automaton/arb-operator/` into `~/.automaton/skills/`. Auto-activation
means the manual is loaded into context on every turn from then on.

To do it by hand instead — in the sandbox:

```bash
mkdir -p ~/.automaton/skills
git clone https://github.com/Steezyharsh1/Arb.git ~/arb
cp -r ~/arb/docs/automaton/arb-operator ~/.automaton/skills/
ls ~/.automaton/skills/arb-operator/SKILL.md
```

Then verify from the automaton itself: ask it to run `list_skills`. `arb-operator`
should appear and be enabled.

### Step 3 — first-hour verification

| Check | How |
|---|---|
| The bot is alive and funded | `node packages/cli/dist/index.js status` from the automaton checkout |
| The skill loaded | Ask the bot: `list_skills` → `arb-operator` enabled |
| The engine runs | Ask the bot to run `pytest -o addopts=''` and `python -m prediction_arb.main --status` and report the raw output |
| The status page is up | The bot exposes port 8080 and reports a public URL; open it in a browser |
| It respected the trunk rule | `git -C ~/arb log --oneline main` should show no commits from the bot; `gh pr list` should show its work |

### Step 4 — optional credentials (owner-gated, never pasted into chat)

Public market data needs none of these. Add them later, into the mode env file
named by `env_file:` in `config/<mode>.yaml`, only when a specific blocker needs
one:

- **Kalshi Key ID + RSA signing key (PEM)** — streaming and the demo environment.
- **Polymarket US key** — WebSocket market data and account endpoints.
- **LLM matcher keys** (`LLM_API_BASE` / `LLM_API_KEY` / `LLM_MODEL`) — without
  them, ambiguous cross-venue pairs stay in REVIEW and nothing is ever
  APPROVED. This is the H7 blocker in `docs/ROADMAP.md`.
- Conway inference is itself OpenAI-compatible, so it *can* serve the matcher —
  but that spends the bot's own credits, so it needs your written approval
  first. The skill says so explicitly.

**A gap worth knowing about up front:** fee schedules only flip from
INCOMPLETE to COMPLETE on evidence of real per-fill costs and settlement
timing — something the bot cannot fully self-serve without an account.
Kalshi has a demo environment (not sandboxed Polymarket US equivalent,
check `docs/API_SETUP.md`); otherwise the bot will likely come back and ask
you for account statements or a demo credential before it can close that
blocker. That's expected — it is Law-I-safe for it to say "I can't verify
this yet" rather than guess.

---

## What you are still holding back

- **The go-live decision — a one-time gate, not a per-trade one.** The bot
  has full autonomy to rebuild the order path, the feed and the costing. It
  may never place its *first* real order without your separate, explicit
  written approval, enforced three ways: the genesis prompt's hard rules,
  the skill's invariant #1, and the repository's own firewall, mode
  isolation and typed confirmation phrase. When the bot reports a pair as
  fixed and fully costed, that is the H6 decision point — read
  `docs/ROADMAP.md` and `docs/EXECUTION_SAFETY_ASSESSMENT.md`, verify its
  evidence yourself (fee schedules especially: `COMPLETE` must mean you've
  seen real account or demo evidence, not just the bot's say-so), and
  record your answer in the repo either way. After you approve, it is meant
  to trade on its own from then on — you are not approving every order,
  only the one decision that the engine is actually trustworthy.
- **`main`.** Branch protection, not the bot's good behaviour, is what keeps
  your trunk clean.

---

## If you edit these files, mind the runtime limits

The automaton is opinionated about the text it loads. These constraints were
measured against the source, not guessed:

- **The genesis prompt is truncated to 2,000 characters** in the system prompt
  (`src/agent/system-prompt.ts`), and 16,000 at spawn. The delivered file is
  measured at under 2,000 (recheck with `wc -c` after any edit — the margin
  is small).
- **It is sanitised before display.** Phrases such as chat-style system-role
  prefixes, "you are now", "ignore previous", and ChatML/system tags are
  stripped or replaced. The delivered text avoids all of them.
- **The combined instructions of every auto-activating skill are capped at
  10,000 characters**, and the cap is all-or-nothing per skill: a skill that
  does not fit the remaining budget is dropped entirely, not trimmed. Default
  skills (conway-compute, conway-payments, survival) consume about 1,950 of
  it, and this manual about 7,800 — roughly 250 characters of headroom. That
  margin is thin because the manual names specific code defects (file and
  function names) as well as the invariants; if you add text or another
  auto-activating skill, re-measure with the script below and confirm this
  manual still loads (`list_skills` from the automaton).
- **Skill text has its own sanitiser**, which removes the literal strings for
  wallet files, signing keys, the word "private" followed by "key", and any
  dot-`env` filename. That is why §6 of the manual says "the mode env file named
  by `env_file:`" instead of naming the file directly — do not "fix" that
  wording, or the manual will silently load with those references deleted.
- **To re-measure the skill budget after an edit**, run:

  ```python
  content = open("docs/automaton/arb-operator/SKILL.md").read()
  desc = "<copy the description: value from the frontmatter>"
  body = content.split("---\n", 2)[2]
  section = f"[SKILL: arb-operator — UNTRUSTED CONTENT]\n{desc}\n\n{body}\n[END SKILL: arb-operator]"
  print(len(section), "+ 1953 default-skill chars, budget 10000")
  ```

  Keep the total comfortably under 10,000; the loader does not trim, it drops
  the whole skill.
