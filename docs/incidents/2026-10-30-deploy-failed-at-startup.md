# The first deploy with a model key failed at startup

**Date:** October 30, 2026 (times Mountain)
**Severity:** low. No visitor was affected: Cloud Run kept serving the previous revision throughout.
**Duration:** 1 hour 24 minutes, from the start of the failed deploy (3:07 pm) to a deploy that recorded every reply (4:31 pm).

## Summary

The public demo had always drafted from a template. When an Ollama Cloud key was added, the next deploy picked
Kimi K2.6, a model that reasons before it answers. Recording its replies into the image failed, so the new
revision tried to draft all 11 seeded cases live while it was starting. Startup ran past Cloud Run's limit, and
the deploy was rejected. Two changes fixed it: a cold start now uses only recorded replies, and the model is asked
not to reason when it writes a paragraph.

## Timeline

| When | What happened |
|---|---|
| 2:59 pm | The Ollama key was stored in Secret Manager with `infra/demo/add-model-key.sh`. |
| 3:07 to 3:36 pm | Deploy of `cdba42a` (run 41128653647). It chose `kimi-k2.6`, its recording step failed with a generic warning, and then the **Deploy** step failed. The previous revision kept serving. |
| 3:36 to 3:39 pm | Investigation. The run logs could not be read from the environment the work was done in (their storage was blocked), so the cause was worked out from the run's annotations and the code: with no recordings, the startup seed called the model 11 times in a row, each with a 90-second timeout. Fix 1 committed as `8b2bff3`. |
| 3:44 to 4:21 pm | Deploy of `8b2bff3` (run 41133247468) succeeded, but recording missed 3 of 17 replies. Now the reasons were visible: "returned no answer (after thinking, out of tokens)" twice, and one dropped connection. |
| 4:22 pm | Fix 2 committed as `5a3fcf4`. |
| 4:28 to 4:31 pm | Deploy of `5a3fcf4` (run 41138355961) recorded 17 of 17 replies in three minutes, with thinking turned off, and published them for review. |

## Causes

1. **A cold start depended on an outside service.** Seeding the demo drafted every case through the model.
   Startup time was then the sum of 11 model calls, and any outage or slow reply would block the service from
   starting. Nothing about seeding needed a live model.
2. **The limits assumed a model that answers straight away.** A reasoning model spends tokens thinking before
   it writes. With 4,000 tokens it sometimes thought until the budget ran out and returned nothing. With a 90-second
   timeout, a long think could exceed it.
3. **Failures were hard to read.** The fallback recorded only an exception type (`ProviderUnavailable`), the
   recording script exited without saying why, and the run logs were out of reach.

The first failure's exact message was never seen; the cause above is inferred, and the second deploy's reasons
are consistent with it.

## What went well

- Cloud Run does not move traffic to a revision that fails to start, so the demo stayed up.
- The template fallback meant a missed reply degraded a draft, never a page.
- The smoke check after each deploy (an unsigned request must be refused) kept running.

## What changed

- **A cold start never calls a model.** The demo seeds inside `Router.recorded_only()`: anything not recorded uses
  the template. Live calls resume once the service is up. A test fails if startup calls the model, even with a key
  set.
- **Requests cannot hang.** The running demo waits at most 20 seconds for the model, under Netlify's 26-second
  proxy limit, and falls back to the template. Recording allows 300 seconds.
- **No reasoning for a paragraph.** Requests set `"think": false`, so the token budget goes to the answer. A model
  that refuses the option is asked again without it, with a larger budget.
- **Recording keeps what it gets.** It retries missed requests up to three times, does not ask again for replies
  already recorded, and keeps a partial set rather than discarding it.
- **Failures say why, without content.** A refusal carries its HTTP status, and an empty answer says whether the
  model ran out of tokens while thinking. The deploy posts the recording script's reasons on the run page.
- **Replies are published for review.** A separate job, the only one able to write to the repository, pushes the
  recorded replies to a branch. They are now committed in `data/recorded/completions.json` after a scan for patient
  identifiers.

## Lessons

- Startup must depend only on what is inside the container. Outside services belong on paths that can degrade.
- Set limits from the model you will run, not the one you tested with. The model bake-off measures each candidate's
  time per reply for that reason.
- Make every failure explain itself where it will be read. Here that was the run's annotations, not its logs.
