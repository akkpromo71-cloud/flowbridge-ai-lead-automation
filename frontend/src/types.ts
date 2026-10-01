export type Temperature = "HOT" | "WARM" | "COLD";
export type Stage =
  "new" | "contacted" | "replied" | "meeting_booked" | "won" | "lost";
export interface PublicConfig {
  brand: { name: string; tagline?: string };
  services: { id: string; name: string; description?: string }[];
  privacy_notice: string;
  timezone: string;
  mode: string;
  branding?: {
    primary_color: string;
    headline: string;
    description: string;
    primary_cta: string;
    secondary_cta: string;
  };
  config_version?: string;
  providers?: { analysis: string; draft: string; communication: string };
}
export interface Operator {
  id: string;
  email: string;
  display_name?: string;
  role?: string;
}
export interface Session {
  operator: Operator;
  csrf_token: string;
}
export interface Lead {
  id: string;
  reference?: string;
  name: string;
  email: string;
  company?: string | null;
  phone?: string | null;
  original_message?: string;
  summary?: string;
  service_id?: string | null;
  source: string;
  created_at: string;
  updated_at?: string;
  processing_status: string;
  sales_stage: Stage;
  temperature: Temperature | null;
  score: number | null;
  version: number;
  priority_override?: string | null;
  communication_stopped?: boolean;
  opted_out?: boolean;
  followup_status?: string;
  followup_due_at?: string;
}
export interface Criterion {
  key?: string;
  name?: string;
  points: number;
  maximum?: number;
  reason: string;
}
export interface Analysis {
  id?: string;
  summary?: string;
  facts?: Record<string, unknown>;
  evidence?: Record<string, unknown>;
  score: number | null;
  temperature: Temperature | null;
  contributions?: Criterion[] | Record<string, number | Criterion>;
  review_reasons?: string[];
  missing_information?: string[];
  config_version?: string;
  prompt_version?: string;
  model?: string;
  created_at?: string;
  latency_ms?: number;
}
export interface MessageVersion {
  id: string;
  revision: number;
  recipient: string;
  subject: string;
  body: string;
  created_at?: string;
  generation?: { provider?: string; model?: string; prompt_version?: string; generated_at?: string; input_tokens?: number | null; output_tokens?: number | null; origin_version_id?: string };
}
export interface Message {
  id: string;
  direction: string;
  kind: string;
  state: string;
  current_version_id?: string;
  version?: number;
  versions?: MessageVersion[];
  current_version?: MessageVersion;
  provider_accepted_at?: string;
  created_at: string;
  subject?: string;
  body?: string;
  sender?: string;
  recipient?: string;
}
export interface AuditEvent {
  id?: string;
  kind: string;
  created_at: string;
  actor?: string;
  detail?: Record<string, unknown>;
}
export interface LeadDetail extends Lead {
  analysis?: Analysis | null;
  messages: Message[];
  audit_events: AuditEvent[];
}
export interface LeadPage {
  items: Lead[];
  total: number;
  page: number;
  page_size: number;
}
export interface Analytics {
  total: number;
  temperatures: Record<string, number>;
  stages: Record<string, number>;
  sources: Record<string, number>;
  services: Record<string, number>;
  pending_approval: number;
  processing_errors: number;
  median_first_response_seconds?: number | null;
  won_conversion?: {
    numerator: number;
    denominator: number;
    value: number | null;
  };
  series: { date: string; count: number }[];
  technical?: {
    ai_calls: number;
    input_tokens: number;
    output_tokens: number;
    unknown_usage_calls: number;
    estimated_cost: null | {
      amount: string;
      currency: string;
      source: string;
      as_of: string;
    };
  };
}
export interface Integration {
  name: string;
  state: string;
  detail?: string;
  last_success_at?: string | null;
  mode?: string;
}
export interface DemoScenario {
  id: string;
  title: string;
  input: string;
  analysis: Analysis;
}
export interface PublicDemo {
  readonly: boolean;
  scenarios: DemoScenario[];
  mode: string;
}
export interface LeadSubmission {
  name: string;
  email: string;
  company: string | null;
  phone: string | null;
  message: string;
  utm: Record<string, string>;
}
