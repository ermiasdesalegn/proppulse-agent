-- Files an agent message carries on the website (a 3D view and model, a comparison PDF). Only what is needed to make the file again is kept;
-- the backend builds the file when the buyer asks for it, so no file is stored here.

alter table chat_messages add column attachments jsonb not null default '[]'::jsonb;
