/**
 * The one shared microphone DEVICE. Read `docs/360-viewer/speech-audio-architecture.md`
 * before changing anything here.
 *
 * ⚠️ THE DEVICE IS OPEN ONLY WHILE A LISTENING WINDOW IS ARMED. It is acquired on arm and
 * released the moment the window disarms — it is NOT held for the viewer session, and
 * nothing opens it at Start (`warmMic()` existed and is deleted).
 *
 * WHY, and this is the whole reason the module looks like this: on Android a LIVE CAPTURE
 * STREAM degrades ALL playback for as long as it is held. Chrome moves playback off
 * STREAM_MUSIC onto STREAM_VOICE_CALL — quieter, often the earpiece, band-limited. It is
 * triggered by the stream EXISTING, not by the constraints below: confirmed on a real
 * device, `track.getSettings()` came back `ec=false ns=false agc=false` — Chrome had applied
 * our constraints exactly — and the audio was still ruined. No constraint avoids it. So the
 * mic may only be open when there is nothing playing that the learner needs to hear, which
 * is why LessonViewer arms only once the video has PAUSED.
 *
 * The answer chime is protected from the other side instead: closing the device makes the OS
 * reconfigure the audio path, so `answerSfx` delays the ding past that transient
 * (CHIME_DELAY_AFTER_MIC_MS). Holding the device open until the chime finished was tried and
 * is WORSE — the video resumes ~1.9s after a correct answer and plays distorted.
 *
 * WHY ITS OWN MODULE, not part of micSession: `useMicSpeech` needs `releaseMicStream`
 * but must NOT statically import micSession, which pulls in the Azure Speech SDK.
 * The SDK is a lazy chunk (`import('./micSession')` on first arm) so viewers that
 * never use the mic never download it. This module has no SDK dependency, so the
 * hook can import it directly without breaking that boundary.
 */

/**
 * ⚠️ DO NOT CHANGE A CONSTRAINT TO CHASE THE ANDROID DISTORTION. Three constraint changes
 * were tried and NONE of them fixed it (Dave 2026-07-12):
 *
 *   1. `echoCancellation: true` → `false`. Symptom got WORSE: "quieter" became "muffled
 *      and distorted, the moment I accepted mic permissions".
 *   2. `noiseSuppression: false, autoGainControl: false` as well — i.e. fully unprocessed
 *      capture, the state below. NO CHANGE.
 *   3. (In micCapture, same hunt.) Dropping `new AudioContext({ sampleRate: 16000 })`.
 *      NO CHANGE.
 *
 * The trigger is the live capture stream itself (see the header). The fix was architectural
 * — open the device only while a window is armed — not a constraint.
 *
 * WHY THE FLAGS STAY OFF ANYWAY. They cost us nothing and AEC actively hurt (fix 1 made
 * things worse). AEC used to be the only thing keeping our own narrator out of the
 * recognizer; that job is now done structurally in LessonViewer, whose window cannot open
 * until the video has paused, so there is no narration to leak. HEADPHONES remain the
 * mitigation anywhere else (no acoustic path from speaker to mic at all). If a PHANTOM PASS
 * (a question going green with nobody speaking) ever does appear, duck the narration while a
 * window is armed — do not restore AEC.
 *
 * ⚠️ CONSEQUENCE: no automatic gain control, so the captured signal is quieter and more
 * variable than it was. If the mic stops hearing the learner, the first thing to look at is
 * micCapture's VAD_RMS_THRESHOLD (0.02) — with AGC gone it may simply be too high to trip.
 * Azure does its own processing server-side, so recognition quality should hold up.
 */
const MIC_CONSTRAINTS: MediaStreamConstraints = {
  audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false },
};

let sharedStream: MediaStream | null = null;
/** The open-in-progress device, so concurrent callers share ONE getUserMedia. */
let pendingStream: Promise<MediaStream> | null = null;
/** Bumped by every release, so a device that finished opening after one is not adopted. */
let releaseCount = 0;

const streamIsLive = (stream: MediaStream | null): stream is MediaStream =>
  stream !== null && stream.getAudioTracks().some((track) => track.readyState === 'live');

const stopTracks = (stream: MediaStream): void =>
  stream.getTracks().forEach((track) => track.stop());

/**
 * Open the device for a listening window. If one is somehow still live (a retry re-arms
 * without the viewer having released — see `releaseMicStream`) it is reused rather than
 * re-opened, which also covers the OS or the user ending the track behind our back
 * (`streamIsLive` detects it and we re-acquire).
 *
 * Concurrent callers share the in-flight `getUserMedia` rather than each opening a device:
 * getUserMedia can take hundreds of ms, or block indefinitely on the permission prompt, so
 * two overlapping arms would otherwise open TWO devices — only the last to resolve retained,
 * the other never stopped, holding the mic (and the browser's recording indicator) open for
 * the rest of the page's life.
 */
export const acquireMicStream = async (): Promise<MediaStream> => {
  if (streamIsLive(sharedStream)) {
    return sharedStream;
  }
  if (!pendingStream) {
    const generation = releaseCount;
    const opening = navigator.mediaDevices.getUserMedia(MIC_CONSTRAINTS).then((stream) => {
      if (generation !== releaseCount) {
        // Released while the device was opening (viewer unmounted / permission prompt
        // outlived the window): do not resurrect it, and do not leave it recording.
        stopTracks(stream);
        throw new DOMException('Microphone released while opening', 'AbortError');
      }
      sharedStream = stream;
      return stream;
    });
    pendingStream = opening;
    void opening.catch(() => undefined).then(() => {
      if (pendingStream === opening) {
        pendingStream = null;
      }
    });
  }
  return pendingStream;
};

/**
 * Close the mic device and extinguish the browser's recording indicator. Called by
 * `useMicSpeech.disarm()` — i.e. the moment the listening window ends — and again on viewer
 * unmount as a backstop. Anything that will make audible sound (the chime, the resuming
 * video, answer/solution audio) must happen AFTER this, or Android plays it through the
 * voice-call stream.
 *
 * The one deliberate exception is a RETRY: a wrong answer disarms micSession's window
 * internally but the viewer re-arms immediately, so the device is left open and reused
 * rather than churned. Idempotent.
 */
export const releaseMicStream = (): void => {
  releaseCount++;
  pendingStream = null;
  if (sharedStream) {
    stopTracks(sharedStream);
  }
  sharedStream = null;
};
