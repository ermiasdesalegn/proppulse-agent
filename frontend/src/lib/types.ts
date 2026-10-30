export type DealStage =
  | "NEW" | "QUALIFIED" | "INVENTORY_PENDING" | "SITE_VISIT_BOOKED" | "TOUR_COMPLETED"
  | "CONTRACT_SIGNED" | "COMMISSION_PENDING" | "COMMISSION_PAID" | "LOST";

export type LedgerStatus = "LEAD_REGISTERED" | "TOUR_COMPLETED" | "CONTRACT_SIGNED" | "COMMISSION_PENDING" | "COMMISSION_PAID" | "CLOSED_LOST";

export interface Buyer {
  id: string;
  thread_id: string;
  telegram_id: number | null;
  full_name: string | null;
  phone: string | null;
  email: string | null;
  max_budget_etb: number | null;
  min_bedrooms: number | null;
  preferred_sub_cities: string[];
  lead_score: number;
  deal_stage: DealStage;
  human_takeover: boolean;
  preference_summary: string | null;
  created_at: string;
  updated_at: string;
}

export interface ChatMessage {
  id: number;
  buyer_id: string;
  role: "buyer" | "agent" | "broker";
  content: string;
  source: "text" | "voice" | "system";
  telegram_file_id: string | null;
  created_at: string;
}

export interface Deal {
  id: string;
  buyer_id: string;
  buyer_name: string | null;
  developer_name: string;
  developer_slug: string;
  property_title: string;
  property_price_etb: number;
  status: LedgerStatus;
  expected_commission_etb: number | null;
  contract_value_etb: number | null;
  next_buyer_milestone_date: string | null;
  lead_reg_email_id: string | null;
  tour_id: string | null;
  lead_score: number;
  created_at: string;
  updated_at: string;
}

export interface QualifiedBuyer {
  id: string;
  full_name: string | null;
  thread_id: string;
  max_budget_etb: number | null;
  min_bedrooms: number | null;
  preferred_sub_cities: string[];
  lead_score: number;
  deal_stage: DealStage;
  human_takeover: boolean;
}

export interface BoardData {
  qualified: QualifiedBuyer[];
  deals: Deal[];
}

export interface Property {
  id: string;
  title: string;
  project_name: string;
  unit_type: string;
  sub_city: string;
  lat: number | null;
  lng: number | null;
  price_etb: number;
  bedrooms: number;
  sqm: number;
  status: string;
  developer_name: string;
  developer_slug: string;
}

export interface TourStop {
  stop_order: number;
  property_id: string;
  title: string;
  lat: number | null;
  lng: number | null;
  sub_city: string;
}

export interface RouteJson {
  geometry: string;
  distance_meters: number;
  duration_seconds: number;
  bbox: [number, number, number, number];
}

export interface Tour {
  id: string;
  kind: "SITE_VISIT" | "CONSULTATION";
  scheduled_start: string;
  mapbox_route_json: RouteJson | null;
  reminder_sent: boolean;
  buyer_id: string;
  full_name: string | null;
  thread_id: string;
  stops: TourStop[];
  project_id?: string | null;
  project_name?: string | null;
}

export interface Stats {
  buyers: number;
  in_takeover: number;
  qualified: number;
  open_deals: number;
  pipeline_commission_etb: number;
  commission_pending_etb: number;
  upcoming_tours: number;
  proactive_sent: number;
  high_value_threshold_etb: number;
  demo_reset_enabled: boolean;
  trends?: Partial<Record<"buyers" | "open_deals" | "pipeline_commission_etb" | "upcoming_tours" | "proactive_sent", { current: number; previous: number }>>;
}

export interface ProactiveEvent {
  id: number;
  kind: string;
  audience: "buyer" | "broker";
  buyer_id: string | null;
  buyer_name: string | null;
  message: string | null;
  channel: string | null;
  created_at: string;
}

export interface Developer {
  id: string;
  name: string;
  slug: string;
  sales_email: string;
  website_url?: string | null;
  scrape_url: string | null;
  scrape_profile: Record<string, unknown> | null;
}

export interface IngestReport {
  developer: string;
  source: string;
  dry_run: boolean;
  full_sync: boolean;
  found: number;
  created: { title: string; price_etb: number; area: string; property_id?: string | null; floor_plan_tags?: string[] }[];
  price_changed: { title: string; old_price_etb: number; new_price_etb: number; change_pct: number; kind?: "PRICE_DROP" | "PRICE_INCREASE" }[];
  image_updated: { title: string }[];
  reactivated: { title: string }[];
  removed: { title: string }[];
  skipped: { title?: string; reason: string }[];
  unchanged: number;
  sync: string;
  notes: string[];
  floor_plan_for?: string;
  parse_errors: string[];
  counts: { created: number; price_changed: number; removed: number; unchanged: number; skipped: number };
  run_id?: number;
  ads: { title: string; kind: string; posted: boolean; error?: string; reason?: string }[];
}

export interface IngestRun {
  id: number;
  source: string;
  dry_run: boolean;
  full_sync: boolean;
  created: number;
  price_changed: number;
  removed: number;
  unchanged: number;
  skipped: number;
  created_at: string;
  developer_name: string | null;
}

export interface AdResult {
  posted: boolean;
  preview?: string;
  buttons?: string[];
  has_photo?: boolean;
  photo_url?: string | null;
  media?: { kind: "photo" | "video"; url: string } | null;
  warning?: string | null;
  message_id?: number;
  text?: string;
  reason?: string;
}

export interface ChannelPost {
  id: number;
  kind: string;
  price_etb: number;
  text: string;
  created_at: string;
  telegram_message_id: number | null;
  title: string;
}

export interface JobResult {
  job: string;
  dry_run: boolean;
  sent: number;
  skipped: number;
  failed: number;
  details: Record<string, unknown>[];
}

export interface JobSummary {
  dry_run: boolean;
  sent: number;
  skipped: number;
  failed: number;
  jobs: JobResult[];
}

// ---- live events (Server-Sent Events) ----

interface EventBase {
  id: number;
  ts: string;
}
export interface TurnStarted extends EventBase { kind: "turn_started"; thread_id: string; buyer_id: string; inbound_kind?: string }
export interface TurnFinished extends EventBase { kind: "turn_finished"; thread_id: string; buyer_id: string; ms: number }
export interface TurnFailed extends EventBase { kind: "turn_failed"; thread_id: string; buyer_id: string; error: string }
export interface NodeEvent extends EventBase {
  kind: "node";
  thread_id: string;
  buyer_id: string;
  node: string;
  ms: number;
  intent?: string;
  urgency?: string;
  query_text?: string;
  deal_stage?: string;
  booked?: boolean;
  transcript_failed?: boolean;
  preferences?: { max_budget_etb: number | null; min_bedrooms: number | null; preferred_sub_cities: string[] };
  search_scope?: { relaxation_step: number; max_relaxation_steps: number; effective_max_budget_etb: number | null; effective_sub_cities: string[] };
  matches?: { title: string; price_etb: number; similarity: number }[];
  compared?: string[];
  reply?: string;
}
export interface MessageEvent2 extends EventBase {
  kind: "message";
  role: "buyer" | "agent" | "broker";
  buyer_id: string;
  message_id: number;
  source: string;
  channel: string;
  text: string;
}
export interface TakeoverEvent extends EventBase { kind: "takeover"; buyer_id: string; on: boolean; name: string | null }
export interface ProactiveLiveEvent extends EventBase { kind: "proactive"; what: string; audience: string; buyer_id: string | null; channel: string; text: string }

/** One request to the language model, as the call log reports it. */
export interface ModelCallEvent extends EventBase {
  kind: "model_call";
  operation: string;
  schema_name: string | null;
  lane: string;
  model: string | null;
  outcome: string;
  latency_ms: number;
  attempt: number;
  fallbacks: string[];
  input_tokens: number | null;
  output_tokens: number | null;
  node: string | null;
  buyer_id?: string;
}
/** One call the agent made to a tool (search, calendar, routing, email, Telegram), with its arguments summarised. */
export interface ToolEvent extends EventBase {
  kind: "tool";
  tool: string;
  args: Record<string, unknown>;
  result: unknown;
  ms: number;
  ok: boolean;
  error: string | null;
  node: string | null;
  buyer_id: string | null;
}
export interface DemoResetEvent extends EventBase { kind: "demo_reset"; deleted: number }

export type LiveEvent = TurnStarted | TurnFinished | TurnFailed | NodeEvent | MessageEvent2 | TakeoverEvent | ProactiveLiveEvent | ModelCallEvent | ToolEvent | DemoResetEvent;

// ---- CRM profile, dossier, analytics ----

export interface ScoreComponent { key: string; label: string; max: number; points: number; earned: boolean }
export interface BuyerProfile {
  buyer: Buyer & { objections?: { text: string; at: string }[] };
  score: { total: number; explained: number; components: ScoreComponent[]; history: { score: number; gained: { label: string; points: number }[]; at: string }[] };
  objections: { text: string; at: string }[];
  tours: Tour[];
}

export interface DossierUnit {
  property_id: string; title: string; developer: string; area: string; price_etb: number; price_per_sqm_etb: number; bedrooms: number;
  commission_rate_pct: number | null; expected_commission_etb: number; payment_plan: string; monthly_equivalent_etb: number; reliability_score: number | null;
}
export interface Dossier {
  tour_id: string;
  kind: "SITE_VISIT" | "CONSULTATION";
  starts_at: string;
  buyer: { id: string; name: string | null; phone: string | null; email: string | null; max_budget_etb: number | null; min_bedrooms: number | null; areas: string[]; lead_score: number; deal_stage: DealStage; wants: string | null };
  objections: string[];
  recent_buyer_messages: string[];
  units: DossierUnit[];
  project?: {
    id: string; name: string; developer: string; area: string | null; location: string | null; summary: string | null; status: string;
    sqm_min: number | null; sqm_max: number | null; bedrooms_min: number | null; bedrooms_max: number | null; floors: number | null;
    price_from_etb: number | null; price_note: string | null; offer: string | null; offer_until: string | null; source_url: string;
  } | null;
  route: { distance_km: number; minutes: number } | null;
  total_value_etb: number;
  expected_commission_etb: number;
  budget_gap_etb: number | null;
  text: string;
}

export interface Analytics {
  generated_at: string;
  days: number;
  totals: { buyers: number; messages: number; tours: number };
  funnel: { stage: DealStage; buyers: number }[];
  ledger: { status: LedgerStatus; deals: number; expected_commission_etb: number; contract_value_etb: number }[];
  commission: { open_etb: number; paid_etb: number };
  messages_per_day: { day: string; role: "buyer" | "agent" | "broker"; n: number }[];
  tours_per_day: { day: string; kind: string; n: number }[];
  turns: { count: number; failed: number; p50_ms: number | null; p95_ms: number | null };
  models: { model: string; calls: number; avg_ms: number; input_tokens: number; output_tokens: number; errors: number; fell_back: number }[];
  nodes: { node: string; calls: number; avg_ms: number; tokens: number }[];
  per_conversation: { buyers: number; model_calls: number; tokens: number };
  score_distribution: { from: number; to: number; buyers: number }[];
  objections: { text: string; n: number }[];
  evaluation: {
    ran_at: string;
    retrieval?: { cases: number; hit_at_3: number; mrr: number; precision_at_3: number; correctly_empty: number; relaxation_recovered: number };
    scenarios?: { passed: number; counted: number; known_gaps: number; grounded_turns: number; turns: number };
  } | null;
}

export interface Project {
  id: string;
  developer_id: string;
  developer_name: string;
  developer_slug: string;
  name: string;
  source_url: string;
  sub_city: string | null;
  location_text: string | null;
  summary: string | null;
  bedrooms_min: number | null;
  bedrooms_max: number | null;
  sqm_min: number | null;
  sqm_max: number | null;
  floors: number | null;
  status: "SELLING" | "SOLD_OUT" | "UPCOMING";
  price_from_etb: number | null;
  price_note: string | null;
  image_url: string | null;
  promo_text: string | null;
  promo_discount_pct: number | null;
  promo_until: string | null;
  last_seen_at: string;
  open_requests: number;
  media_count: number;
  cover_media_id: number | null;
  cover_media_kind: "photo" | "video" | null;
}

export interface SweepStatus {
  sweep: {
    running: boolean;
    dry_run?: boolean;
    current?: string | null;
    last?: { dry_run: boolean; sent: number; skipped: number; failed: number; details: { developer?: string; url?: string; pages_read?: number; new?: string[]; changed?: string[]; unchanged?: number; error?: string; notes?: string[] }[] } | null;
  };
  sources: { developer: string; url: string }[];
}

export interface PriceRequest {
  id: number;
  status: "OPEN" | "ANSWERED";
  created_at: string;
  answered_at: string | null;
  project_name: string;
  buyer_name: string | null;
  thread_id: string;
}

export interface CrawlReport {
  developer: string;
  dry_run: boolean;
  pages_read: number;
  created: { name: string; area: string | null; status: string; offer: string | null; source_url: string }[];
  updated: { name: string; area: string | null; status: string; offer: string | null; source_url: string }[];
  unchanged: string[];
  notes: string[];
}

export type CrawlStatus = "QUEUED" | "RUNNING" | "FINISHED" | "BLOCKED" | "FAILED";

export interface CrawlRun {
  id: number;
  portal: string;
  portal_name: string;
  request_id: string | null;
  trigger: "dashboard" | "schedule" | "manual";
  status: CrawlStatus;
  dry_run: boolean;
  mode: "full" | "incremental" | "limited";
  page_limit: number | null;
  run_url: string | null;
  stats: Partial<Record<"pages" | "failed_pages" | "local_price" | "no_local_price" | "not_for_sale" | "model_reads" | "created" | "price_changed" | "unchanged" | "skipped" | "retired" | "batches_failed", number>>;
  blocked_reason: string | null;
  error: string | null;
  requested_at: string;
  started_at: string | null;
  finished_at: string | null;
  waiting_too_long: boolean;
  success_rate: number | null;
}

export interface PortalRuns {
  config: { portals: { slug: string; name: string }[]; can_start: boolean; min_success_rate: number };
  runs: CrawlRun[];
}

export interface LayoutRoom {
  name: string;
  x: number;
  y: number;
  w: number;
  d: number;
}

/** The rooms of a unit in metres; the 3D model and its picture are built from this on the server. */
export interface UnitLayout {
  title: string;
  width_m: number;
  depth_m: number;
  wall_height_m: number;
  rooms: LayoutRoom[];
}
