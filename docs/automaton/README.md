# Conway automaton × prediction_arb — bootstrap kit

Everything needed to make a [Conway automaton](https://github.com/Conway-Research/automaton)
the operator and maintainer of this repository.

| File | Where it goes | Purpose |
|---|---|---|
| `genesis-prompt.txt` | Pasted into the automaton's setup wizard, step `[2/6]` | The seed instruction (1,987 characters — inside the 2,000-character system-prompt limit, 13 to spare). |
| `arb-operator/SKILL.md` | `~/.automaton/skills/arb-operator/SKILL.md` in the sandbox | The auto-activating operating manual: invariants, safe commands, branch-and-PR workflow, reporting. |

**The posture:** Arb is unfinished. It is meant to become the bot's income,
but only honestly — by finishing the engine's missing pieces first (a real
two-venue feed, complete fee/settlement costing, an LLM matcher), then
executing only trades where every cost is known and the arbitrage is
mathematically guaranteed. It is never allowed to bet on an uncertain
outcome, and it is never allowed to place a real order without the creator's
separate, explicit written approval — those two rules do not expire once a
trade looks profitable. The genesis prompt and skill both say this in those
words; the fee-INCOMPLETE gates, the order firewall, mode isolation and the
kill switch are the mechanism that makes "guaranteed" checkable instead of
assumed, so they stay. Build work is unrestricted; approval to place a real
order is not.

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

---

## What you are still holding back

- **The go-live decision.** The bot can build toward a tradeable engine with
  full autonomy, but it may never place a real order without your separate,
  explicit written approval — even after it reports a pair as fully costed
  and guaranteed. That is enforced three ways: the genesis prompt's hard
  rules, the skill's invariant #1, and the repository's own firewall, mode
  isolation and typed confirmation phrase. When the bot tells you a pair is
  ready, that is the H6 decision point — read `docs/ROADMAP.md` and
  `docs/EXECUTION_SAFETY_ASSESSMENT.md`, verify its evidence yourself, and
  record your answer in the repo either way.
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
  it, and this manual about 7,800 — roughly 200-250 characters of headroom.
  That margin is intentionally thin because the manual now has to state the
  income/guarantee posture as well as the invariants; if you add text or
  another auto-activating skill, re-measure with the script below and confirm
  this manual still loads (`list_skills` from the automaton).
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
