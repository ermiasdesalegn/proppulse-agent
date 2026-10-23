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
  scheduled_start: string;
  mapbox_route_json: RouteJson | null;
  reminder_sent: boolean;
  buyer_id: string;
  full_name: string | null;
  thread_id: string;
  stops: TourStop[];
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

export type LiveEvent = TurnStarted | TurnFinished | TurnFailed | NodeEvent | MessageEvent2 | TakeoverEvent | ProactiveLiveEvent;
