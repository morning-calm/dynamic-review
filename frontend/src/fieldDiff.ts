import type { Field, LocalizationBlock, LocalizationScripts } from './api';

// _ZH edits live in the localization block (cur vs orig per script), NOT current_text,
// so any "did this field change?" check must diff each of the 4 scripts.
export const ZH_SCRIPTS: [keyof LocalizationScripts, string][] = [
  ['Hant', 'Traditional'],
  ['Hans', 'Simplified'],
  ['zhuyin', 'Zhuyin'],
  ['en', 'English'],
];

export const zhChangedScripts = (b: LocalizationBlock): [keyof LocalizationScripts, string][] =>
  ZH_SCRIPTS.filter(([s]) => (b.cur[s] ?? '') !== (b.orig[s] ?? ''));

/**
 * The four scripts are ONE fact in four forms, so a PARTIAL edit (some changed, some not)
 * means the untouched ones are probably stale (2026-07-08: a whole trip shipped with Hans
 * edited but Hant/zhuyin/en untouched). Returns the split, or null when the edit isn't
 * partial. Soft signal — it never blocks. Shown both above the 4-script block
 * (LocalizationEditor) and next to Mark done (FlagControl), so it can't be scrolled past.
 */
export const zhPartialEdit = (
  b: LocalizationBlock,
): { changed: (keyof LocalizationScripts)[]; unchanged: (keyof LocalizationScripts)[] } | null => {
  const present = ZH_SCRIPTS.map(([s]) => s).filter((s) => b.cur[s] != null);
  const changed = present.filter((s) => (b.cur[s] ?? '') !== (b.orig[s] ?? ''));
  const unchanged = present.filter((s) => !changed.includes(s));
  return changed.length > 0 && unchanged.length > 0 ? { changed, unchanged } : null;
};

export const fieldChanged = (f: Field): boolean =>
  f.localization ? zhChangedScripts(f.localization).length > 0 : f.current_text !== f.original_text;
