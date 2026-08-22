/**
 * 1:1 TypeScript port of the VR app's fuzzy answer scorer
 * (dynamic-languages: Assets/_Scripts/Speech Check/SpeechCheck.cs +
 * Extensions/StringExtensions.cs NormalizeKD + AnswerCheckDetails/PossibleSolution).
 *
 * Scores are persisted in UserStats session docs alongside VR-produced scores,
 * so numeric behaviour must match the C# implementation exactly — including
 * its quirks. Every function below mirrors its C# counterpart bug-for-bug;
 * do not "fix" behaviour here without changing the VR app in lockstep.
 * Verified against machine-generated vectors: see __tests__/generator/.
 *
 * Known C#↔JS divergence risks (accepted, covered by tests where possible):
 * - Character classification (letter/digit) uses each runtime's Unicode
 *   tables; both iterate UTF-16 code units, so behaviour matches for all
 *   content languages (incl. Japanese kana/kanji).
 * - C# does float (32-bit) division in the final normalization; we apply
 *   Math.fround to match. A 1-ulp double-rounding difference is theoretically
 *   possible but far below the 3-decimal rounding used when persisting.
 */

// C#: SpeechCheck static score weights.
export const CONTAINS_CHARACTER_SCORE = 2;
export const CHARACTER_POS_SCORE = 1;
export const SEQUENCE_SCORE = 5;
export const LENGTH_SCORE = 3;
export const EXTRA_CHARACTER_PENALTY = -5;

// C#: UserSettings — SpeechDifficulty slider (default set in SettingsProperties).
export const DEFAULT_SPEECH_DIFFICULTY = 0.75;
export const MIN_SPEECH_DIFFICULTY = 0.5;
export const MAX_SPEECH_DIFFICULTY = 1;

/** VR: MainMenuSettingsUI.UpdateDifficultyDisplay ("Difficulty {value}%"). */
export const speechDifficultyLabel = (value: number): string =>
  `Difficulty ${Math.round(value * 100)}%`;

/**
 * VR app's Voice Recognition difficulty info-panel copy, verbatim
 * (Assets/_Prefabs/Menus/SettingsCanvas.prefab, DifficultyInfoPanel text).
 * Shared by every web surface that exposes this setting (LessonViewer's
 * PlaybackBar menu, ViewerSettingsMenu for TripViewer/RecallQuiz) so the
 * wording stays identical to the VR app across all of them.
 */
export const SPEECH_DIFFICULTY_INFO_PARAGRAPHS: readonly string[] = [
  'This % is the acceptance level or pass rate for the voice recognition. Your % score is calculated by comparing your speech output to a list of allowable answers.',
  'The higher the %, the more difficult and stricter the voice recognition requirements are, e.g. 100% requires perfect answers and pronunciation.',
  "By lowering this pass rate %, you'll progress through the interactions more easily. The recommended and default setting is 75%.",
];

// C#: AnswerCheckDetails.SpaceFreeLanguages (TargetLanguage enum names).
const SPACE_FREE_LANGUAGES: readonly string[] = ['JP', 'JP_TRIPS', 'KO', 'ZH'];

/**
 * Whether a content language code (the Lessons/Trips doc's own `language`
 * string, e.g. "JP", "JP_TRIPS") gets space-free treatment: callers must
 * pre-normalize input AND solutions with normalizeKD(text, true) before the
 * static scoring API, exactly like the VR call sites do
 * (QuickTripQuizController / RecallQuizController).
 */
export const isSpaceFreeLanguage = (languageCode: string): boolean =>
  SPACE_FREE_LANGUAGES.includes(languageCode);

// C#: SpeechCheck.punctuation.
const PUNCTUATION: readonly string[] = ['.', ',', '!', '?', '-', '¿', '。', '、'];

// C# char.IsLetterOrDigit = Unicode categories Lu/Ll/Lt/Lm/Lo/Nd, evaluated
// per UTF-16 code unit (lone surrogates are category Cs → false).
const LETTER_OR_DIGIT = /[\p{Lu}\p{Ll}\p{Lt}\p{Lm}\p{Lo}\p{Nd}]/u;
const DIGIT = /\p{Nd}/u;

const clamp01f = (value: number): number => Math.fround(Math.min(Math.max(value, 0), 1));

/**
 * C#: StringExtensions.NormalizeKD. Despite the name it does NOT apply
 * Unicode NFKD — it strips every char that isn't a letter/digit (keeping
 * spaces only when removeSpaces=false), lowercases, collapses runs of
 * whitespace and trims.
 */
export const normalizeKD = (input: string, removeSpaces = false): string => {
  let str = input;
  if (removeSpaces) {
    str = str.trim().replace(/\s+/g, ' ');
  }
  let filtered = '';
  for (let i = 0; i < str.length; i++) {
    const c = str[i];
    if (LETTER_OR_DIGIT.test(c)) {
      filtered += c;
    } else if (!removeSpaces && c === ' ') {
      filtered += c;
    }
  }
  return filtered.toLowerCase().replace(/\s+/g, ' ').trim();
};

/** C#: SpeechCheck.CleanPunctuation — each punctuation char becomes a space. */
export const cleanPunctuation = (input: string): string => {
  let output = input;
  for (const p of PUNCTUATION) {
    output = output.split(p).join(' ');
  }
  return output;
};

/** C#: SpeechCheck.IsXSolution — any whole word equal to "X"/"x". */
export const isXSolution = (solution: string): boolean => {
  if (!solution) return false;
  return solution.split(' ').some((w) => w.toLowerCase() === 'x');
};

// C#: SpeechCheck.GetWordScore — ported exactly, including the quirk that the
// bounds check (`j >= solution.length`) runs AFTER the contains/penalty
// scoring, so the first extra input char beyond the solution's length is
// still scored before the loop breaks.
const getWordScore = (solutionWord: string, inputWord: string): number => {
  let score = 0;
  if (inputWord.length === solutionWord.length) {
    score += LENGTH_SCORE;
  }
  let j = 0;
  let lastChar = ' ';
  for (let i = 0; i < inputWord.length; i++) {
    const c = inputWord[i];
    if (solutionWord.includes(c)) {
      score += CONTAINS_CHARACTER_SCORE;
    } else {
      score += EXTRA_CHARACTER_PENALTY;
    }
    if (j >= solutionWord.length) break;
    if (solutionWord[j] === c) {
      score += CHARACTER_POS_SCORE;
      if (j > 0 && solutionWord[j - 1] === lastChar) {
        score += SEQUENCE_SCORE;
      }
      lastChar = c;
    }
    j++;
  }
  return score;
};

const explodeToChars = (word: string): string[] => word.split('');

// C#: SpeechCheck.ScoreWithoutX. Single-word vs single-word comparisons are
// exploded to per-character tokens (space-free-language path).
const scoreWithoutX = (solutionWordsIn: string[], inputWordsIn: string[]): number => {
  let solutionWords = solutionWordsIn;
  let inputWords = inputWordsIn;
  if (solutionWords.length === 1 && inputWords.length === 1) {
    solutionWords = explodeToChars(solutionWords[0]);
    inputWords = explodeToChars(inputWords[0]);
  }
  let score = 0;
  for (const inputWord of inputWords) {
    const maxWordScore = getWordScore(inputWord, inputWord);
    if (maxWordScore === 0) continue;
    let bestWordScore = 0;
    for (const solutionWord of solutionWords) {
      const newScore = getWordScore(solutionWord, inputWord);
      if (newScore > bestWordScore) {
        bestWordScore = newScore;
      }
    }
    score += bestWordScore;
  }
  return score;
};

// C#: SpeechCheck.ScoreWithX — words before the first X and after the last X
// are position-scored against the input's start/end; too-short input falls
// back to regular scoring.
const scoreWithX = (
  solutionString: string,
  inputString: string,
  solutionWordsIn: string[],
  inputWordsIn: string[],
): number => {
  let solutionWords = solutionWordsIn;
  let inputWords = inputWordsIn;
  let score = 0;
  if (solutionString === 'X' || solutionString === 'x') {
    return 1;
  }
  if (solutionWords.length === 1) {
    solutionWords = explodeToChars(solutionString);
    inputWords = explodeToChars(inputString);
  }
  let wordsBeforeX = 0;
  let wordsAfterX = 0;
  for (let i = 0; i < solutionWords.length; i++) {
    if (solutionWords[i] === 'X' || solutionWords[i] === 'x') break;
    wordsBeforeX++;
  }
  for (let i = solutionWords.length - 1; i >= 0; i--) {
    if (solutionWords[i] === 'X' || solutionWords[i] === 'x') break;
    wordsAfterX++;
  }
  if (inputWords.length < wordsBeforeX + wordsAfterX) {
    return scoreWithoutX(solutionWords, inputWords);
  }
  for (let j = 0; j < wordsBeforeX; j++) {
    score += getWordScore(solutionWords[j], inputWords[j]);
  }
  for (let j = 0; j < wordsAfterX; j++) {
    score += getWordScore(
      solutionWords[solutionWords.length - 1 - j],
      inputWords[inputWords.length - 1 - j],
    );
  }
  return score;
};

// C#: SpeechCheck.GetStringScore. The X branch triggers on the solution
// CONTAINING the letter x anywhere (case-insensitive), not on a whole-word X
// — e.g. "taxi" takes the X path and then falls back. Ported as-is.
const getStringScore = (solutionStringIn: string, inputStringIn: string): number => {
  const solutionString = cleanPunctuation(solutionStringIn);
  const inputString = cleanPunctuation(inputStringIn);
  const solutionWords = solutionString.split(' ');
  const inputWords = inputString.split(' ');
  if (solutionString.toLowerCase().includes('x')) {
    return scoreWithX(solutionString, inputString, solutionWords, inputWords);
  }
  return scoreWithoutX(solutionWords, inputWords);
};

const isAllDigits = (s: string): boolean => {
  if (s.length === 0) return false;
  for (let i = 0; i < s.length; i++) {
    if (!DIGIT.test(s[i])) return false;
  }
  return true;
};

// C#: SpeechCheck.HasNumeralMismatch — a pure-digit solution word missing
// verbatim from the input forces the score to 0 (N-best/mutter path ONLY;
// the static CheckAnswer path deliberately has no numeral guard, matching VR).
const hasNumeralMismatch = (solutionNormalized: string, inputNormalized: string): boolean => {
  const solutionWords = solutionNormalized.split(' ');
  const inputWords = inputNormalized.split(' ');
  return solutionWords.some((w) => isAllDigits(w) && !inputWords.includes(w));
};

// C#: SpeechCheck.GetMaxScore — a solution scored against itself.
const getMaxScore = (solutionString: string): number => getStringScore(solutionString, solutionString);

// C#: SpeechCheck.GetNormalizedScore.
const getNormalizedScore = (input: string, solution: string): number => {
  const normalizedSolution = normalizeKD(solution);
  let maxScore = getMaxScore(normalizedSolution);
  if (maxScore === 0) maxScore = 1;
  return clamp01f(getStringScore(normalizedSolution, normalizeKD(input)) / maxScore);
};

// C#: SpeechCheck.GetBestScore.
const getBestScore = (input: string, solutions: readonly string[] | null | undefined): number => {
  if (!solutions) return 0;
  let bestScore = 0;
  for (const solution of solutions) {
    const score = getNormalizedScore(input, solution);
    if (score > bestScore) {
      bestScore = score;
    }
  }
  return bestScore;
};

/**
 * C#: SpeechCheck.GetScore / GetBestMatchScore — best normalized score of the
 * input against a set of solutions (0..1). For space-free languages, callers
 * pre-apply normalizeKD(text, true) to input and every solution first (see
 * checkAnswerForLanguage for a helper that does this).
 */
export const getBestMatchScore = (input: string, solutions: readonly string[] | null | undefined): number =>
  getBestScore(input, solutions);

/**
 * C#: SpeechCheck.CheckAnswer (static path used by trip quizzes and Recall
 * Quiz): passes when best score >= threshold AND >= the best incorrect-answer
 * score. Note: the live Azure mutter path uses a STRICT > threshold instead
 * (see checkMutter) — that asymmetry exists in VR and is preserved.
 */
export const checkAnswer = (
  input: string,
  possibleSolutions: readonly string[],
  incorrectSolutions: readonly string[] | null,
  correctThreshold: number,
): boolean => {
  const highestScore = getBestScore(input, possibleSolutions);
  const bestIncorrectScore = getBestScore(input, incorrectSolutions);
  return highestScore >= correctThreshold && highestScore >= bestIncorrectScore;
};

export interface AnswerCheckResult {
  passed: boolean;
  /** Best 0..1 score vs the correct solutions (what VR persists, rounded to 3dp at the call site). */
  score: number;
}

/**
 * Convenience wrapper replicating the VR call-site pattern
 * (QuickTripQuizController.cs / RecallQuizController.cs): applies the
 * space-free-language pre-normalization when needed, then runs the static
 * scorer. Use this from web viewers instead of hand-rolling the dance.
 *
 * `additionalSolutions` (JP reading-space marking, and now DE transliteration
 * — see answerNotes.germanTransliterationVariants): extra accepted forms —
 * e.g. hiragana readings alongside kanji `possibleSolutions`, or "ue/oe/ae/ss"
 * spellings alongside umlaut/eszett ones — folded into the SAME solutions
 * array before scoring, for EVERY language (not just space-free ones: DE
 * needs the merge too, since short words like "baer"/"Bär" score below
 * threshold on fuzzy matching alone). The result is exactly "best score
 * across either set" since getBestScore/checkAnswer already take the max
 * over the whole array; no behaviour change for callers that omit it
 * (existing call sites that never pass it are byte-identical). Romaji must
 * NEVER be passed here — it's display-only (see vrmSchedule hintSolutions).
 * Recognising "wrong kanji but right reading" as a distinct (fail-with-note)
 * outcome instead of plain acceptance is future work.
 */
export const checkAnswerForLanguage = (
  input: string,
  possibleSolutions: readonly string[],
  incorrectSolutions: readonly string[] | null,
  correctThreshold: number,
  languageCode: string,
  additionalSolutions: readonly string[] | null = null,
): AnswerCheckResult => {
  const combinedSolutions =
    additionalSolutions && additionalSolutions.length > 0
      ? [...possibleSolutions, ...additionalSolutions]
      : possibleSolutions;
  let scoredInput = input;
  let solutions: readonly string[] = combinedSolutions;
  let incorrect = incorrectSolutions;
  if (isSpaceFreeLanguage(languageCode)) {
    scoredInput = normalizeKD(input, true);
    solutions = combinedSolutions.map((s) => normalizeKD(s, true));
    incorrect = incorrectSolutions ? incorrectSolutions.map((s) => normalizeKD(s, true)) : null;
  }
  return {
    passed: checkAnswer(scoredInput, solutions, incorrect, correctThreshold),
    score: getBestScore(scoredInput, solutions),
  };
};

// ---------------------------------------------------------------------------
// N-best ("mutter") path — used with Azure Speech Detailed results, where each
// utterance yields several candidate transcriptions (lexical + ITN forms).
// C#: SpeechCheck.CheckMutter/GetMutterScore + AzureController.RecognitionOption
// + PossibleSolution.
// ---------------------------------------------------------------------------

export interface RecognitionOption {
  /** Raw transcription as shown to the user (C#: Original). */
  original: string;
  /** normalizeKD(original) (C#: Normalized). */
  normalized: string;
  /** normalizeKD(original, true) (C#: NormalizedAndTrimmed). */
  normalizedTrimmed: string;
}

/** C#: AzureController.RecognitionOption constructor for a candidate form. */
export const makeRecognitionOption = (form: string): RecognitionOption => ({
  original: form,
  normalized: normalizeKD(form),
  normalizedTrimmed: normalizeKD(form, true),
});

interface PreparedSolution {
  normalized: string;
  unsolvedNormalizedTrimmed: string;
}

// C#: PossibleSolution constructor (removeSpaces from IsSpaceFreeLanguage()).
const prepareSolution = (original: string, removeSpaces: boolean): PreparedSolution => {
  const normalized = normalizeKD(original, removeSpaces);
  const normalizedTrimmed = normalizeKD(normalized, true);
  return {
    normalized,
    unsolvedNormalizedTrimmed: normalizedTrimmed.split(' ').join(''),
  };
};

interface MutterScore {
  score: number;
  bestString: string;
}

// C#: SpeechCheck.GetMutterScore — exact trimmed match short-circuits to 1;
// otherwise normalized string score with the numeral guard applied.
const getMutterScore = (solution: PreparedSolution, options: readonly RecognitionOption[]): MutterScore => {
  let bestScore = 0;
  let bestString = '';
  let maxScore = getMaxScore(solution.normalized);
  if (maxScore === 0) maxScore = 1;
  for (const mutter of options) {
    if (mutter.normalizedTrimmed === solution.unsolvedNormalizedTrimmed) {
      return { score: 1, bestString: mutter.original };
    }
    let score = clamp01f(getStringScore(solution.normalized, mutter.normalized) / maxScore);
    if (hasNumeralMismatch(solution.normalized, mutter.normalized)) {
      score = 0;
    }
    if (score >= bestScore || bestString === '') {
      bestScore = score;
      bestString = mutter.original;
    }
  }
  return { score: bestScore, bestString };
};

export interface MutterCheckResult {
  /** True when the utterance passes: best score STRICTLY > difficulty and >= best incorrect score. */
  isSolved: boolean;
  /** Best 0..1 score vs the correct solutions. */
  score: number;
  /** The raw candidate transcription that produced the best score (shown to the user). */
  userAnswer: string;
}

/**
 * C#: SpeechCheck.CheckMutter — scores a set of N-best recognition candidates
 * against the question's solutions. Returns null when there are no solutions
 * (C# clears the pending-mutter flag and does nothing).
 *
 * `removeSpaces` = isSpaceFreeLanguage(content language). Note the STRICT
 * `score > difficulty` here vs `>=` on the static path — a VR asymmetry
 * preserved for score comparability.
 */
export const checkMutter = (
  options: readonly RecognitionOption[],
  possibleSolutions: readonly string[],
  incorrectAnswers: readonly string[] | null,
  difficulty: number,
  removeSpaces: boolean,
): MutterCheckResult | null => {
  if (possibleSolutions.length === 0) return null;

  let bestScore = 0;
  let bestString = '';
  for (const solutionText of possibleSolutions) {
    const solution = prepareSolution(solutionText, removeSpaces);
    const result = getMutterScore(solution, options);
    if (result.score >= bestScore || bestString === '') {
      bestScore = result.score;
      bestString = result.bestString;
    }
  }

  let bestIncorrectScore = 0;
  if (incorrectAnswers) {
    for (const incorrectText of incorrectAnswers) {
      const incorrect = prepareSolution(incorrectText, removeSpaces);
      const result = getMutterScore(incorrect, options);
      if (result.score >= bestIncorrectScore) {
        bestIncorrectScore = result.score;
      }
    }
  }

  return {
    isSolved: bestScore > difficulty && bestScore >= bestIncorrectScore,
    score: bestScore,
    userAnswer: bestString,
  };
};
