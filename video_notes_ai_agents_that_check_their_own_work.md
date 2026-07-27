# Notes: "How to Build AI Agents That Check Their Own Work"

**Source:** [youtube.com/watch?v=0YeeJHYy-Vc](https://www.youtube.com/watch?v=0YeeJHYy-Vc)
**Guest:** Jared Zoneraich, Builder in Residence at Cognition (makers of Devin)
**Interviewer:** Peter Yang
**Runtime:** 47:13 | Published: 2026-07-12

Watched via AIBMM/Amplifiers' YouTube tools (transcript + metadata) as a source of implementable ideas for `pipeline_agents.py`, this project's multi-agent orchestrator (`OrchestratorAgent`, `ReviewAgent`, `ResearchAgent`) — reviewed specifically to inform the "agent swarm" / human-in-the-loop direction under discussion for this pipeline.

---

## Chapter-by-chapter summary

### Why teams overbuild their first agent (0:00)
The most common failure mode Jared sees: teams build an orchestrator, an elaborate eval harness, and heavy scaffolding *before* showing the product to a single real user. His framing — "perfect is always the enemy of complete" — is a direct rebuke of front-loaded architecture. Reaching 80% capability on a first agent build takes about an hour of real effort; the remaining 20% (craft, taste, edge cases) takes closer to a year. Most teams never get to see that 80% because they spend their time planning for the full 100% before shipping anything.

### Let the model cook before adding rules (2:50)
Explicit scaffolding built to compensate for a weaker model — forced chain-of-thought, rigid step-by-step prompts — becomes dead weight as the underlying model improves. Jared notes this already happened once with "reasoning" prompts two years ago, and it's happening again now. His rule of thumb: if you feel you need ultra-specific prompt instructions today, don't treat that specificity as your product's defensibility. Build assuming the model will close that gap on its own, and revisit the scaffolding periodically rather than treating it as permanent infrastructure.

### Why tools matter more than better prompts (3:16)
"Tool engineering is going to be the new thing." Rather than scripting an exact procedure into the system prompt, give the model a well-designed set of tools and trust it to sequence them correctly. The system prompt or skills file should work like a cheat sheet — pointing at *what* tool to reach for in *what* situation — not a rigid step-by-step script. A model given only a script will often ignore it and try everything anyway; a model given good tools plus loose guidance tends to converge on a sensible path itself.

### Why you should build evals after shipping (10:51)
Counter to a lot of engineering instinct, Jared argues against building a comprehensive eval suite before v1 ships. Some of the best teams he's seen ship straight to production with no eval set at all — validating results against real usage first, then formalizing checks once there's something real to check against. His own practical method on a side project: have the agent itself generate example test cases (data recall, tool use, response length), then build lightweight smoke tests the agent runs on its own output before a human ever looks at it. Mechanically, this is just a script that dumps old-output-vs-new-output into a markdown file, which the agent then reads to make its own accept/reject call — not a formal testing framework.

### What cloud agents can do that local agents can't (16:06)
Devin was built cloud-first: every agent session gets its own full VM (compute, browser, display), which enables genuinely asynchronous work — kick off a task from your phone, close your laptop, come back to a finished result. Jared mentions some CTOs do the bulk of their agent-launching during their commute, treating agent kickoff as a lightweight, anywhere action rather than something that requires sitting at a workstation.

### How to get one agent to manage other agents (20:30)
A "master" agent can spin up multiple child agent sessions, each an independent VM/session, then send follow-up instructions into each child's thread exactly the way a human manager would check in on a report — e.g., "make sure each of you responds with a screenshot before you're done." The parent doesn't do the work itself; it delegates, waits, and follows up.

### Demo: one agent launches a team of ten (25:39)
Fan-out is used specifically for large, parallelizable work — the example given is ten landing-page redesign variants generated concurrently, or up to a hundred agents searching through a large dataset in parallel. Two reasons given for why this works well: raw speed from parallelism, and (arguably more important) keeping each individual agent's context window small and focused. Jared's framing: "agents are much better when they're doing one thing specifically... just like humans are, to be honest."

### How agents prove their work before you review it (28:03)
This is presented as Devin's core differentiator. Worker agents don't just hand back generated code — they run it themselves, take screenshots of the actual result, and inspect that output before returning anything to the human. A specific feature, informally called "the Devon test," auto-launches an integration test that clicks through the real UI, unprompted by the user. The framing throughout: an agent behaving like "a full-fledged teammate," not a code-generation function.

### The best tasks for agent teams to work on (33:18)
Brownfield work outperforms greenfield work for agent teams: large-scale migrations (COBOL to modern languages, JS to TS, React Native to Swift), backfilling test coverage, and the general "developer grunt work" that exists in codebases where only a handful of people, out of an entire engineering org, actually understand the legacy system. The common thread across all the good examples: tasks that decompose cleanly into independently verifiable, testable chunks — the same property that makes fan-out viable at all.

---

## What maps directly onto this project

Four ideas were identified as concretely applicable to `pipeline_agents.py` and its supporting scripts, now the subject of an active implementation plan:

1. **Loosen rigid rubrics, add self-inspection.** `review_script.py`'s 9-dimension scoring rubric and `generate_image_prompts.py`'s exhaustive instruction blocks are exactly the kind of model-compensating scaffolding Jared warns rots as models improve. The fix isn't to delete the rubric — it's to give the reviewer a way to check its own prior output (what was flagged last time, was it fixed) rather than only ever scoring fresh from zero context.

2. **A persistent, after-the-fact eval — not a pre-built one.** This is the closest literal match to Jared's own practice: dump old-vs-new output into a comparison artifact, let the agent read that to decide accept/reject. Directly applicable to the script-rewrite loop in `_stage_review_script` — right now a rewrite is scored with zero memory of what was wrong the first time.

3. **Auto-run evidence-gathering instead of leaving it manual.** `local_mp4_analyzer.py` (audio loudness + Whisper transcript on the finished MP4) currently has to be run by hand after every stitch. Devin's "prove your work before I look at it" pattern argues for wiring this into the `stitch` stage automatically as evidence the reviewer consumes, rather than a step a human has to remember.

4. **Parallelize the one genuinely independent, decomposable unit.** Image generation (`generate_images_flux.py`) is currently sequential — one xAI API call at a time — despite every scene's image being fully independent of every other scene's. This is precisely the "verifiable, testable, independent chunk" property Jared describes as the right shape of work for fan-out, just applied to throughput rather than a multi-agent team.

## What doesn't apply here, and why

Cloud-VM-per-agent execution, ten-to-a-hundred-agent fan-out for large migrations, automatic cost-based model routing, and auto-generated internal wikis were all judged **not** currently relevant. Each solves a problem of scale — large engineering teams, large legacy codebases, many independent parallel workstreams — that a single-creator, linear video-essay pipeline doesn't have. `CLAUDE.md` already fills the role their "auto-doc" pitch addresses elsewhere; there's no multi-engineer discovery problem to solve.

## Quotes worth keeping in mind

> "Perfect is always the enemy of complete."

> "You'll be surprised how good these things are and how easy it is to get to 80%... now the last mile is the hard part."

> "Some of the best teams, believe it or not, are just shipping to prod without an eval set."

> "Agents are much better when they're doing one thing specifically... just like humans are, to be honest."

> "It's really a full-fledged teammate."

That last line about agents doing "one thing specifically" is a reasonable gut-check for this project going forward: anytime `ReviewAgent` or `OrchestratorAgent` is tempted to grow a new responsibility inside an existing method, it's usually a sign that responsibility wants to be its own focused stage instead.
