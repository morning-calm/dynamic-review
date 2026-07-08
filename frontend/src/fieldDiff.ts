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

export const fieldChanged = (f: Field): boolean =>
  f.localization ? zhChangedScripts(f.localization).length > 0 : f.current_text !== f.original_text;
