import { useEffect, useMemo, useState } from 'react';
import { toast } from 'react-toastify';
import { api, ApiError } from '../api';

/**
 * Admin-only inline editor for a trip's categories (writes staging `tripCategories`
 * via the existing structure-categories endpoint). Current categories show as
 * removable chips; a free-form input adds new ones; and the content-enrichment
 * proposals for the trip (staging ContentEnrichment sidecar) are offered as one-tap
 * "add" chips. Reviewers keep the read-only list (this isn't rendered for them).
 */
const CategoryEditor = ({
  tripId,
  categories,
  onChange,
}: {
  tripId: string;
  categories: string[];
  onChange: (cats: string[]) => void;
}) => {
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState('');
  const [applicable, setApplicable] = useState<string[]>([]);
  const [suggestions, setSuggestions] = useState<string[]>([]);

  // Enrichment proposals — best-effort; the editor is fully usable without them.
  useEffect(() => {
    let cancelled = false;
    api
      .enrichmentCategories(tripId)
      .then((r) => {
        if (cancelled) return;
        setApplicable(r.applicable ?? []);
        setSuggestions(r.suggestions ?? []);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [tripId]);

  // Enrichment chips not already applied, applicable (in-vocabulary) first.
  const proposed = useMemo(() => {
    const have = new Set(categories.map((c) => c.toLowerCase()));
    const seen = new Set<string>();
    const out: { name: string; isNew: boolean }[] = [];
    for (const [list, isNew] of [
      [applicable, false],
      [suggestions, true],
    ] as const) {
      for (const c of list) {
        const k = c.toLowerCase();
        if (!c || have.has(k) || seen.has(k)) continue;
        seen.add(k);
        out.push({ name: c, isNew });
      }
    }
    return out;
  }, [applicable, suggestions, categories]);

  const write = (next: string[]) => {
    // Dedupe (case-insensitive), preserve order, drop blanks.
    const seen = new Set<string>();
    const clean = next
      .map((c) => c.trim())
      .filter((c) => {
        if (!c) return false;
        const k = c.toLowerCase();
        if (seen.has(k)) return false;
        seen.add(k);
        return true;
      });
    setBusy(true);
    api
      .structureCategories(tripId, clean)
      .then((r) => {
        onChange(r.structure.categories);
        r.warnings?.forEach((w) => toast.warn(w));
      })
      .catch((e: unknown) =>
        toast.error(`Couldn't save categories: ${e instanceof ApiError ? e.detail : 'network error'}`),
      )
      .finally(() => setBusy(false));
  };

  const add = (c: string) => {
    const v = c.trim();
    if (!v) return;
    if (categories.some((x) => x.toLowerCase() === v.toLowerCase())) return;
    write([...categories, v]);
  };
  const remove = (c: string) => write(categories.filter((x) => x !== c));

  return (
    <div className="space-y-2">
      <p className="text-xs font-medium uppercase tracking-wide text-gray-400">Trip categories (admin — edits staging)</p>

      <div className="flex flex-wrap gap-2">
        {categories.length === 0 && <span className="text-xs text-gray-500">none yet</span>}
        {categories.map((c) => (
          <span key={c} className="inline-flex items-center gap-1 rounded bg-gray-700 px-2 py-1 text-xs text-gray-100">
            {c}
            <button
              type="button"
              disabled={busy}
              onClick={() => remove(c)}
              aria-label={`Remove ${c}`}
              title={`Remove ${c}`}
              className="text-gray-400 enabled:hover:text-rose-400 disabled:opacity-40"
            >
              ✕
            </button>
          </span>
        ))}
      </div>

      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          add(draft);
          setDraft('');
        }}
      >
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Add a category…"
          className="min-w-0 flex-1 rounded border border-gray-700 bg-gray-900 px-3 py-2 text-base text-gray-100 outline-none focus:border-custom-green sm:text-sm"
        />
        <button
          type="submit"
          disabled={busy || !draft.trim()}
          className="shrink-0 rounded border border-custom-green px-3 py-2 text-xs text-custom-green enabled:hover:bg-custom-green enabled:hover:text-white disabled:opacity-40 sm:py-1"
        >
          Add
        </button>
      </form>

      {proposed.length > 0 && (
        <div>
          <p className="mb-1 text-[11px] text-gray-500">From content enrichment — tap to add:</p>
          <div className="flex flex-wrap gap-2">
            {proposed.map(({ name, isNew }) => (
              <button
                key={name}
                type="button"
                disabled={busy}
                onClick={() => add(name)}
                title={isNew ? 'New category the enrichment suggested (not yet in the vocabulary)' : 'Enrichment pick from the category vocabulary'}
                className={`inline-flex items-center gap-1 rounded border px-2 py-1 text-xs disabled:opacity-40 ${
                  isNew
                    ? 'border-sky-700 text-sky-300 enabled:hover:bg-sky-900/40'
                    : 'border-gray-600 text-gray-300 enabled:hover:bg-gray-700'
                }`}
              >
                <span aria-hidden="true">+</span>
                {name}
                {isNew && <span className="text-[9px] uppercase text-sky-500">new</span>}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};

export default CategoryEditor;
