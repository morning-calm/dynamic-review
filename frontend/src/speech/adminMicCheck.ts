import { acquireMicStream, releaseMicStream } from './micStream';
import { createMicCapture, type MicCapture } from './micCapture';
import {
  createManagedRecognizer,
  createPcmPushStream,
  type ManagedRecognizer,
} from './azureSpeechService';
import type { PushAudioInputStream } from 'microsoft-cognitiveservices-speech-sdk';
import {
  checkMutter,
  isSpaceFreeLanguage,
  type MutterCheckResult,
  type RecognitionOption,
} from './speechCheck';

/**
 * REVIEW-APP admin listening window — a lean sibling of micSession.armWindow for
 * the Final-check keyword tool. Same capture pipeline (VAD-gated push stream, we
 * feed Azure), two deliberate differences:
 *   1. The result EXPOSES the raw N-best candidates: the tool's whole point is
 *      offering the variants Azure heard that FAIL the current keys as one-tap
 *      additions to additionalAnswerKeys (armWindow only reports the best form).
 *   2. One tap = one utterance = full teardown INCLUDING the mic device
 *      (releaseMicStream) — the admin speaks occasionally, so there is no
 *      re-arm churn to optimise, and holding the device open would keep
 *      Android/desktop capture routing active between checks.
 *
 * Load only via dynamic import (the Azure SDK is ~1MB — its own lazy chunk).
 */

export interface AdminMicResult extends MutterCheckResult {
  /** Every scoring candidate Azure produced (lexical + ITN of each N-best). */
  candidates: RecognitionOption[];
}

export interface AdminListenArgs {
  /** Accepted answers (correct + additionalAnswerKeys); seeds the phrase grammar. */
  solutions: readonly string[];
  /** The question's OTHER options (tie-break; a heard distractor must not pass). */
  incorrectSolutions?: readonly string[] | null;
  /** Content language prefix code ('EN', 'JP', …) — locale + space-free rules. */
  languageCode: string;
  difficulty: number;
  onLiveTranscript?: (text: string) => void;
  onResult: (result: AdminMicResult) => void;
  /** Azure produced nothing scoreable; the window has CLOSED (unlike armWindow). */
  onEmpty?: () => void;
  onFailure?: (reason: string) => void;
}

export interface AdminMicWindow {
  stop: () => void;
  readonly isOpen: () => boolean;
}

const MAX_WINDOW_MS = 30_000;

export const adminListen = async (args: AdminListenArgs): Promise<AdminMicWindow> => {
  let open = true;
  let stream: MediaStream | null = null;
  let recognizer: ManagedRecognizer | null = null;
  let capture: MicCapture | null = null;
  let pushStream: PushAudioInputStream | null = null;
  let safetyTimer = 0;

  const stop = (): void => {
    if (!open) return;
    open = false;
    window.clearTimeout(safetyTimer);
    // Capture first: stop producing frames before closing the stream they feed.
    if (capture) {
      capture.stop();
      capture = null;
    }
    if (pushStream) {
      pushStream.close();
      pushStream = null;
    }
    if (recognizer) {
      void recognizer.dispose();
      recognizer = null;
    }
    if (stream) {
      releaseMicStream();
      stream = null;
    }
  };
  const window_: AdminMicWindow = { stop, isOpen: () => open };

  try {
    stream = await acquireMicStream();
  } catch (error) {
    open = false;
    args.onFailure?.(error instanceof DOMException ? error.name : 'getUserMediaFailed');
    return window_;
  }
  if (!open) return window_;

  safetyTimer = window.setTimeout(stop, MAX_WINDOW_MS);
  const removeSpaces = isSpaceFreeLanguage(args.languageCode);

  try {
    const activeStream = stream;
    capture = await createMicCapture(activeStream, {
      shouldSend: () => open,
      onAudio: (pcm) => {
        if (!open || !pushStream) return;
        pushStream.write(pcm.buffer as ArrayBuffer);
      },
    });
    if (!open) {
      capture.stop();
      capture = null;
      return window_;
    }
    pushStream = createPcmPushStream(capture.sampleRate);
    recognizer = await createManagedRecognizer(pushStream, args.languageCode, args.solutions, {
      onRecognizing: (text) => {
        if (open) args.onLiveTranscript?.(text);
      },
      onRecognized: (options, displayText) => {
        if (!open) return;
        if (options.length === 0) {
          stop();
          args.onEmpty?.();
          return;
        }
        const result = checkMutter(
          options,
          args.solutions,
          args.incorrectSolutions ?? null,
          args.difficulty,
          removeSpaces,
        );
        stop();
        if (result === null) {
          args.onEmpty?.();
          return;
        }
        args.onResult({
          ...result,
          userAnswer: result.userAnswer || displayText,
          candidates: [...options],
        });
      },
      onError: (message) => {
        if (!open) return;
        stop();
        args.onFailure?.(message || 'recognitionError');
      },
    });
    if (!open) {
      // Stopped while the recognizer was being built (token fetch / socket setup)
      // — stop() ran before `recognizer` was assigned, so dispose it here or it
      // leaks a live Azure connection. Same guard as micSession.armWindow.
      void recognizer.dispose();
      recognizer = null;
      return window_;
    }
    await recognizer.start();
  } catch (error) {
    stop();
    args.onFailure?.(error instanceof Error ? error.message : 'recognizerStartFailed');
  }
  return window_;
};
