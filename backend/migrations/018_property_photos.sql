-- The cover photo a portal shows for a listing. Kept apart from floor_plan_image_url, which the vision model reads as a floor plan.
-- The website uses it as the unit's picture; units without one fall back to their project's picture, then to a drawing.

alter table properties add column if not exists photo_url text;
