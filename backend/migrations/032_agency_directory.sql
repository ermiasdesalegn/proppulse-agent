-- The public website shows the agency a visitor chose (docs/PLAN_REMAINING_SAAS.md, item 7).
--
-- Every agency is reachable at /<slug> on the website. Listing it in the public directory (/agencies) is a separate choice the agency makes:
-- off until it turns it on.

alter table org_settings add column if not exists listed boolean not null default false;
