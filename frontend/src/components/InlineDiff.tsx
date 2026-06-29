import { useMemo } from 'react';
import { diff_match_patch, type Diff } from 'diff-match-patch';

const DIFF_DELETE = -1;
const DIFF_INSERT = 1;

interface InlineDiffProps {
  original: string;
  current: string;
}

const dmp = new diff_match_patch();

/**
 * Inline original-vs-new diff. Deletions are red + struck through, insertions
 * are green. `diff_cleanupSemantic` makes the diff read at a word level rather
 * than character level. The computation is memoised so it does not run on every
 * keystroke of the parent (the parent already debounces what it passes in).
 */
const InlineDiff = ({ original, current }: InlineDiffProps) => {
  const diffs = useMemo<Diff[]>(() => {
    const d = dmp.diff_main(original, current);
    dmp.diff_cleanupSemantic(d);
    return d;
  }, [original, current]);

  return (
    <div className="mt-2 rounded border border-gray-700 bg-gray-900/60 p-2 text-sm leading-relaxed">
      <div className="mb-1 text-[10px] uppercase tracking-wide text-gray-500">Original → new</div>
      <p className="whitespace-pre-wrap break-words">
        {diffs.map((part, i) => {
          const [op, text] = part;
          if (op === DIFF_DELETE) {
            return (
              <span key={i} className="bg-red-900/40 text-red-300 line-through">
                {text}
              </span>
            );
          }
          if (op === DIFF_INSERT) {
            return (
              <span key={i} className="bg-green-900/40 text-green-300">
                {text}
              </span>
            );
          }
          return (
            <span key={i} className="text-gray-300">
              {text}
            </span>
          );
        })}
      </p>
    </div>
  );
};

export default InlineDiff;
