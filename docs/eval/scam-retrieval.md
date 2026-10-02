# Known scam scripts: retrieval and judgement evaluation

Evidence for the thresholds in `backend/src/hearally/scam_library.py` and the behaviour in ARCHITECTURE.md section 5. Re-run both scripts after changing a card, a threshold, the prompt or a model, and replace the tables.

The 22 calls are in `backend/tests/fixtures/scam_calls.json`. They are invented: 12 scams across the library, and 10 ordinary calls chosen to look like scams on the surface (a real grandson, a real bank, a real Medicare conversation, a prize at church bingo, a plumber the account holder booked). **22 calls is a small set.** Treat the thresholds as preliminary until the set is larger and includes real, consented recordings.

## 1. Retrieval alone (`backend/scripts/eval_scam_retrieval.py`)

Qwen3-Embedding-8B on Nebius Token Factory, 4096 dimensions, cosine similarity. The query is the other speakers' words with numbers masked; the account holder's replies are left out.

Run 2026-09-24, 22 curated cards:

| | Result |
|---|---|
| Scams with the right card first | **12 / 12** |
| Lowest top score for a scam | 0.606 (romance) |
| Highest top score for an ordinary call | 0.592 (son talking about Medicare enrolment) |
| `REFERENCE_SCORE` 0.50: scams below it | 0 |
| `CITE_SCORE` 0.60: scams below it / ordinary calls at or above it | 0 / 0 |

The margin between 0.592 and 0.606 is thin. That is why retrieval never names a script on its own: a citation also needs Nemotron to judge the call as scam pressure with concrete signals and to match the same card (section 2).

Choices tested and rejected, 2026-09-24:

| Variant | Effect |
|---|---|
| Qwen3's recommended query instruction ("Instruct: … Query: …") | Raised ordinary calls' scores more than scams': the real bank call went from 0.51 to 0.59 |
| 1024 dimensions instead of 4096 | Worse separation again, for a smaller file |
| The romance card without the usual cover stories (oil rig, military, customs fees) | The romance scam scored 0.51 and could not be named; the card was rewritten from the public descriptions |

## 2. The whole judgement (`backend/scripts/eval_scam_judgement.py`)

Nemotron 3 Super on Token Factory, `temperature` 0, every call judged twice: with retrieved scripts and without.

Run 2026-09-24:

| | Result |
|---|---|
| Scams given a priority heads-up naming the right script | **12 / 12** |
| Scams flagged but unnamed or misnamed | 0 |
| Scams missed | 0 |
| Ordinary calls flagged as scam pressure | **0 / 10** |
| Calls whose scam flag changed when scripts were shown | **0** |

The last row is the important one: showing scam scripts did not make Nemotron more suspicious of ordinary calls. The scripts changed nothing but the name on the heads-up, which is what section 7b requires.

## 3. The weekly Tavily refresh (`backend/src/hearally/scam_discovery.py`)

First run, invoked by hand, 2026-09-25:

| | Result |
|---|---|
| Tavily searches (fixed queries, trusted publishers only) | 5 |
| Pages returned | 33 |
| Judged not a new phone or doorstep scam | 28 |
| Cards written | 5 |
| Good on review | 3: consulate impostor calls (FCC), debt relief (AARP), fake recall notices (AARP) |
| Weak on review | 2: "blackmail scam" from a CFPB overview page, recast as a phone call; "money mule scam", not a phone scam |

Fix: the extraction now reports `page_kind` and `channel`, and code accepts only `one_scam` with `phone` or `doorstep`. Re-run on the same five pages:

| Page | page_kind | channel | Outcome |
|---|---|---|---|
| CFPB common types of fraud | overview | phone | refused |
| FCC consulate impostor calls | one_scam | phone | refused as already covered by government impersonation (the card from the first run is kept) |
| AARP debt relief | one_scam | phone | accepted |
| AARP fake recall notices | one_scam | phone | accepted |
| AARP money mule | one_scam | online_only | refused |

The two weak cards were deleted. A second run read nothing new: all 33 pages were already remembered, so no Nemotron calls were made.
