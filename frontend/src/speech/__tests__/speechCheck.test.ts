import { describe, expect, it } from 'vitest';
import {
  checkAnswer,
  checkAnswerForLanguage,
  checkMutter,
  cleanPunctuation,
  getBestMatchScore,
  isSpaceFreeLanguage,
  isXSolution,
  makeRecognitionOption,
  normalizeKD,
} from '../speechCheck';
import { mutterVectors, staticVectors } from './vectors.generated';

// C# computes the final normalized score in 32-bit floats; the TS port uses
// Math.fround to match. Compare with a tolerance far tighter than the 3-dp
// rounding applied when scores are persisted to session docs.
const SCORE_TOLERANCE = 1e-6;

describe('C#-generated shared vectors: static path (checkAnswer/getBestMatchScore)', () => {
  for (const vector of staticVectors) {
    it(vector.name, () => {
      // Replicate the VR call-site pre-normalization for space-free languages.
      const input = vector.spaceFree ? normalizeKD(vector.input, true) : vector.input;
      const solutions = vector.spaceFree ? vector.solutions.map((s) => normalizeKD(s, true)) : vector.solutions;
      const incorrect = vector.spaceFree
        ? (vector.incorrect ? vector.incorrect.map((s) => normalizeKD(s, true)) : null)
        : vector.incorrect;

      const score = getBestMatchScore(input, solutions);
      const passed = checkAnswer(input, solutions, incorrect, vector.threshold);

      expect(Math.abs(score - vector.expectedScore)).toBeLessThanOrEqual(SCORE_TOLERANCE);
      expect(passed).toBe(vector.expectedPass);
    });
  }

  it('checkAnswerForLanguage applies the space-free pre-normalization itself', () => {
    for (const vector of staticVectors) {
      const result = checkAnswerForLanguage(
        vector.input,
        vector.solutions,
        vector.incorrect,
        vector.threshold,
        vector.spaceFree ? 'JP' : 'ES',
      );
      expect(Math.abs(result.score - vector.expectedScore)).toBeLessThanOrEqual(SCORE_TOLERANCE);
      expect(result.passed).toBe(vector.expectedPass);
    }
  });
});

describe('checkAnswerForLanguage: additionalSolutions (JP reading-space marking)', () => {
  // 知りたい (shiritai, "want to know") — kanji solution with its hiragana
  // reading passed as an additional solution, mirroring
  // VoiceRecMoment.possibleSolutionsKana plumbed from LessonViewer.
  const kanjiSolution = ['知りたい'];
  const kanaReading = ['しりたい'];

  it('accepts a kana-only typed answer via the additional (kana) solutions', () => {
    const result = checkAnswerForLanguage('しりたい', kanjiSolution, null, 0.75, 'JP', kanaReading);
    expect(result.passed).toBe(true);
  });

  it('still accepts the original kanji answer unchanged', () => {
    const result = checkAnswerForLanguage('知りたい', kanjiSolution, null, 0.75, 'JP', kanaReading);
    expect(result.passed).toBe(true);
  });

  it('is a no-op for non-space-free languages (additionalSolutions ignored)', () => {
    const withAdditional = checkAnswerForLanguage('hola', ['hola'], null, 0.75, 'ES', ['ola']);
    const withoutAdditional = checkAnswerForLanguage('hola', ['hola'], null, 0.75, 'ES');
    expect(withAdditional).toEqual(withoutAdditional);
  });

  it('never accepts romaji as a typed answer (display-only, not plumbed as a solution)', () => {
    const result = checkAnswerForLanguage('shiritai', kanjiSolution, null, 0.75, 'JP', kanaReading);
    expect(result.passed).toBe(false);
  });

  it('omitting additionalSolutions behaves exactly as before (byte-identical for existing callers)', () => {
    const withDefault = checkAnswerForLanguage('知りたい', kanjiSolution, null, 0.75, 'JP');
    const withExplicitNull = checkAnswerForLanguage('知りたい', kanjiSolution, null, 0.75, 'JP', null);
    expect(withDefault).toEqual(withExplicitNull);
  });
});

describe('C#-generated shared vectors: N-best mutter path (checkMutter)', () => {
  for (const vector of mutterVectors) {
    it(vector.name, () => {
      const options = vector.options.map(makeRecognitionOption);
      const result = checkMutter(options, vector.solutions, vector.incorrect, vector.difficulty, vector.removeSpaces);

      expect(result).not.toBeNull();
      if (!result) return;
      expect(Math.abs(result.score - vector.expectedScore)).toBeLessThanOrEqual(SCORE_TOLERANCE);
      expect(result.isSolved).toBe(vector.expectedSolved);
      expect(result.userAnswer).toBe(vector.expectedUserAnswer);
    });
  }

  it('returns null when there are no solutions (C# clears the mutter and does nothing)', () => {
    expect(checkMutter([makeRecognitionOption('hola')], [], null, 0.75, false)).toBeNull();
  });
});

describe('normalizeKD', () => {
  it('strips punctuation and symbols, lowercases, collapses whitespace', () => {
    expect(normalizeKD('  ¿Cómo  ESTÁS?! ')).toBe('cómo estás');
  });

  it('does NOT apply Unicode NFKD despite the name (parity with C#)', () => {
    // 'ñ' precomposed stays a single letter; it is not decomposed and the
    // combining tilde is not stripped.
    expect(normalizeKD('mañana')).toBe('mañana');
  });

  it('removeSpaces=true removes every space', () => {
    expect(normalizeKD(' こんにち は 。', true)).toBe('こんにちは');
  });

  it('keeps letters and digits from any script', () => {
    expect(normalizeKD('水を25ください！')).toBe('水を25ください');
  });

  it('drops non-space whitespace (tabs/newlines) via the letter-or-digit filter', () => {
    expect(normalizeKD('hola\tque\ntal')).toBe('holaquetal');
  });
});

describe('cleanPunctuation', () => {
  it('replaces each listed punctuation char with a space', () => {
    expect(cleanPunctuation('a.b,c!d?e-f¿g。h、i')).toBe('a b c d e f g h i');
  });

  it('leaves other characters alone', () => {
    expect(cleanPunctuation('a:b;c')).toBe('a:b;c');
  });
});

describe('isXSolution', () => {
  it('matches a standalone X word in any case', () => {
    expect(isXSolution('me llamo X')).toBe(true);
    expect(isXSolution('x')).toBe(true);
  });

  it('does not match the letter x inside a word', () => {
    expect(isXSolution('taxi')).toBe(false);
    expect(isXSolution('')).toBe(false);
  });
});

describe('isSpaceFreeLanguage', () => {
  it('covers the VR SpaceFreeLanguages set', () => {
    expect(isSpaceFreeLanguage('JP')).toBe(true);
    expect(isSpaceFreeLanguage('JP_TRIPS')).toBe(true);
    expect(isSpaceFreeLanguage('KO')).toBe(true);
    expect(isSpaceFreeLanguage('ZH')).toBe(true);
    expect(isSpaceFreeLanguage('ES')).toBe(false);
    expect(isSpaceFreeLanguage('ES_PACK')).toBe(false);
    expect(isSpaceFreeLanguage('DE')).toBe(false);
  });
});

describe('threshold semantics asymmetry (VR parity)', () => {
  it('static path passes at score == threshold (>=)', () => {
    expect(checkAnswer('hola', ['hola'], null, 1)).toBe(true);
  });

  it('mutter path fails at score == difficulty (strict >)', () => {
    const result = checkMutter([makeRecognitionOption('hola')], ['hola'], null, 1, false);
    expect(result?.score).toBe(1);
    expect(result?.isSolved).toBe(false);
  });
});
