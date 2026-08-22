/**
 * Microphone → 16-bit mono PCM, with a pre-roll buffer, an energy VAD, and a hard gate
 * on whether anything is sent at all.
 *
 * WHY (Dave 2026-07-12). We used to hand Azure the raw MediaStream
 * (`AudioConfig.fromStreamInput(stream)`) and let the SDK pull from the mic in real
 * time for the whole armed window. That meant Azure received — and we paid for —
 * silence, room noise, and any narration the mic picked up. It also
 * meant the ONLY thing standing between the narrator and a phantom pass was a filter on
 * results, which could not be airtight: Azure endpoints on silence, and the silence that
 * ends the narrator's utterance IS the video pausing, so his transcript could be
 * finalised just after the learner's turn began and be scored as their answer.
 *
 * Feeding Azure a PUSH STREAM instead means we choose exactly which audio it ever sees.
 * Nothing is pushed unless `shouldSend()` is true AND the VAD hears speech.
 *
 * THE PRE-ROLL IS THE WHOLE TRICK. Gating on VAD onset ALONE still clips the learner: by
 * the time frame energy crosses the threshold, the first phoneme has already happened.
 * That is precisely why the previous VAD design lost the opening word ("the mic is not
 * picking up the first thing I say"). We keep a rolling PRE_ROLL_MS ring buffer of recent
 * audio and, on speech onset, flush it FIRST — so Azure receives the audio from BEFORE
 * the trigger — then stream live frames.
 *
 * ENDPOINTING TAIL. Azure segments utterances on silence
 * (Speech_SegmentationSilenceTimeoutMs = 750 ms). If we simply stopped pushing when the
 * learner went quiet, Azure would sit waiting and might never finalise. So when the VAD
 * decides speech has ended we push an explicit run of digital silence to force
 * `recognized`.
 *
 * ────────────────────────────────────────────────────────────────────────────────────
 * THE GRAPH IS BUILT ONCE AND NEVER TORN DOWN MID-LESSON (Dave 2026-07-12, second pass).
 *
 * The first version created `createMediaStreamSource(stream) → worklet → gain → destination`
 * PER ARMED WINDOW and disconnected them in `stop()` — which `disarm()` calls microseconds
 * BEFORE the viewer plays the answer chime. That is the exact failure we had already fixed
 * once at the DEVICE level and then reintroduced one layer up: routing a mic into a live
 * AudioContext and then yanking it glitches that context's output, and every context shares
 * one output device, so the ding got mangled. viewerAudioService.ts:70-73 documents this
 * outright ("arming the mic routes a MediaStreamSource into that context and glitches its
 * output").
 *
 * So: the context AND its nodes are module-scoped, wired once, and left alone. A window
 * only swaps the callbacks and its own VAD state. `disarm()` now touches nothing in the
 * audio graph, so nothing can glitch the chime that follows it. The whole thing is torn
 * down once, by `releaseMicCapture()` on viewer unmount.
 *
 * ⚠️ The DEVICE does NOT share this lifetime — not any more. It is opened per armed window
 * and released on disarm (micStream; a live capture stream degrades Android playback), so
 * `attachStream` re-points the source node at the new device each time. The context and
 * worklet must NOT be rebuilt with it: that is exactly the rapid create/close churn Safari
 * (~4 live AudioContexts) and iOS cannot take — viewerAudioService.ts:86.
 * ────────────────────────────────────────────────────────────────────────────────────
 *
 * No Azure SDK imports here on purpose: this module is about capture, and micSession
 * (which owns the SDK) does the pushing.
 */

/**
 * The rate we SEND to Azure. We resample to it ourselves; we no longer ask the AudioContext
 * for it.
 *
 * DON'T go back to `new AudioContext({ sampleRate: 16000 })`. Chrome opens an OUTPUT stream
 * at the context's own rate, so a 16 kHz context band-limits that context's playback to
 * ~8 kHz — telephone bandwidth. Running at the device's own rate and resampling ourselves
 * costs one `makeResampler` and cannot reconfigure anything.
 *
 * ⚠️ BUT IT WAS NOT THE ANDROID DISTORTION, so do not read this as "fixed" (Dave 2026-07-12).
 * Dropping the forced 16 kHz changed NOTHING, and it could not have: this context is built at
 * the FIRST ARMED WINDOW, whereas the audio degrades the instant `getUserMedia` resolves —
 * before any AudioContext exists. The trigger is the live capture STREAM (Android's
 * MODE_IN_COMMUNICATION), not this context or the mic constraints. Full account, and the
 * architectural fix it implies, in micStream.ts's MIC_CONSTRAINTS block.
 */
export const CAPTURE_SAMPLE_RATE = 16_000;

/** Audio retained ahead of speech onset, flushed on trigger so the first word survives. */
const PRE_ROLL_MS = 500;
/** Frame RMS (0..1) above which we call it voice. */
const VAD_RMS_THRESHOLD = 0.02;
/** Quiet must persist this long before we decide the utterance has ended. */
const VAD_HANGOVER_MS = 600;
/** Silence pushed after speech ends, to trip Azure's 750 ms endpointing. Must exceed it. */
const SILENCE_TAIL_MS = 1_000;

/**
 * Emits a copy of each render quantum's samples to the main thread. A copy is essential:
 * the worklet reuses its input buffer between calls.
 *
 * This is source text because `audioWorklet.addModule` accepts a URL, not a processor
 * class. `ensureGraph` wraps it in an in-memory JavaScript Blob and supplies that Blob's
 * URL, avoiding a separately deployed worklet asset and its associated path handling.
 */
const PCM_PROCESSOR_NAME = 'pcm-processor';
const WORKLET_SOURCE = `
class PcmProcessor extends AudioWorkletProcessor {
  // Zero outputs (see the AudioWorkletNode options): a sink node, kept actively
  // processing by its connected input, with no path to the destination.
  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (channel) {
      this.port.postMessage(new Float32Array(channel));
    }
    return true;
  }
}
registerProcessor('${PCM_PROCESSOR_NAME}', PcmProcessor);
`;

export interface MicCaptureOptions {
  /**
   * Is it the learner's turn? Checked per frame. While false NOTHING is transmitted —
   * a hard gate on the AUDIO, not a filter on results, so the narrator is never sent.
   */
  shouldSend: () => boolean;
  /** Receives 16-bit mono PCM at `MicCapture.sampleRate`, ready to push to Azure. */
  onAudio: (pcm: Int16Array) => void;
}

export interface MicCapture {
  /**
   * The rate the audio in `onAudio` is at — always CAPTURE_SAMPLE_RATE now, because we
   * resample to it ourselves rather than asking the AudioContext for it (which band-limited
   * the device's playback). The caller must still declare THIS value to the SDK rather than
   * hardcoding a rate: a push stream declared at the wrong rate makes Azure hear chipmunks
   * and silently recognise nothing, and this stays the single source of truth if the
   * resampling target ever changes.
   */
  readonly sampleRate: number;
  /**
   * Detach this window. Deliberately does NOT disconnect any audio node, close the context,
   * or stop the mic device — tearing anything down here would glitch the answer chime that
   * plays immediately afterwards (see the header). It only stops us sending.
   */
  stop: () => void;
}

const toPcm16 = (samples: Float32Array): Int16Array => {
  const pcm = new Int16Array(samples.length);
  for (let i = 0; i < samples.length; i++) {
    const clamped = Math.max(-1, Math.min(1, samples[i]));
    pcm[i] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;
  }
  return pcm;
};

const rmsOf = (samples: Float32Array): number => {
  let sumSquares = 0;
  for (let i = 0; i < samples.length; i++) {
    sumSquares += samples[i] * samples[i];
  }
  return Math.sqrt(sumSquares / samples.length);
};

/**
 * Linear-interpolating resampler from the device's rate down to CAPTURE_SAMPLE_RATE.
 *
 * It is STATEFUL, and has to be. Resampling each 128-sample quantum independently would
 * restart the read position at every frame boundary, dropping or duplicating a fraction of a
 * sample each time — an audible click 375 times a second, and a stream Azure would struggle
 * with. So the fractional read position carries across frames, and the previous frame's last
 * sample is kept so the interpolation spanning the boundary has a left-hand value.
 *
 * Linear interpolation (rather than a windowed-sinc job) is fine here: we are feeding a
 * speech recogniser, not mastering audio, and 48k→16k is a gentle 3:1.
 */
export const makeResampler = (inputRate: number): ((frame: Float32Array) => Float32Array) => {
  const ratio = inputRate / CAPTURE_SAMPLE_RATE;
  // Read position within the virtual array [previousLast, ...frame]; carried between frames.
  let position = 0;
  let previousLast = 0;

  return (frame: Float32Array): Float32Array => {
    if (frame.length === 0) {
      return frame;
    }
    if (ratio === 1) {
      previousLast = frame[frame.length - 1];
      return frame;
    }
    // Stop at frame.length - 1, NOT frame.length. Two reasons, and both bite:
    //  - interpolating at `index` reads `index + 1`, so running to the last sample would
    //    read one PAST the frame and produce NaN;
    //  - it also keeps the carried position >= -1, so the next frame never needs more than
    //    ONE sample of history (previousLast) to interpolate across the boundary.
    const last = frame.length - 1;
    const out = new Float32Array(Math.max(0, Math.ceil((last - position) / ratio)));
    let written = 0;
    // sampleAt(-1) is the previous frame's final sample — what makes the interpolation
    // continuous across the frame boundary rather than clicking at every one.
    const sampleAt = (index: number): number => (index < 0 ? previousLast : frame[index]);
    while (position < last && written < out.length) {
      const index = Math.floor(position);
      const fraction = position - index;
      out[written++] = sampleAt(index) * (1 - fraction) + sampleAt(index + 1) * fraction;
      position += ratio;
    }
    previousLast = frame[last];
    position -= frame.length; // carry the leftover fraction into the next frame
    return written === out.length ? out : out.subarray(0, written);
  };
};

/** The persistent capture graph. One per viewer session, not one per question. */
interface CaptureGraph {
  readonly context: AudioContext;
  readonly worklet: AudioWorkletNode;
  readonly sampleRate: number;
  /** Swapped by attachStream when the device is re-acquired; context+worklet persist. */
  stream: MediaStream;
  source: MediaStreamAudioSourceNode;
}

/** The window currently allowed to send. Swapped per question; the graph never changes. */
interface ActiveWindow {
  shouldSend: () => boolean;
  onAudio: (pcm: Int16Array) => void;
  speaking: boolean;
  quietSinceMs: number;
}

let graphReady: Promise<CaptureGraph> | null = null;
let active: ActiveWindow | null = null;

// Ring buffer lives with the GRAPH, not the window: it must already hold recent audio the
// moment a window arms, or the very first utterance of a question has no pre-roll to flush.
// It is filled only while NOT speaking — during speech the frames are already going out
// live, and buffering them too would let a later flush re-send audio Azure has had.
let preRoll: Float32Array[] = [];
let preRollSamples = 0;

const buildGraph = async (stream: MediaStream): Promise<CaptureGraph> => {
  // NO sampleRate option: forcing 16 kHz here makes Chrome open a 16 kHz OUTPUT stream, which
  // band-limits that context's playback to telephone bandwidth (see CAPTURE_SAMPLE_RATE — and
  // note that removing it did NOT fix the Android distortion). Run at the device's own rate
  // and resample ourselves.
  const context = new AudioContext();
  const moduleUrl = URL.createObjectURL(new Blob([WORKLET_SOURCE], { type: 'application/javascript' }));
  try {
    await context.audioWorklet.addModule(moduleUrl);
  } catch (error) {
    void context.close();
    throw error;
  } finally {
    URL.revokeObjectURL(moduleUrl);
  }
  // A context created outside a user gesture starts suspended, and a suspended context never
  // pumps the worklet — no audio at all, silently. The viewers unlock on their Start gesture,
  // so this is normally a no-op; cheap insurance against a failure that would look exactly
  // like "the mic doesn't work".
  if (context.state === 'suspended') {
    await context.resume();
  }

  const source = context.createMediaStreamSource(stream);
  // NO OUTPUT, AND NOTHING CONNECTED TO `context.destination` (Dave 2026-07-12: "is the LV
  // video volume being turned down? it got quieter immediately after the first speech
  // bubble and never recovered").
  //
  // The first speech bubble is the first armWindow, i.e. when this graph gets built — and
  // the previous version wired the MICROPHONE through to `context.destination` (via a
  // gain=0 node, purely to keep the worklet pumped). That creates an audio RENDER stream
  // fed by a mic input, which is the classic trigger for Windows' "Communications" ducking
  // — whose default setting is literally "reduce the volume of other sounds by 80%". And
  // because the graph is deliberately persistent for the session, nothing ever undid it:
  // the volume never came back. It is almost certainly the same reason the answer chime
  // sounded wrong.
  //
  // A worklet declared with ZERO OUTPUTS is a sink: the spec keeps it actively processing
  // while it has a connected input, so `process()` is still pumped with no path to the
  // destination at all. So the capture graph now drives no output whatsoever.
  const worklet = new AudioWorkletNode(context, PCM_PROCESSOR_NAME, { numberOfOutputs: 0 });
  source.connect(worklet);

  // Frames are resampled to CAPTURE_SAMPLE_RATE the moment they arrive, so the VAD, the
  // ring buffer, the silence tail and the push stream all speak one rate.
  const resample = makeResampler(context.sampleRate);
  const preRollSampleLimit = (PRE_ROLL_MS / 1000) * CAPTURE_SAMPLE_RATE;

  const pushSilence = (window_: ActiveWindow, durationMs: number): void => {
    window_.onAudio(new Int16Array(Math.round((durationMs / 1000) * CAPTURE_SAMPLE_RATE)));
  };

  /** Speech ended (or the turn did): flush the endpointing tail so Azure finalises. */
  const endUtterance = (window_: ActiveWindow): void => {
    window_.speaking = false;
    window_.quietSinceMs = 0;
    pushSilence(window_, SILENCE_TAIL_MS);
  };

  worklet.port.onmessage = (event: MessageEvent<Float32Array>) => {
    // Resample FIRST and unconditionally: the resampler is stateful (it carries a fractional
    // read position between frames), so skipping frames while no window is armed would break
    // its continuity and click on the next one.
    const frame = resample(event.data);
    if (frame.length === 0) {
      return;
    }
    const window_ = active;

    // No armed window: keep the ring warm so the next one has a pre-roll from the outset.
    if (!window_ || !window_.shouldSend()) {
      if (window_?.speaking) {
        endUtterance(window_); // turn ended mid-utterance — close it out
      }
      bufferPreRoll(frame, preRollSampleLimit);
      return;
    }

    const loudEnough = rmsOf(frame) >= VAD_RMS_THRESHOLD;
    const nowMs = context.currentTime * 1000;

    if (!window_.speaking) {
      bufferPreRoll(frame, preRollSampleLimit);
      if (!loudEnough) {
        return; // still quiet — send nothing, keep buffering
      }
      window_.speaking = true;
      window_.quietSinceMs = 0;
      // FLUSH THE PRE-ROLL: the learner's opening phoneme, captured before the VAD fired.
      // It includes `frame`, so `frame` is sent exactly once. Clearing it afterwards is what
      // guarantees no audio is ever sent twice.
      for (const buffered of preRoll) {
        window_.onAudio(toPcm16(buffered));
      }
      preRoll = [];
      preRollSamples = 0;
      return;
    }

    window_.onAudio(toPcm16(frame));

    if (loudEnough) {
      window_.quietSinceMs = 0;
      return;
    }
    if (window_.quietSinceMs === 0) {
      window_.quietSinceMs = nowMs;
    } else if (nowMs - window_.quietSinceMs >= VAD_HANGOVER_MS) {
      endUtterance(window_);
    }
  };

  return { context, stream, source, worklet, sampleRate: CAPTURE_SAMPLE_RATE };
};

const bufferPreRoll = (frame: Float32Array, limit: number): void => {
  preRoll.push(frame);
  preRollSamples += frame.length;
  while (preRoll.length > 1 && preRollSamples - preRoll[0].length >= limit) {
    preRollSamples -= (preRoll.shift() as Float32Array).length;
  }
};

const teardownGraph = (graph: CaptureGraph): void => {
  graph.worklet.port.onmessage = null;
  graph.source.disconnect();
  graph.worklet.disconnect();
  void graph.context.close();
};

/**
 * Close the capture graph and its context. Call when the viewer that used the mic unmounts —
 * the counterpart of micStream's `releaseMicStream()`. NEVER call it between questions: the
 * whole point of the persistent graph is that nothing is torn down next to an answer chime.
 * Idempotent.
 */
export const releaseMicCapture = (): void => {
  const pending = graphReady;
  graphReady = null;
  active = null;
  preRoll = [];
  preRollSamples = 0;
  if (pending) {
    void pending.then(teardownGraph).catch(() => undefined);
  }
};

/**
 * Point the existing graph at a NEW mic device, swapping ONLY the source node.
 *
 * The device is now acquired per question and released after it (Android holds playback on the
 * voice-call stream for as long as ANY capture stream is live — see micStream), so `stream`
 * changes every question. Rebuilding the whole graph each time would close and reopen an
 * AudioContext per question, which is precisely the rapid create/close churn Safari (~4 live
 * contexts) and iOS cannot take — and which viewerAudioService.ts:86 explicitly warns against.
 * The context and worklet are expensive and device-independent, so they stay; only the
 * MediaStreamSource is rebuilt.
 */
const attachStream = (graph: CaptureGraph, stream: MediaStream): void => {
  graph.source.disconnect();
  graph.source = graph.context.createMediaStreamSource(stream);
  graph.source.connect(graph.worklet);
  graph.stream = stream;
  // The old device's tail is meaningless against the new one — start the pre-roll clean.
  preRoll = [];
  preRollSamples = 0;
};

/**
 * Arm capture for one listening window. Reuses the persistent context+worklet, re-pointing it
 * at the current device if that changed; a rejection is never cached, so a transient addModule
 * failure cannot deafen the rest of the lesson.
 */
export const createMicCapture = async (
  stream: MediaStream,
  options: MicCaptureOptions
): Promise<MicCapture> => {
  if (!graphReady) {
    const building = buildGraph(stream);
    graphReady = building;
    building.catch(() => {
      if (graphReady === building) {
        graphReady = null;
      }
    });
  }
  const pending = graphReady;
  const graph = await pending;
  if (graphReady !== pending) {
    // releaseMicCapture() ran while we were awaiting the graph (viewer unmounted, or the
    // window was abandoned mid-arm): `graph` is already being torn down. Attaching to it
    // would call createMediaStreamSource on a CLOSING context, and — worse — would leave
    // this window in `active` after releaseMicCapture had cleared it, so the NEXT graph
    // (a re-entered viewer builds a fresh one) would push its frames into this dead
    // window's callbacks. Fail the arm instead; micSession's catch disarms it cleanly.
    throw new Error('[micCapture] capture released while arming');
  }
  if (graph.stream !== stream) {
    attachStream(graph, stream);
  }

  const window_: ActiveWindow = {
    shouldSend: options.shouldSend,
    onAudio: options.onAudio,
    speaking: false,
    quietSinceMs: 0,
  };
  active = window_;

  return {
    sampleRate: graph.sampleRate,
    stop: () => {
      // Detach only. Disconnecting nodes here is what mangled the answer chime.
      if (active === window_) {
        active = null;
      }
    },
  };
};
