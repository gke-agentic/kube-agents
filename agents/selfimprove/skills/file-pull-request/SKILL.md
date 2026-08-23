---
name: file-pull-request
description: Turn one promoted self-improvement finding into one upstream pull request — branch, minimal fix, the five-part body, and the honest statement of what could not be validated.
---

# File a Pull Request

Runs only in `fork` and `upstream` mode, once per finding that cleared the gate, in a turn of its
own. The investigation is over; do not re-investigate. Your job is to write the smallest change that
fixes the finding you were handed, and to describe it so a reviewer can judge it in one pass.

## 0. Before you write anything

- Re-read the finding. Open the file it names, at the revision in the checkout you were given.
- **If the code does not say what the finding says it says, stop and open nothing.** Print
  `SKIPPED: <why>` and end the turn. A stale finding filed as a pull request costs a human more
  than a missed fix does. This happens: the finding may be hours old and `main` moves.
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
  - `"state": "closed"` with a `merged_at` timestamp is **merged**. Compare it against the commit
    your checkout is at — `git show -s --format=%cI HEAD`, which asks for the one commit a depth-1
    clone actually has:
    - Merged **after** your commit: the fix is in `main` and your checkout predates it, so the
      finding is already answered. Stop, and print `SKIPPED: fixed in #<n>`.
    - Merged **before** your commit: the deployed code already carries that fix and the finding
      recurred anyway. Do not stop, and do not re-file the same change — it did not work. File what
      you actually found, and say in the body that #`<n>` was supposed to fix this and did not.
      Treating merged as a permanent stop is how a regression gets silenced forever.
  - Keep the number of whatever you find: §4 needs it.

## 1. Branch

```bash
cd <the source checkout>
git switch -c selfimprove/<signal>-<short-slug>
```

Branch from the deployed revision the checkout is already at, not from `main`. The finding is
evidenced against that commit and a reviewer needs the diff to line up with it.

The checkout is a shallow `git` clone with two remotes: `origin` is the upstream repository and
`fork` is where you push. It is detached at the deployed commit, so `git switch -c` here names a
branch at that commit — which is what you want. Depth is 1: `git log`, `git blame` and
`git merge-base` see one commit and will mislead you, so do not reach for them.

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
  names. The token can open a pull request there but cannot write a branch there, so a push to
  `origin` fails; the branch has to live on the fork for the pull request to have a head.

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

## 5. Open it

Write the body to a file first — it is long, and a `--body` argument that size is a quoting
accident waiting to happen. Then:

```bash
gh pr create \
  --repo '<the Upstream from your brief>' \
  --head '<the owner half of "Push branches to">:selfimprove/<signal>-<short-slug>' \
  --base main \
  --title 'fix(operator): stop the reconciler retrying a Secret it cannot read' \
  --body-file <the body file>
```

Both flags are load-bearing and neither can be left to a default. `origin` in this checkout is the
upstream repository, so without `--repo` gh may still guess right — but the branch is on `fork`,
and without `--head` gh looks for it on the repository it is creating against, does not find it,
and fails or offers to push. There is nothing interactive here to accept that offer.

`--head` takes `owner:branch`, not the remote name: the owner is the part of your brief's
`Push branches to` line before the slash. Under `mode: fork` upstream and fork are the same
repository and the flag is still correct.

If `gh pr create` fails, read the error rather than retrying. `403` on the upstream means the token
has `pull_requests: write` and you tried to write something else. "No commits between" means the
push in §3 did not land. Neither is fixed by running the command again; print `SKIPPED: <the
error>` and end the turn.

## 6. Finish

- Print the pull request URL on the last line of your reply, alone, with nothing after it. The
  runner reads that line and records it in the ledger; without it the finding is filed but looks
  unfiled, and the next run files it again.
- Do not wait for review, do not merge, do not comment further.

## Refuse to file when

- The code does not match the finding (§0).
- The fix would touch the self-improvement loop's own gate, ledger, or grants. A loop that can widen
  its own permissions is the failure mode this whole design is arranged around. Report it as a
  finding for a human instead.
- The fix needs a credential, a cluster change, or a decision about product direction.
- You are not confident. Print `SKIPPED: <why>`. The finding stays in the ledger, the count keeps
  rising, and a later run with better evidence can file it.
