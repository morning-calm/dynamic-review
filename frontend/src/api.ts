// Typed client for the review-app backend. Every endpoint in API_CONTRACT.md
// has a matching function here, and every request carries the shared review
// token header. The frontend NEVER constructs audio paths — it uses the URLs
// the backend returns in Field.audio / Field.versions.

const TOKEN: string = import.meta.env.VITE_REVIEW_TOKEN ?? 'dev-token';

// ---------------------------------------------------------------------------
// Types (mirror the contract's core objects)
// ---------------------------------------------------------------------------

export type FlagValue = 'none' | 'done' | 'edit_required';
export type RegenerateMode = 'segment' | 'whole' | 'highlight' | 'alt';
export type FallbackExtent = 'sentence' | 'scene' | 'custom';
export type SessionStatus = 'in_review' | 'submitted';

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
  url: string;
  created_at: number;
}

export interface Field {
  fid: number;
  scene_index: number | null;
  field_path: FieldPath;
  has_audio: boolean;
  original_text: string;
  current_text: string;
  /** Editable English translation for non-_EN trips (empty when N/A / same as target). */
  source_text: string;
  /** The English at seed — for the original→new diff on the English editor. */
  original_source: string;
  flag: FlagValue;
  comment: string;
  splice_confidence: number | null;
  played_coverage: Array<[number, number]>;
  original_played_coverage: Array<[number, number]>;
  can_mark_done: boolean;
  audio: AudioLinks;
  versions: AudioVersion[];
  manual_clips: ManualClip[];
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

export interface Session {
  id: string;
  trip_id: string;
  folder_name: string;
  status: SessionStatus;
  voice: string;
  voice_display: string;
  speed: number;
  speed_override: number | null;
  model: string;
  model_override: string | null;
  trip_categories: string[];
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
  lane: string | null;
  /** Variant label (EN / A12 / B1 / N4 / HSK1-2 …) and the family (place) base id. */
  level: string;
  family: string;
  reviewable: boolean;
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

export interface SubmitResponse {
  ok: boolean;
  validation: ValidationIssue[];
  written: FieldPath[];
  awaiting_stage9: boolean;
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

const jsonHeaders = (): HeadersInit => ({
  'X-Review-Token': TOKEN,
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
    res = await fetch(path, init);
  } catch (e) {
    // Network failure / backend down — surface as a 0-status ApiError so the
    // UI can degrade gracefully rather than throwing a raw TypeError.
    throw new ApiError(0, 'network', e instanceof Error ? e.message : 'network error');
  }
  if (!res.ok) await throwFromResponse(res);
  return (await res.json()) as T;
};

const getJson = <T>(path: string): Promise<T> => requestJson<T>(path, { headers: { 'X-Review-Token': TOKEN } });

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

  // Manual backstop: trim a leftover sliver/noise the reviewer highlighted in the narration.
  trimNoise: (sid: string, fid: number, start: number, end: number): Promise<Field> =>
    postJson(field(sid, fid, '/trim'), { start, end }),

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
      headers: { 'X-Review-Token': TOKEN },
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

  // --- Manual-edit clip workspace ---
  createClip: (sid: string, fid: number, text: string): Promise<Field> =>
    postJson(field(sid, fid, '/clips'), { text }),

  importClip: async (sid: string, fid: number, file: File): Promise<Field> => {
    const form = new FormData();
    form.append('file', file);
    return requestJson<Field>(field(sid, fid, '/clips/upload'), {
      method: 'POST',
      headers: { 'X-Review-Token': TOKEN },
      body: form,
    });
  },

  regenClip: (sid: string, fid: number, cid: number, text?: string): Promise<Field> =>
    postJson(field(sid, fid, `/clips/${cid}/regenerate`), { text }),

  useClip: (sid: string, fid: number, cid: number): Promise<Field> =>
    postJson(field(sid, fid, `/clips/${cid}/use`)),

  deleteClip: (sid: string, fid: number, cid: number): Promise<Field> =>
    requestJson<Field>(field(sid, fid, `/clips/${cid}`), { method: 'DELETE', headers: jsonHeaders() }),

  /**
   * Download the session zip. A plain <a href> can't send the X-Review-Token
   * header (→ 401), so the caller fetches the blob with the header and triggers
   * a programmatic download.
   */
  downloadZip: async (sid: string): Promise<Blob> => {
    let res: Response;
    try {
      res = await fetch(`/api/sessions/${encodeURIComponent(sid)}/download`, {
        headers: { 'X-Review-Token': TOKEN },
      });
    } catch (e) {
      throw new ApiError(0, 'network', e instanceof Error ? e.message : 'network error');
    }
    if (!res.ok) await throwFromResponse(res);
    return res.blob();
  },

  submit: (sid: string): Promise<SubmitResponse> =>
    postJson(`/api/sessions/${encodeURIComponent(sid)}/submit`),
};

/**
 * Best-effort flush of a single field's text on page unload. `sendBeacon`
 * cannot set the X-Review-Token header, so we use `fetch(..., keepalive)` which
 * survives unload AND keeps the auth header the contract requires.
 */
export const flushFieldBeacon = (sid: string, fid: number, currentText: string): void => {
  try {
    void fetch(field(sid, fid), {
      method: 'PUT',
      keepalive: true,
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
      headers: jsonHeaders(),
      body: JSON.stringify({ text }),
    });
  } catch {
    /* nothing else we can do during unload */
  }
};
