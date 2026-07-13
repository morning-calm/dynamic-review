import type { Field, Finding } from './api';

/** `questionOption[1]` — the shape `Field.field_path` already uses, so a finding and the
 * field it is about can be matched on (scene, field_path) without either side guessing. */
export const findingFieldPath = (f: Finding): string =>
  f.option !== null ? `${f.field}[${f.option}]` : f.field;

/** Findings about one field, for the copy rendered INSIDE the scene (dave, 2026-07-13: the
 * AI's remark has to be readable where the text is, not only in the summary panel up top). */
export const findingsForField = (findings: Finding[], field: Field): Finding[] =>
  findings.filter(
    (f) => f.scene === field.scene_index && findingFieldPath(f) === field.field_path,
  );
