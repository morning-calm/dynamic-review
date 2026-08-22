import { acquireMicStream } from './micStream';
import { createMicCapture, type MicCapture } from './micCapture';
import {
  createManagedRecognizer,
  createPcmPushStream,
  type ManagedRecognizer,
} from './azureSpeechService';
import type { PushAudioInputStream } from 'microsoft-cognitiveservices-speech-sdk';
import {
  isBackendScoringEnabled,
  prewarmScoring,
  scoreMutterViaBackend,
  type ScoredBy,
} from './backendScoring';
import { checkMutter, isSpaceFreeLanguage, type MutterCheckResult } from './speechCheck';

/**
 * "Armed window" controller (proposal A6). Loaded lazily (with the Azure SDK) via
 * useMicSpeech. The mic DEVICE itself is owned by ./micStream, not here.
 *
 * SUPERSEDED — the old A6 hard invariant ("never arm while app audio is playing")
 * is GONE as a RUNTIME BAIL (Dave 2026-07-12). `armWindow` used to refuse to open whenever
 * a speech-like mixer track was audible, and duck everything to 15% while listening. That
 * produced two reported bugs: the scene audio dropping out whenever a question appeared
 * ("it makes it sound weird"), and a question whose window bailed into 'error', which
 * nothing ever cleared, never getting a mic at all.
 *
 * The invariant is now enforced STRUCTURALLY BY THE CALLERS instead, and more strictly than
 * it ever was here: the mic DEVICE is only open while a window is armed, and LessonViewer
 * arms only once the video has PAUSED (see docs/360-viewer/speech-audio-architecture.md).
 * So there is no app audio to bail on, and nothing to duck.
 *
 * WE FEED AZURE; IT DOES NOT PULL THE MIC (Dave 2026-07-12). The recognizer is bound to
 * a PUSH STREAM (createPcmPushStream) that ./micCapture fills, so we control precisely
 * which audio Azure ever receives.
 *
 * Two earlier designs both failed, in opposite directions, and the current one exists
 * to satisfy both constraints at once:
 *  - An energy VAD used to delay CREATING the recognizer until speech onset. But
 *    creating it costs a Cloud-Function token round-trip plus an Azure websocket
 *    handshake, and nothing buffered the mic meanwhile — so the learner's opening word
 *    was lost every time ("the mic is not picking up the first thing I say").
 *  - Starting the recognizer eagerly on the raw MediaStream fixed that, but then Azure
 *    was pulling the mic continuously: it received (and was billed for) silence, room
 *    noise, and any narration the mic picked up — and the only thing
 *    stopping a phantom pass was a filter on RESULTS, which cannot be airtight, because
 *    Azure endpoints on silence and the silence ending the narrator's utterance IS the
 *    video pausing.
 *
 * Now: the recognizer is created and connected at ARM time (warm, so there is no
 * startup latency when the learner speaks), but micCapture pushes audio only while the
 * VAD hears speech (and `isAnswerWindowOpen()`, if a caller passes one) — preceded by a
 * ~500ms PRE-ROLL so the opening phoneme, which precedes any VAD trigger, is not clipped.
 * Silence is never transmitted.
 */

/**
 * Armed-window lifecycle states. A `const` object (not a TS `enum`) because
 * `erasableSyntaxOnly` forbids real enums — see tsconfig. Values are the exact
 * strings the window controller has always emitted; keep them unchanged, a
 * stacked branch (r1a6, mic integration) consumes these literals directly.
 */
export const MicWindowStatus = {
  Arming: 'arming',
  WaitingForSpeech: 'waitingForSpeech',
  Listening: 'listening',
  Denied: 'denied',
  Error: 'error',
  Closed: 'closed',
} as const;

export type MicWindowStatus = (typeof MicWindowStatus)[keyof typeof MicWindowStatus];

export interface MicWindowResult extends MutterCheckResult {
  /** All raw candidate forms Azure produced (observability/debug). */
  candidateCount: number;
  /** Backend-scoring rollout (VITE_SPEECH_SCORING_BACKEND): display-ready
   *  percent from the shared endpoint. Absent while the flag is off. */
  percent?: number;
  /** Which scorer produced `score`. Absent while the flag is off. */
  scoredBy?: ScoredBy;
  /** Scorer contract version that answered. Absent while the flag is off. */
  algorithmVersion?: string;
}

/**
 * Failed-quiz mic hint shown by both TripViewer and LessonViewer. With the
 * backend-scoring flag on, every scored result carries a VR-parity `percent`
 * (from the endpoint, or computed identically by the local fallback) and the
 * hint surfaces it; flag off, `percent` is absent and the copy is unchanged.
 */
export const quizFailMicHint = (percent?: number): string =>
  percent !== undefined
    ? `Not quite (${percent}%) — try again, or tap an answer.`
    : 'Not quite — try again, or tap an answer.';

/**
 * Why a scored utterance came back empty — the distinction already exists in
 * this file (Azure produced no candidates vs candidates came back but none
 * matched) and was previously collapsed into a bare counter before it reached
 * Firestore (missing-session-data audit #2).
 * - `noCandidates`: Azure returned nothing scoreable (NoMatch / empty N-best) —
 *   points at mic timing / pause window / audio.
 * - `noMatch`: candidates existed but `checkMutter` scored none (incl. the
 *   authoring case of a moment with no solutions) — points at scoring/authoring.
 */
export type EmptyUtteranceReason = 'noCandidates' | 'noMatch';

export interface ArmWindowArgs {
  /** The question's PossibleSolutions (also seeds the PhraseListGrammar). */
  solutions: readonly string[];
  /** Distractor answers for the tie-break clause (quizzes; VR parity). */
  incorrectSolutions?: readonly string[] | null;
  /** Content language string (drives locale + space-free normalization). */
  languageCode: string;
  /** Doc id of the viewed trip/lesson; lets the server authorise demo callers. */
  contentId?: string;
  /** Pass threshold (persisted viewer setting; strict > on this path). */
  difficulty: number;
  onLiveTranscript?: (text: string) => void;
  /** Final scored utterance. The window auto-closes before this fires. */
  onResult: (result: MicWindowResult) => void;
  /** Azure produced nothing scoreable (VR RecordEmptyUtterance). Window stays armed. */
  onEmptyUtterance?: (reason: EmptyUtteranceReason) => void;
  /** The window failed to open the mic — carries the `DOMException.name` (or
   *  'recognizerStartFailed') that the 'denied'/'error' status previously hid. */
  onMicFailure?: (reason: string) => void;
  /**
   * True once it is genuinely the learner's turn to speak. A gate on the AUDIO:
   * micCapture checks it per frame and TRANSMITS NOTHING while it is false, so audio
   * heard before the turn never reaches Azure at all.
   *
   * NO CALLER PASSES IT TODAY, and none needs to: a window is only ever armed when it IS
   * the learner's turn (the device is not even open otherwise — LessonViewer waits for the
   * video to pause; the quizzes wait for the prompt audio to finish). The gate is retained
   * as the seam for a future surface where our own audio plays over the learner's turn and
   * cannot be silenced — e.g. if early answering comes back by ducking the video rather
   * than pausing it. It is applied to RESULTS too, as defence in depth.
   */
  isAnswerWindowOpen?: () => boolean;
  onStatus?: (status: MicWindowStatus) => void;
}

export interface MicWindow {
  /**
   * Close the window: dispose the Azure recognizer. Idempotent.
   *
   * ⚠️ Does NOT stop the mic DEVICE — `useMicSpeech.disarm()` does that, via
   * `releaseMicStream()`. That asymmetry is deliberate and load-bearing: `disarm()` also
   * runs INTERNALLY here (on a scored utterance, on a recognition error, on the safety
   * timer), and a wrong answer re-arms immediately, so closing the device on every internal
   * disarm would churn it once per retry. The rule the viewers must therefore honour: any
   * path that ends the learner's turn has to call the HOOK's disarm, not just let this one
   * fire — see docs/360-viewer/speech-audio-architecture.md.
   */
  disarm: () => void;
  readonly isOpen: () => boolean;
}

/** Safety net: a forgotten window never streams to Azure indefinitely. */
const MAX_WINDOW_MS = 60_000;

/**
 * Open one armed listening window. Resolves once the mic is live (or the
 * window failed to open — status callbacks carry the reason).
 */
export const armWindow = async (args: ArmWindowArgs): Promise<MicWindow> => {
  const status = (value: MicWindowStatus): void => args.onStatus?.(value);
  let open = true;
  let stream: MediaStream | null = null;
  let recognizer: ManagedRecognizer | null = null;
  let capture: MicCapture | null = null;
  let pushStream: PushAudioInputStream | null = null;
  let safetyTimer = 0;

  const disarm = (): void => {
    if (!open) {
      return;
    }
    open = false;
    window.clearTimeout(safetyTimer);
    // Capture first: stop producing frames before closing the stream they feed, so a
    // late frame can't write to a closed push stream.
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
    // The mic DEVICE is deliberately NOT stopped here — see MicWindow.disarm. The viewer's
    // `useMicSpeech.disarm()` owns it, so a retry can re-arm without churning the device.
    stream = null;
    status(MicWindowStatus.Closed);
  };

  const window_: MicWindow = { disarm, isOpen: () => open };

  status(MicWindowStatus.Arming);

  // The old A6 hard invariant ("never arm while our own audio is audible") used to BAIL
  // here — set status 'error' and return a dead window. Two problems, both reported by
  // Dave 2026-07-12:
  //  1. It bailed rather than waiting, and nothing ever moved micState off 'error', so the
  //     viewers' auto-arm effects (which require 'idle') never retried — a question could
  //     end up never getting a mic at all.
  //  2. It came with duckAllTracks(0.15), which is what made the scene audio drop out when
  //     a question opened. Dave: "no need to mute, it makes it sound weird."
  // Both are gone. The callers now guarantee the same property by construction (nothing of
  // ours is playing when a window arms) — see the header.
  //
  // The backend-scoring branch was written BEFORE that change and re-introduced both the
  // bail and the duck here; only its flag sample is kept on rebase (2026-07-28).

  // Backend-scoring flag is sampled once per window; while off, everything
  // below the flag checks is byte-for-byte the pre-flag behaviour.
  const backendScoring = isBackendScoringEnabled();
  if (backendScoring) {
    // Cold-start mitigation (§8): arming precedes the answer by seconds.
    prewarmScoring(args.contentId);
  }

  try {
    stream = await acquireMicStream();
  } catch (error) {
    open = false;
    // Preserve WHY the mic failed (NotAllowedError / NotFoundError /
    // NotReadableError / OverconstrainedError …) — previously discarded by a
    // bare `catch {}`. The UI still shows the single 'denied' state.
    const reason = error instanceof DOMException ? error.name : 'getUserMediaFailed';
    args.onMicFailure?.(reason);
    status(MicWindowStatus.Denied);
    return window_;
  }

  if (!open) {
    // Disarmed while the permission prompt was up. Don't touch the device: whoever disarmed
    // us owns it (useMicSpeech.disarm already released it, and may have armed a new window
    // on a fresh one since).
    return window_;
  }

  safetyTimer = window.setTimeout(disarm, MAX_WINDOW_MS);

  const removeSpaces = isSpaceFreeLanguage(args.languageCode);

  /**
   * Is it actually the learner's turn to speak? A gate on the AUDIO (micCapture consults
   * it per frame and transmits nothing while false), not a filter on results.
   *
   * Defaults to true, and no caller currently overrides it — see ArmWindowArgs. It is
   * still applied to results below as cheap defence in depth: with the audio gated, a
   * result from outside the turn cannot arrive, so that check should never fire.
   */
  const answerWindowOpen = (): boolean => args.isAnswerWindowOpen?.() ?? true;

  const startRecognizer = async (): Promise<void> => {
    if (!open || !stream) {
      return;
    }
    try {
      // WE feed Azure, rather than letting it pull the mic. micCapture pushes only the
      // learner's speech — preceded by a ~500ms pre-roll so the opening phoneme (which
      // happens BEFORE any VAD can fire) is not lost, and followed by a silence tail so
      // Azure's 750ms endpointing actually triggers. Silence and narration are never
      // transmitted, which is where the cost and the privacy exposure went.
      //
      // Capture FIRST: the push stream's PCM format must declare the rate micCapture is
      // actually producing (it resamples the device's native rate down to its own
      // CAPTURE_SAMPLE_RATE), and a format that lies about the rate fails silently — Azure
      // just hears the learner at the wrong speed. Hence `capture.sampleRate`, never a
      // hardcoded 16000.
      const activeStream = stream;
      capture = await createMicCapture(activeStream, {
        shouldSend: () => open && answerWindowOpen(),
        onAudio: (pcm) => {
          // Frames captured before the push stream exists (a microtask) are dropped here.
          if (!open || !pushStream) {
            return;
          }
          pushStream.write(pcm.buffer as ArrayBuffer);
        },
      });
      if (!open) {
        // Disarmed while the worklet was loading.
        capture.stop();
        capture = null;
        return;
      }
      pushStream = createPcmPushStream(capture.sampleRate);
      recognizer = await createManagedRecognizer(pushStream, args.languageCode, args.solutions, {
        onRecognizing: (text) => {
          if (!open || !answerWindowOpen()) {
            return;
          }
          // First accepted interim word = the learner is actually talking.
          status(MicWindowStatus.Listening);
          args.onLiveTranscript?.(text);
        },
        onRecognized: (options, displayText) => {
          if (!open) {
            return;
          }
          // Heard while the narrator was still speaking — not an answer. Drop it.
          if (!answerWindowOpen()) {
            return;
          }
          if (options.length === 0) {
            args.onEmptyUtterance?.('noCandidates');
            return;
          }
          if (backendScoring) {
            // The no-solutions authoring case is the ONLY way checkMutter
            // returns null — decide it client-side so the endpoint is never
            // called without scoreable solutions (§4.2).
            if (args.solutions.length === 0) {
              args.onEmptyUtterance?.('noMatch');
              return;
            }
            // Same one-utterance-per-window contract as the local path:
            // close before the round-trip so the result handler can re-arm
            // without racing this window's teardown.
            disarm();
            void scoreMutterViaBackend(
              options,
              args.solutions,
              args.incorrectSolutions ?? null,
              args.difficulty,
              removeSpaces,
              args.languageCode,
              args.contentId,
            ).then((scored) => {
              if (!scored) return;
              args.onResult({
                ...scored,
                userAnswer: scored.userAnswer || displayText,
                candidateCount: options.length,
              });
            });
            return;
          }
          const result = checkMutter(
            options,
            args.solutions,
            args.incorrectSolutions ?? null,
            args.difficulty,
            removeSpaces,
          );
          if (!result) {
            args.onEmptyUtterance?.('noMatch');
            return;
          }
          const finalResult: MicWindowResult = {
            ...result,
            userAnswer: result.userAnswer || displayText,
            candidateCount: options.length,
          };
          // One utterance per window: close first so the result handler can
          // immediately re-arm (retry) without racing this window's teardown.
          disarm();
          args.onResult(finalResult);
        },
        onError: (message) => {
          if (!open) {
            return;
          }
          console.warn('[micSession] recognition error:', message);
          status(MicWindowStatus.Error);
          disarm();
        },
      }, args.contentId);
      if (!open) {
        void recognizer.dispose();
        recognizer = null;
        return;
      }
      await recognizer.start();
    } catch (error) {
      console.warn('[micSession] failed to start recognition:', error);
      args.onMicFailure?.('recognizerStartFailed');
      status(MicWindowStatus.Error);
      disarm();
    }
  };

  // START AZURE NOW, NOT ON SPEECH ONSET (Dave 2026-07-12: "the mic is not picking
  // up the first thing I say" in LessonViewer).
  //
  // The recognizer used to be created only once an energy VAD heard voice onset —
  // i.e. only AFTER the user had already started speaking. But `startRecognizer`
  // then has to (1) fetch a speech token via an httpsCallable round-trip to a
  // europe-west1 Cloud Function — a cold start there costs seconds — and (2) open a
  // websocket to Azure. Nothing buffers the microphone during that, so the opening
  // word (often the whole answer, on a short speak-along) was simply gone before
  // Azure was listening. Lazily opening the stream saved Azure minutes; it cost us
  // the first thing the learner said, which is the one thing that had to work.
  //
  // Starting at arm time means the connection is live and streaming BEFORE the user speaks.
  // Nothing of ours is audible while a window is armed (the callers guarantee it — see the
  // header), so there is no narration for the eager stream to pick up.
  //
  // This also removes the VAD's MediaStreamSource from the shared AudioContext,
  // which the mixer notes as a cause of output glitches. Cost: Azure now streams for
  // the armed window rather than only after speech onset.
  status(MicWindowStatus.WaitingForSpeech);
  void startRecognizer();

  return window_;
};
