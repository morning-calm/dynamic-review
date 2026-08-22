import { useCallback, useEffect, useRef, useState } from 'react';
import { toast } from 'react-toastify';
import { api, ApiError, type FinalKeywords } from '../api';
import {
  checkMutter,
  DEFAULT_SPEECH_DIFFICULTY,
  isSpaceFreeLanguage,
  type RecognitionOption,
} from '../speech/speechCheck';
import type { AdminMicWindow } from '../speech/adminMicCheck';

const errText = (e: unknown, fallback: string): string =>
  e instanceof ApiError ? e.detail || e.code : fallback;

/** Review-app language NAME → the speech stack's prefix code (locale table +
 * space-free normalization both key on these). */
const LANG_CODE: Record<string, string> = {
  English: 'EN',
  French: 'FR',
  German: 'DE',
  Italian: 'IT',
  Spanish: 'ES',
  Japanese: 'JP',
  Korean: 'KO',
  Mandarin: 'ZH',
};

type MicState = 'idle' | 'arming' | 'listening';

interface SceneResult {
  isSolved: boolean;
  score: number;
  userAnswer: string;
  /** Every heard form, each rescored alone so the add-button can say pass/fail. */
  candidates: { form: string; passes: boolean }[];
}

/** Check-6 body: per Q&A/keyword scene the admin PLAYS the answer, SPEAKS it,
 * and any reasonable variant Azure heard that fails the current keys is a
 * one-tap add to additionalAnswerKeys (add-only; the server re-runs the
 * stage9/answer_keys.py collision rule). Scoring is the ported library-app
 * engine — the same maths the headset runs. */
const KeywordCheckPanel = ({ tripId }: { tripId: string }) => {
  const [model, setModel] = useState<FinalKeywords | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [micBlocked, setMicBlocked] = useState<string | null>(null);
  const [micScene, setMicScene] = useState<number | null>(null);
  const [micState, setMicState] = useState<MicState>('idle');
  const [live, setLive] = useState('');
  const [results, setResults] = useState<Record<number, SceneResult>>({});
  const [typed, setTyped] = useState<Record<number, string>>({});
  const [busy, setBusy] = useState(false);
  const windowRef = useRef<AdminMicWindow | null>(null);

  const load = useCallback(() => {
    api
      .getFinalKeywords(tripId)
      .then(setModel)
      .catch((e: unknown) => setError(errText(e, 'Failed to load keywords')));
    // Pre-flight: surface a missing Azure key as a banner instead of a dead mic.
    api.finalSpeechToken().catch((e: unknown) => {
      setMicBlocked(errText(e, 'speech token unavailable'));
    });
  }, [tripId]);
  useEffect(load, [load]);
  useEffect(() => () => windowRef.current?.stop(), []);

  const langCode = model ? (LANG_CODE[model.language] ?? '') : '';

  const solutionsFor = (s: FinalKeywords['scenes'][number]) => [
    s.correct,
    ...s.additional.filter((a) => a.toLowerCase() !== s.correct.toLowerCase()),
  ];

  const speak = async (s: FinalKeywords['scenes'][number]) => {
    windowRef.current?.stop();
    setMicScene(s.scene_index);
    setMicState('arming');
    setLive('');
    const solutions = solutionsFor(s);
    const incorrect = s.options.slice(1);
    try {
      const { adminListen } = await import('../speech/adminMicCheck');
      windowRef.current = await adminListen({
        solutions,
        incorrectSolutions: incorrect,
        languageCode: langCode,
        difficulty: DEFAULT_SPEECH_DIFFICULTY,
        onLiveTranscript: (t) => {
          setMicState('listening');
          setLive(t);
        },
        onResult: (r) => {
          setMicState('idle');
          setMicScene(null);
          const removeSpaces = isSpaceFreeLanguage(langCode);
          const seen = new Set<string>();
          const candidates = r.candidates
            .filter((c: RecognitionOption) => {
              const k = c.original.trim().toLowerCase();
              if (!k || seen.has(k)) return false;
              seen.add(k);
              return true;
            })
            .map((c: RecognitionOption) => ({
              form: c.original,
              passes:
                checkMutter([c], solutions, incorrect, DEFAULT_SPEECH_DIFFICULTY, removeSpaces)
                  ?.isSolved ?? false,
            }));
          setResults((prev) => ({
            ...prev,
            [s.scene_index]: {
              isSolved: r.isSolved,
              score: r.score,
              userAnswer: r.userAnswer,
              candidates,
            },
          }));
        },
        onEmpty: () => {
          setMicState('idle');
          setMicScene(null);
          toast.warn('Heard nothing scoreable — try again.');
        },
        onFailure: (reason) => {
          setMicState('idle');
          setMicScene(null);
          toast.error(`Mic failed: ${reason}`);
        },
      });
      if (windowRef.current.isOpen()) setMicState('listening');
    } catch (e: unknown) {
      setMicState('idle');
      setMicScene(null);
      toast.error(errText(e, 'Could not start the mic'));
    }
  };

  const removeKey = (sceneIndex: number, key: string) => {
    setBusy(true);
    api
      .deleteFinalAnswerKey(tripId, sceneIndex, key)
      .then(() => {
        toast.success(`“${key}” removed from additionalAnswerKeys (staging)`);
        load();
      })
      .catch((e: unknown) => toast.error(errText(e, 'Remove failed')))
      .finally(() => setBusy(false));
  };

  const addKey = (sceneIndex: number, key: string) => {
    const v = key.trim();
    if (!v) return;
    setBusy(true);
    api
      .addFinalAnswerKey(tripId, sceneIndex, v)
      .then(() => {
        toast.success(`“${v}” added to additionalAnswerKeys (staging)`);
        setTyped((t) => ({ ...t, [sceneIndex]: '' }));
        load();
      })
      .catch((e: unknown) => toast.error(errText(e, 'Add failed')))
      .finally(() => setBusy(false));
  };

  if (error) return <p className="text-xs text-rose-400">{error}</p>;
  if (!model) return <p className="text-xs text-gray-500">Loading…</p>;

  return (
    <div className="space-y-3">
      {micBlocked && (
        <p className="rounded border border-amber-800 bg-amber-900/20 p-2 text-xs text-amber-100">
          Speaking is unavailable: {micBlocked}. Typed additions below still work.
        </p>
      )}
      {!langCode && (
        <p className="text-xs text-amber-300">
          Unknown language “{model.language}” — no Azure locale; typed additions only.
        </p>
      )}
      {model.scenes.length === 0 && (
        <p className="text-xs text-gray-500">No question/keyword scenes on this trip.</p>
      )}
      {model.scenes.map((s) => {
        const r = results[s.scene_index];
        const accepted = new Set(solutionsFor(s).map((x) => x.toLowerCase()));
        return (
          <div key={s.scene_index} className="space-y-2 rounded border border-gray-700 bg-gray-900/30 p-3">
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <span className="font-semibold text-gray-200">Scene {s.scene_index}</span>
              <span className="rounded bg-gray-700 px-1.5 py-0.5 text-[10px] uppercase text-gray-300">
                {s.is_keyword ? 'keyword (speak & repeat)' : 'quiz question'}
              </span>
              <audio controls preload="none" src={s.answer_audio} className="h-7"
                title={s.is_keyword ? 'The spoken word clip' : 'The answer clip'} />
            </div>
            {s.question && <p className="text-sm text-gray-200">{s.question}</p>}
            {s.question_en && s.question_en !== s.question && (
              <p className="text-xs text-gray-500">{s.question_en}</p>
            )}
            <div className="flex flex-wrap gap-1.5 text-xs">
              <span className="rounded bg-emerald-800/70 px-2 py-0.5 text-emerald-100" title="Correct answer">
                {s.correct}
              </span>
              {s.additional.map((a) => (
                <span key={a} className="flex items-center gap-1 rounded bg-teal-800/60 px-2 py-0.5 text-teal-100"
                  title="additionalAnswerKeys — extra accepted spoken form">
                  {a}
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => removeKey(s.scene_index, a)}
                    className="rounded px-0.5 text-teal-300 hover:bg-teal-700 hover:text-white disabled:opacity-50"
                    title={`Remove “${a}” from additionalAnswerKeys (staging)`}
                    aria-label={`Remove accepted form ${a}`}
                  >
                    ×
                  </button>
                </span>
              ))}
              {s.options.slice(1).map((o) => (
                <span key={o} className="rounded bg-gray-700/60 px-2 py-0.5 text-gray-400"
                  title="Wrong option (a spoken variant must never equal one)">
                  {o}
                </span>
              ))}
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                disabled={!!micBlocked || !langCode || (micState !== 'idle' && micScene !== s.scene_index)}
                onClick={() =>
                  micScene === s.scene_index && micState !== 'idle'
                    ? (windowRef.current?.stop(), setMicState('idle'), setMicScene(null))
                    : speak(s)
                }
                className={
                  micScene === s.scene_index && micState !== 'idle'
                    ? 'rounded bg-rose-700 px-3 py-1 text-xs font-medium text-white'
                    : 'rounded bg-sky-700 px-3 py-1 text-xs font-medium text-white hover:bg-sky-600 disabled:opacity-50'
                }
              >
                {micScene === s.scene_index && micState === 'arming'
                  ? 'Starting…'
                  : micScene === s.scene_index && micState === 'listening'
                    ? '■ Stop listening'
                    : '🎤 Speak the answer'}
              </button>
              {micScene === s.scene_index && live && (
                <span className="text-xs italic text-gray-400">“{live}”</span>
              )}
              <input
                value={typed[s.scene_index] ?? ''}
                onChange={(e) => setTyped((t) => ({ ...t, [s.scene_index]: e.target.value }))}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault();
                    addKey(s.scene_index, typed[s.scene_index] ?? '');
                  }
                }}
                placeholder="add an accepted form by hand…"
                className="rounded border border-gray-600 bg-gray-900 px-2 py-1 text-xs text-gray-100"
              />
              <button
                type="button"
                disabled={busy || !(typed[s.scene_index] ?? '').trim()}
                onClick={() => addKey(s.scene_index, typed[s.scene_index] ?? '')}
                className="rounded border border-gray-600 px-2 py-1 text-xs text-gray-200 hover:bg-gray-700 disabled:opacity-50"
              >
                Add
              </button>
            </div>
            {r && (
              <div
                className={`rounded border p-2 text-xs ${
                  r.isSolved
                    ? 'border-emerald-800 bg-emerald-900/20 text-emerald-100'
                    : 'border-amber-800 bg-amber-900/20 text-amber-100'
                }`}
              >
                <p className="mb-1">
                  {r.isSolved ? '✓ accepted' : '✗ not accepted'} — best form “{r.userAnswer}”
                  (score {Math.round(r.score * 100)}%)
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {r.candidates.map((c) => (
                    <span
                      key={c.form}
                      className={`flex items-center gap-1 rounded px-2 py-0.5 ${
                        c.passes ? 'bg-emerald-800/50' : 'bg-gray-800/80'
                      }`}
                    >
                      {c.form}
                      {!c.passes && !accepted.has(c.form.trim().toLowerCase()) && (
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => addKey(s.scene_index, c.form)}
                          className="rounded bg-teal-700 px-1.5 text-[10px] font-semibold text-white hover:bg-teal-600 disabled:opacity-50"
                          title="Accept this heard form: add to additionalAnswerKeys"
                        >
                          + add
                        </button>
                      )}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
};

export default KeywordCheckPanel;
