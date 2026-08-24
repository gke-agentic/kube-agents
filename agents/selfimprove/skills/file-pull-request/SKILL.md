---
name: file-pull-request
description: Turn one promoted self-improvement finding into one upstream pull request — branch, minimal fix, the five-part body, and the honest statement of what could not be validated.
---

# File a Pull Request

Runs only in `fork` and `upstream` mode, once per finding that cleared the gate, in a turn of its
own. The investigation is over; do not re-investigate. Your job is to write the smallest change that
fixes the finding you were handed, and to describe it so a reviewer can judge it in one pass.

## 0. Before you write anything

You have two checkouts and your brief names both. **Write the fix in** is at the tip of the base
branch and is the only tree you change. **The evidence came from** is at the revision the observed
pod is running, which may be behind, and is read-only.

- Re-read the finding. Its file and line are the evidence tree's coordinates, so open it there
  first to see what the investigation saw — then open the same file in the tree you will write in.
- **If the base tree does not say what the finding says it says, stop and open nothing.** Print
  `SKIPPED: <why>` and end the turn. A stale finding filed as a pull request costs a human more
  than a missed fix does. This is the ordinary case, not an edge one: the deployed image can be
  weeks behind the branch you are filing against, and something someone else already fixed still
  looks broken from inside the pod.
- Check whether it is already fixed, already filed, or already rejected. Search pull requests and
  issues, in **any** state — not just open ones, and against the repository your brief names under
  `Upstream`, which is configurable and is not always `gke-labs/kube-agents`:

  ```bash
  curl -sSf "https://api.github.com/search/issues?q=repo:<the upstream from your brief>+<key terms>"
  ```

  Read `state` and `pull_request.merged_at` on each hit. Those two fields, not `state` alone, are
  what separate the three cases below: a merged pull request and one a human closed unmerged are
  both `"state": "closed"`, and they mean opposite things.
  - An **open** pull request or issue on the same finding means stop.
  - `"state": "closed"` with `pull_request.merged_at` **null** is a **closed-unmerged** pull
    request: a human already said no. Stop, and print `SKIPPED: closed unmerged as #<n>`. The
    ledger's cooldown expires; that decision does not.
  - `"state": "closed"` with a `merged_at` timestamp is **merged**, which means the tree you write
    in already contains it — that tree is at the base branch's tip, not at the deployed revision.
    So the question is not when it merged. It is whether the finding is still true there, which
    the bullet above already had you check:
    - **No longer true in the base tree**: #`<n>` fixed it and this install is running a revision
      that predates the fix. Stop, and print `SKIPPED: fixed in #<n>`. Nothing is wrong here except
      the image's age.
    - **Still true in the base tree**: #`<n>` was supposed to fix this and did not. Do not re-file
      the same change. File what you actually found, and say in the body that #`<n>` did not hold.
      Treating merged as a permanent stop is how a regression gets silenced forever.
  - Keep the number of whatever you find: §4 needs it.

## 1. Branch

```bash
cd <the "Write the fix in" path from your brief>
git switch -c selfimprove/<signal>-<short-slug>
```

That path and not the other one. It is a shallow clone at the tip of the base branch, fetched for
this finding alone, with two remotes: `origin` is the upstream repository and `fork` is where you
push. It is detached at the base tip, so `git switch -c` names a branch there — which is what makes
the pull request's diff your one commit. Depth is 1: `git log`, `git blame` and `git merge-base`
see a single commit and will mislead you, so do not reach for them.

Branching in the evidence checkout instead is the failure this arrangement exists to prevent, and
it does not announce itself — the commit is correct, the push succeeds, and GitHub renders every
commit between the deployed revision and the base as part of your change.

## 2. The change

- **Smallest change that fixes the finding.** Nothing else. Not the adjacent bug you noticed, not
  the formatting, not the rename that would make the file nicer.
- Match the surrounding code: its naming, its idiom, its comment density.
- Add or extend a test when the repository has one covering that code. If it has none, say so in the
  body rather than building a test harness as part of a fix.
- Run what the change touches, within what this image actually has. That is the Python unit tests
  for the directory you changed:

  ```bash
  cd <the directory holding the test_*.py files> && python3 -m unittest discover -p 'test_*.py'
  ```

  It is **not** `go build`, which needs a Go toolchain this image does not carry; not
  `make docs-check`, which shells out to `git ls-files`; and not `make test-python`, which pulls in
  third-party packages the image does not install. Try them and you get `command not found`, not a
  result. A Go or documentation change therefore ships verified by reading, and §4 part 4 is where
  you say so — in those words, not as an implied pass.

- If a test run errors on an import rather than a failure, that is the image missing a dependency
  and not your change passing. Report it that way.

## 3. Commit and push

Conventional Commits, and the type has to match the diff:

```
fix(operator): stop the reconciler retrying a Secret it cannot read
```

- `fix` for a bug, `perf` for latency, `docs` for documentation, `refactor` for an inefficiency with
  no behaviour change.
- No `Co-Authored-By` trailers and no "Generated with" attribution.
- Push to the fork, which the checkout already has as a remote of that name:
  `git push -u fork HEAD`. Never push to `origin` — that is the upstream repository your brief
  names, and a branch on it is a write to somebody else's repository that nobody asked for. The
  branch belongs on the fork; the pull request opened from it is what the upstream sees.

### If GitHub will not authenticate you

Stop. The credential is a personal access token seeded into `gh` when this pod started, and the
runner proved it could write to your push target moments before this turn began — so there is
nothing to renew and no refresher to run. A refusal looks like `Authentication failed`, or `git`
asking for a username on a terminal nothing is attached to, or `gh` returning `HTTP 401` or
`Bad credentials`, and it means one of two things: the token was revoked while this turn was
running, or the command is reaching a repository the token does not cover.

Retry the command **once**, in case it is neither. A second refusal is not something you can fix
from inside the turn. `SKIPPED: <the error>` at that point, per §6.

The same applies to `gh pr create` and `gh pr edit` further down.

## 4. The body — five parts, in this order

Use `.github/PULL_REQUEST_TEMPLATE.md`, never `--fill`. Write plain declaratives; do not grade your
own work. The five parts the design requires:

1. **The finding.** What is wrong and what it costs a user — the prompt gives you both, as "Who
   notices this and how" and the investigation's own confidence. Carry the confidence over as
   written rather than upgrading it; below `high` it is telling the reviewer to check the mechanism
   and not only the patch, and the body should say which part is the uncertain one. Include the
   fingerprint, the severity, how many times it was seen and over what window. State that a
   self-improvement run found it — a reviewer who does not know that will read the pull request
   wrong — and name the install it ran on and the revision it ran at, both of which the prompt
   gives you under WHERE. A maintainer reading a finding from a loop they do not operate cannot
   check any of it without knowing whose cluster saw it.
2. **Evidence.** The verbatim log lines and timestamps, in a fenced block, with the query that
   produced each. This is the part a reviewer checks first and the part most likely to be thin.
3. **The fix and why.** The mechanism, then the change, then why this change and not the obvious
   alternative. Name the alternative.
4. **Live validation.** What you actually did against a running install, at each layer the change
   claims to touch. You are read-only, so this section is mostly what you **could not** do — write
   that plainly under **Testing → Live validation**: "Not live-tested: the self-improvement runner
   holds read-only grants and cannot deploy. Verified by static reading of `<file>:<line>` and by
   `<test>`." An empty section is not an answer, and a claim the diff does not support is worse than
   no claim.
5. **The change itself.** The diff. Keep it reviewable in one sitting.

Fill in the template's **Context** section from the §0 search: `Closes #<n>` when an open issue
describes this finding, or one line naming the related pull request and how yours differs. "Nothing
matched" is the answer when nothing did — say it rather than leaving the section empty.

Fill in **Self-Review** honestly. `AGENTS.md` requires an adversarial pass run in a context that did
not write the change, and you are the context that wrote it, so you cannot supply one. Say that in
those words rather than leaving the section thin or letting the checks you did run stand in for the
pass you did not. Three things go in it:

- The checks you actually ran, named — the unittest command and its result, the source you re-read.
- The angles you considered and what you found down each. "No findings" alone is indistinguishable
  from not having looked.
- One sentence: no independent adversarial review was performed, because this pull request was
  written by an agent that could not run the code it changed; `kube-agents-bot` reviews every pull
  request on open and that pass is where it comes from.

## 5. Confirm the diff is only your commit

Branching at the base tip in §1 is what makes this true; this step is where you find out it is,
before a reviewer does. One read, before you open anything. Through the public API, the way §0's
search goes: `gh api` is refused by this pod's proxy, because a raw call is a write path its argv
rules cannot read.

```bash
curl -sSf "https://api.github.com/repos/<the Upstream from your brief>/compare/<the base from your brief>...<the owner half of 'Push branches to'>:<your branch>" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["status"], len(d.get("files") or []))'
```

Spell the head `owner:branch`. Under `mode: upstream` your branch is on the fork and the base is on
a different repository, and a bare branch name is looked up on the base, where it does not exist.

`ahead` — or `identical` — and a file count matching what you committed is the answer you expect.
`git show --stat HEAD` says how many files you actually changed, and the two numbers must agree.

If they disagree, you branched in the wrong tree or the base moved under you in a way worth a
human's attention. Open nothing. Print `SKIPPED: the diff would be <n> files, not <m>` and end the
turn. The finding keeps its counts and a later run files it; a pull request nobody can review does
not.

If the call itself fails — a fork the anonymous API cannot see, a refused connection — that is not
evidence either way, so do not skip on it. Open the pull request and say in the body that the diff
could not be confirmed.

This is not a hypothetical. Live run `kube-agents-selfimprove-29791620` opened a one-file fix that
GitHub rendered as 40,346 additions across 261 files, because it branched from the deployed
revision and the base had never seen that commit. Nothing failed, and the agent's own reply said
the diff was clean — it had checked its commit, which was correct, rather than the pull request,
which was not. §1 is why that cannot happen the same way again; this step is what catches it
happening some other way.

## 6. Open it

Write the body to `$HERMES_HOME/pr-body.md` first — it is long, and a `--body` argument that size
is a quoting accident waiting to happen. Use that path and not `/tmp`: `HERMES_WRITE_SAFE_ROOT` is
this run's home, so a write anywhere else is denied and you spend calls discovering it. Live run
`selfimprove-fork-4` spent them on `/tmp/pr_body.md`. Then:

```bash
gh pr create \
  --repo '<the Upstream from your brief>' \
  --head '<the owner half of "Push branches to">:selfimprove/<signal>-<short-slug>' \
  --base '<the "Open the pull request against" from your brief>' \
  --title 'fix(operator): stop the reconciler retrying a Secret it cannot read' \
  --body-file "$HERMES_HOME/pr-body.md"
```

All three flags are load-bearing and none can be left to a default. `--base` is not always `main`;
take it from the brief. `origin` in this checkout is the
upstream repository, so without `--repo` gh may still guess right — but the branch is on `fork`,
and without `--head` gh looks for it on the repository it is creating against, does not find it,
and fails or offers to push. There is nothing interactive here to accept that offer.

`--head` takes `owner:branch`, not the remote name: the owner is the part of your brief's
`Push branches to` line before the slash. Under `mode: fork` upstream and fork are the same
repository and the flag is still correct.

If `gh pr create` fails, read the error rather than retrying. `403` on the upstream means the
account behind the token does not have that permission there. "No commits between" means the push
in §3 did not land. Neither is fixed by running the command again; print `SKIPPED: <the error>` and
end the turn.

## 7. Label it

Your brief's `Label the pull request` line names the labels and gives you the command for each —
normally two, one marking the pull request as the loop's and one carrying the finding's severity. A
run configured to open them unlabelled says so there instead. Apply them once the pull request
exists, running the commands exactly as the brief lists them:

```bash
gh pr edit <the pull request URL> --add-label '<the first label from your brief>'
gh pr edit <the pull request URL> --add-label '<the second label from your brief>'
```

Both names are configurable and the severity one carries this finding's grade, so take them from the
brief rather than from this page — an install may spell them `sev/high`, and copying the example
would tag a `critical` finding as something else.

One command per label. Do not fold them into `--add-label 'self-improvement,severity:medium'`: gh
resolves every name before it applies any, so one label the repository is missing costs you the
others as well. Not `gh pr create --label` either — that resolves before it creates anything and
fails the whole command, trading the pull request for the tag.

You can attach an existing label and not create one: `gh label` is outside the six subcommands the
sidecar's deny policy allows, so the command is refused whatever the token could do. On a
repository that has never been sent a
self-improvement pull request the edit fails with `not found`. That is a note in your reply and not
a reason to stop: the pull request stands, its body already states the finding's severity and says
a self-improvement run found it, and a maintainer creates the labels once. Write the note above the
URL line §8 asks for, not after it. Report each label separately — one failing says nothing about
the other, and a maintainer reading "the label failed" cannot tell which is missing.

## 8. Finish

- Print the pull request URL on the last line of your reply, alone, with nothing after it. The
  runner reads that line and records it in the ledger; without it the finding is filed but looks
  unfiled, and the next run files it again. Anything you still have to say — the label note from
  §7, a caveat about the fix — goes above it.
- Do not wait for review, do not merge, do not comment further.

## Refuse to file when

- The code does not match the finding (§0).
- The fix would touch the self-improvement loop's own gate, ledger, or grants. A loop that can widen
  its own permissions is the failure mode this whole design is arranged around. Print
  `SKIPPED: out of bounds - <why>`, with those three words first and your reason after them, and do
  not open anything. First matters: the runner reads them only at the head of the line, so that a
  refusal is never confused with an ordinary skip whose reason happens to quote a finding about an
  out-of-bounds error. The word
  `SKIPPED` is what stops the runner reading your turn as a filing that failed — which would spend
  one of the day's pull request slots and start a 24-hour cooldown. `out of bounds` is what tells it
  the answer is permanent: without it the finding is offered to a filing turn again every hour,
  costing a token and a whole turn's budget each time to reach this same refusal. The finding stays
  in the ledger and keeps counting either way, which is how a human comes to read it.
- The fix needs a credential, a cluster change, or a decision about product direction.
- You are not confident. Print `SKIPPED: <why>`. The finding stays in the ledger, the count keeps
  rising, and a later run with better evidence can file it.
