# Design decisions

Short records of choices that are not obvious from the code, with the reason and what was given up.

## Embeddings: `gemini-embedding-001` at 768 dimensions, not `text-embedding-004`

The specification names `text-embedding-004`. That model is being retired, so a project started now would break on a fixed date.
`gemini-embedding-001` accepts `output_dimensionality=768`, which keeps the schema (`vector(768)`) and the HNSW index exactly as
specified. The similarity threshold (0.64) was tuned on this model, so changing models later means re-running `scripts/tune_threshold.py`.

## Dashboard live updates: our own event stream, not Supabase Realtime

Supabase Realtime publishes row changes. The dashboard needs more than rows: which graph node is running, how long it took, which
model answered, when a proactive message went out. Those are events, not table changes, and they must reach the browser through the
server (the browser never holds a database key). So the backend writes events to `event_log` and streams them over Server-Sent
Events. The Realtime publication on `chat_messages` stays in the migrations as an option; nothing depends on it.

**The stream reads the table by polling, not LISTEN/NOTIFY.** The first version used LISTEN/NOTIFY. psycopg's `notifies()` does not
honour task cancellation here, which made shutdown hang about one run in three, and a dedicated listening connection is also not
something a pooled database promises to keep alive. Polling `event_log` by id every 0.5 s (and immediately after the instance's own
writes) costs one indexed query per instance and needs no special connection. The trade-off is up to 0.5 s of delay for events written
by another instance.

## Tests run on an embedded Postgres, not Docker and not the hosted database

The suite used to run against the hosted Supabase database: 23 minutes, flaky when the network was slow, and sharing data with real
use. Docker Desktop needs several GB of disk and a running engine; neither could be assumed. `pgserver` ships PostgreSQL 16 with
pgvector as a pip package, so `pytest` works on a clean checkout with no services. The seed uses stored embeddings
(`tests/fixtures/seed_embeddings.json`), so building the database needs no model quota. CI can point `TEST_DATABASE_URL` at a
pgvector service container instead.

## Reading button taps in code

Buttons send fixed sentences ("Tell me more about option 2", "Book Tue 03 Nov, 10:00"). Those are recognised with exact patterns, so
a tap costs no model call and cannot be misread. Anything that does not match goes to the model as before. The button data is the
sentence itself (at most 64 bytes), so a tap needs no stored state and shows in the transcript as what the buyer chose. A time offered
for booking remembers which options it was for (`pending_booking`), so tapping a time books the unit chosen earlier.

## Developer reliability: a capped reward, not a re-sort

Search results are ordered by similarity plus `0.05 x reliability`. The reward can settle a near-tie in favour of the developer who
delivers, but a match that is more than 0.05 better always stays ahead. An earlier version bucketed scores into bands; two matches
0.006 apart could land in different bands and be ordered by the wrong rule, so it was replaced.

## Payment plans

A plan is `{installments, interval_months, handover_pct}`, stored on the unit if it differs from its developer's usual terms and
falling back field by field to the developer's, then to a generic plan. Schedules are computed in code and always sum to the price
exactly. They are the developer's advertised terms, not offers: the brochure says so, and payment dates assume signing a week after
the visit.

## Announcement emails

The mail service posts to `/api/webhooks/announcement` with a shared secret. Each message id is processed once (a redelivery gets
`duplicate`). The sender is matched to a developer by exact sales address, then by company domain if exactly one developer uses it; a
shared domain such as gmail.com matches nobody, and unknown senders are recorded and ignored. An unusable attachment never blocks the
announcement itself.

## Brochure fonts

PDFs use the built-in Latin-1 fonts, so characters outside that range (Amharic) are replaced by `?` rather than crashing. The HTML
brochure and the chat show them correctly. A bundled Unicode font for Amharic PDFs is planned with the rest of the Amharic work.
