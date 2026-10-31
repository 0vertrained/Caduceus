"""Which model should write the drafts? The same de-identified requests, every candidate model, the same checks.

    python scripts/bakeoff.py capture evals/bakeoff/requests.json      # the demo's requests, via a stub (local)
    OLLAMA_API_KEY=... python scripts/bakeoff.py run evals/bakeoff/requests.json out/   # every model (CI)

Each reply is scored two ways.
- Rules (deterministic): placeholders invented, numbers not in the facts, length against the instructions, the
  criteria it mentions, that a pending attestation is stated, and for appeals that the reason and policy are quoted.
- A judge model that is not a contestant reads the facts and the reply and lists any claim the facts do not support.

Only de-identified text is involved: the requests are what the router lets leave, and nothing else is sent.
"""

from __future__ import annotations

import json
import re
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

CONTESTANTS = ["kimi-k2.6", "deepseek-v3.2", "glm-5", "gpt-oss:120b"]
JUDGES = ["qwen3.5", "mistral-large-3", "deepseek-v3.1"]
TOKEN = re.compile(r"\[[A-Z_]+_\d+\]")
NUMBER = re.compile(r"(?<![\w\[])\d+(?:\.\d+)?(?![\w\]])")
WORDS = {"draft_request": (90, 140), "draft_appeal": (120, 180)}
# Models write typographic hyphens, dashes and quotes ("apnea\u2011hypopnea", \u201c...\u201d). Compare plain forms,
# or a faithful quote is scored as missing.
PLAIN = str.maketrans({c: "-" for c in "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"}
                      | {c: '"' for c in "\u201c\u201d\u201e"} | {c: "'" for c in "\u2018\u2019"}
                      | {c: " " for c in "\u00a0\u202f\u2009"})


def plain(text: str) -> str:
    return text.translate(PLAIN)


JUDGE_SYSTEM = (
    "You check a paragraph against the facts it was written from. List every claim in the paragraph that the facts "
    "do not support: an added diagnosis, date, number, test result, treatment or opinion. Placeholders such as "
    "[NAME_1] are fine. Answer only with JSON: {\"unsupported\": [\"...\"], \"clarity\": 1-5}, where clarity rates "
    "how plainly a payer's reviewer could follow it."
)


def capture(out: Path) -> None:
    """Run the demo's story with a stub model and keep every de-identified request it would send."""
    import os

    import caduceus.router.providers as providers
    from scripts.record_completions import story  # the same story the deploy records

    seen: dict[tuple[str, str, int], dict] = {}
    original = providers.StubProvider.complete

    def keep(self, system, user, max_tokens=800):
        task = "draft_appeal" if "reconsideration" in system else "draft_request"
        seen.setdefault((system, user, max_tokens),
                        {"task": task, "system": system, "user": user, "max_tokens": max_tokens})
        return original(self, system, user, max_tokens)

    providers.StubProvider.complete = keep
    os.environ["CADUCEUS_EXTERNAL_PROVIDER"] = "stub"
    providers.CachingProvider._replies.clear()
    tmp = out.with_suffix(".tmp.json")
    story(tmp)
    tmp.unlink(missing_ok=True)
    requests = [{"id": f"r{i:02d}"} | r for i, r in enumerate(seen.values(), start=1)]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(requests, indent=1) + "\n")
    print(f"{len(requests)} requests captured in {out}")


def rules(req: dict, text: str) -> dict:
    facts, text = plain(req["user"]), plain(text)
    words = len(text.split())
    lo, hi = WORDS.get(req["task"], (0, 10_000))
    invented_tokens = sorted(set(TOKEN.findall(text)) - set(TOKEN.findall(facts)))
    fact_numbers = set(NUMBER.findall(TOKEN.sub(" ", facts)))
    invented_numbers = sorted({n for n in NUMBER.findall(TOKEN.sub(" ", text)) if n not in fact_numbers})
    criteria = [ln[2:].split(":")[0] for ln in facts.splitlines() if ln.startswith("- ")]

    def mentioned(c: str) -> bool:
        key = [w for w in re.findall(r"[a-z]+", c.lower()) if len(w) > 4]
        return not key or sum(w in text.lower() for w in key) >= max(1, len(key) // 2)

    out = {"words": words, "length_ok": lo <= words <= hi, "invented_placeholders": invented_tokens,
           "invented_numbers": invented_numbers,
           "criteria_mentioned": f"{sum(mentioned(c) for c in criteria)}/{len(criteria)}"}
    if "awaiting clinician attestation" in facts:
        out["pending_stated"] = bool(re.search(r"attest|confirm|awaiting|pending", text, re.I))
    from caduceus.drafting import contradicts
    out["misstates_criteria"] = bool(req["task"] == "draft_request" and contradicts(facts, text))
    if req["task"] == "draft_appeal":
        reason = re.search(r"Payer's reason for denial: (.*)", facts)
        policy = re.search(r'Coverage policy sentence: "(.*)"', facts)
        out["quotes_reason"] = bool(reason and reason.group(1)[:40].lower() in text.lower())
        out["quotes_policy"] = bool(policy and policy.group(1)[:40].lower() in text.lower())
    return out


def run(requests_path: Path, out_dir: Path) -> None:
    import os

    import httpx

    from caduceus.router.providers import OllamaChatProvider, ProviderUnavailable

    key = os.environ["OLLAMA_API_KEY"]
    offered = [m["name"] for m in httpx.get("https://ollama.com/api/tags", headers={"Authorization": f"Bearer {key}"},
                                            timeout=30).json()["models"]]

    def resolve(want: str) -> str | None:
        return next((m for m in offered if m == want), None) or next((m for m in offered
                                                                     if m.startswith(want + ":")), None)

    contestants = [m for m in (resolve(w) for w in CONTESTANTS) if m]
    judge = next((m for m in (resolve(w) for w in JUDGES) if m and m not in contestants), None)
    requests = json.loads(requests_path.read_text())
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"contestants {contestants}; judge {judge}; {len(requests)} requests", flush=True)

    def ask(model: str, system: str, user: str, max_tokens: int) -> dict:
        provider = OllamaChatProvider(key, model, timeout=300)
        for attempt in range(3):
            start = time.monotonic()
            try:
                c = provider.complete(system, user, max_tokens)
                return {"text": c.text, "seconds": round(time.monotonic() - start, 1),
                        "input_tokens": c.input_tokens, "output_tokens": c.output_tokens}
            except ProviderUnavailable as e:
                error = str(e)
                time.sleep(10 * (attempt + 1))
        return {"error": error}

    def one(model: str, req: dict) -> dict:
        reply = ask(model, req["system"], req["user"], req["max_tokens"])
        row = {"model": model, "request": req["id"], "task": req["task"]} | reply
        if "text" in reply:
            row["rules"] = rules(req, reply["text"])
            if judge:
                verdict = ask(judge, JUDGE_SYSTEM, f"FACTS:\n{req['user']}\n\nPARAGRAPH:\n{reply['text']}", 400)
                try:
                    found = json.loads(re.search(r"\{.*\}", verdict.get("text", ""), re.S).group(0))
                    row["judge"] = {"unsupported": found.get("unsupported", []), "clarity": found.get("clarity")}
                except (AttributeError, ValueError):
                    row["judge"] = {"error": verdict.get("error") or "unreadable verdict"}
        print(f"{model} {req['id']} {'ok' if 'text' in reply else reply.get('error')}", flush=True)
        return row

    rows = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        for model in contestants:
            rows += list(pool.map(lambda r, m=model: one(m, r), requests))
    (out_dir / "results.json").write_text(json.dumps({"judge": judge, "rows": rows}, indent=1) + "\n")
    (out_dir / "summary.md").write_text(summary(contestants, judge, rows, requests) + "\n")
    print((out_dir / "summary.md").read_text())


def summary(models: list[str], judge: str | None, rows: list[dict], requests: list[dict]) -> str:
    lines = [f"Judge: {judge or 'none available'}. Requests: {len(requests)}.", "",
             "| Model | Answered | Median seconds | Length within instructions | Invented placeholders | "
             "Numbers not in the facts | Says every criterion is met when one is not | "
             "Appeal quotes reason and policy | Judge: replies with unsupported claims | Judge: clarity (1-5) |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for m in models:
        mine = [r for r in rows if r["model"] == m]
        ok = [r for r in mine if "text" in r]
        judged = [r for r in ok if "unsupported" in r.get("judge", {})]
        clarity = [r["judge"]["clarity"] for r in judged if isinstance(r["judge"].get("clarity"), (int, float))]
        appeals = [r for r in ok if r["task"] == "draft_appeal"]
        quoted = sum(bool(r["rules"].get("quotes_reason") and r["rules"].get("quotes_policy")) for r in appeals)
        lines.append(
            f"| {m} | {len(ok)}/{len(mine)} | {statistics.median([r['seconds'] for r in ok]) if ok else '-'} | "
            f"{sum(r['rules']['length_ok'] for r in ok)}/{len(ok)} | "
            f"{sum(bool(r['rules']['invented_placeholders']) for r in ok)} | "
            f"{sum(bool(r['rules']['invented_numbers']) for r in ok)} | "
            f"{sum(bool(r['rules'].get('misstates_criteria')) for r in ok)} | {quoted}/{len(appeals)} | "
            f"{sum(bool(r['judge']['unsupported']) for r in judged)}/{len(judged)} | "
            f"{round(statistics.mean(clarity), 2) if clarity else '-'} |")
    return "\n".join(lines)


if __name__ == "__main__":
    cmd, *rest = sys.argv[1:] or ["help"]
    if cmd == "capture" and len(rest) == 1:
        capture(Path(rest[0]))
    elif cmd == "run" and len(rest) == 2:
        run(Path(rest[0]), Path(rest[1]))
    else:
        print(__doc__)
        sys.exit(2)
