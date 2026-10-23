# Evaluation report

Run on 2026-10-23T10:43:24+00:00 in `replay` mode. Everything runs against the demo catalogue (15 units, 3 developers) with the model's recorded answers, so a replay run is repeatable and free; `--mode record` refreshes the recording from the live model.

## Retrieval

58 buyer messages with labelled correct units, in English, Amharic and mixed. The agent's real front half runs: the model reads the message, the query is embedded, hybrid search runs, and the search widens (budget +12% then +24%, neighbouring areas) while nothing is found.

| Metric | All | am | en | mix |
|---|---|---|---|---|
| Hit@3 (a correct unit in the top 3) | 100% | 100% | 100% | 100% |
| Recall@3 | 94% | 96% | 93% | 100% |
| Precision@3 | 83% | 95% | 79% | 82% |
| MRR | 0.99 | 1.00 | 0.99 | 1.00 |

| Difficulty | Cases | Hit@3 | Precision@3 | MRR |
|---|---|---|---|---|
| everyday | 44 | 100% | 85% | 1.00 |
| hard | 14 | 100% | 76% | 0.96 |

| Case | Result |
|---|---|
| Requests nothing matches (5) | correctly empty 100%, false positives 0 |
| Needs the widening loop (3) | recovered 100% |
| Messages not read as a search | 0 |

### Why the similarity threshold is 0.64

The same cases re-run with different thresholds. Too low and requests nothing matches return units anyway (false positives); too high and real requests find nothing.

| Threshold | Hit@3 | Precision@3 | False positives | Correctly empty |
|---|---|---|---|---|
| 0.50 | 100% | 75% | 1 | 80% |
| 0.55 | 100% | 75% | 1 | 80% |
| 0.60 | 100% | 76% | 0 | 100% |
| 0.64 **(used)** | 100% | 83% | 0 | 100% |
| 0.68 | 83% | 71% | 0 | 100% |
| 0.72 | 66% | 57% | 0 | 100% |
| 0.76 | 47% | 43% | 0 | 100% |
| 0.80 | 17% | 14% | 0 | 100% |

## Conversations

23 scripted conversations through the real graph with a rolled-back database and mock calendar, email and Telegram: **23 pass**. 1 more is a known gap (failing today for a stated reason, to be closed in a later phase; 0 already pass). Replies were checked for invented numbers: **37 of 37** replies state only figures present in the facts or the buyer's own words.

| Scenario | Checks | Result |
|---|---|---|
| greeting | 4/4 | pass |
| search_english | 7/7 | pass |
| search_amharic | 4/4 | pass |
| reply_in_amharic | 1/2 | known gap |
| relaxation_widens | 5/5 | pass |
| no_match_waitlist | 5/5 | pass |
| compare_two | 4/4 | pass |
| option_details_tap | 5/5 | pass |
| booking_asks_for_contact | 3/3 | pass |
| booking_full | 8/8 | pass |
| booking_conflict_and_slot_tap | 6/6 | pass |
| multi_site_tour | 5/5 | pass |
| consultation_call | 5/5 | pass |
| voice_note | 4/4 | pass |
| takeover_pauses_the_agent | 5/5 | pass |
| high_value_hands_over | 4/4 | pass |
| prompt_injection | 5/5 | pass |
| off_topic | 3/3 | pass |
| preferences_are_remembered | 4/4 | pass |
| ambiguous_booking_request | 2/2 | pass |
| channel_ad_ask | 4/4 | pass |
| channel_ad_stale_link | 3/3 | pass |
| price_drop_reaches_the_right_buyer | 4/4 | pass |
| tour_reminder_is_sent_once | 5/5 | pass |

### Not passing

- **reply_in_amharic** (known gap: replies are written in the buyer's language only by instruction; no per-message language handling yet (Phase 14)): the intro and closing are written in Amharic (0% Ethiopic letters)
