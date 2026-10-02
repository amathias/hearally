"""The whole judgement on scripted calls, live: Nemotron on Token Factory, with
and without known scam scripts retrieved (docs/ARCHITECTURE.md section 5).

Checks two things:
- each scam gets a heads-up naming the right script;
- showing scripts does not make Nemotron call an ordinary call a scam, by
  comparing every call's outcome with retrieval against without it.

    python backend/scripts/eval_scam_judgement.py

The calls are invented. Costs a few cents of Token Factory credit.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from embed_scam_library import nebius_key  # noqa: E402

from hearally.judgement import Decision, NemotronClient, Segment, judge  # noqa: E402
from hearally.scam_library import EmbeddingClient, ScamRetriever, load_library  # noqa: E402

FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "scam_calls.json"


def heads_up(decisions: list[Decision]) -> Decision | None:
    return next(
        (d for d in decisions if d.outcome == "send" and d.candidate.category == "scam_pressure"),
        None,
    )


def main() -> None:
    calls = json.loads(FIXTURE.read_text(encoding="utf-8"))["calls"]
    key = nebius_key()
    nemotron = NemotronClient(lambda: key)
    retriever = ScamRetriever(load_library(), EmbeddingClient(lambda: key))

    right = missed = wrong_name = false_alarm = changed = 0
    print(f"{'call':28s} {'expect':16s} {'with scripts':34s} without")
    for call in calls:
        segments = [
            Segment(i, s["speaker"], s["text"], 0.95) for i, s in enumerate(call["segments"])
        ]
        cited = heads_up(judge(segments, nemotron, priority_available=True, patterns=retriever))
        plain = heads_up(judge(segments, nemotron, priority_available=True))
        expect = call["expect"]

        def show(d: Decision | None) -> str:
            if d is None:
                return "no scam item"
            name = d.pattern.pattern_id if d.pattern else "unnamed"
            return f"{d.grade} {name}"

        print(f"{call['name']:28s} {str(expect):16s} {show(cited):34s} {show(plain)}")
        if (cited is None) != (plain is None):
            changed += 1
        if expect is None:
            false_alarm += cited is not None
        elif cited is None:
            missed += 1
        elif cited.pattern and cited.pattern.pattern_id == expect:
            right += 1
        else:
            wrong_name += 1

    scams = sum(1 for c in calls if c["expect"])
    print()
    print(f"scams with the right script named: {right}/{scams}")
    print(f"scams flagged but not named or misnamed: {wrong_name}; scams missed: {missed}")
    print(f"ordinary calls flagged as scam pressure: {false_alarm}")
    print(f"calls whose scam flag changed when scripts were shown: {changed}")


if __name__ == "__main__":
    main()
