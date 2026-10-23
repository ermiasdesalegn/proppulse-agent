# Evaluation report

Run on 2026-10-23T15:47:19+00:00 in `replay` mode. Everything runs against the demo catalogue (15 units, 3 developers) with the model's recorded answers, so a replay run is repeatable and free; `--mode record` refreshes the recording from the live model.

## Retrieval

58 buyer messages with labelled correct units, in English, Amharic and mixed. The agent's real front half runs: the model reads the message, the query is embedded, hybrid search runs, and the search widens (budget +12% then +24%, neighbouring areas) while nothing is found.

| Metric | All | am | en | mix |
|---|---|---|---|---|
| Hit@3 (a correct unit in the top 3) | 100% | 100% | 100% | 100% |
| Recall@3 | 96% | 96% | 95% | 100% |
| Precision@3 | 83% | 95% | 78% | 89% |
| MRR | 0.99 | 1.00 | 0.99 | 1.00 |

| Difficulty | Cases | Hit@3 | Precision@3 | MRR |
|---|---|---|---|---|
| everyday | 44 | 100% | 86% | 1.00 |
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
| 0.50 | 100% | 76% | 1 | 80% |
| 0.55 | 100% | 76% | 1 | 80% |
| 0.60 | 100% | 78% | 0 | 100% |
| 0.64 **(used)** | 100% | 83% | 0 | 100% |
| 0.68 | 83% | 72% | 0 | 100% |
| 0.72 | 64% | 56% | 0 | 100% |
| 0.76 | 45% | 42% | 0 | 100% |
| 0.80 | 19% | 17% | 0 | 100% |

## Conversations

28 scripted conversations through the real graph with a rolled-back database and mock calendar, email and Telegram: **28 pass**. 0 more are known gaps (failing today for a stated reason, to be closed in a later phase; 0 already pass). Replies were checked for invented numbers: **44 of 44** replies state only figures present in the facts or the buyer's own words.

| Scenario | Checks | Result |
|---|---|---|
| greeting | 4/4 | pass |
| search_english | 7/7 | pass |
| search_amharic | 4/4 | pass |
| reply_in_amharic | 5/5 | pass |
| amharic_booking_flow | 3/3 | pass |
| affordability_estimate | 5/5 | pass |
| handoff_on_request | 5/5 | pass |
| worry_answered_from_facts | 3/3 | pass |
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
| prompt_injection | 6/6 | pass |
| off_topic | 3/3 | pass |
| preferences_are_remembered | 4/4 | pass |
| ambiguous_booking_request | 2/2 | pass |
| channel_ad_ask | 4/4 | pass |
| channel_ad_stale_link | 3/3 | pass |
| price_drop_reaches_the_right_buyer | 4/4 | pass |
| tour_reminder_is_sent_once | 5/5 | pass |
