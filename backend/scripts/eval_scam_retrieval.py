"""Calibrate scam retrieval against scripted calls, live on Token Factory.

For each call in tests/fixtures/scam_calls.json, embed the caller's words and
report the nearest cards. Then check the thresholds in scam_library.py:
every scam should reach REFERENCE_SCORE with the right card first, and no
ordinary call should reach CITE_SCORE.

    python backend/scripts/eval_scam_retrieval.py

The calls are invented; nothing here is a real person or recording.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from embed_scam_library import nebius_key  # noqa: E402

from hearally.judgement import Segment, retrieval_query  # noqa: E402
from hearally.scam_library import (  # noqa: E402
    CITE_SCORE,
    REFERENCE_SCORE,
    EmbeddingClient,
    load_library,
)

FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "scam_calls.json"


def main() -> None:
    calls = json.loads(FIXTURE.read_text(encoding="utf-8"))["calls"]
    library = load_library()
    key = nebius_key()
    client = EmbeddingClient(lambda: key)
    queries = [
        retrieval_query(
            [Segment(i, s["speaker"], s["text"], 1.0) for i, s in enumerate(c["segments"])]
        )
        for c in calls
    ]
    vectors = client.embed(queries)

    scam_scores, ordinary_scores, wrong = [], [], []
    print(f"{'call':28s} {'expect':16s} {'top card':16s} score  2nd")
    for call, vector in zip(calls, vectors, strict=True):
        first, second = library.search(vector, k=2)
        expect = call["expect"]
        name, expected, top = call["name"], str(expect), first.card.id
        print(f"{name:28s} {expected:16s} {top:16s} {first.score:.3f}  {second.score:.3f}")
        if expect:
            scam_scores.append(first.score)
            if top != expect:
                wrong.append(name)
        else:
            ordinary_scores.append(first.score)

    right = len(scam_scores) - len(wrong)
    below_reference = sum(s < REFERENCE_SCORE for s in scam_scores)
    below_cite = sum(s < CITE_SCORE for s in scam_scores)
    ordinary_cited = sum(s >= CITE_SCORE for s in ordinary_scores)
    print()
    total = len(scam_scores)
    print(f"scams:    lowest top score {min(scam_scores):.3f}, right card first {right}/{total}")
    print(f"ordinary: highest top score {max(ordinary_scores):.3f}")
    print(f"REFERENCE_SCORE {REFERENCE_SCORE}: scams below it {below_reference}")
    print(f"CITE_SCORE {CITE_SCORE}: scams below it {below_cite}, ordinary above {ordinary_cited}")
    if wrong:
        print("wrong card first:", ", ".join(wrong))


if __name__ == "__main__":
    main()
