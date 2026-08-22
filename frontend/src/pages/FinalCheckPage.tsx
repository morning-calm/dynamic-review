import { useCallback, useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { toast } from 'react-toastify';
import {
  api,
  ApiError,
  type FinalCategoryCheck,
  type FinalCheck,
  type FinalCheckDetail,
  type FinalCheckKey,
} from '../api';
import KeywordCheckPanel from '../components/KeywordCheckPanel';
import LocationEditor from '../components/LocationEditor';
import NavBar from '../components/NavBar';
import StaticImagesPanel from '../components/StaticImagesPanel';

const errText = (e: unknown, fallback: string): string =>
  e instanceof ApiError ? e.detail || e.code : fallback;

const SCOPE_LABEL = {
  trip: 'this trip',
  group: 'whole family',
  location: 'whole location',
} as const;

/** One check's header row: state pill, tick/reopen with an optional note. */
const CheckHeader = ({
  check,
  busy,
  onSet,
}: {
  check: FinalCheck;
  busy: boolean;
  onSet: (state: 'open' | 'done', note: string) => void;
}) => {
  const [note, setNote] = useState(check.note);
  useEffect(() => setNote(check.note), [check.note]);
  const done = check.state === 'done';
  return (
    <div className="mb-2 flex flex-wrap items-center gap-2">
      <h2 className="text-sm font-semibold text-white">{check.label}</h2>
      <span
        className="rounded bg-gray-700 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-gray-300"
        title={`Ticked once for the ${SCOPE_LABEL[check.scope]} (${check.scope_id})`}
      >
        {SCOPE_LABEL[check.scope]}
      </span>
      {done && (
        <span className="text-xs text-emerald-400">
          ✓ {check.by}
          {check.at ? ` · ${new Date(check.at * 1000).toLocaleDateString()}` : ''}
        </span>
      )}
      <span className="ml-auto flex items-center gap-2">
        <input
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="note…"
          className="w-40 rounded border border-gray-600 bg-gray-900 px-2 py-1 text-xs text-gray-100"
        />
        <button
          type="button"
          disabled={busy}
          onClick={() => onSet(done ? 'open' : 'done', note)}
          className={
            done
              ? 'rounded border border-gray-600 px-2 py-1 text-xs text-gray-200 hover:bg-gray-700 disabled:opacity-50'
              : 'rounded bg-emerald-700 px-3 py-1 text-xs font-medium text-white hover:bg-emerald-600 disabled:opacity-50'
          }
        >
          {done ? 'Reopen' : 'Mark done'}
        </button>
      </span>
    </div>
  );
};

/** Check-7 body: current TripGroup thumbnail from R2 + replace-by-upload (R2 +
 * staging field; a thumbnail_local_copy bus job carries it into the workstation's
 * local tree at publish). */
const ThumbnailPanel = ({ tripId }: { tripId: string }) => {
  const [info, setInfo] = useState<Awaited<ReturnType<typeof api.getFinalThumbnail>> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [cacheBust, setCacheBust] = useState(0);

  const load = useCallback(() => {
    api
      .getFinalThumbnail(tripId)
      .then(setInfo)
      .catch((e: unknown) => setError(errText(e, 'Failed to load thumbnail info')));
  }, [tripId]);
  useEffect(load, [load]);

  const upload = (file: File | undefined) => {
    if (!file) return;
    setUploading(true);
    api
      .uploadFinalThumbnail(tripId, file)
      .then((r) => {
        toast.success(
          `Thumbnail replaced (R2 + staging${r.local_copy_job ? '; local-tree copy queued for the Publisher' : ''}).`,
        );
        setCacheBust(Date.now());
        load();
      })
      .catch((e: unknown) => toast.error(errText(e, 'Upload failed')))
      .finally(() => setUploading(false));
  };

  if (error) return <p className="text-xs text-rose-400">{error}</p>;
  if (!info) return <p className="text-xs text-gray-500">Loading…</p>;
  return (
    <div className="flex flex-wrap items-start gap-4">
      <div>
        {info.url ? (
          <img
            src={cacheBust ? `${info.url}?t=${cacheBust}` : info.url}
            alt={`${info.tg_id} thumbnail`}
            className="max-h-48 rounded border border-gray-700"
          />
        ) : (
          <p className="text-xs text-amber-300">No thumbnailTextureId set on the TripGroup.</p>
        )}
      </div>
      <div className="space-y-2 text-xs text-gray-400">
        <p>
          thumbnailTextureId:{' '}
          <span className="text-gray-200">{info.thumbnailTextureId || '—'}</span>
          {info.on_r2 === false && (
            <span className="ml-2 rounded bg-rose-900/60 px-1.5 py-0.5 text-[10px] uppercase text-rose-200">
              object missing on R2
            </span>
          )}
        </p>
        <label className="inline-block cursor-pointer rounded border border-gray-600 px-3 py-1.5 text-sm text-gray-200 hover:bg-gray-700">
          {uploading ? 'Uploading…' : info.url ? 'Replace thumbnail (JPEG)…' : 'Upload thumbnail (JPEG)…'}
          <input
            type="file"
            accept="image/jpeg"
            className="hidden"
            disabled={uploading}
            onChange={(e) => {
              upload(e.target.files?.[0]);
              e.target.value = '';
            }}
          />
        </label>
        <p>
          Writes R2 <code>dynamic-languages-thumbs/&lt;stem&gt;.jpg</code> + staging{' '}
          <code>thumbnailTextureId</code>; the workstation Publisher copies it into the local
          “App thumbnails” tree via the queued job.
        </p>
      </div>
    </div>
  );
};

/** Per-trip Final-check page: the 7 pre-publish checks, stored at the level each is
 * true at (family/location checks green every sibling). Every check has its in-app
 * tooling inline (spec phases 1–5): description, categories, title key, the
 * TripLocation/pin editor, static-image timing + credits, the keyword mic check,
 * and the thumbnail panel. */
const FinalCheckPage = () => {
  const { tripId = '' } = useParams();
  const navigate = useNavigate();

  const [item, setItem] = useState<FinalCheckDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [titleKey, setTitleKey] = useState('');
  const [categories, setCategories] = useState<string[]>([]);
  const [newCat, setNewCat] = useState('');
  const [usedCats, setUsedCats] = useState<{ name: string; count: number }[]>([]);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [check, setCheck] = useState<FinalCategoryCheck | null>(null);

  const load = useCallback(() => {
    api
      .getFinalCheck(tripId)
      .then((r) => {
        setItem(r);
        setTitleKey(r.title_key.staging);
        setCategories(r.categories);
      })
      .catch((e: unknown) => setError(errText(e, 'Failed to load')));
  }, [tripId]);

  useEffect(load, [load]);

  // Category vocabulary (country-scoped) + enrichment proposals — best-effort.
  const tgId = item?.tg_id;
  useEffect(() => {
    if (!tgId) return;
    api
      .tripDescCategories(tgId)
      .then((r) => setUsedCats(r.categories))
      .catch(() => {});
    api
      .enrichmentCategories(tripId)
      .then((r) => setSuggestions([...r.applicable, ...r.suggestions]))
      .catch(() => {});
  }, [tgId, tripId]);

  const setCheckState = (key: FinalCheckKey, state: 'open' | 'done', note: string) => {
    setBusy(true);
    api
      .setFinalCheck(tripId, key, state, note)
      .then(() => load())
      .catch((e: unknown) => toast.error(errText(e, 'Failed to save check')))
      .finally(() => setBusy(false));
  };

  /** Write the category list to staging immediately (targeted TripGroup update). */
  const saveCats = (next: string[]) => {
    const prev = categories;
    setCategories(next);
    api.saveFinalCategories(tripId, next).catch((e: unknown) => {
      setCategories(prev);
      toast.error(errText(e, 'Category save failed'));
    });
    if (check && !next.some((c) => c.toLowerCase() === check.category.toLowerCase()))
      setCheck(null);
  };

  const addCat = (raw: string) => {
    const v = raw.trim();
    if (!v || categories.some((c) => c.toLowerCase() === v.toLowerCase())) return;
    saveCats([...categories, v]);
    api
      .finalCategoryCheck(tripId, v)
      .then(setCheck)
      .catch(() => setCheck(null));
  };

  const saveTitle = () => {
    setBusy(true);
    api
      .saveFinalTitleKey(tripId, titleKey)
      .then(() => toast.success('Title key written to staging'))
      .catch((e: unknown) => toast.error(errText(e, 'Save failed')))
      .finally(() => setBusy(false));
  };

  const editDescription = () => {
    setBusy(true);
    api
      .reopenFinalDescription(tripId)
      .then((r) => navigate(`/descriptions/${encodeURIComponent(r.tg_id)}`))
      .catch((e: unknown) => toast.error(errText(e, 'Could not open the description item')))
      .finally(() => setBusy(false));
  };

  if (error) {
    return (
      <div className="min-h-screen">
        <NavBar title="Release prep" backTo="/final-check" backLabel="Release prep" />
        <main className="mx-auto max-w-review px-4 py-8">
          <p className="text-rose-400">{error}</p>
        </main>
      </div>
    );
  }
  if (!item) {
    return <p className="mx-auto max-w-review px-4 py-8 text-gray-400">Loading…</p>;
  }

  const byKey = Object.fromEntries(item.checks.map((c) => [c.key, c])) as Record<
    FinalCheckKey,
    FinalCheck
  >;
  const doneCount = item.checks.filter((c) => c.state === 'done').length;
  const allDone = doneCount === item.checks.length;

  const readyToPublish = () => {
    setBusy(true);
    api
      .readyFinalCheck(item.trip_id)
      .then((r) =>
        toast.success(
          `Release queued: publish job ${r.publish_job} — run it on the workstation Publisher. The family Trello card is stamped per rung and moves to Live only when every rung is published.`,
        ),
      )
      .catch((e: unknown) => toast.error(errText(e, 'Could not queue the release')))
      .finally(() => setBusy(false));
  };

  return (
    <div className="min-h-screen">
      <NavBar
        title={`${item.trip_id} — release prep`}
        subtitle={`${item.language}${item.tg_id ? ` · ${item.tg_id}` : ''} · ${doneCount}/${item.checks.length} checks done`}
        backTo="/final-check"
        backLabel="Release prep"
        right={
          allDone && !item.pending_delta ? (
            <button
              type="button"
              disabled={busy}
              onClick={readyToPublish}
              className="rounded bg-emerald-700 px-3 py-1.5 text-xs font-semibold text-white hover:bg-emerald-600 disabled:opacity-50"
              title="All checks green — queue the publish_docs bus job for the workstation Publisher (the family Trello card is stamped per published rung; it moves to Live when the whole family is out)"
            >
              Ready to publish
            </button>
          ) : undefined
        }
      />
      <main className="mx-auto max-w-review space-y-6 px-4 py-6">
        {item.pending_delta && (
          <div className="rounded-lg border border-sky-800 bg-sky-900/20 p-3 text-sm text-sky-100">
            <span className="font-semibold">Back in review:</span> this trip has changed clips
            awaiting a delta re-review — finish that (reviewer approves, which consumes the
            delta) before completing release prep or publishing.
          </div>
        )}
        {!item.tg_exists && (
          <div className="rounded-lg border border-rose-800 bg-rose-900/20 p-3 text-sm text-rose-100">
            No staging TripGroup found for this trip — the family-level checks (description,
            categories, title key, thumbnail) can’t read or write until it exists.
          </div>
        )}

        {/* 1 — Description re-read */}
        <section className="rounded-lg border border-gray-700 bg-gray-800/60 p-4">
          <CheckHeader
            check={byKey.desc_reread}
            busy={busy}
            onSet={(s, n) => setCheckState('desc_reread', s, n)}
          />
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <p className="mb-1 text-[11px] uppercase tracking-wide text-gray-500">English</p>
              <p className="whitespace-pre-wrap text-sm text-gray-200">
                {item.description.home || '—'}
              </p>
            </div>
            <div>
              <p className="mb-1 text-[11px] uppercase tracking-wide text-gray-500">Target</p>
              <p className="whitespace-pre-wrap text-sm text-gray-200">
                {item.description.target || '—'}
              </p>
            </div>
          </div>
          <div className="mt-3 flex items-center gap-2">
            <button
              type="button"
              disabled={busy || !item.tg_exists}
              onClick={editDescription}
              className="rounded border border-gray-600 px-2 py-1 text-xs text-gray-200 hover:bg-gray-700 disabled:opacity-50"
            >
              Edit description…
            </button>
            {item.description.tripdesc_status && (
              <span className="text-xs text-gray-500">
                description review: {item.description.tripdesc_status}
              </span>
            )}
          </div>
        </section>

        {/* 2 — Categories */}
        <section className="rounded-lg border border-gray-700 bg-gray-800/60 p-4">
          <CheckHeader
            check={byKey.categories}
            busy={busy}
            onSet={(s, n) => setCheckState('categories', s, n)}
          />
          <div className="mb-2 flex flex-wrap gap-2">
            {categories.map((c) => (
              <span
                key={c}
                className="flex items-center gap-1 rounded bg-gray-700 px-2 py-0.5 text-xs text-gray-100"
              >
                {c}
                <button
                  type="button"
                  aria-label={`Remove ${c}`}
                  onClick={() => saveCats(categories.filter((x) => x !== c))}
                  className="text-gray-400 hover:text-rose-400"
                >
                  ×
                </button>
              </span>
            ))}
            {categories.length === 0 && <span className="text-xs text-gray-500">none</span>}
          </div>
          {(() => {
            const appliedLower = new Set(categories.map((c) => c.toLowerCase()));
            const usedLower = new Set(usedCats.map((c) => c.name.toLowerCase()));
            const usedAvailable = usedCats.filter((c) => !appliedLower.has(c.name.toLowerCase()));
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
                        {check.is_new && (
                          <span className="ml-1 rounded bg-sky-800 px-1 text-[10px] uppercase">
                            new category
                          </span>
                        )}
                        {' — '}
                        {fits.length > 0
                          ? `${fits.length} other trip${fits.length === 1 ? '' : 's'} in ${
                              check.locations.map((l) => l.name).join(', ') || 'this playlist'
                            } may also fit it (description mention):`
                          : 'no sibling description mentions it.'}
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
                            {s.snippet && (
                              <span className="text-amber-100/70"> — “{s.snippet}”</span>
                            )}
                          </li>
                        ))}
                      </ul>
                    )}
                    {check.enrichment_matches.length > 0 && (
                      <div className="mt-2 border-t border-amber-800/60 pt-2">
                        <p className="mb-1 font-semibold">
                          Enrichment signals from country-mates ({check.enrichment_matches.length}):
                        </p>
                        <ul className="space-y-1">
                          {check.enrichment_matches.map((m) => (
                            <li key={m.doc_id}>
                              <span className="font-medium text-amber-200">{m.doc_id}</span>
                              <span className="text-amber-100/70">
                                {' — '}
                                {m.hits.map((h) => `${h.field}: “${h.value}”`).join('; ')}
                              </span>
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {check.siblings.some((s) => s.has_category) && (
                      <p className="mt-1 text-amber-100/60">
                        Already tagged:{' '}
                        {check.siblings
                          .filter((s) => s.has_category)
                          .map((s) => s.tg_id)
                          .join(', ')}
                      </p>
                    )}
                  </div>
                )}
              </>
            );
          })()}
        </section>

        {/* 3 — TripGroup title key */}
        <section className="rounded-lg border border-gray-700 bg-gray-800/60 p-4">
          <CheckHeader
            check={byKey.title_key}
            busy={busy}
            onSet={(s, n) => setCheckState('title_key', s, n)}
          />
          <div className="flex flex-wrap items-center gap-2">
            <input
              value={titleKey}
              onChange={(e) => setTitleKey(e.target.value)}
              className="min-w-64 flex-1 rounded border border-gray-600 bg-gray-900 px-2 py-1.5 text-sm text-gray-100"
            />
            <button
              type="button"
              disabled={busy || !item.tg_exists || titleKey.trim() === item.title_key.staging}
              onClick={saveTitle}
              className="rounded bg-custom-green px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
            >
              Save to staging
            </button>
          </div>
          <p className="mt-2 text-xs text-gray-400">
            {item.title_key.prod_group != null ? (
              <>
                Production TripGroup:{' '}
                <span
                  className={
                    item.title_key.prod_group === titleKey.trim()
                      ? 'text-emerald-400'
                      : 'text-amber-300'
                  }
                >
                  “{item.title_key.prod_group}”
                </span>
                {item.title_key.snapshot_at && (
                  <span className="text-gray-500">
                    {' '}
                    (snapshot {new Date(item.title_key.snapshot_at * 1000).toLocaleDateString()})
                  </span>
                )}
              </>
            ) : (
              'No production snapshot for this family yet — drift unknown (first publish, or the workstation snapshot predates trip_group support).'
            )}
          </p>
        </section>

        {/* 4 — TripLocation + map pin */}
        <section className="rounded-lg border border-gray-700 bg-gray-800/60 p-4">
          <CheckHeader
            check={byKey.trip_location}
            busy={busy}
            onSet={(s, n) => setCheckState('trip_location', s, n)}
          />
          <p className="mb-3 text-xs text-gray-400">
            Targeted staging writes only; the pin reaches production at publish (the
            Publisher’s <code>publish_pin</code> job). Tile order = button order in the headset.
          </p>
          <LocationEditor tripId={item.trip_id} />
        </section>

        {/* 5 — Static image timing + credits */}
        <section className="rounded-lg border border-gray-700 bg-gray-800/60 p-4">
          <CheckHeader
            check={byKey.static_images}
            busy={busy}
            onSet={(s, n) => setCheckState('static_images', s, n)}
          />
          <StaticImagesPanel tripId={item.trip_id} />
        </section>

        {/* 6 — Keyword check */}
        <section className="rounded-lg border border-gray-700 bg-gray-800/60 p-4">
          <CheckHeader
            check={byKey.keywords}
            busy={busy}
            onSet={(s, n) => setCheckState('keywords', s, n)}
          />
          <KeywordCheckPanel tripId={item.trip_id} />
        </section>

        {/* 7 — Thumbnail */}
        <section className="rounded-lg border border-gray-700 bg-gray-800/60 p-4">
          <CheckHeader
            check={byKey.thumbnail}
            busy={busy}
            onSet={(s, n) => setCheckState('thumbnail', s, n)}
          />
          <ThumbnailPanel tripId={item.trip_id} />
        </section>
      </main>
    </div>
  );
};

export default FinalCheckPage;
