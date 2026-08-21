import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { toast } from 'react-toastify';
import { api, ApiError, type CategoryCheck, type TripDescItem } from '../api';
import { useAuth } from '../authContext';
import NavBar from '../components/NavBar';

const errText = (e: unknown, fallback: string): string =>
  e instanceof ApiError ? e.detail || e.code : fallback;

type SaveBody = { en_text?: string; categories?: string[]; tl_text?: string };

/** One family's description review. Admins get the full checking context (the
 * family's scenes + categories) while the item awaits its English check; a
 * translator gets just the machine-translated TL text with the approved English
 * as reference. Text edits autosave (debounced). */
const TripDescPage = () => {
  const { tgId = '' } = useParams();
  const navigate = useNavigate();
  const { user } = useAuth();
  const isAdmin = user?.role === 'admin';

  const [item, setItem] = useState<TripDescItem | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [enText, setEnText] = useState('');
  const [tlText, setTlText] = useState('');
  const [categories, setCategories] = useState<string[]>([]);
  const [newCat, setNewCat] = useState('');
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [usedCats, setUsedCats] = useState<{ name: string; count: number }[]>([]);
  const [check, setCheck] = useState<CategoryCheck | null>(null);
  const [busy, setBusy] = useState(false);
  const [saveState, setSaveState] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // The not-yet-flushed debounced edit — approve must flush it first, or an edit made
  // within the debounce window would be silently dropped (approve reads the server row).
  const pendingSave = useRef<SaveBody | null>(null);

  const load = useCallback(() => {
    api
      .getTripDesc(tgId)
      .then((r) => {
        setItem(r);
        setEnText(r.en_text);
        setTlText(r.tl_text);
        setCategories(r.categories);
      })
      .catch((e: unknown) => setError(errText(e, 'Failed to load')));
  }, [tgId]);

  useEffect(load, [load]);

  // Enrichment category suggestions (admin, EN stage only) — best-effort.
  const itemStatus = item?.status;
  const repTripId = item?.rep_trip_id;
  useEffect(() => {
    if (!isAdmin || itemStatus !== 'pending_en' || !repTripId) return;
    api
      .enrichmentCategories(repTripId)
      .then((r) => setSuggestions([...r.applicable, ...r.suggestions]))
      .catch(() => {});
  }, [isAdmin, itemStatus, repTripId]);

  // The live category vocabulary (every category any staging TripGroup carries).
  useEffect(() => {
    if (!isAdmin || itemStatus !== 'pending_en') return;
    api
      .tripDescCategories()
      .then((r) => setUsedCats(r.categories))
      .catch(() => {});
  }, [isAdmin, itemStatus]);

  // While the machine translation runs, poll for its arrival.
  useEffect(() => {
    if (itemStatus !== 'translating') return;
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [itemStatus, load]);

  const doSave = (body: SaveBody) =>
    api
      .saveTripDesc(tgId, body)
      .then(() => setSaveState('saved'))
      .catch((e: unknown) => {
        setSaveState('error');
        toast.error(errText(e, 'Save failed'));
        throw e;
      });

  const scheduleSave = (body: SaveBody) => {
    if (saveTimer.current) clearTimeout(saveTimer.current);
    pendingSave.current = { ...pendingSave.current, ...body };
    setSaveState('saving');
    saveTimer.current = setTimeout(() => {
      const b = pendingSave.current;
      pendingSave.current = null;
      if (b) doSave(b).catch(() => {});
    }, 600);
  };

  /** Push any debounce-pending edit to the server NOW (before an approve). */
  const flushSave = (): Promise<unknown> => {
    if (!pendingSave.current) return Promise.resolve();
    if (saveTimer.current) clearTimeout(saveTimer.current);
    const b = pendingSave.current;
    pendingSave.current = null;
    return doSave(b);
  };

  const setCats = (next: string[]) => {
    setCategories(next);
    scheduleSave({ categories: next });
    // Drop the sibling-fit panel once its category is no longer applied.
    if (check && !next.some((c) => c.toLowerCase() === check.category.toLowerCase())) setCheck(null);
  };

  /** Add a category and run the sibling-fit check (other trips in the same
   * country/playlist whose description mentions it but lack the tag). */
  const addCat = (raw: string) => {
    const v = raw.trim();
    if (!v || categories.some((c) => c.toLowerCase() === v.toLowerCase())) return;
    setCats([...categories, v]);
    api
      .tripDescCategoryCheck(tgId, v)
      .then(setCheck)
      .catch(() => setCheck(null));
  };

  const action = (fn: () => Promise<TripDescItem>, done?: string): Promise<TripDescItem | null> => {
    setBusy(true);
    return flushSave()
      .then(fn)
      .then((r) => {
        setItem(r);
        setEnText(r.en_text);
        setTlText(r.tl_text);
        setCategories(r.categories);
        if (done) toast.success(done);
        return r;
      })
      .catch((e: unknown) => {
        toast.error(errText(e, 'Action failed'));
        return null;
      })
      .finally(() => setBusy(false));
  };

  if (error) {
    return (
      <div className="min-h-screen">
        <NavBar title="Trip description" backTo="/descriptions" backLabel="Descriptions" />
        <main className="mx-auto max-w-review px-4 py-8">
          <p className="text-rose-400">{error}</p>
        </main>
      </div>
    );
  }
  if (!item) {
    return <p className="mx-auto max-w-review px-4 py-8 text-gray-400">Loading…</p>;
  }

  const saveLabel =
    saveState === 'saving' ? 'Saving…' : saveState === 'saved' ? 'Saved' : saveState === 'error' ? 'Save failed' : '';

  return (
    <div className="min-h-screen">
      <NavBar
        title={`${item.family || item.tg_id} — description`}
        subtitle={`${item.tg_id} · ${item.language}`}
        backTo="/descriptions"
        backLabel="Descriptions"
        right={
          <span className={`text-xs ${saveState === 'error' ? 'text-rose-400' : 'text-gray-400'}`}>
            {saveLabel}
          </span>
        }
      />
      <main className="mx-auto max-w-review space-y-6 px-4 py-6">
        {/* ---- Stage banner ---- */}
        {item.status === 'translating' && (
          <div className="rounded-lg border border-sky-800 bg-sky-900/30 p-4 text-sm text-sky-100">
            {item.last_error ? (
              <>
                <p className="mb-2">
                  Machine translation failed: <span className="text-rose-300">{item.last_error}</span>
                </p>
                {isAdmin && (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => action(() => api.retryTripDescTranslate(tgId), 'Translation restarted')}
                    className="rounded bg-sky-700 px-3 py-1.5 text-white hover:bg-sky-600 disabled:opacity-50"
                  >
                    Retry translation
                  </button>
                )}
              </>
            ) : (
              <p>Translating into {item.language}… this page refreshes automatically.</p>
            )}
          </div>
        )}
        {item.status === 'done' && (
          <div className="rounded-lg border border-emerald-800 bg-emerald-900/30 p-4 text-sm text-emerald-100">
            Done — written to staging.
            {isAdmin && (
              <button
                type="button"
                disabled={busy}
                onClick={() => action(() => api.reopenTripDesc(tgId), 'Reopened for the English check')}
                className="ml-3 rounded border border-emerald-600 px-3 py-1 text-emerald-100 hover:bg-emerald-800/50 disabled:opacity-50"
              >
                Reopen
              </button>
            )}
          </div>
        )}

        {/* ---- English description (admin edits during pending_en; reference later) ---- */}
        <section className="rounded-lg border border-gray-700 bg-gray-800/60 p-4">
          <h2 className="mb-2 text-sm font-semibold text-white">
            English description
            {item.en_by && (
              <span className="ml-2 text-xs font-normal text-gray-400">approved by {item.en_by}</span>
            )}
          </h2>
          {isAdmin && item.status === 'pending_en' ? (
            <>
              <p className="mb-2 text-xs text-gray-400">
                Check it against the scenes below: accurate, and covers the most important places.
                Keep the metadata lines (Trip Type / guide / duration) intact.
              </p>
              <textarea
                value={enText}
                onChange={(e) => {
                  setEnText(e.target.value);
                  scheduleSave({ en_text: e.target.value });
                }}
                rows={8}
                className="w-full rounded border border-gray-600 bg-gray-900 p-3 text-sm text-gray-100"
              />
            </>
          ) : (
            <p className="whitespace-pre-wrap text-sm text-gray-200">{item.en_text || '—'}</p>
          )}
        </section>

        {/* ---- Categories (admin, EN stage) ---- */}
        {isAdmin && (
          <section className="rounded-lg border border-gray-700 bg-gray-800/60 p-4">
            <h2 className="mb-2 text-sm font-semibold text-white">Categories (TripGroup)</h2>
            <div className="mb-2 flex flex-wrap gap-2">
              {categories.map((c) => (
                <span
                  key={c}
                  className="flex items-center gap-1 rounded bg-gray-700 px-2 py-0.5 text-xs text-gray-100"
                >
                  {c}
                  {item.status === 'pending_en' && (
                    <button
                      type="button"
                      aria-label={`Remove ${c}`}
                      onClick={() => setCats(categories.filter((x) => x !== c))}
                      className="text-gray-400 hover:text-rose-400"
                    >
                      ×
                    </button>
                  )}
                </span>
              ))}
              {categories.length === 0 && <span className="text-xs text-gray-500">none</span>}
            </div>
            {item.status === 'pending_en' && (() => {
              const appliedLower = new Set(categories.map((c) => c.toLowerCase()));
              const usedLower = new Set(usedCats.map((c) => c.name.toLowerCase()));
              const usedAvailable = usedCats.filter((c) => !appliedLower.has(c.name.toLowerCase()));
              // Enrichment proposals outside the live vocabulary = never used before.
              const neverUsed = suggestions.filter(
                (s, i, a) =>
                  !appliedLower.has(s.toLowerCase()) &&
                  !usedLower.has(s.toLowerCase()) &&
                  a.findIndex((x) => x.toLowerCase() === s.toLowerCase()) === i,
              );
              const fits = check?.siblings.filter((s) => s.mentions && !s.has_category) ?? [];
              return (
                <>
                  <div className="flex gap-2">
                    <input
                      value={newCat}
                      onChange={(e) => setNewCat(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') {
                          e.preventDefault();
                          addCat(newCat);
                          setNewCat('');
                        }
                      }}
                      placeholder="Add a category…"
                      className="rounded border border-gray-600 bg-gray-900 px-2 py-1 text-xs text-gray-100"
                    />
                    <button
                      type="button"
                      onClick={() => {
                        addCat(newCat);
                        setNewCat('');
                      }}
                      className="rounded border border-gray-600 px-2 py-1 text-xs text-gray-200 hover:bg-gray-700"
                    >
                      Add
                    </button>
                  </div>
                  {usedAvailable.length > 0 && (
                    <div className="mt-3">
                      <p className="mb-1 text-[11px] uppercase tracking-wide text-gray-500">
                        In use on other trips — tap to add
                      </p>
                      <div className="flex flex-wrap gap-1.5">
                        {usedAvailable.map((c) => (
                          <button
                            key={c.name}
                            type="button"
                            onClick={() => addCat(c.name)}
                            title={`Used by ${c.count} trip group${c.count === 1 ? '' : 's'}`}
                            className="rounded border border-gray-600 px-2 py-0.5 text-xs text-gray-300 hover:bg-gray-700"
                          >
                            + {c.name} <span className="text-gray-500">({c.count})</span>
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                  {neverUsed.length > 0 && (
                    <div className="mt-3 rounded border border-sky-800 bg-sky-900/20 p-2">
                      <p className="mb-1 text-[11px] uppercase tracking-wide text-sky-400">
                        Never used before — new to the category vocabulary
                      </p>
                      <div className="flex flex-wrap gap-1.5">
                        {neverUsed.map((s) => (
                          <button
                            key={s}
                            type="button"
                            onClick={() => addCat(s)}
                            title="Enrichment proposal — no existing trip carries this category yet"
                            className="rounded border border-sky-700 px-2 py-0.5 text-xs text-sky-300 hover:bg-sky-900/40"
                          >
                            + {s} <span className="text-[9px] uppercase text-sky-500">new</span>
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                  {check && (
                    <div className="mt-3 rounded border border-amber-800 bg-amber-900/20 p-3 text-xs text-amber-100">
                      <div className="mb-1 flex items-start justify-between gap-2">
                        <p className="font-semibold">
                          “{check.category}”
                          {check.is_new && <span className="ml-1 rounded bg-sky-800 px-1 text-[10px] uppercase">new category</span>}
                          {' — '}
                          {fits.length > 0
                            ? `${fits.length} other trip${fits.length === 1 ? '' : 's'} in ${
                                check.locations.map((l) => l.name).join(', ') || 'this playlist'
                              } may also fit it:`
                            : 'no other trip in this country/playlist looks like it fits it.'}
                        </p>
                        <button
                          type="button"
                          onClick={() => setCheck(null)}
                          aria-label="Dismiss"
                          className="text-amber-400 hover:text-amber-200"
                        >
                          ✕
                        </button>
                      </div>
                      {fits.length > 0 && (
                        <ul className="space-y-1">
                          {fits.map((s) => (
                            <li key={s.tg_id}>
                              <span className="font-medium text-amber-200">{s.tg_id}</span>
                              {s.snippet && <span className="text-amber-100/70"> — “{s.snippet}”</span>}
                            </li>
                          ))}
                        </ul>
                      )}
                      {check.siblings.some((s) => s.has_category) && (
                        <p className="mt-1 text-amber-100/60">
                          Already tagged: {check.siblings.filter((s) => s.has_category).map((s) => s.tg_id).join(', ')}
                        </p>
                      )}
                    </div>
                  )}
                </>
              );
            })()}
          </section>
        )}

        {/* ---- Target-language description ---- */}
        {!item.en_target && (item.status === 'pending_tl' || item.status === 'done' || !isAdmin) && (
          <section className="rounded-lg border border-gray-700 bg-gray-800/60 p-4">
            <h2 className="mb-2 text-sm font-semibold text-white">
              {item.language} description
              {item.tl_by && (
                <span className="ml-2 text-xs font-normal text-gray-400">approved by {item.tl_by}</span>
              )}
            </h2>
            {item.status === 'pending_tl' ? (
              <>
                <p className="mb-2 text-xs text-gray-400">
                  Machine-translated from the approved English above — correct anything unnatural or
                  inaccurate, keeping the usual metadata phrasing.
                </p>
                <textarea
                  value={tlText}
                  onChange={(e) => {
                    setTlText(e.target.value);
                    scheduleSave({ tl_text: e.target.value });
                  }}
                  rows={8}
                  className="w-full rounded border border-gray-600 bg-gray-900 p-3 text-sm text-gray-100"
                />
              </>
            ) : (
              <p className="whitespace-pre-wrap text-sm text-gray-200">{item.tl_text || '—'}</p>
            )}
          </section>
        )}

        {/* ---- Actions ---- */}
        {isAdmin && item.status === 'pending_en' && (
          <button
            type="button"
            disabled={busy || !enText.trim()}
            onClick={() =>
              action(
                () => api.approveTripDescEn(tgId),
                item.en_target
                  ? 'Approved — written to staging'
                  : `Approved — translating into ${item.language}`,
              )
            }
            className="rounded bg-emerald-700 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-600 disabled:opacity-50"
          >
            {item.en_target ? 'Approve & write to staging' : 'Approve English → translate'}
          </button>
        )}
        {item.status === 'pending_tl' && (
          <button
            type="button"
            disabled={busy || !tlText.trim()}
            onClick={() =>
              action(() => api.approveTripDescTl(tgId), 'Approved — written to staging').then((r) => {
                // Only leave on success — a 409/422 must keep the translator on the page.
                if (r && !isAdmin) navigate('/descriptions');
              })
            }
            className="rounded bg-emerald-700 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-600 disabled:opacity-50"
          >
            Approve translation & write to staging
          </button>
        )}

        {/* ---- Scene context (admin) ---- */}
        {isAdmin && (item.scenes?.length ?? 0) > 0 && (
          <section>
            <h2 className="mb-3 text-sm font-semibold text-white">
              Scenes ({item.rep_trip_id})
            </h2>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {item.scenes!.map((s) => (
                <div key={s.index} className="overflow-hidden rounded-lg border border-gray-700 bg-gray-800/60">
                  {s.thumb_url ? (
                    <img src={s.thumb_url} alt="" className="aspect-video w-full object-cover" loading="lazy" />
                  ) : (
                    <div className="flex aspect-video w-full items-center justify-center bg-gray-900 text-xs text-gray-600">
                      no thumbnail
                    </div>
                  )}
                  <div className="p-3">
                    <p className="text-sm font-medium text-white">
                      {s.index + 1}. {s.title || '—'}
                    </p>
                    <p className="mt-1 whitespace-pre-wrap text-xs text-gray-400">{s.description}</p>
                  </div>
                </div>
              ))}
            </div>
          </section>
        )}
      </main>
    </div>
  );
};

export default TripDescPage;
