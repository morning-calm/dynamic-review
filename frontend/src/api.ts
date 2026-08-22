// Typed client for the review-app backend. Every endpoint in API_CONTRACT.md
// has a matching function here. Authenticated requests carry an opaque bearer
// token (issued by POST /api/login, persisted in localStorage); safe GETs for
// media/download additionally ride the httpOnly `review_session` cookie the
// backend sets on login — `credentials: 'include'` on every fetch lets that
// cookie travel. The frontend NEVER constructs audio paths — it uses the URLs
// the backend returns in Field.audio / Field.versions.

const TOKEN_STORAGE_KEY = 'review_app_token';

let token: string | null = null;
try {
  token = localStorage.getItem(TOKEN_STORAGE_KEY);
} catch {
  /* localStorage unavailable (private mode etc.) — falls back to in-memory only */
}

/** Current bearer token, if any (rehydrated from localStorage on module load). */
export const getToken = (): string | null => token;

/** Set (or clear, with null) the bearer token and persist it. */
export const setToken = (t: string | null): void => {
  token = t;
  try {
    if (t) localStorage.setItem(TOKEN_STORAGE_KEY, t);
    else localStorage.removeItem(TOKEN_STORAGE_KEY);
  } catch {
    /* best effort */
  }
};

export const clearToken = (): void => setToken(null);

let unauthorizedHandler: (() => void) | null = null;

/** Registered once by AuthProvider: clears app auth state on any 401 response. */
export const setUnauthorizedHandler = (fn: (() => void) | null): void => {
  unauthorizedHandler = fn;
};

// ---------------------------------------------------------------------------
// Types (mirror the contract's core objects)
// ---------------------------------------------------------------------------

export type FlagValue = 'none' | 'done' | 'edit_required';
export type RegenerateMode = 'whole' | 'highlight' | 'alt';
export type FallbackExtent = 'sentence' | 'scene' | 'custom';
export type SessionStatus =
  | 'in_review' | 'submitted' | 'approving' | 'approved' | 'changes_requested' | 'ai_review';

/** Statuses in which a reviewer may still edit text/audio/flags/narration.
 * `submitted`/`approving`/`approved` are locked (read-only) in the FE; the
 * backend enforces the same boundary with a 403 on the write endpoints.
 * `ai_review` = Gate 2 sent findings back to the reviewer: editable again, because
 * actioning a suggestion means editing the text. */
export const isEditableStatus = (s: SessionStatus): boolean =>
  s === 'in_review' || s === 'changes_requested' || s === 'ai_review';

export type Role = 'admin' | 'reviewer';

export interface AuthUser {
  username: string;
  role: Role;
  languages: string[];
}

export interface LoginResponse {
  token: string;
  user: AuthUser;
}

export interface ReviewQueueItem {
  sid: string;
  trip_id: string;
  title: string;
  language: string;
  submitted_by: string | null;
  submitted_at: number | null;
  edit_required: boolean;
  /** Delta re-review of a completed trip (changed clips only) — approving it
   * re-finalises rather than first-ships. */
  delta: boolean;
}

// --- Delta reviews: changed clips on already-completed trips -----------------

/** One changed scene from a `review-audio/_delta/<cid>.json` manifest. The
 * questionKey/questionOptionKeys mirror staging and are display hints only —
 * the session itself always shows live staging text. */
export interface DeltaScene {
  index: number;
  clips: string[];
  questionKey?: string;
  questionOptionKeys?: string[];
}

/** A compact "N changed clips" card for a COMPLETED trip whose audio/text was
 * partially regenerated after approval. Opening it seeds a session holding only
 * the changed fields; approving that session consumes the manifest. */
export interface DeltaCard {
  trip_id: string;
  title: string;
  level: string;
  family: string;
  created: string;
  reason: string;
  scenes: DeltaScene[];
  n_clips: number;
  has_session: boolean;
  status: SessionStatus | null;
}

// --- TripGroup description review (family-level) -----------------------------

export type TripDescStatus = 'pending_en' | 'translating' | 'pending_tl' | 'done';

/** One family's description-review item. Admin gets `scenes` (checking context);
 * a translator's copy carries only the texts. */
export interface TripDescItem {
  tg_id: string;
  language: string;
  family: string;
  rep_trip_id: string;
  status: TripDescStatus;
  en_text: string;
  en_original: string;
  tl_text: string;
  tl_original: string;
  categories: string[];
  last_error: string;
  en_by: string | null;
  en_at: number | null;
  tl_by: string | null;
  tl_at: number | null;
  updated_at: number;
  /** English-target family (Scotland/UK): EN approval finishes the item. */
  en_target: boolean;
  scenes?: TripDescScene[];
}

export interface TripDescScene {
  index: number;
  thumb_url: string | null;
  title: string;
  description: string;
}

/** Sibling-fit report after adding a category on the Trip descriptions page. */
export interface CategoryCheck {
  category: string;
  is_new: boolean;
  locations: { name: string; country: string }[];
  siblings: { tg_id: string; has_category: boolean; mentions: boolean; snippet: string | null }[];
}

export interface TripDescList {
  items: TripDescItem[];
  counts: Record<TripDescStatus, number>;
}

// --- Final check (admin; docs/post-approval-admin-spec.md §2) ---
export type FinalCheckKey =
  | 'desc_reread'
  | 'categories'
  | 'title_key'
  | 'trip_location'
  | 'static_images'
  | 'keywords'
  | 'thumbnail';
export type FinalCheckScope = 'trip' | 'group' | 'location';
export type FinalCheckState = 'open' | 'done';

export interface FinalCheck {
  key: FinalCheckKey;
  scope: FinalCheckScope;
  scope_id: string;
  label: string;
  /** false = no in-app tooling yet — tick by hand after doing the work outside. */
  tooling: boolean;
  state: FinalCheckState;
  by: string;
  at: number | null;
  note: string;
}

export interface FinalCheckRow {
  trip_id: string;
  /** Changed clips await a delta re-review — final check on hold. */
  pending_delta: boolean;
  lane: string; // '10' | '10b' | '11' | 'manual'
  family: string;
  language: string;
  tg_id: string | null;
  tg_resolved: boolean;
  card_url: string;
  added_by: string;
  done: number;
  total: number;
  checks: Record<FinalCheckKey, FinalCheckState>;
}

export type ReleaseRungStatus =
  | 'live'
  | 're_review'
  | 'ready'
  | 'final_check'
  | 'reviewed'
  | 'in_review'
  | 'not_started';

export interface ReleaseRung {
  trip_id: string;
  status: ReleaseRungStatus;
  /** Changed clips await a delta re-review (independent of status: a LIVE rung
   * keeps its LIVE badge but gets a hold marker). */
  pending_delta: boolean;
  /** Stage-9 finalise state from the bus ledger (subtitles/ogg/S3/re-encode/
   * enrich all covered): 'shipped' | 'restale' (re-approved since) | null. */
  finalised: 'shipped' | 'restale' | null;
  /** Recall-quiz readiness per the eligibility rule (leveled rung + ≥1 keyword
   * scene + EN rungs only for UK families). */
  recall_quiz: 'present' | 'missing' | 'na';
  /** 4K webapp stills: on the static4k ledger / has static scenes but no
   * record / no static scenes. */
  four_k: 'built' | 'missing' | 'na';
  /** A12→EN keyword-copy state (TL families' native _EN rung only). */
  keyword_copy: 'copied' | 'missing' | 'na';
  checks_done: number;
  checks_total: number;
  review_lane: string;
  completed_method: string;
  card_url: string;
}

/** A recent bus job targeting a family (its tg_id, any rung cid, or one of its
 * TripLocation doc ids) — the inline chips on the group card. */
export interface ReleaseGroupJob {
  id: string;
  kind: BusJobKind;
  trip_id: string;
  status: 'queued' | 'dry_run' | 'done' | 'failed';
  note: string;
  requested_at: number;
}

export interface ReleaseGroup {
  tg_id: string;
  in_prod: boolean;
  live_count: number;
  ready_count: number;
  /** `id` is the TripLocation DOC ID (the publish_pin job target — it diverges
   * from the display name on ~1 in 6 docs, e.g. Ainsa → "Aragon"). */
  locations: { id: string; name: string; country: string }[];
  jobs: ReleaseGroupJob[];
  rungs: ReleaseRung[];
}

/** A release batch — the named set of trips/groups/locations shipping together
 * (authored in the Publishing Queue, optionally seeded from the Trello
 * "TG Release Schedule" lane). */
export type ReleaseBatchStatus = 'planned' | 'published' | 'archived';

export interface ReleaseBatchMember {
  kind: 'trip' | 'group' | 'location';
  id: string;
}

export interface ReleaseBatch {
  id: number;
  name: string;
  status: ReleaseBatchStatus;
  source: 'manual' | 'trello';
  trello_card: string;
  members: ReleaseBatchMember[];
  created_by: string;
  created_at: number;
  updated_at: number;
  /** Members expanded to concrete ids (the FE groups the board by these). */
  resolved: { trip_ids: string[]; group_ids: string[]; location_ids: string[] };
  /** Launch-post readiness probed from the Comms tree; 'unknown' off the
   * workstation (never a false 'missing'). */
  social: {
    state: 'ready' | 'partial' | 'missing' | 'unknown';
    meta: string | null;
    linkedin: string | null;
    news: boolean | null;
  };
}

export interface ReleaseBatchList {
  batches: ReleaseBatch[];
  social_probe: 'local' | 'unavailable';
}

export interface TrelloBatchImport {
  imported: string[];
  updated: string[];
  unmatched: { card: string; token: string }[];
}

export interface PublishedTrip {
  trip_id: string;
  title: string;
  published_at: number;
  published_by: string;
  batch_id: number | null;
  source: string;
  note: string;
}

export interface PublishedList {
  months: number;
  trips: PublishedTrip[];
}

export interface ReleaseBoard {
  groups: ReleaseGroup[];
  prod_snapshot_at: string;
  prod_snapshot_has_rungs: boolean;
}

export interface CreditProposal {
  filename: string;
  status: 'proposed' | 'already_added' | 'needs_hand_edit' | 'no_attribution';
  entry: string;
  detail: string;
}

export interface CreditProposals {
  trip_id: string;
  header: string;
  proposals: CreditProposal[];
}

export interface ReleaseGroupDiff {
  tg_id: string;
  snapshot_trip: string | null;
  snapshot_at: number | null;
  prod_missing: boolean | null;
  changed: { field: string; staging: string; prod: string }[];
  hint: string;
}

export interface FinalCheckList {
  items: FinalCheckRow[];
  /** Completed trips on NO lane-10+ card — candidates for a manual start. */
  audit: { trip_id: string; method: string; completed_at: number; card_lane: string }[];
  /** false until the Trello export has run with final-lane support. */
  manifest_has_final: boolean;
}

export interface FinalCheckDetail {
  trip_id: string;
  tg_id: string | null;
  tg_resolved: boolean;
  tg_exists: boolean;
  pending_delta: boolean;
  language: string;
  locations: { name: string; country: string }[];
  checks: FinalCheck[];
  description: { home: string; target: string; tripdesc_status: TripDescStatus | null };
  categories: string[];
  title_key: {
    staging: string;
    prod_group: string | null;
    prod_trip: string | null;
    snapshot_at: number | null;
  };
}

/** A staging map-pin / extra-button entry (CustomizableMenus arrays). */
export interface MenuPin {
  LocationId: string;
  xPos: number;
  yPos: number;
  [k: string]: unknown;
}

export interface FinalLocation {
  id: string;
  contentId: string;
  locationName: string;
  locationTitleKey: string;
  locationCountry: string;
  skyboxTextureId: string;
  trips: string[]; // TripGroup ids, tile order
  groups: { tg_id: string; exists: boolean; is_this_family: boolean }[];
  pin: { menu_id: string; field: 'Pins' | 'ExtraMapButtons'; x: number; y: number } | null;
}

export interface FinalLocationModel {
  trip_id: string;
  tg_id: string;
  locations: FinalLocation[];
  menus: { id: string; map_name: string; pins: MenuPin[]; extra_buttons: MenuPin[] }[];
  skyboxes: {
    used: { id: string; count: number }[];
    manifest: string[];
    manifest_generated_at: string | null;
  };
}

export interface FinalStaticImages {
  trip_id: string;
  scenes: {
    scene_index: number;
    narration: string;
    audio_url: string;
    overlays: { filename: string; appear: number | null; disappear: number | null; url: string }[];
  }[];
  rules: { min_appear: number; min_display: number; max_display: number; gap: number };
}

export interface FinalKeywords {
  trip_id: string;
  language: string;
  scenes: {
    scene_index: number;
    question: string;
    question_en: string;
    options: string[];
    correct: string;
    additional: string[];
    is_keyword: boolean;
    question_audio: string;
    answer_audio: string;
  }[];
}

export interface CreditsDoc {
  exists: boolean;
  credits: { header: string; entries: string[] }[];
}

/** CategoryCheck + the ContentEnrichment country-mates arm. */
export interface FinalCategoryCheck extends CategoryCheck {
  enrichment_matches: {
    doc_id: string;
    tg_id: string | null;
    countries: string[];
    hits: { field: string; value: string }[];
  }[];
}

/** `approved` = completed via the normal submit→approve flow (has a session);
 * `manual` = admin bypass for work already done in the old system (no session). */
export type CompletionMethod = 'approved' | 'manual';

export interface CompletedItem {
  trip_id: string;
  title: string;
  language: string;
  method: CompletionMethod;
  completed_by: string;
  completed_at: number;
  /** The approved session, when method is `approved`; null for `manual`. */
  session_id: string | null;
  /** Stage-9 finalised-bus cross-reference (read-only, best-effort): `shipped` =
   * the current approval was finalised + uploaded (published); `restale` = shipped
   * once but re-approved since (re-finalise pending); null = not finalised. */
  finalised: 'shipped' | 'restale' | null;
  /** When Stage 9 last finalised this trip (epoch seconds); null if never. */
  finalised_at: number | null;
}

export type BugStatusValue = 'open' | 'investigating' | 'resolved';

export interface BugMessage {
  author: string;
  author_role: Role;
  body: string;
  created_at: number;
}

export interface BugReport {
  id: number;
  session_id: string | null;
  field_id: number | null;
  trip_id: string;
  scene_index: number | null;
  field_path: string;
  reporter: string;
  reporter_role: Role;
  body: string;
  status: BugStatusValue;
  created_at: number;
  updated_at: number;
  message_count: number;
  last_message_at: number | null;
  /** Snapshot audio URLs captured at report time (absent for text-only fields). */
  audio: { working?: string; candidate?: string };
  /** Present only on the detail fetch. */
  messages?: BugMessage[];
  text_snapshot?: {
    field_path?: string;
    scene_index?: number | null;
    current_text?: string;
    original_text?: string;
    working_text?: string;
    localization?: LocalizationBlock | null;
  };
}

/** Badge counts: admins get `open`, reviewers get `unread` (their reports with a new reply). */
export interface BugCounts {
  role: Role;
  open?: number;
  unread?: number;
}

// --- Presence + recall ---

/** One live user (heartbeat within the server's live window) and what they're doing. */
export interface PresenceEntry {
  username: string;
  role: Role;
  /** Human-readable context, e.g. "Scene 4 · SceneDesc — editing". */
  context: string;
  updated_at: number;
  sid: string;
  trip_id: string;
  session_status: SessionStatus;
}

export type RecallRequestStatus = 'open' | 'granted' | 'declined';

export interface RecallRequest {
  id: number;
  sid: string;
  trip_id: string;
  requested_by: string;
  reason: string;
  status: RecallRequestStatus;
  created_at: number;
  resolved_by: string | null;
  resolved_at: number | null;
  resolution_note: string;
  /** Present on the admin list fetch only. */
  session_status?: SessionStatus | null;
  completed_method?: CompletionMethod | null;
  title?: string;
  language?: string;
}

/** What the Recall button should offer right now (GET /sessions/{sid}/recall). */
export interface RecallState {
  status: SessionStatus;
  /** This user may recall (submitter or admin) from the current status. */
  can_recall: boolean;
  /** A recall would be granted immediately (no admin live, not approved). */
  auto: boolean;
  /** Why auto-recall isn't available: already approved / an admin is mid-review. */
  blocker: 'approved' | 'admin_reviewing' | null;
  /** Latest request for this session (any status) — drives the waiting/declined banners. */
  request: RecallRequest | null;
}

export interface RecallResponse {
  ok: boolean;
  /** true = auto-granted, the session is editable again (`status` says which state). */
  recalled: boolean;
  status?: SessionStatus;
  request_id?: number;
  /** true when an open request already existed (no duplicate was created). */
  existing?: boolean;
}

// --- External (stage-4b web/VR) bug reports ---

export type ExternalReportStatus = 'open' | 'acknowledged' | 'resolved';

/** A bug report filed from the customer web/VR app during stage-4b review, mirrored
 * from staging Firebase `UserReports` (only structured, scene-scoped payloads). */
export interface ExternalReport {
  id: string;
  trip_id: string;
  scene_index: number | null;
  scene_id: string | null;
  source: string; // 'web' | 'vr' | ''
  report_type: string;
  categories: string[];
  body: string;
  reporter: string;
  created_at: number | null;
  status: ExternalReportStatus;
  resolved_by: string | null;
  resolved_at: number | null;
}

export interface ExternalReportsResponse {
  trip_id: string;
  reports: ExternalReport[];
  /** Set when refresh=1 couldn't reach staging — cached rows are still returned. */
  sync_error: string | null;
}

// --- Scene-structure editor (direct staging writes, admin-only) ---

export interface StructureScene {
  index: number;
  scene_id: string | null;
  video_url: string | null;
  is_static_image: boolean;
  has_audio: boolean;
  title: string;
  desc_snippet: string;
  thumb_url: string | null;
  static_images: string[];
}

export interface StructureOpRecord {
  op: string;
  by: string;
  at: number;
  payload: Record<string, unknown>;
}

export interface TripStructure {
  trip_id: string;
  title: string;
  tripgroup_id: string;
  categories: string[];
  scenes: StructureScene[];
  /** Concurrency fingerprint — echo back on every op; 409 state_changed on mismatch. */
  base: string[];
  localization_doc: boolean;
  recent_ops: StructureOpRecord[];
}

export interface StructureOpResult {
  ok: boolean;
  warnings: string[];
  structure: TripStructure;
}

// --- Pipeline (R2 review-bus publish handshake) ---

/** A job on the R2 review bus. Queued by any admin; executed only on the workstation
 * (publisher mode / publish_inbox.py) where the production key lives. */
export type BusJobKind =
  | 'publish'
  | 'publish_docs'
  | 'publish_pin'
  | 'add_to_location'
  | 'thumbnail_local_copy'
  | 'replace_overlay'
  | 'publish_credits'
  | 'trello_move'
  | 'tool';

export interface BusJob {
  id: string;
  /** trip_id carries the kind's TARGET id: trip cid (publish/publish_docs),
   * TripGroup id (add_to_location), TripLocation id (publish_pin). */
  kind: BusJobKind;
  trip_id: string;
  note: string;
  requested_by: string;
  requested_at: number;
  status: 'queued' | 'dry_run' | 'done' | 'failed';
  resolved_by?: string;
  resolved_at?: number;
  log?: string;
}

export interface DriftResponse {
  trip_id: string;
  /** null = no prod snapshot on the bus yet (run publish_inbox.py snapshot). */
  snapshot_at: number | null;
  /** Display fields differing staging vs the prod snapshot; null when no snapshot. */
  fields_differ: string[] | null;
}

/** One row of the admin staging-wide trip search (GET /api/admin/staging-trips). */
export interface AdminStagingTrip {
  trip_id: string;
  title: string;
  folder_name: string;
  /** ", "-joined display strings of `locations`/`countries` (a trip can sit in several TripLocations). */
  location: string;
  country: string;
  /** All values, deduped — the server's location/country filters match ANY of these. */
  locations: string[];
  countries: string[];
  language: string;
  has_session: boolean;
  status: SessionStatus | null;
  edit_required: boolean;
  completed_method: CompletionMethod | null;
  completed_by: string | null;
}

export interface AdminStagingList {
  /** Matches before the 200-row cap. */
  total: number;
  shown: number;
  trips: AdminStagingTrip[];
  /** Distinct, sorted, non-empty values from the FULL index (not the filtered rows). */
  locations: string[];
  countries: string[];
}

/** field_path values from the contract's field_path table. */
export type FieldPath =
  | 'contentTitleKey'
  | 'tripgroup_description'
  | 'SceneDesc'
  | 'titleKey'
  | 'questionKey'
  | string; // questionOption[k]

export interface AudioLinks {
  original: string | null;
  working: string | null;
  candidate: string | null;
  fallback: string | null;
  /** `_ZH` A/B audition only (review-app-chinese-review.md Part 3) — set when this
   * field's take exists under both ElevenLabs versions. Null once A/B mode is
   * retired for a trip (or always, for non-`_ZH` fields). */
  v2: string | null;
  v3: string | null;
}

export interface AudioVersion {
  label: string;
  // How this take was produced. Display-only and open-ended — the backend adds kinds as
  // tools are added (currently v0_original | splice | admin_import | manual_edit |
  // noise_trim | silence_trim | insert_silence | remove_silence | wave_insert_silence |
  // wave_delete | wave_silence | wave_move | wave_insert_clip); nothing in the FE
  // switches on it.
  kind: string;
  url: string;
}

export interface ManualClip {
  id: number;
  text: string;
  kind: string; // generated | imported
  comment: string; // instructions to the admin about this take
  url: string;
  created_at: number;
}

/** The 4 scripts reviewed for Mandarin (`_ZH`) trips — NOT pinyin (regenerated
 * server-side from the confirmed Zhuyin on approve; see review-app-chinese-review.md). */
export type ZhScript = 'Hant' | 'Hans' | 'zhuyin' | 'en';

export interface LocalizationScripts {
  Hant: string;
  Hans: string;
  /** Null for fields with no phonetic script (e.g. the trip description). */
  zhuyin: string | null;
  en: string;
}

/** Present only on `_ZH` fields seeded from `TripLocalizations`; null for every
 * other field (which keeps using current_text/original_text/source_text as today). */
export interface LocalizationBlock {
  cur: LocalizationScripts;
  orig: LocalizationScripts;
  /** The Simplified hanzi the WORKING take currently says — re-baselined at each combine.
   * Undefined before the first combine (then compare against `orig.Hans`). The splice
   * engine's diff baseline for the highlight/alt tools. */
  working_hans?: string | null;
}

/** Min/max envelope of a take, for the waveform editor. `peaks` is interleaved
 * [min0, max0, min1, max1, …], one pair per bucket, each -127..127. */
export interface Waveform {
  duration: number;
  buckets: number;
  peaks: number[];
  /** Working-take content hash — changes whenever the audio does, so the view can tell
   * a stale envelope from a current one. */
  hash: string;
}

export interface Field {
  fid: number;
  scene_index: number | null;
  field_path: FieldPath;
  has_audio: boolean;
  original_text: string;
  current_text: string;
  /** What the WORKING take says (seeded to original_text; re-set at each combine) —
   * the splice engine's diff baseline. The `_ZH` sibling is `localization.working_hans`. */
  working_text: string | null;
  /** Offer the "Audio already matches" re-baseline (`api.textMatchesAudio`)? True only
   * when the text is ahead of the take (CJK: measured on the spoken text — `_ZH` Hans /
   * `_JP` kana) AND the take was shaped by hand rather than generated — i.e. the one
   * situation where the app's record of what the audio says is stale. Deliberately false
   * during ordinary edit-then-regenerate, where the audio genuinely doesn't say it yet. */
  can_accept_text_as_voiced: boolean;
  /** Editable English translation for non-_EN trips (empty when N/A / same as target). */
  source_text: string;
  /** The English at seed — for the original→new diff on the English editor. */
  original_source: string;
  /** `_ZH` 4-script block (Traditional/Simplified/Zhuyin/English); null elsewhere. */
  localization: LocalizationBlock | null;
  flag: FlagValue;
  comment: string;
  /** Who last changed this field (best-effort audit) — the approve page badges
   * fields touched by someone other than the submitter (i.e. admin touch-ups). */
  edited_by: string | null;
  splice_confidence: number | null;
  played_coverage: Array<[number, number]>;
  original_played_coverage: Array<[number, number]>;
  can_mark_done: boolean;
  can_undo: boolean;
  can_redo: boolean;
  audio: AudioLinks;
  /** The working take differs from the pristine v0 master (server hash compare — the
   * same test approve uses to decide what to promote). Badges audio-only alterations
   * on the Changes page, which the text diff misses. */
  audio_changed: boolean;
  versions: AudioVersion[];
  manual_clips: ManualClip[];
  /** The filename this field's take carries inside the per-scene download zip (e.g.
   * `Tokyo_08_scene3_questionOption1.mp3`); null when the field has no audio. Server-owned
   * — the import guard compares an uploaded file's name against it to catch a take being
   * re-imported into the WRONG field. */
  download_name: string | null;
  /** Transient (regenerate response only): a CJK surgical splice was requested but bailed,
   * so the WHOLE narration was regenerated instead. Lets the FE flag that the whole clip
   * changed. Not persisted — absent on normal field fetches. */
  cjk_fallback?: boolean;
}

export interface Overlay {
  filename: string;
  url: string;
}

export interface Scene {
  index: number;
  video_id: string | null;
  is_static_image: boolean;
  has_audio: boolean;
  image_url: string | null;
  thumb_url: string | null;
  overlays: Overlay[];
  fields: Field[];
}

/** The reviewer's per-trip pick between the two temporary A/B ElevenLabs takes. */
export type PreferredVersion = 'v2' | 'v3';

/** One field's verdict inside a Gate-2 auto-review report (scripts/claude_review.py). */
export interface AutoReviewField {
  scene: number | null;
  field: string;
  option: number | null;
  verdict: 'ok' | 'warning' | 'needs_human';
  reasons: string[];
  suggested_fix: Record<string, string> | null;
  suggested_fix_verified?: boolean | null;
}

/** How a reviewer may answer one Gate-2 finding (backend auto_review_ingest.RESPONSES).
 * `rejected` REQUIRES a note — it's the admin's only record of why the AI was overruled.
 * `deferred` = the finding is about the English/source, so it's the admin's call. */
export type FindingAction = 'resolved' | 'rejected' | 'deferred';
export type FindingStatus = 'open' | FindingAction;

/** One triage item: a non-clean Gate-2 verdict the reviewer must answer before the trip
 * goes back to the admin. */
export interface Finding {
  id: number;
  scene: number | null;
  field: string;
  option: number | null;
  verdict: 'warning' | 'needs_human';
  reasons: string[];
  suggested_fix: Record<string, string> | null;
  suggested_fix_verified: boolean | null;
  status: FindingStatus;
  note: string;
  responded_by: string | null;
  responded_at: number | null;
  created_at: number;
}

export interface FindingsPayload {
  findings: Finding[];
  open: number;
  status: SessionStatus;
}

export interface FindingsInbox {
  sessions: {
    session_id: string;
    trip_id: string;
    submitted_by: string | null;
    open: number;
    updated_at: number;
  }[];
  count: number;
}

export interface AutoReviewReport {
  id: number;
  created_at: number;
  model: string;
  status: 'ok' | 'error';
  ok: number;
  warn: number;
  flag: number;
  summary: string;
  fields: AutoReviewField[];
}

export interface Session {
  id: string;
  trip_id: string;
  folder_name: string;
  status: SessionStatus;
  submitted_by: string | null;
  approved_by: string | null;
  /** Set by admin request-changes; the reason to show the reviewer. */
  review_note: string | null;
  /** True when the trip expects narration audio but the session seeded with none
   * (masters unresolvable locally/R2 — admin text-only editing). Soft warning only;
   * audio tools are already disabled per-field. */
  audio_unavailable: boolean;
  voice: string;
  voice_display: string;
  speed: number;
  speed_override: number | null;
  model: string;
  model_override: string | null;
  trip_categories: string[];
  /** Mandarin (`_ZH`) mode flag (review-app-chinese-review.md): gates the 4-script
   * editor + V2/V3 audition and hides splice/regenerate/coverage UI. Additive —
   * every other language renders exactly as before. */
  is_zh: boolean;
  /** Narration language ("English" | "Mandarin" | "Japanese"). Gates the CJK-specific
   * SceneDesc controls (JP hides the English selection ops; its last/kana line is voiced). */
  language: string;
  /** Reviewer's per-trip V2/V3 pick for the A/B audition; null until chosen. */
  preferred_version: PreferredVersion | null;
  /** Delta review summary (null for a normal full-review session): this session
   * holds ONLY the changed clips of an already-completed trip. */
  delta: { created: string; reason: string; n_clips: number } | null;
  trip_fields: Field[];
  scenes: Scene[];
}

export interface VoiceInfo {
  name: string;
  display: string;
  gender: string;
  language: string;
  country: string;
  model: string;
}

export interface VoicesResponse {
  voices: VoiceInfo[];
  models: string[];
}

export interface NarrationUpdate {
  voice?: string;
  speed?: number;
  model?: string;
  clear_speed?: boolean;
  clear_model?: boolean;
  reset_regenerated?: boolean;
}

export interface TripListItem {
  trip_id: string;
  title: string;
  folder_name: string;
  has_session: boolean;
  status: SessionStatus | null;
  /** Any field in the latest session flagged edit_required. */
  edit_required: boolean;
  lane: string | null;
  /** Variant label (EN / A12 / B1 / N4 / HSK1-2 …) and the family (place) base id. */
  level: string;
  family: string;
  reviewable: boolean;
  /** Admin pinned this trip to the top of the reviewer list (above Trello order). */
  pinned: boolean;
  /** Admin priority score (higher = review sooner); scored trips order first. Null = none. */
  priority: number | null;
  /** The trip's language, from its id suffix ("English" / "Japanese" / "Mandarin" / …) —
   * what a reviewer holding that language ACL sees. Drives the admin's language filter. */
  language: string;
  /** Total seconds of review audio in this trip (all narration + Q&A clips), or null
   * when unknown. Manifest-stamped by the Scripts export, else measured server-side. */
  duration_sec: number | null;
}

export interface PlayedResponse {
  played_coverage: Array<[number, number]>;
  can_mark_done: boolean;
}

export interface ValidationIssue {
  scene_index: number | null;
  field_path: FieldPath;
  issue: string;
}

/** POST /submit is validate-only (no writes) — on ok it just locks the session
 * to `submitted` and awaits admin approval. */
export interface SubmitResponse {
  ok: boolean;
  validation: ValidationIssue[];
}

/** POST /approve runs the actual commit (staging text write + master mp3
 * promotion) that used to happen on submit. Admin-only. */
export interface ApproveResponse {
  ok: boolean;
  validation: ValidationIssue[];
  written: FieldPath[];
  promoted_mp3: FieldPath[];
  awaiting_stage9: boolean;
  /** _ZH only: pinyin-regeneration warnings from the staging writeback (a field whose
   * Zhuyin didn't validate fell back to hanzi-derived pinyin). */
  zh_warnings?: string[];
}

export interface ApiErrorBody {
  error: string;
  detail: string;
}

// ---------------------------------------------------------------------------
// Error type (erasableSyntaxOnly: no constructor parameter properties)
// ---------------------------------------------------------------------------

export class ApiError extends Error {
  status: number;
  code: string;
  detail: string;

  constructor(status: number, code: string, detail: string) {
    super(detail || code || `HTTP ${status}`);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

// ---------------------------------------------------------------------------
// Low-level fetch helpers
// ---------------------------------------------------------------------------

const authHeaders = (): HeadersInit => (token ? { Authorization: `Bearer ${token}` } : {});

const jsonHeaders = (): HeadersInit => ({
  ...authHeaders(),
  'Content-Type': 'application/json',
});

const throwFromResponse = async (res: Response): Promise<never> => {
  let body: Partial<ApiErrorBody> = {};
  try {
    body = (await res.json()) as Partial<ApiErrorBody>;
  } catch {
    /* non-JSON error body */
  }
  throw new ApiError(res.status, body.error ?? 'error', body.detail ?? res.statusText);
};

const requestJson = async <T>(path: string, init?: RequestInit): Promise<T> => {
  let res: Response;
  try {
    // credentials: 'include' lets the httpOnly review_session cookie ride
    // along (media/download GETs authenticate that way); writes still need
    // the explicit Authorization header set by the caller.
    res = await fetch(path, { credentials: 'include', ...init });
  } catch (e) {
    // Network failure / backend down — surface as a 0-status ApiError so the
    // UI can degrade gracefully rather than throwing a raw TypeError.
    throw new ApiError(0, 'network', e instanceof Error ? e.message : 'network error');
  }
  // Central 401 handling: an expired/invalid/revoked token clears local auth
  // state so the route guard bounces to Login. Exempt /api/login itself — a
  // bad-credentials 401 there is a form error, not a "your session expired"
  // event.
  if (res.status === 401 && path !== '/api/login') {
    clearToken();
    unauthorizedHandler?.();
  }
  if (!res.ok) await throwFromResponse(res);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
};

/** GET a binary body (the download zips). Same auth + 401 handling as requestJson, but
 * the response is a Blob the caller hands to a programmatic download. */
const fetchBlob = async (path: string): Promise<Blob> => {
  let res: Response;
  try {
    res = await fetch(path, { credentials: 'include', headers: authHeaders() });
  } catch (e) {
    throw new ApiError(0, 'network', e instanceof Error ? e.message : 'network error');
  }
  if (res.status === 401) {
    clearToken();
    unauthorizedHandler?.();
  }
  if (!res.ok) await throwFromResponse(res);
  return res.blob();
};

const getJson = <T>(path: string): Promise<T> => requestJson<T>(path, { headers: authHeaders() });

const postJson = <T>(path: string, body?: unknown): Promise<T> =>
  requestJson<T>(path, {
    method: 'POST',
    headers: jsonHeaders(),
    body: body === undefined ? undefined : JSON.stringify(body),
  });

const putJson = <T>(path: string, body: unknown): Promise<T> =>
  requestJson<T>(path, {
    method: 'PUT',
    headers: jsonHeaders(),
    body: JSON.stringify(body),
  });

// ---------------------------------------------------------------------------
// Endpoint functions
// ---------------------------------------------------------------------------

const field = (sid: string, fid: number, suffix = ''): string =>
  `/api/sessions/${encodeURIComponent(sid)}/fields/${fid}${suffix}`;

export const api = {
  health: (): Promise<{ ok: boolean }> => getJson('/api/health'),

  listTrips: (): Promise<TripListItem[]> => getJson('/api/trips'),

  /** Delta cards: completed trips with an unconsumed `_delta/<cid>.json` manifest
   * on R2 (changed clips awaiting re-confirmation). Language-filtered server-side. */
  listDeltas: (): Promise<DeltaCard[]> => getJson('/api/deltas'),

  /** Open (or resume) the delta session for a completed trip — a normal session
   * seeded with only the manifest's changed fields. Never resets completed status. */
  openDelta: (tripId: string): Promise<Session> =>
    postJson(`/api/deltas/${encodeURIComponent(tripId)}/open`),

  /** ADMIN: drop the open zero-work delta session WITHOUT consuming the manifest,
   * so the card re-seeds fresh on the next open. 409 if it holds reviewer work. */
  discardDelta: (tripId: string): Promise<{ ok: boolean; discarded: string }> =>
    postJson(`/api/deltas/${encodeURIComponent(tripId)}/discard`),

  listVoices: (): Promise<VoicesResponse> => getJson('/api/voices'),

  setNarration: (sid: string, body: NarrationUpdate): Promise<Session> =>
    postJson(`/api/sessions/${encodeURIComponent(sid)}/narration`, body),

  createOrResumeSession: (tripId: string): Promise<Session> =>
    postJson('/api/sessions', { trip_id: tripId }),

  getSession: (sid: string): Promise<Session> => getJson(`/api/sessions/${encodeURIComponent(sid)}`),

  putField: (sid: string, fid: number, currentText: string): Promise<Field> =>
    putJson(field(sid, fid), { current_text: currentText }),

  putSource: (sid: string, fid: number, text: string): Promise<Field> =>
    putJson(field(sid, fid, '/source'), { text }),

  /** `_ZH` only: autosave one script of the 4-script block (Hant/Hans/zhuyin/en). */
  putLocalization: (sid: string, fid: number, script: ZhScript, text: string): Promise<Field> =>
    putJson(field(sid, fid, '/localization'), { script, text }),

  /** Gate 2 of the auto-review pipeline: the latest Claude report (null until the
   * server-side runner has reviewed this session's submission). */
  getAutoReview: (sid: string): Promise<{ report: AutoReviewReport | null }> =>
    getJson(`/api/sessions/${encodeURIComponent(sid)}/auto-review`),

  /** `_ZH` only: apply a machine-verified suggested fix from the latest Gate-2 report to
   * one field (identified by its report location). Returns the updated field + a fresh
   * Gate-1 pass so any newly-introduced blocker is visible. */
  applySuggestedFix: (
    sid: string,
    loc: { scene: number; field: string; option: number | null },
  ): Promise<{ field: Field; applied: string[]; skipped: { script: string; reason: string }[] }> =>
    postJson(`/api/sessions/${encodeURIComponent(sid)}/auto-review/apply`, loc),

  /** Gate-2 triage: the findings the reviewer must answer before the trip returns to the
   * admin. Present for any session; `open > 0` is what holds it in `ai_review`. */
  getFindings: (sid: string): Promise<FindingsPayload> =>
    getJson(`/api/sessions/${encodeURIComponent(sid)}/findings`),

  /** Answer one finding. `rejected` requires a note (the admin reads it). */
  respondFinding: (
    sid: string,
    fid: number,
    action: FindingAction,
    note = '',
  ): Promise<FindingsPayload> =>
    postJson(`/api/sessions/${encodeURIComponent(sid)}/findings/${fid}/respond`, { action, note }),

  /** ADMIN: reclaim an `ai_review` trip without the reviewer's triage (open findings are
   * marked deferred-to-admin). The escape hatch so the gate can't wedge a trip. */
  skipFindingsTriage: (sid: string, note = ''): Promise<FindingsPayload> =>
    postJson(`/api/sessions/${encodeURIComponent(sid)}/findings/skip`, { note }),

  /** Nav badge: sessions waiting on THIS user's AI-review triage. */
  getFindingsInbox: (): Promise<FindingsInbox> => getJson('/api/findings/inbox'),

  regenerate: (
    sid: string,
    fid: number,
    mode: RegenerateMode,
    range?: { start: number; end: number },
    altText?: string,
    model?: string, // one-off ElevenLabs model for THIS candidate (e.g. 'eleven_v3')
  ): Promise<Field> =>
    postJson(field(sid, fid, '/regenerate'), {
      mode,
      ...(range ? { range } : {}),
      ...(altText !== undefined ? { alt_text: altText } : {}),
      ...(model ? { model } : {}),
    }),

  combine: (sid: string, fid: number): Promise<Field> => postJson(field(sid, fid, '/combine')),

  // Nudge the trailing trim on the current candidate before combining (drop a TTS
  // breath/next-sound bleed). deltaMs > 0 trims more off the end, < 0 restores.
  trimCandidate: (sid: string, fid: number, deltaMs: number): Promise<Field> =>
    postJson(field(sid, fid, '/trim-candidate'), { delta_ms: deltaMs }),

  // Manual backstop: trim a leftover sliver/noise the reviewer highlighted in the narration.
  trimNoise: (sid: string, fid: number, start: number, end: number): Promise<Field> =>
    postJson(field(sid, fid, '/trim'), { start, end }),

  // Normalize the trailing pause: beginner-trip NARRATION (SceneDesc) = 3s; questions,
  // options and every other level keep a small 0.4s tail (excess trimmed).
  trimSilence: (sid: string, fid: number): Promise<Field> =>
    postJson(field(sid, fid, '/trim-silence')),

  // Insert `seconds` of silence into the working take at the TEXT caret `pos` (char offset).
  insertSilence: (sid: string, fid: number, pos: number, seconds = 1): Promise<Field> =>
    postJson(field(sid, fid, '/insert-silence'), { pos, seconds }),

  // Shorten the pause at the TEXT caret by up to `seconds` (inverse of insertSilence; a
  // minimum natural pause always remains — 409 when there's no excess to remove).
  removeSilence: (sid: string, fid: number, pos: number, seconds = 1): Promise<Field> =>
    postJson(field(sid, fid, '/remove-silence'), { pos, seconds }),

  // --- Waveform editor ------------------------------------------------------------
  // These address the audio by TIME, straight off the waveform, instead of through a
  // text caret — so they need no Whisper/aligner mapping (fast) and can put a cut
  // exactly where the reviewer says (precise), at the cost of the text-anchored tools'
  // safety rails. Every one archives a version and re-locks the Done gate.
  waveform: (sid: string, fid: number, track: 'working' | 'original' = 'working'): Promise<Waveform> =>
    getJson(field(sid, fid, `/waveform?track=${track}`)),

  waveInsertSilence: (sid: string, fid: number, at: number, seconds: number): Promise<Field> =>
    postJson(field(sid, fid, '/wave/insert-silence'), { at, seconds }),

  // Remove the selected span entirely (the two sides are butted together).
  waveDelete: (sid: string, fid: number, start: number, end: number): Promise<Field> =>
    postJson(field(sid, fid, '/wave/delete'), { start, end }),

  // Blank the selected span to silence, keeping the clip's length.
  waveSilence: (sid: string, fid: number, start: number, end: number): Promise<Field> =>
    postJson(field(sid, fid, '/wave/silence'), { start, end }),

  // Cut [start,end) out and paste it at `to` (all measured on the clip as it stands now).
  waveMove: (sid: string, fid: number, start: number, end: number, to: number): Promise<Field> =>
    postJson(field(sid, fid, '/wave/move'), { start, end, to }),

  // Drop a "Create new" take into the working audio at `at` — the paste that completes
  // "voice a replacement → delete the bad audio → insert the new take". Level-matched to
  // the surrounding audio server-side.
  waveInsertClip: (sid: string, fid: number, at: number, clipId: number): Promise<Field> =>
    postJson(field(sid, fid, '/wave/insert-clip'), { at, clip_id: clipId }),

  /**
   * "The audio already says this" — re-baseline the working take's text to the field's
   * current text after the reviewer made the audio match BY HAND (waveform editor).
   * Nothing is generated and no audio changes; it only records that take and text agree,
   * which is what the splice engine diffs against. Without it, hand-removing audio for
   * deleted words wedges every text-addressed tool (409 `unvoiced_edits_outside_highlight`
   * one way, "edit removed text only" the other) — offer it only while
   * `field.can_accept_text_as_voiced`.
   */
  textMatchesAudio: (sid: string, fid: number): Promise<Field> =>
    postJson(field(sid, fid, '/text-matches-audio')),

  fallback: (
    sid: string,
    fid: number,
    extent: FallbackExtent,
    description: string,
    text?: string,
  ): Promise<Field> =>
    postJson(field(sid, fid, '/fallback'), text !== undefined ? { extent, description, text } : { extent, description }),

  importMp3: async (sid: string, fid: number, file: File): Promise<Field> => {
    const form = new FormData();
    form.append('file', file);
    // NOTE: do not set Content-Type — the browser sets the multipart boundary.
    return requestJson<Field>(field(sid, fid, '/import-mp3'), {
      method: 'POST',
      headers: authHeaders(),
      body: form,
    });
  },

  postPlayed: (
    sid: string,
    fid: number,
    ranges: Array<[number, number]>,
    track: 'working' | 'original' = 'working',
  ): Promise<PlayedResponse> => postJson(field(sid, fid, '/played'), { ranges, track }),

  postFlag: (sid: string, fid: number, flag: FlagValue): Promise<Field> =>
    postJson(field(sid, fid, '/flag'), { flag }),

  postComment: (sid: string, fid: number, text: string): Promise<Field> =>
    postJson(field(sid, fid, '/comment'), { text }),

  revert: (sid: string, fid: number): Promise<Field> => postJson(field(sid, fid, '/revert')),

  // Step the working audio back/forward through its version history (undo/redo).
  undoAudio: (sid: string, fid: number): Promise<Field> => postJson(field(sid, fid, '/undo')),
  redoAudio: (sid: string, fid: number): Promise<Field> => postJson(field(sid, fid, '/redo')),

  // --- "Create new" attachments (manual edit): new takes for the admin, NOT the working take ---
  createClip: (sid: string, fid: number, text: string, comment: string, model?: string): Promise<Field> =>
    postJson(field(sid, fid, '/clips'), { text, comment, ...(model ? { model } : {}) }),

  importClip: async (sid: string, fid: number, file: File, comment: string): Promise<Field> => {
    const form = new FormData();
    form.append('file', file);
    form.append('comment', comment);
    return requestJson<Field>(field(sid, fid, '/clips/upload'), {
      method: 'POST',
      headers: authHeaders(),
      body: form,
    });
  },

  regenClip: (sid: string, fid: number, cid: number, text?: string, model?: string): Promise<Field> =>
    postJson(field(sid, fid, `/clips/${cid}/regenerate`), { text, ...(model ? { model } : {}) }),

  // Attach / edit the admin note on a take. A non-empty note commits a draft (flags the
  // field edit-required); '' leaves it a draft.
  setClipComment: (sid: string, fid: number, cid: number, comment: string): Promise<Field> =>
    postJson(field(sid, fid, `/clips/${cid}/comment`), { comment }),

  deleteClip: (sid: string, fid: number, cid: number): Promise<Field> =>
    requestJson<Field>(field(sid, fid, `/clips/${cid}`), { method: 'DELETE', headers: jsonHeaders() }),

  /**
   * Download the whole-session zip (admin only). A plain <a href> can't send the
   * Authorization header (→ 401), so the caller fetches the blob with the header and
   * triggers a programmatic download.
   */
  downloadZip: (sid: string): Promise<Blob> =>
    fetchBlob(`/api/sessions/${encodeURIComponent(sid)}/download`),

  /**
   * Download ONE scene's audio (admin only): every audio field's working take, named for
   * the field it belongs to, plus the pristine v0s under `orig/`. The round trip for a
   * reviewer's `edit_required` flag — fix the mp3 offline, re-import it at that field.
   */
  downloadSceneZip: (sid: string, sceneIndex: number): Promise<Blob> =>
    fetchBlob(`/api/sessions/${encodeURIComponent(sid)}/scenes/${sceneIndex}/download`),

  /** Reviewer/admin: validate only (no writes) and lock the session to `submitted`. */
  submit: (sid: string): Promise<SubmitResponse> =>
    postJson(`/api/sessions/${encodeURIComponent(sid)}/submit`),

  /** Admin only: commit — staging text write + master mp3 promotion. 409 if the
   * session isn't currently `submitted` (double-click / two admins racing). */
  approve: (sid: string): Promise<ApproveResponse> =>
    postJson(`/api/sessions/${encodeURIComponent(sid)}/approve`),

  /** Admin only: send the session back to the reviewer with a note. */
  requestChanges: (sid: string, note: string): Promise<{ ok: boolean }> =>
    postJson(`/api/sessions/${encodeURIComponent(sid)}/request-changes`, { note }),

  /** Admin only: sessions currently awaiting approval. */
  reviewQueue: (): Promise<ReviewQueueItem[]> => getJson('/api/review-queue'),

  /** Nav-badge count of submitted sessions awaiting the admin ({open:0} for reviewers). */
  reviewQueueCount: (): Promise<{ open: number }> => getJson('/api/review-queue/count'),

  /** Both roles: trips that are done (approved or manually completed). Reviewers
   * are filtered to their languages server-side; sorted newest first. */
  completed: (): Promise<CompletedItem[]> => getJson('/api/completed'),

  /** Admin only, bypass: mark a trip complete without a review session (work
   * already done in the old system). Writes nothing to staging/masters — purely
   * a workflow marker. Idempotent upsert; 200 even if the trip has no session. */
  completeTrip: (tripId: string, note?: string): Promise<{ ok: boolean }> =>
    postJson(`/api/trips/${encodeURIComponent(tripId)}/complete`, note !== undefined ? { note } : undefined),

  /** Admin only: un-complete — the trip returns to the main list and is reviewable again. */
  uncompleteTrip: (tripId: string): Promise<{ ok: boolean }> =>
    requestJson<{ ok: boolean }>(`/api/trips/${encodeURIComponent(tripId)}/complete`, {
      method: 'DELETE',
      headers: jsonHeaders(),
    }),

  /** Admin only: pin a trip to the top of the reviewer list (above the Trello base order). */
  pinTrip: (tripId: string): Promise<{ ok: boolean }> =>
    postJson(`/api/trips/${encodeURIComponent(tripId)}/pin`),

  /** Admin only: remove a trip's pin — it returns to the Trello base order. */
  unpinTrip: (tripId: string): Promise<{ ok: boolean }> =>
    requestJson<{ ok: boolean }>(`/api/trips/${encodeURIComponent(tripId)}/pin`, {
      method: 'DELETE',
      headers: jsonHeaders(),
    }),

  /** Admin only: set (score) or clear (null) a trip's priority — higher scores order
   * first in every reviewer's list, above pins. */
  setTripPriority: (tripId: string, score: number | null): Promise<{ ok: boolean }> =>
    postJson(`/api/trips/${encodeURIComponent(tripId)}/priority`, { score }),

  login: (username: string, password: string): Promise<LoginResponse> =>
    postJson('/api/login', { username, password }),

  logout: (): Promise<void> => requestJson<void>('/api/logout', { method: 'POST', headers: authHeaders() }),

  me: (): Promise<AuthUser> => getJson('/api/me'),

  // --- External (stage-4b web/VR) bug reports ---
  /** Reports for this session's trip; refresh=true re-syncs from staging first. */
  externalReports: (sid: string, refresh = false): Promise<ExternalReportsResponse> =>
    getJson(`/api/sessions/${encodeURIComponent(sid)}/external-reports${refresh ? '?refresh=1' : ''}`),

  /** Admin only: triage an external report (mirrored back to the staging doc). */
  setExternalReportStatus: (reportId: string, status: ExternalReportStatus): Promise<ExternalReport> =>
    postJson(`/api/external-reports/${encodeURIComponent(reportId)}/status`, { status }),

  // --- Admin staging-wide editor (search/open ANY staging trip) ---
  /** Admin only: search the whole staging Trips collection by id/title substring. */
  adminStagingTrips: (q: string, refresh = false, location = '', country = ''): Promise<AdminStagingList> =>
    getJson(`/api/admin/staging-trips?q=${encodeURIComponent(q)}${refresh ? '&refresh=1' : ''}`
      + (location ? `&location=${encodeURIComponent(location)}` : '')
      + (country ? `&country=${encodeURIComponent(country)}` : '')),

  /** Admin only: open ANY staging trip (bypasses the manifest + completed exclusion). */
  adminOpenTrip: (tripId: string): Promise<Session> =>
    postJson('/api/admin/open', { trip_id: tripId }),

  // --- Scene-structure editor (admin; immediate staging writes) ---
  getStructure: (tripId: string): Promise<TripStructure> =>
    getJson(`/api/admin/structure/${encodeURIComponent(tripId)}`),
  structureReorder: (tripId: string, order: number[], base: string[]): Promise<StructureOpResult> =>
    postJson(`/api/admin/structure/${encodeURIComponent(tripId)}/reorder`, { order, base }),
  structureRemove: (tripId: string, index: number, base: string[]): Promise<StructureOpResult> =>
    postJson(`/api/admin/structure/${encodeURIComponent(tripId)}/remove`, { index, base }),
  structureAdd: (
    tripId: string,
    position: number,
    base: string[],
    opts: { video_url?: string; is_static?: boolean; scene_id?: string },
  ): Promise<StructureOpResult> =>
    postJson(`/api/admin/structure/${encodeURIComponent(tripId)}/add`, { position, base, ...opts }),
  structureSwapVideo: (
    tripId: string,
    index: number,
    videoUrl: string,
    rekey: boolean,
    base: string[],
    sceneId?: string,
  ): Promise<StructureOpResult> =>
    postJson(`/api/admin/structure/${encodeURIComponent(tripId)}/swap-video`, {
      index,
      video_url: videoUrl,
      rekey,
      base,
      ...(sceneId ? { scene_id: sceneId } : {}),
    }),
  structureStaticImages: (
    tripId: string,
    index: number,
    filenames: string[],
    base: string[],
  ): Promise<StructureOpResult> =>
    postJson(`/api/admin/structure/${encodeURIComponent(tripId)}/static-images`, { index, filenames, base }),
  structureCategories: (tripId: string, categories: string[]): Promise<StructureOpResult> =>
    postJson(`/api/admin/structure/${encodeURIComponent(tripId)}/categories`, { categories }),

  /** Admin only: content-enrichment category proposals for a trip (one-tap add
   * suggestions on the review-page category editor). Best-effort; empty when the trip
   * has no ContentEnrichment sidecar on staging. */
  enrichmentCategories: (tripId: string): Promise<{ applicable: string[]; suggestions: string[] }> =>
    getJson(`/api/admin/enrichment-categories/${encodeURIComponent(tripId)}`),

  // --- Pipeline (publish bus) ---
  /** Admin only: queue a staging→prod TEXT publish request on the R2 bus. */
  queuePublish: (tripId: string, note = ''): Promise<BusJob> =>
    postJson('/api/admin/pipeline/queue', { trip_id: tripId, kind: 'publish', note }),

  /** Admin only: jobs on the bus (optionally one trip's). */
  pipelineJobs: (tripId = ''): Promise<{ publisher_mode: boolean; jobs: BusJob[] }> =>
    getJson(`/api/admin/pipeline/jobs${tripId ? `?trip_id=${encodeURIComponent(tripId)}` : ''}`),

  /** Publisher mode only: execute a queued job (dry-run unless both flags true). */
  runPipelineJob: (jobId: string, apply = false, iAmSure = false): Promise<BusJob> =>
    postJson('/api/admin/pipeline/run', { job_id: jobId, apply, i_am_sure: iAmSure }),

  /** Admin only: queue any bus job kind (the trip_id field carries the kind's target id). */
  queueBusJob: (kind: BusJobKind, targetId: string, note = ''): Promise<BusJob> =>
    postJson('/api/admin/pipeline/queue', { trip_id: targetId, kind, note }),

  /** Admin only: is THIS instance the workstation publisher (nav gating). */
  publisherMode: (): Promise<{ publisher_mode: boolean }> =>
    getJson('/api/admin/publisher-mode'),

  /** Publisher mode only: run the read-only audio-gate sweep (long — S3 per rung). */
  gateReport: (): Promise<{ ok: boolean; log: string }> =>
    postJson('/api/admin/pipeline/gate-report'),

  /** Admin only: staging vs live drift for a trip (vs the bus prod snapshot). */
  drift: (tripId: string): Promise<DriftResponse> =>
    getJson(`/api/admin/drift/${encodeURIComponent(tripId)}`),

  // --- Presence + recall ---
  /** Presence ping (~30s while a session page is open): what this user is looking at.
   * Allowed in any session state — an admin's heartbeat on a submitted trip is what
   * turns a reviewer's recall into a request instead of a silent yank. */
  heartbeat: (sid: string, context: string): Promise<{ ok: boolean }> =>
    postJson(`/api/sessions/${encodeURIComponent(sid)}/heartbeat`, { context }),

  /** Everyone live right now (reviewers see their languages only, like the trip list). */
  presence: (): Promise<PresenceEntry[]> => getJson('/api/presence'),

  /** What the Recall button should offer for this session right now. */
  recallState: (sid: string): Promise<RecallState> =>
    getJson(`/api/sessions/${encodeURIComponent(sid)}/recall`),

  /** Recall a submitted trip. Without a reason: auto-grants when possible, else 409
   * `reason_required` — re-call with the reason to file a pinned admin request. */
  recall: (sid: string, reason = ''): Promise<RecallResponse> =>
    postJson(`/api/sessions/${encodeURIComponent(sid)}/recall`, { reason }),

  /** Admin only: recall requests (default the open ones, pinned atop the queue). */
  recallRequests: (status: RecallRequestStatus = 'open'): Promise<RecallRequest[]> =>
    getJson(`/api/recall-requests?status=${encodeURIComponent(status)}`),

  /** Admin only: open recall-request count for the nav badge. */
  recallCounts: (): Promise<{ open: number }> => getJson('/api/recall-requests/count'),

  /** Admin only: grant (send back to reviewer; un-completes an approved trip first)
   * or decline a recall request. */
  resolveRecall: (
    rid: number,
    action: 'grant' | 'decline',
    note = '',
  ): Promise<{ ok: boolean; session_status: SessionStatus | null }> =>
    postJson(`/api/recall-requests/${rid}/resolve`, { action, note }),

  // --- Bug reports ---
  createBugReport: (sid: string, fid: number, body: string): Promise<BugReport> =>
    postJson(field(sid, fid, '/bug-report'), { body }),
  listBugReports: (): Promise<BugReport[]> => getJson('/api/bug-reports'),
  getBugReport: (rid: number): Promise<BugReport> => getJson(`/api/bug-reports/${rid}`),
  replyBugReport: (rid: number, body: string): Promise<BugReport> =>
    postJson(`/api/bug-reports/${rid}/messages`, { body }),
  setBugStatus: (rid: number, status: BugStatusValue): Promise<BugReport> =>
    postJson(`/api/bug-reports/${rid}/status`, { status }),
  bugCounts: (): Promise<BugCounts> => getJson('/api/bug-reports/count'),

  // --- TripGroup description review (family-level; docs/tripgroup-description-review-proposal.md)
  listTripDescs: (): Promise<TripDescList> => getJson('/api/tripdesc'),
  tripDescCounts: (): Promise<{ open: number }> => getJson('/api/tripdesc/count'),
  getTripDesc: (tgId: string): Promise<TripDescItem> =>
    getJson(`/api/tripdesc/${encodeURIComponent(tgId)}`),
  saveTripDesc: (
    tgId: string,
    body: { en_text?: string; categories?: string[]; tl_text?: string },
  ): Promise<TripDescItem> => putJson(`/api/tripdesc/${encodeURIComponent(tgId)}`, body),
  approveTripDescEn: (tgId: string): Promise<TripDescItem> =>
    postJson(`/api/tripdesc/${encodeURIComponent(tgId)}/approve-en`),
  approveTripDescTl: (tgId: string): Promise<TripDescItem> =>
    postJson(`/api/tripdesc/${encodeURIComponent(tgId)}/approve-tl`),
  retryTripDescTranslate: (tgId: string): Promise<TripDescItem> =>
    postJson(`/api/tripdesc/${encodeURIComponent(tgId)}/retry-translate`),
  reopenTripDesc: (tgId: string): Promise<TripDescItem> =>
    postJson(`/api/tripdesc/${encodeURIComponent(tgId)}/reopen`),
  /** Admin: the category vocabulary with counts — scoped to `tgId`'s country when
   * given (global fallback when the group sits in no TripLocation). */
  tripDescCategories: (
    tgId?: string,
  ): Promise<{ categories: { name: string; count: number }[]; scope: string }> =>
    getJson(`/api/tripdesc/categories${tgId ? `?tg_id=${encodeURIComponent(tgId)}` : ''}`),
  /** Admin: is this category new, and do same-location siblings look like they fit it? */
  tripDescCategoryCheck: (tgId: string, category: string): Promise<CategoryCheck> =>
    getJson(
      `/api/tripdesc/${encodeURIComponent(tgId)}/category-check?category=${encodeURIComponent(category)}`,
    ),

  // --- Final check (admin only; docs/post-approval-admin-spec.md §2) ---
  finalCount: (): Promise<{ open: number }> => getJson('/api/final/count'),
  listFinalChecks: (): Promise<FinalCheckList> => getJson('/api/final'),
  listReleases: (): Promise<ReleaseBoard> => getJson('/api/final/releases'),
  releaseGroupDiff: (tgId: string): Promise<ReleaseGroupDiff> =>
    getJson(`/api/final/releases/${encodeURIComponent(tgId)}/diff`),

  /** Release batches + their resolved membership and social-post readiness. */
  listReleaseBatches: (): Promise<ReleaseBatchList> => getJson('/api/final/batches'),

  /** Create (omit `id`) or update a release batch. A blank `id` with an existing
   * name updates that batch server-side. */
  saveReleaseBatch: (body: {
    name: string;
    members: ReleaseBatchMember[];
    id?: number;
    status?: ReleaseBatchStatus;
  }): Promise<ReleaseBatch> => postJson('/api/final/batches', body),

  /** Seed/update batches from the Trello "TG Release Schedule" lane. */
  importReleaseBatches: (): Promise<TrelloBatchImport> =>
    postJson('/api/final/batches/import-trello'),

  deleteReleaseBatch: (batchId: number): Promise<{ deleted: number }> =>
    requestJson<{ deleted: number }>(`/api/final/batches/${batchId}`, {
      method: 'DELETE',
      headers: jsonHeaders(),
    }),

  /** The durable published_trips ledger — "Recently published" on the Publisher. */
  recentlyPublished: (months = 12): Promise<PublishedList> =>
    getJson(`/api/final/published?months=${months}`),
  creditProposals: (tripId: string): Promise<CreditProposals> =>
    getJson(`/api/final/${encodeURIComponent(tripId)}/credit-proposals`),
  /** Publisher mode only: write a diagnostic bundle for a failed job and open a
   * new terminal running claude (opus, high effort) pre-briefed on it. */
  investigateJob: (jobId: string): Promise<{ bundle: string; launched: boolean }> =>
    postJson('/api/admin/pipeline/investigate', { job_id: jobId }),
  getFinalCheck: (tripId: string): Promise<FinalCheckDetail> =>
    getJson(`/api/final/${encodeURIComponent(tripId)}`),
  /** Manual "start final check" for a completed trip on no lane-10+ card. */
  startFinalCheck: (tripId: string): Promise<{ trip_id: string; started: boolean }> =>
    postJson('/api/final/start', { trip_id: tripId }),
  setFinalCheck: (
    tripId: string,
    key: FinalCheckKey,
    state: FinalCheckState,
    note = '',
  ): Promise<FinalCheck> =>
    postJson(`/api/final/${encodeURIComponent(tripId)}/check/${key}`, { state, note }),
  /** Targeted staging write: the TripGroup's contentTitleKey. */
  saveFinalTitleKey: (
    tripId: string,
    value: string,
  ): Promise<{ tg_id: string; contentTitleKey: string }> =>
    putJson(`/api/final/${encodeURIComponent(tripId)}/title-key`, { value }),
  /** Targeted staging write: the TripGroup's tripCategories. */
  saveFinalCategories: (
    tripId: string,
    categories: string[],
  ): Promise<{ tg_id: string; categories: string[] }> =>
    putJson(`/api/final/${encodeURIComponent(tripId)}/categories`, { categories }),
  /** Sibling-description check + ContentEnrichment country-mates for a category. */
  finalCategoryCheck: (tripId: string, category: string): Promise<FinalCategoryCheck> =>
    getJson(
      `/api/final/${encodeURIComponent(tripId)}/category-check?category=${encodeURIComponent(category)}`,
    ),
  /** Check-1 escape hatch: (re)open the family's tripdesc item; returns its tg_id. */
  reopenFinalDescription: (tripId: string): Promise<{ tg_id: string }> =>
    postJson(`/api/final/${encodeURIComponent(tripId)}/reopen-description`),
  /** Check-4 read model: TripLocation docs, menus/pins, skybox vocabulary. */
  getFinalLocation: (tripId: string): Promise<FinalLocationModel> =>
    getJson(`/api/final/${encodeURIComponent(tripId)}/location`),
  /** Targeted staging TripLocation update (title key / skybox / trips REORDER). */
  saveFinalLocation: (
    tripId: string,
    body: { loc_id: string; locationTitleKey?: string; skyboxTextureId?: string; trips?: string[] },
  ): Promise<{ loc_id: string; updated: string[] }> =>
    putJson(`/api/final/${encodeURIComponent(tripId)}/location`, body),
  /** Check-7: the TripGroup thumbnail (stem + public R2 url + existence check). */
  getFinalThumbnail: (
    tripId: string,
  ): Promise<{ tg_id: string; thumbnailTextureId: string; url: string | null; on_r2: boolean | null }> =>
    getJson(`/api/final/${encodeURIComponent(tripId)}/thumbnail`),
  /** Replace the family thumbnail: R2 + staging field + a thumbnail_local_copy bus job. */
  uploadFinalThumbnail: async (
    tripId: string,
    file: File,
  ): Promise<{
    tg_id: string;
    thumbnailTextureId: string;
    url: string | null;
    on_r2: boolean | null;
    local_copy_job: string | null;
  }> => {
    const form = new FormData();
    form.append('file', file);
    return requestJson(`/api/final/${encodeURIComponent(tripId)}/thumbnail`, {
      method: 'POST',
      headers: authHeaders(),
      body: form,
    });
  },
  /** Upsert the STAGING map pin (prod follows at publish via the publish_pin job). */
  saveFinalPin: (
    tripId: string,
    body: { loc_id: string; menu_id: string; x: number; y: number },
  ): Promise<{ menu_id: string; field: string; loc_id: string; x: number; y: number }> =>
    putJson(`/api/final/${encodeURIComponent(tripId)}/pin`, body),
  /** Check-5: scenes with staticImages[] + timing + audio/image urls. */
  getFinalStaticImages: (tripId: string): Promise<FinalStaticImages> =>
    getJson(`/api/final/${encodeURIComponent(tripId)}/static-images`),
  /** Targeted staging write of one overlay's appear/disappear (0.1s floats, stage10 rules as warnings). */
  setFinalImageTiming: (
    tripId: string,
    body: { scene_index: number; filename: string; appear: number; disappear: number },
  ): Promise<{ scene_index: number; filename: string; appear: number; disappear: number; warnings: string[] }> =>
    putJson(`/api/final/${encodeURIComponent(tripId)}/static-images/timing`, body),
  /** Replace an overlay image: R2 review copy now + a replace_overlay bus job for
   * the canonical distribution (stage10_static_check.py replace on the workstation). */
  replaceFinalOverlay: async (
    tripId: string,
    filename: string,
    file: File,
  ): Promise<{ filename: string; r2_key: string; family: string; replace_job: string | null }> => {
    const form = new FormData();
    form.append('file', file);
    return requestJson(
      `/api/final/${encodeURIComponent(tripId)}/static-images/replace?filename=${encodeURIComponent(filename)}`,
      { method: 'POST', headers: authHeaders(), body: form },
    );
  },
  /** Undo the last Replace-image of one overlay (one-level revert). */
  revertFinalOverlay: (
    tripId: string,
    filename: string,
  ): Promise<{ filename: string; mode: string; replace_job: string | null }> =>
    postJson(
      `/api/final/${encodeURIComponent(tripId)}/static-images/revert?filename=${encodeURIComponent(filename)}`,
      {},
    ),
  /** The app's single credits button: CustomizableMenus/Credits (format fixed by the VR app). */
  getFinalCredits: (): Promise<CreditsDoc> => getJson('/api/final/credits'),
  /** Append one credit entry under a header (add-only; 409 duplicate_credit). */
  addFinalCredit: (header: string, entry: string): Promise<CreditsDoc> =>
    postJson('/api/final/credits', { header, entry }),
  /** Check-6 read model: Q&A/keyword scenes + accepted sets + audio urls. */
  getFinalKeywords: (tripId: string): Promise<FinalKeywords> =>
    getJson(`/api/final/${encodeURIComponent(tripId)}/keywords`),
  /** Add-only additionalAnswerKeys append (collision-checked vs other options). */
  addFinalAnswerKey: (
    tripId: string,
    sceneIndex: number,
    key: string,
  ): Promise<{ scene_index: number; additional: string[] }> =>
    postJson(`/api/final/${encodeURIComponent(tripId)}/answer-keys`, {
      scene_index: sceneIndex,
      key,
    }),
  /** Remove one additionalAnswerKeys entry (mis-added / test variant). */
  deleteFinalAnswerKey: (
    tripId: string,
    sceneIndex: number,
    key: string,
  ): Promise<{ scene_index: number; additional: string[] }> =>
    postJson(`/api/final/${encodeURIComponent(tripId)}/answer-keys/delete`, {
      scene_index: sceneIndex,
      key,
    }),
  /** Short-lived Azure Speech token (admin; 503 azure_not_configured until the key lands). */
  finalSpeechToken: (): Promise<{ token: string; region: string }> =>
    getJson('/api/final/speech-token'),
  /** All 7 checks green → queue the release (a publish_docs bus job; the family
   * Trello card is stamped/moved by the publish apply hook, not queued here). */
  readyFinalCheck: (
    tripId: string,
  ): Promise<{ trip_id: string; publish_job: string; trello_job: string | null }> =>
    postJson(`/api/final/${encodeURIComponent(tripId)}/ready`),
  /** Publisher mode only: run a whitelisted Scripts tool; returns the "tool" bus
   * job it reports into (long tools finish in the background — watch the inbox). */
  runTool: (body: {
    tool: string;
    target?: string;
    steps?: string;
    lane?: string;
    apply?: boolean;
  }): Promise<BusJob> => postJson('/api/admin/pipeline/tool', body),
};

/**
 * Best-effort flush of a single field's text on page unload. `sendBeacon`
 * cannot set the Authorization header, so we use `fetch(..., keepalive)` which
 * survives unload AND keeps the auth header the contract requires.
 */
export const flushFieldBeacon = (sid: string, fid: number, currentText: string): void => {
  try {
    void fetch(field(sid, fid), {
      method: 'PUT',
      keepalive: true,
      credentials: 'include',
      headers: jsonHeaders(),
      body: JSON.stringify({ current_text: currentText }),
    });
  } catch {
    /* nothing else we can do during unload */
  }
};

/** Best-effort flush of a field comment on page unload (keepalive keeps the token header). */
export const flushCommentBeacon = (sid: string, fid: number, text: string): void => {
  try {
    void fetch(field(sid, fid, '/comment'), {
      method: 'POST',
      keepalive: true,
      credentials: 'include',
      headers: jsonHeaders(),
      body: JSON.stringify({ text }),
    });
  } catch {
    /* nothing else we can do during unload */
  }
};

/** Best-effort flush of un-POSTed playback coverage when the tab hides (mobile
 * backgrounding/screen-lock lands mid-debounce). Same `/played` body the normal
 * debounced POST sends; the server merges ranges idempotently, so a redundant
 * flush is harmless. */
export const flushPlayedBeacon = (
  sid: string,
  fid: number,
  ranges: Array<[number, number]>,
  track: 'working' | 'original' = 'working',
): void => {
  try {
    void fetch(field(sid, fid, '/played'), {
      method: 'POST',
      keepalive: true,
      credentials: 'include',
      headers: jsonHeaders(),
      body: JSON.stringify({ ranges, track }),
    });
  } catch {
    /* nothing else we can do during unload */
  }
};

/** Best-effort flush of a single `_ZH` script on page unload (mirrors flushFieldBeacon). */
export const flushLocalizationBeacon = (sid: string, fid: number, script: ZhScript, text: string): void => {
  try {
    void fetch(field(sid, fid, '/localization'), {
      method: 'PUT',
      keepalive: true,
      credentials: 'include',
      headers: jsonHeaders(),
      body: JSON.stringify({ script, text }),
    });
  } catch {
    /* nothing else we can do during unload */
  }
};
