import { createContext, useContext } from 'react';

/** The session's effective narration model + speed, for controls buried too deep to
 * hand the whole session to (RegenerateControls / ManualEditModal). Drives the
 * per-clip "Voice with V3" offer: shown only when the session voices with v2 (CJK
 * sessions are already v3), with a warning when the trip narrates slowed (A12 0.7 /
 * B1 0.85) because v3 ignores the speed setting. */
export interface NarrationInfo {
  /** The session's effective ElevenLabs model (`session.model`). */
  model: string;
  /** The session's effective narration speed (`session.speed`). */
  speed: number;
}

export const NarrationContext = createContext<NarrationInfo | null>(null);

/** Null when no provider is mounted — consumers then simply don't offer the V3 option. */
export const useNarration = (): NarrationInfo | null => useContext(NarrationContext);

/** The model id sent as the one-off per-take override. */
export const V3_MODEL = 'eleven_v3';
const V2_MODEL = 'eleven_multilingual_v2';

/** Whether to offer the per-take V3 checkbox: only on sessions voiced with v2. CJK
 * (ZH/JP/KO) sessions are eleven_v3 end-to-end, so there is nothing to switch to —
 * and this is the ONLY place that decision is spelled out, so a third model can't
 * silently fall through the gap (the repo's recurring enumerated-set bug class). */
export const offersV3 = (n: NarrationInfo | null): boolean => n?.model === V2_MODEL;

/** v3 ignores the narration speed, so a slowed CEFR trip (A12 0.7 / B1 0.85) gets a
 * full-speed take — worth warning about next to the checkbox. */
export const v3IgnoresSpeed = (n: NarrationInfo | null): boolean => (n?.speed ?? 1) !== 1;
