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
export type RegenerateMode = 'segment' | 'whole' | 'highlight' | 'alt';
export type FallbackExtent = 'sentence' | 'scene' | 'custom';
export type SessionStatus = 'in_review' | 'submitted' | 'approving' | 'approved' | 'changes_requested';

/** Statuses in which a reviewer may still edit text/audio/flags/narration.
 * `submitted`/`approving`/`approved` are locked (read-only) in the FE; the
 * backend enforces the same boundary with a 403 on the write endpoints. */
export const isEditableStatus = (s: SessionStatus): boolean =>
  s === 'in_review' || s === 'changes_requested';

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
export interface BusJob {
  id: string;
  kind: 'publish';
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
  kind: string; // v0_original | splice | fallback | admin_import
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
   * Undefined before the first combine (then compare against `orig.Hans`). Drives whether
   * "Generate from edit" has anything new to do since the last take was built. */
  working_hans?: string | null;
}

export interface Field {
  fid: number;
  scene_index: number | null;
  field_path: FieldPath;
  has_audio: boolean;
  original_text: string;
  current_text: string;
  /** What the WORKING take says (seeded to original_text; re-set at each combine). The
   * JP "Generate from edit" gate compares the kana line against THIS, not the seed —
   * the `_ZH` sibling is `localization.working_hans`. */
  working_text: string | null;
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
  versions: AudioVersion[];
  manual_clips: ManualClip[];
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

  regenerate: (
    sid: string,
    fid: number,
    mode: RegenerateMode,
    range?: { start: number; end: number },
    altText?: string,
  ): Promise<Field> =>
    postJson(field(sid, fid, '/regenerate'), {
      mode,
      ...(range ? { range } : {}),
      ...(altText !== undefined ? { alt_text: altText } : {}),
    }),

  combine: (sid: string, fid: number): Promise<Field> => postJson(field(sid, fid, '/combine')),

  // Nudge the trailing trim on the current candidate before combining (drop a TTS
  // breath/next-sound bleed). deltaMs > 0 trims more off the end, < 0 restores.
  trimCandidate: (sid: string, fid: number, deltaMs: number): Promise<Field> =>
    postJson(field(sid, fid, '/trim-candidate'), { delta_ms: deltaMs }),

  // Manual backstop: trim a leftover sliver/noise the reviewer highlighted in the narration.
  trimNoise: (sid: string, fid: number, start: number, end: number): Promise<Field> =>
    postJson(field(sid, fid, '/trim'), { start, end }),

  // Normalize the trailing pause to the trip's level requirement (beginner = 3s, else trim).
  trimSilence: (sid: string, fid: number): Promise<Field> =>
    postJson(field(sid, fid, '/trim-silence')),

  // Insert `seconds` of silence into the working take at the TEXT caret `pos` (char offset).
  insertSilence: (sid: string, fid: number, pos: number, seconds = 1): Promise<Field> =>
    postJson(field(sid, fid, '/insert-silence'), { pos, seconds }),

  // Shorten the pause at the TEXT caret by up to `seconds` (inverse of insertSilence; a
  // minimum natural pause always remains — 409 when there's no excess to remove).
  removeSilence: (sid: string, fid: number, pos: number, seconds = 1): Promise<Field> =>
    postJson(field(sid, fid, '/remove-silence'), { pos, seconds }),

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
  createClip: (sid: string, fid: number, text: string, comment: string): Promise<Field> =>
    postJson(field(sid, fid, '/clips'), { text, comment }),

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

  regenClip: (sid: string, fid: number, cid: number, text?: string): Promise<Field> =>
    postJson(field(sid, fid, `/clips/${cid}/regenerate`), { text }),

  // Attach / edit the admin note on a take. A non-empty note commits a draft (flags the
  // field edit-required); '' leaves it a draft.
  setClipComment: (sid: string, fid: number, cid: number, comment: string): Promise<Field> =>
    postJson(field(sid, fid, `/clips/${cid}/comment`), { comment }),

  deleteClip: (sid: string, fid: number, cid: number): Promise<Field> =>
    requestJson<Field>(field(sid, fid, `/clips/${cid}`), { method: 'DELETE', headers: jsonHeaders() }),

  /**
   * Download the session zip. A plain <a href> can't send the Authorization
   * header (→ 401), so the caller fetches the blob with the header and triggers
   * a programmatic download.
   */
  downloadZip: async (sid: string): Promise<Blob> => {
    let res: Response;
    try {
      res = await fetch(`/api/sessions/${encodeURIComponent(sid)}/download`, {
        credentials: 'include',
        headers: authHeaders(),
      });
    } catch (e) {
      throw new ApiError(0, 'network', e instanceof Error ? e.message : 'network error');
    }
    if (res.status === 401) {
      clearToken();
      unauthorizedHandler?.();
    }
    if (!res.ok) await throwFromResponse(res);
    return res.blob();
  },

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
