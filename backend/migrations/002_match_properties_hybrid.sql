-- Hybrid retrieval: hard SQL filters, then cosine ranking with a fixed similarity floor.
-- NULL or empty p_sub_cities means "no geographic filter" (relaxation step 2).

create or replace function match_properties_hybrid(
  query_embedding        vector(768),
  p_max_budget_etb       float,
  p_min_bedrooms         int,
  p_sub_cities           text[],
  p_match_count          int   default 3,
  p_similarity_threshold float default 0.65
)
returns table (
  id           uuid,
  developer_id uuid,
  title        text,
  sub_city     text,
  price_etb    float,
  bedrooms     int,
  similarity   float
)
language plpgsql stable
as $$
begin
  return query
  select p.id,
         p.developer_id,
         p.title,
         p.sub_city,
         p.price_etb::float,
         p.bedrooms,
         (1 - (p.embedding <=> query_embedding))::float as similarity
  from properties p
  where p.status = 'AVAILABLE'
    and p.embedding is not null
    and (p_max_budget_etb is null or p.price_etb <= p_max_budget_etb)
    and (p_min_bedrooms   is null or p.bedrooms  >= p_min_bedrooms)
    and (array_length(p_sub_cities, 1) is null or p.sub_city = any(p_sub_cities))
    and (1 - (p.embedding <=> query_embedding)) >= p_similarity_threshold
  order by p.embedding <=> query_embedding
  limit p_match_count;
end $$;
