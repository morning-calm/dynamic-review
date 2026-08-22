/**
 * REVIEW-APP STUB of library-app's backendScoring.ts.
 *
 * The library-app original ships an optional Firebase callable
 * (`scoreSpeechAnswer`, flag VITE_SPEECH_SCORING_BACKEND) that scores mutters
 * server-side. The review-app's Final-check keyword tool always scores LOCALLY
 * with the ported speechCheck engine, so the flag is permanently off here —
 * this stub keeps micSession.ts byte-identical to the library-app copy.
 */
import type { MutterCheckResult } from './speechCheck';
import type { RecognitionOption } from './speechCheck';

export type ScoredBy = 'local' | 'backend';

export const isBackendScoringEnabled = (): boolean => false;

export const prewarmScoring = (_contentId?: string): void => {
  /* backend scoring disabled in the review-app */
};

export const scoreMutterViaBackend = (
  _options: readonly RecognitionOption[],
  _solutions: readonly string[],
  _incorrectSolutions: readonly string[] | null,
  _difficulty: number,
  ..._rest: unknown[]
): Promise<
  (MutterCheckResult & { percent?: number; scoredBy?: ScoredBy; algorithmVersion?: string }) | null
> => Promise.reject(new Error('backend scoring is disabled in the review-app'));
