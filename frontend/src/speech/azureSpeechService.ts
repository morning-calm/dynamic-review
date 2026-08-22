import {
  AudioConfig,
  AudioInputStream,
  AudioStreamFormat,
  OutputFormat,
  PhraseListGrammar,
  PropertyId,
  ResultReason,
  SpeechConfig,
  SpeechRecognizer,
  type PushAudioInputStream,
} from 'microsoft-cognitiveservices-speech-sdk';
import { api } from '../api';
import { isXSolution, makeRecognitionOption, type RecognitionOption } from './speechCheck';

/**
 * Azure Speech recognizer factory for the web viewers (proposal A5).
 *
 * IMPORTANT: this module (and everything else under the speech phase that
 * imports the Azure SDK) must only ever be loaded via dynamic import — the
 * SDK is ~1MB minified and belongs in its own lazy chunk, never in the main
 * or base-viewer bundles. Viewers reach it through useMicSpeech's dynamic
 * import of micSession.ts.
 *
 * VR parity (research/lessonviewer.md §2, AzureController.cs): region
 * uksouth, Detailed output format, 750ms segmentation silence timeout,
 * PhraseListGrammar seeded from PossibleSolutions (minus "X" placeholders),
 * N-best candidates = lexical + ITN forms of every alternative. The one
 * deliberate divergence: the key never ships client-side — short-lived auth
 * tokens come from this app's backend (see fetchSpeechToken below).
 */

const FALLBACK_REGION = 'uksouth'; // AzureController.cs:22 hardcodes this region.
/**
 * Azure speech tokens live 10 minutes — but the BACKEND already serves each mint
 * from an 8-minute cache, so a token can arrive up to ~8 minutes old. Caching it
 * another 9 minutes here (the library-app value, whose callable minted fresh)
 * would stack to ~17 minutes and hand recognizers an EXPIRED token. Keep the
 * client cache to a minute: total age ≤ ~9 min, and the request is a cheap GET
 * to our own backend.
 */
const TOKEN_TTL_MS = 60 * 1000;
const SEGMENTATION_SILENCE_TIMEOUT_MS = '750';
/** micCapture always hands us mono, 16-bit signed PCM (see createPcmPushStream). */
const PCM_BITS_PER_SAMPLE = 16;
const PCM_CHANNEL_COUNT = 1;

/**
 * Content language → Azure recognition locale (VR LessonController locale
 * table). Matched by prefix so suffixed content codes ("ES_PACK",
 * "JP_TRIPS") resolve too.
 */
const LOCALE_BY_LANGUAGE_PREFIX: readonly [string, string][] = [
  ['JP', 'ja-JP'],
  ['ES', 'es-ES'],
  ['EN', 'en-GB'],
  ['FR', 'fr-FR'],
  ['IT', 'it-IT'],
  ['DE', 'de-DE'],
  ['KO', 'ko-KR'],
  ['ZH', 'zh-CN'],
];

export const recognitionLocaleForLanguage = (languageCode: string): string | null => {
  const upper = languageCode.trim().toUpperCase();
  const match = LOCALE_BY_LANGUAGE_PREFIX.find(([prefix]) => upper.startsWith(prefix));
  return match ? match[1] : null;
};

interface CachedToken {
  token: string;
  region: string;
  fetchedAt: number;
}

let cachedToken: CachedToken | null = null;

/** REVIEW-APP DIVERGENCE from library-app: the token comes from our own backend
 * (`GET /api/final/speech-token`, admin-only) instead of a Firebase callable —
 * the capped Azure key lives in the Scripts .env on the host, never client-side.
 * `contentId` is accepted for call-site parity but unused. */
const fetchSpeechToken = async (_contentId?: string): Promise<CachedToken> => {
  if (cachedToken && Date.now() - cachedToken.fetchedAt < TOKEN_TTL_MS) {
    return cachedToken;
  }
  const data = await api.finalSpeechToken();
  const token = data.token ?? '';
  const region = data.region || FALLBACK_REGION;
  if (token === '') {
    throw new Error('speech-token returned no token');
  }
  cachedToken = { token, region, fetchedAt: Date.now() };
  return cachedToken;
};

/** Detailed-format JSON payload attached to recognized results. */
interface DetailedNBestEntry {
  Lexical?: string;
  ITN?: string;
  Display?: string;
}

interface DetailedRecognitionJson {
  NBest?: DetailedNBestEntry[];
  DisplayText?: string;
}

/**
 * VR parity (AzureController.SpeechRecognized): every N-best alternative
 * contributes BOTH its lexical and ITN form as scoring candidates, so
 * "twenty five" and "25" both match a numeric solution.
 */
export const recognitionOptionsFromResultJson = (json: string): RecognitionOption[] => {
  const options: RecognitionOption[] = [];
  const push = (form: string | undefined): void => {
    if (form && form.trim() !== '') {
      options.push(makeRecognitionOption(form));
    }
  };
  try {
    const parsed = JSON.parse(json) as DetailedRecognitionJson;
    for (const entry of parsed.NBest ?? []) {
      push(entry.Lexical);
      push(entry.ITN);
    }
    if (options.length === 0) {
      push(parsed.DisplayText);
    }
  } catch {
    // Malformed payload — treat as no candidates; caller records an empty utterance.
  }
  return options;
};

export interface RecognizerCallbacks {
  /** Interim hypothesis (live transcript in the speech bubble). */
  onRecognizing: (text: string) => void;
  /** Final segment: N-best candidates ready for checkMutter (empty = nothing scoreable). */
  onRecognized: (options: RecognitionOption[], displayText: string) => void;
  onError: (message: string) => void;
}

export interface ManagedRecognizer {
  start: () => Promise<void>;
  /** Stops recognition and releases the recognizer. Idempotent. */
  dispose: () => Promise<void>;
}

/**
 * A stream WE feed, rather than one Azure pulls from the mic itself.
 *
 * This is what lets micCapture decide exactly which audio Azure ever receives: only
 * the learner's speech (with its pre-roll), never silence and never the narrator. The
 * old `AudioConfig.fromStreamInput(mediaStream)` handed the SDK the raw mic and it
 * pulled continuously for the whole armed window.
 *
 * `sampleRate` MUST be the capture's actual rate (`MicCapture.sampleRate`), not the one
 * we asked the browser for: a format that lies about the rate does not fail loudly, it
 * just makes Azure hear the learner at the wrong speed and recognise nothing.
 */
export const createPcmPushStream = (sampleRate: number): PushAudioInputStream =>
  AudioInputStream.createPushStream(
    AudioStreamFormat.getWaveFormatPCM(sampleRate, PCM_BITS_PER_SAMPLE, PCM_CHANNEL_COUNT),
  );

/**
 * Build a continuous recognizer bound to a push stream (see createPcmPushStream).
 * `phraseHints` should be the question's PossibleSolutions — whole-"X"
 * wildcard placeholders are filtered here (VR parity).
 */
export const createManagedRecognizer = async (
  pushStream: PushAudioInputStream,
  languageCode: string,
  phraseHints: readonly string[],
  callbacks: RecognizerCallbacks,
  contentId?: string,
): Promise<ManagedRecognizer> => {
  const locale = recognitionLocaleForLanguage(languageCode);
  if (!locale) {
    throw new Error(`No recognition locale for content language "${languageCode}"`);
  }
  const { token, region } = await fetchSpeechToken(contentId);

  const speechConfig = SpeechConfig.fromAuthorizationToken(token, region);
  speechConfig.speechRecognitionLanguage = locale;
  speechConfig.outputFormat = OutputFormat.Detailed;
  speechConfig.setProperty(
    PropertyId.Speech_SegmentationSilenceTimeoutMs,
    SEGMENTATION_SILENCE_TIMEOUT_MS,
  );

  const audioConfig = AudioConfig.fromStreamInput(pushStream);
  const recognizer = new SpeechRecognizer(speechConfig, audioConfig);

  const phraseList = PhraseListGrammar.fromRecognizer(recognizer);
  for (const hint of phraseHints) {
    if (!isXSolution(hint)) {
      phraseList.addPhrase(hint);
    }
  }

  recognizer.recognizing = (_sender, event) => {
    if (event.result.reason === ResultReason.RecognizingSpeech) {
      callbacks.onRecognizing(event.result.text);
    }
  };
  recognizer.recognized = (_sender, event) => {
    if (event.result.reason !== ResultReason.RecognizedSpeech) {
      if (event.result.reason === ResultReason.NoMatch) {
        callbacks.onRecognized([], '');
      }
      return;
    }
    const json = event.result.properties.getProperty(
      PropertyId.SpeechServiceResponse_JsonResult,
      '',
    );
    callbacks.onRecognized(recognitionOptionsFromResultJson(json), event.result.text);
  };
  recognizer.canceled = (_sender, event) => {
    // Token expiry / network loss mid-window. Surface as a typed status; the
    // window controller degrades to typed input.
    callbacks.onError(event.errorDetails || 'Speech recognition was cancelled');
  };

  let disposed = false;
  const start = (): Promise<void> =>
    new Promise((resolve, reject) => {
      recognizer.startContinuousRecognitionAsync(resolve, (error) => reject(new Error(error)));
    });

  const dispose = (): Promise<void> =>
    new Promise((resolve) => {
      if (disposed) {
        resolve();
        return;
      }
      disposed = true;
      recognizer.stopContinuousRecognitionAsync(
        () => {
          recognizer.close();
          resolve();
        },
        () => {
          // Stop failed (already torn down) — still release the handle.
          recognizer.close();
          resolve();
        },
      );
    });

  return { start, dispose };
};
