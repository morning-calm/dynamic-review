import { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { toast } from 'react-toastify';
import {
  api,
  ApiError,
  type BusJob,
  type BusJobKind,
  type PublishedTrip,
  type ReleaseBatch,
  type ReleaseBatchMember,
  type ReleaseBoard,
  type ReleaseGroup,
  type ReleaseGroupDiff,
  type ReleaseGroupJob,
  type ReleaseRungStatus,
} from '../api';
import NavBar from '../components/NavBar';

const errText = (e: unknown, fallback: string): string =>
  e instanceof ApiError ? e.detail || e.code : fallback;

/** Rung pipeline positions on the release board, in release order. */
const RUNG_STATUS: Record<ReleaseRungStatus, { label: string; cls: string; hint: string }> = {
  live: {
    label: 'LIVE',
    cls: 'bg-emerald-800 text-emerald-100',
    hint: 'In the production TripGroup — players can see it.',
  },
  re_review: {
    label: 'back in review',
    cls: 'bg-sky-800 text-sky-100',
    hint: 'Changed clips await a delta re-review — not publishable until the reviewer approves.',
  },
  ready: {
    label: 'READY TO PUBLISH',
    cls: 'bg-indigo-600 text-white',
    hint: 'All 7 final checks done — queue "publish docs" (or it is already in the inbox).',
  },
  final_check: {
    label: 'in release prep',
    cls: 'bg-amber-700/80 text-amber-100',
    hint: 'Release prep in progress — open the checklist to finish them.',
  },
  reviewed: {
    label: 'review done',
    cls: 'bg-sky-800/80 text-sky-100',
    hint: 'Review complete but no release prep started yet (Release prep page → audit section).',
  },
  in_review: {
    label: 'in review',
    cls: 'bg-gray-600 text-gray-100',
    hint: 'On the review queue (Trello lane 6/7) — must be reviewed before release prep.',
  },
  not_started: {
    label: 'not started',
    cls: 'bg-gray-800 text-gray-400',
    hint: 'Not yet in review or release prep.',
  },
};

/** The release board: one card per TripGroup with per-rung pipeline status.
 * This is the "what can I publish, and what is each sibling waiting on" view. */
/** Lazy staging→prod field diff for one group card. */
const GroupDiff = ({ tgId }: { tgId: string }) => {
  const [diff, setDiff] = useState<ReleaseGroupDiff | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api
      .releaseGroupDiff(tgId)
      .then(setDiff)
      .catch((e: unknown) => setError(errText(e, 'Diff failed')));
  }, [tgId]);
  if (error) return <p className="mt-2 text-xs text-rose-400">{error}</p>;
  if (!diff) return <p className="mt-2 text-xs text-gray-500">Diffing vs prod snapshot…</p>;
  return (
    <div className="mt-2 rounded border border-gray-700 bg-black/30 p-2 text-xs">
      {diff.hint && <p className="text-amber-300">{diff.hint}</p>}
      {diff.snapshot_trip && (
        <p className="mb-1 text-gray-500">
          vs prod snapshot of <span className="text-gray-400">{diff.snapshot_trip}</span>
          {diff.snapshot_at
            ? ` (${new Date(diff.snapshot_at * 1000).toISOString().slice(0, 16).replace('T', ' ')})`
            : ''}{' '}
          — refresh via tool “4 · Refresh prod snapshot”.
        </p>
      )}
      {diff.prod_missing === false && diff.changed.length === 0 && (
        <p className="text-emerald-300">No field drift vs the snapshot.</p>
      )}
      {diff.changed.map((c) => (
        <div key={c.field} className="mb-1.5">
          <p className="font-semibold text-gray-300">{c.field}</p>
          <p className="text-rose-300/90">prod: {c.prod}</p>
          <p className="text-emerald-300/90">staging: {c.staging}</p>
        </div>
      ))}
    </div>
  );
};

type ReleaseSort = 'ready' | 'cid' | 'location';
/** How the queue nests the family cards. Location = the TripLocation tile the
 * family sits on; Batch = the saved release batch it belongs to. */
type GroupBy = 'location' | 'batch' | 'none';

const NO_LOCATION = '(no location)';
const NO_BATCH = '(no batch)';

/** Inline bus-job chips on a family card — the full log stays in the Job inbox. */
const JOB_CHIP: Record<ReleaseGroupJob['status'], { icon: string; cls: string }> = {
  queued: { icon: '⏳', cls: 'bg-blue-900/60 text-blue-200' },
  dry_run: { icon: 'dry', cls: 'bg-amber-900/60 text-amber-200' },
  done: { icon: '✓', cls: 'bg-emerald-900/60 text-emerald-200' },
  failed: { icon: '✗', cls: 'bg-red-900/60 text-red-200' },
};

const SOCIAL_CHIP: Record<ReleaseBatch['social']['state'], { label: string; cls: string }> = {
  ready: { label: 'social ✓', cls: 'bg-emerald-900/60 text-emerald-200' },
  partial: { label: 'social partial', cls: 'bg-amber-900/60 text-amber-200' },
  missing: { label: 'social missing', cls: 'bg-rose-900/60 text-rose-200' },
  unknown: { label: 'social ?', cls: 'bg-gray-700 text-gray-300' },
};

/** What the readiness chip actually looked for (spelled out in the tooltip —
 * 'unknown' just means this host has no Comms tree, not that anything is late). */
const socialTitle = (b: ReleaseBatch): string =>
  b.social.state === 'unknown'
    ? 'Not probed on this host — Comms\\Social Posts lives on the workstation.'
    : `${b.name}_meta.txt: ${b.social.meta ?? 'missing'} · ${b.name}_linkedin.txt: ${
        b.social.linkedin ?? 'missing'
      } · news.json mention: ${
        b.social.news === null ? 'unknown' : b.social.news ? 'yes' : 'no'
      }`;

const ReleasesPanel = () => {
  const navigate = useNavigate();
  const [board, setBoard] = useState<ReleaseBoard | null>(null);
  const [batches, setBatches] = useState<ReleaseBatch[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [sort, setSort] = useState<ReleaseSort>('ready');
  const [groupBy, setGroupBy] = useState<GroupBy>('location');
  const [country, setCountry] = useState('');
  const [openDiff, setOpenDiff] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [batchBusy, setBatchBusy] = useState(false);

  useEffect(() => {
    api
      .listReleases()
      .then(setBoard)
      .catch((e: unknown) => setError(errText(e, 'Failed to load the release board')));
  }, []);

  // Batches are best-effort furniture for the board: a failure must not blank it.
  const loadBatches = useCallback(() => {
    api
      .listReleaseBatches()
      .then((r) => setBatches(r.batches))
      .catch(() => setBatches([]));
  }, []);
  useEffect(loadBatches, [loadBatches]);

  const toggleTrips = (ids: string[], on: boolean) =>
    setSelected((prev) => {
      const next = new Set(prev);
      for (const id of ids) {
        if (on) next.add(id);
        else next.delete(id);
      }
      return next;
    });

  /** Selection → batch members: a family with EVERY rung ticked ships as a
   * `group` (so a rung added later joins automatically); a partial pick ships as
   * the individual `trip`s. */
  const selectedMembers = (): ReleaseBatchMember[] => {
    const out: ReleaseBatchMember[] = [];
    for (const g of board?.groups ?? []) {
      const ids = g.rungs.map((r) => r.trip_id);
      const picked = ids.filter((id) => selected.has(id));
      if (picked.length === 0) continue;
      if (picked.length === ids.length) out.push({ kind: 'group', id: g.tg_id });
      else out.push(...picked.map((id): ReleaseBatchMember => ({ kind: 'trip', id })));
    }
    return out;
  };

  /** Save (or update, by name) a batch from the current selection. The wizard
   * needs a saved batch to scope to, so "Open in wizard" saves first. */
  const saveBatch = (after?: (b: ReleaseBatch) => void) => {
    const name = window.prompt('Name this release batch (an existing name updates it):')?.trim();
    if (!name) return;
    setBatchBusy(true);
    api
      .saveReleaseBatch({ name, members: selectedMembers() })
      .then((b) => {
        toast.success(`Batch “${b.name}” saved — ${b.resolved.trip_ids.length} rungs.`);
        loadBatches();
        after?.(b);
      })
      .catch((e: unknown) => toast.error(errText(e, 'Could not save the batch')))
      .finally(() => setBatchBusy(false));
  };

  const importTrello = () => {
    setBatchBusy(true);
    api
      .importReleaseBatches()
      .then((r) => {
        toast.success(
          `Trello: ${r.imported.length} imported, ${r.updated.length} updated` +
            (r.unmatched.length ? `, ${r.unmatched.length} unmatched family token(s)` : '') + '.',
        );
        loadBatches();
      })
      .catch((e: unknown) => toast.error(errText(e, 'Trello import failed')))
      .finally(() => setBatchBusy(false));
  };

  if (error) return <p className="text-xs text-rose-400">{error}</p>;
  if (!board) return <p className="text-sm text-gray-500">Loading release board…</p>;

  const countries = [
    ...new Set(board.groups.flatMap((g) => g.locations.map((l) => l.country)).filter(Boolean)),
  ].sort();
  const locKey = (g: ReleaseBoard['groups'][number]) =>
    g.locations[0]?.name?.toLowerCase() ?? '￿';
  const shown = board.groups
    .filter((g) => !country || g.locations.some((l) => l.country === country))
    .sort((a, b) =>
      sort === 'cid'
        ? a.tg_id.toLowerCase().localeCompare(b.tg_id.toLowerCase())
        : sort === 'location'
          ? locKey(a).localeCompare(locKey(b)) || a.tg_id.toLowerCase().localeCompare(b.tg_id.toLowerCase())
          : b.ready_count - a.ready_count || a.tg_id.toLowerCase().localeCompare(b.tg_id.toLowerCase()),
    );

  /** A family belongs to the first batch whose resolved membership covers it —
   * by group id, or by any of its rungs (a partial pick). */
  const batchOf = (g: ReleaseGroup): ReleaseBatch | null =>
    batches.find(
      (b) =>
        b.resolved.group_ids.includes(g.tg_id) ||
        g.rungs.some((r) => b.resolved.trip_ids.includes(r.trip_id)),
    ) ?? null;

  // Nest the (already sorted) cards under their header; the "no …" bucket sorts
  // last so an unplaced family never hides the real work at the top.
  const buckets: { key: string; label: string; batch: ReleaseBatch | null; groups: ReleaseGroup[] }[] =
    [];
  const byKey = new Map<string, (typeof buckets)[number]>();
  for (const g of shown) {
    if (groupBy === 'none') {
      let b = byKey.get('');
      if (!b) byKey.set('', (b = { key: '', label: '', batch: null, groups: [] }));
      b.groups.push(g);
      continue;
    }
    const batch = groupBy === 'batch' ? batchOf(g) : null;
    const loc = g.locations[0];
    const key =
      groupBy === 'batch'
        ? batch
          ? String(batch.id)
          : NO_BATCH
        : loc
          ? loc.name
          : NO_LOCATION;
    const label =
      groupBy === 'batch'
        ? (batch?.name ?? NO_BATCH)
        : loc
          ? `${loc.name}${loc.country ? ` · ${loc.country}` : ''}`
          : NO_LOCATION;
    let b = byKey.get(key);
    if (!b) byKey.set(key, (b = { key, label, batch, groups: [] }));
    b.groups.push(g);
  }
  buckets.push(
    ...[...byKey.values()].sort((a, b) => {
      const placeholder = (x: string) => (x === NO_LOCATION || x === NO_BATCH ? 1 : 0);
      return (
        placeholder(a.key) - placeholder(b.key) ||
        a.label.toLowerCase().localeCompare(b.label.toLowerCase())
      );
    }),
  );

  return (
    <section className="rounded-lg border border-gray-700 bg-gray-800/60 p-4">
      <h2 className="mb-1 text-sm font-semibold text-white">Publishing Queue</h2>
      <p className="mb-3 text-xs text-gray-400">
        Every family with release activity. A <span className="font-semibold">partial</span>{' '}
        publish is fine: “publish docs” writes the production TripGroup with{' '}
        <span className="font-semibold">only the live + newly-released rungs</span> in{' '}
        <code>trips[]</code> (creating the group if it is new, updating it in place if it
        already exists) — siblings join on their own later publish.
        {board.prod_snapshot_at && (
          <> Live status from the prod snapshot of {board.prod_snapshot_at.slice(0, 16)}
          {' '}(refresh: re-run the Trello export).</>
        )}
      </p>
      {!board.prod_snapshot_has_rungs && (
        <p className="mb-3 rounded border border-amber-800 bg-amber-900/20 p-2 text-xs text-amber-100">
          prod_tripgroups.json predates the per-rung snapshot — re-run{' '}
          <code>Trello/export_review_trips.py</code> to see LIVE badges.
        </p>
      )}
      <div className="mb-3 flex flex-wrap items-center gap-2 text-xs">
        <label className="text-gray-400">
          Sort{' '}
          <select
            value={sort}
            onChange={(e) => setSort(e.target.value as ReleaseSort)}
            className="rounded border border-gray-600 bg-gray-900 px-1.5 py-1 text-gray-100"
          >
            <option value="ready">ready first</option>
            <option value="cid">content id</option>
            <option value="location">trip location</option>
          </select>
        </label>
        <label className="text-gray-400">
          Country{' '}
          <select
            value={country}
            onChange={(e) => setCountry(e.target.value)}
            className="rounded border border-gray-600 bg-gray-900 px-1.5 py-1 text-gray-100"
          >
            <option value="">all</option>
            {countries.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </label>
        <label className="text-gray-400">
          Group by{' '}
          <select
            value={groupBy}
            onChange={(e) => setGroupBy(e.target.value as GroupBy)}
            className="rounded border border-gray-600 bg-gray-900 px-1.5 py-1 text-gray-100"
          >
            <option value="location">Location</option>
            <option value="batch">Batch</option>
            <option value="none">None</option>
          </select>
        </label>
        <button
          type="button"
          disabled={batchBusy}
          onClick={importTrello}
          className="rounded border border-gray-600 px-2 py-1 text-gray-300 hover:bg-gray-700 disabled:opacity-50"
          title="Seed/update release batches from the Trello “TG Release Schedule” lane — the card is the plan, this copy is the contract (editable after)."
        >
          Import from Trello
        </button>
        <span className="text-gray-500">
          {shown.length}/{board.groups.length} families
        </span>
      </div>
      {shown.length === 0 && (
        <p className="text-sm text-gray-500">No families in flight{country ? ` in ${country}` : ''}.</p>
      )}
      <div className="space-y-4">
        {buckets.map((bucket) => {
          const bucketIds = bucket.groups.flatMap((g) => g.rungs.map((r) => r.trip_id));
          const bucketAll =
            bucketIds.length > 0 && bucketIds.every((id) => selected.has(id));
          return (
            <div key={bucket.key || 'all'}>
              {groupBy !== 'none' && (
                <div className="mb-1.5 flex flex-wrap items-center gap-2 border-b border-gray-700/70 pb-1 text-xs">
                  <input
                    type="checkbox"
                    checked={bucketAll}
                    onChange={() => toggleTrips(bucketIds, !bucketAll)}
                    title="Select every rung under this header"
                  />
                  <span className="font-semibold uppercase tracking-wide text-gray-300">
                    {groupBy === 'location' ? '📍 ' : ''}
                    {bucket.label}
                  </span>
                  {bucket.batch && (
                    <span
                      className={`rounded px-1.5 py-0.5 text-[10px] uppercase ${
                        SOCIAL_CHIP[bucket.batch.social.state].cls
                      }`}
                      title={socialTitle(bucket.batch)}
                    >
                      {SOCIAL_CHIP[bucket.batch.social.state].label}
                    </span>
                  )}
                  {bucket.batch && (
                    <Link
                      to={`/publisher/release-batch/${bucket.batch.id}`}
                      className="text-[11px] text-sky-400 hover:underline"
                      title="Guided release scoped to this batch"
                    >
                      wizard →
                    </Link>
                  )}
                  <span className="text-gray-500">
                    {bucket.groups.length} famil{bucket.groups.length === 1 ? 'y' : 'ies'}
                  </span>
                </div>
              )}
              <div className="space-y-3">
                {bucket.groups.map((g) => {
                  const rungIds = g.rungs.map((r) => r.trip_id);
                  const groupAll =
                    rungIds.length > 0 && rungIds.every((id) => selected.has(id));
                  return (
          <div key={g.tg_id} className="rounded border border-gray-700 bg-gray-900/40 p-3">
            <p className="mb-2 flex flex-wrap items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={groupAll}
                onChange={() => toggleTrips(rungIds, !groupAll)}
                title="Select every rung of this family"
              />
              <span className="font-semibold text-gray-100">{g.tg_id}</span>
              {g.locations.map((l) => (
                <Link
                  key={l.name}
                  to={`/publisher/release-location/${encodeURIComponent(l.name)}`}
                  className="rounded bg-gray-700/70 px-1.5 py-0.5 text-[10px] text-gray-300 hover:bg-gray-600 hover:text-white"
                  title={`Release the whole ${l.name} tile — every in-flight family on it, rung by rung`}
                >
                  📍 {l.name}
                </Link>
              ))}
              <span
                className={`rounded px-1.5 py-0.5 text-[10px] uppercase ${
                  g.in_prod ? 'bg-emerald-900/70 text-emerald-200' : 'bg-gray-700 text-gray-300'
                }`}
                title={
                  g.in_prod
                    ? 'The production TripGroup exists — publishing a rung UPDATES it (adds the rung + refreshes button/tooltip text).'
                    : 'No production TripGroup yet — the first rung publish CREATES it with just that rung.'
                }
              >
                {g.in_prod ? `in prod (${g.live_count} live)` : 'not in prod yet'}
              </span>
              {/* Recent bus jobs targeting this family — a glance at what is in
                  flight without scrolling to the inbox. */}
              {g.jobs.map((j) => (
                <span
                  key={j.id}
                  className={`rounded px-1.5 py-0.5 text-[10px] ${JOB_CHIP[j.status].cls}`}
                  title={`${j.kind} · ${j.trip_id}${j.note ? ` — ${j.note}` : ''}`}
                >
                  {JOB_CHIP[j.status].icon} {j.kind}
                </span>
              ))}
              <span className="ml-auto flex items-center gap-2">
                {g.rungs.some(
                  (r) => r.recall_quiz === 'missing' || r.keyword_copy === 'missing',
                ) && (
                  <button
                    type="button"
                    onClick={() => {
                      api
                        .runTool({ tool: 'stage10b', target: g.tg_id, apply: true })
                        .then(() => toast.success(`stage 10b is running in the background (writes the recall quizzes to staging, ~1–2 min). Its full log lands in the Publisher's Job inbox — Refresh there to see the result.`))
                        .catch((e: unknown) => toast.error(errText(e, 'stage 10b failed to start')));
                    }}
                    className="rounded border border-amber-700 px-2 py-0.5 text-[11px] text-amber-300 hover:bg-amber-900/30"
                    title="Run stage 10b for this family (recall quizzes + keyword copy + verify) — fixes the amber chips"
                  >
                    Run stage 10b
                  </button>
                )}
                {g.ready_count > 0 && (
                  <Link
                    to={`/publisher/release-family/${encodeURIComponent(g.tg_id)}`}
                    className="rounded bg-indigo-600 px-2 py-0.5 text-[11px] font-semibold text-white hover:bg-indigo-500"
                    title="Guided family release — tick the rungs to ship, VR-check gate, then the publish stages"
                  >
                    Publish family…
                  </Link>
                )}
                <button
                  type="button"
                  onClick={() => setOpenDiff(openDiff === g.tg_id ? null : g.tg_id)}
                  className="rounded border border-gray-600 px-2 py-0.5 text-[11px] text-gray-300 hover:bg-gray-700"
                  title="Field-level staging → production diff (from the per-trip prod snapshot)"
                >
                  {openDiff === g.tg_id ? 'hide diff' : 'diff vs prod'}
                </button>
              </span>
            </p>
            <ul className="space-y-1">
              {g.rungs.map((r) => {
                const s = RUNG_STATUS[r.status];
                return (
                  <li key={r.trip_id} className="flex flex-wrap items-center gap-2 text-xs">
                    <input
                      type="checkbox"
                      checked={selected.has(r.trip_id)}
                      onChange={() => toggleTrips([r.trip_id], !selected.has(r.trip_id))}
                    />
                    <span
                      className={`w-36 rounded px-1.5 py-0.5 text-center text-[10px] font-semibold uppercase ${s.cls}`}
                      title={
                        r.status === 'live' && r.pending_delta
                          ? `${s.hint} Changed clips await a delta re-review — the live audio is the OLD version until it is approved and republished.`
                          : s.hint
                      }
                    >
                      {s.label}
                      {r.status === 'live' && r.pending_delta && ' ⟳'}
                    </span>
                    <span className="text-gray-200">{r.trip_id}</span>
                    {r.status === 'ready' && (
                      <Link
                        to={`/publisher/release/${encodeURIComponent(r.trip_id)}`}
                        className="rounded bg-indigo-600 px-2 py-0.5 text-[11px] font-semibold text-white hover:bg-indigo-500"
                        title="Guided release: publish docs → tile → pin → cache-bust → log → snapshot, confirming each step"
                      >
                        Publish…
                      </Link>
                    )}
                    {(r.status === 'final_check' || r.status === 'ready') && (
                      <Link
                        to={`/final-check/${encodeURIComponent(r.trip_id)}`}
                        className="text-sky-400 hover:underline"
                      >
                        checklist {r.checks_done}/{r.checks_total} →
                      </Link>
                    )}
                    {r.status === 'reviewed' && (
                      <Link to="/final-check" className="text-sky-400 hover:underline">
                        start release prep →
                      </Link>
                    )}
                    {r.status === 'in_review' && (
                      <span className="text-gray-500">lane {r.review_lane} — review queue</span>
                    )}
                    {r.finalised === 'restale' && (
                      <span
                        className="rounded bg-amber-900/70 px-1.5 py-0.5 text-[10px] uppercase text-amber-200"
                        title="Re-approved since the last stage-9 finalise — re-finalise (subs/ogg/S3) before releasing"
                      >
                        re-finalise pending
                      </span>
                    )}
                    {r.recall_quiz === 'missing' && (
                      <span
                        className="rounded bg-amber-900/70 px-1.5 py-0.5 text-[10px] uppercase text-amber-200"
                        title="Quiz-eligible (leveled rung with keyword scenes) but no recallQuiz on staging — run stage 10b"
                      >
                        recall quiz missing
                      </span>
                    )}
                    {r.four_k === 'missing' && (
                      <span
                        className="rounded bg-amber-900/70 px-1.5 py-0.5 text-[10px] uppercase text-amber-200"
                        title="Has static-image scenes but no 4K-webapp record — run static_pic_4k build (or `gap` to verify existing derivatives onto the ledger)"
                      >
                        4K stills missing
                      </span>
                    )}
                    {r.keyword_copy === 'missing' && (
                      <span
                        className="rounded bg-amber-900/70 px-1.5 py-0.5 text-[10px] uppercase text-amber-200"
                        title="The leveled rung's keywords haven't been copied to this EN rung — stage 10b step 2 (CopyKeywordsfromBegtoEn + keyword OGGs)"
                      >
                        EN keywords not copied
                      </span>
                    )}
                    {r.card_url && (
                      <a
                        href={r.card_url}
                        target="_blank"
                        rel="noreferrer"
                        className="text-gray-500 hover:text-gray-300"
                        title="Trello card"
                      >
                        🗂
                      </a>
                    )}
                  </li>
                );
              })}
            </ul>
            {openDiff === g.tg_id && <GroupDiff tgId={g.tg_id} />}
          </div>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>
      {selected.size > 0 && (
        <div className="fixed bottom-4 left-1/2 z-40 flex -translate-x-1/2 flex-wrap items-center gap-3 rounded-lg border border-gray-600 bg-gray-900/95 px-4 py-2 text-xs shadow-lg">
          <span className="text-gray-300">
            {selected.size} rung{selected.size === 1 ? '' : 's'} selected
          </span>
          <button
            type="button"
            disabled={batchBusy}
            onClick={() => saveBatch()}
            className="rounded border border-gray-600 px-2 py-1 text-gray-200 hover:bg-gray-700 disabled:opacity-50"
            title="Save the selection as a named release batch — the unit the Batch view, the wizard and the launch posts all work from"
          >
            Save as release batch…
          </button>
          <button
            type="button"
            disabled={batchBusy}
            onClick={() => saveBatch((b) => navigate(`/publisher/release-batch/${b.id}`))}
            className="rounded bg-indigo-600 px-2 py-1 font-semibold text-white hover:bg-indigo-500 disabled:opacity-50"
            title="Saves the selection as a batch (the wizard scopes to a saved batch), then opens the guided release"
          >
            Open in wizard →
          </button>
          <button
            type="button"
            onClick={() => setSelected(new Set())}
            className="text-gray-400 underline hover:text-gray-200"
          >
            clear
          </button>
        </div>
      )}
    </section>
  );
};

/** What actually went to production, from the durable `published_trips` ledger
 * (collapsed by default — it is a record, not a work list). */
const RecentlyPublished = () => {
  const [open, setOpen] = useState(false);
  const [trips, setTrips] = useState<PublishedTrip[] | null>(null);

  useEffect(() => {
    if (!open || trips) return;
    api
      .recentlyPublished()
      .then((r) => setTrips(r.trips))
      .catch((e: unknown) => toast.error(errText(e, 'Failed to load the published list')));
  }, [open, trips]);

  return (
    <section className="rounded-lg border border-gray-700 bg-gray-800/60 p-4">
      <h2 className="text-sm font-semibold text-white">
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          className="flex items-center gap-2 text-white hover:text-gray-300"
        >
          <span aria-hidden="true" className="text-gray-500">
            {open ? '▾' : '▸'}
          </span>
          Recently published
          {trips && <span className="font-normal text-gray-400">({trips.length})</span>}
        </button>
      </h2>
      {open && (
        <>
          {!trips && <p className="mt-2 text-sm text-gray-500">Loading…</p>}
          {trips && trips.length === 0 && (
            <p className="mt-2 text-sm text-gray-500">(nothing published through the app yet)</p>
          )}
          {trips && trips.length > 0 && (
            <ul className="mt-2 divide-y divide-gray-700/60">
              {trips.map((t) => (
                <li
                  key={`${t.trip_id}:${t.published_at}`}
                  className="flex flex-wrap items-center gap-x-3 gap-y-1 py-1.5 text-xs"
                >
                  <span className="text-gray-200">{t.title}</span>
                  <span className="text-gray-500">{t.trip_id}</span>
                  {t.source === 'trello_backfill' && (
                    <span
                      className="rounded bg-gray-700 px-1.5 py-0.5 text-[10px] uppercase text-gray-300"
                      title="Reconstructed from the Trello card’s published= stamp, not published through this console"
                    >
                      backfill
                    </span>
                  )}
                  <span className="ml-auto text-gray-500">
                    {new Date(t.published_at * 1000).toLocaleDateString()}
                    {t.published_by ? ` · ${t.published_by}` : ''}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
};

const STATUS_CLS: Record<BusJob['status'], string> = {
  queued: 'bg-blue-600',
  dry_run: 'bg-amber-600',
  done: 'bg-emerald-700',
  failed: 'bg-red-700',
};

const KIND_LABEL: Record<BusJobKind, string> = {
  publish: 'publish text (Trip fields)',
  publish_docs: 'publish docs (Trip → group/rungs cascade)',
  publish_pin: 'publish map pin (TripLocation id)',
  add_to_location: 'add group to TripLocation trips[]',
  thumbnail_local_copy: 'copy R2 thumbnail into the local tree',
  replace_overlay: 'overlay replace → canonical distribution (stage10)',
  publish_credits: 'publish the Credits doc',
  trello_move: 'move the Trello card',
  tool: 'workstation tool run',
};

/** The tool rack (spec §4.4/§4.5): post-publish sequence + local-copy/S3
 * wrappers. Every button shells a whitelisted Scripts tool; results land as
 * kind-"tool" jobs in the inbox (long runs finish in the background — Refresh). */
const ToolsPanel = ({ onRan }: { onRan: () => void }) => {
  const [target, setTarget] = useState('');
  const [steps, setSteps] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [orderOpen, setOrderOpen] = useState(false);

  const run = (tool: string, apply: boolean, extra: { lane?: string; steps?: string } = {}) => {
    setBusy(tool);
    api
      .runTool({ tool, target: target.trim(), apply, ...extra })
      .then((j) => {
        toast.success(`${tool} ${apply ? 'started' : 'dry-run'} — job ${j.id} (watch the inbox).`);
        onRan();
      })
      .catch((e: unknown) => toast.error(errText(e, `${tool} failed to start`)))
      .finally(() => setBusy(null));
  };

  const btn = 'rounded border border-gray-600 px-2 py-1 text-[11px] text-gray-200 hover:bg-gray-700 disabled:opacity-50';
  const applyBtn = 'rounded bg-rose-800/80 px-2 py-1 text-[11px] font-medium text-white hover:bg-rose-700 disabled:opacity-50';
  const needsTarget = !target.trim();

  const row = (
    label: string,
    tip: string,
    tool: string,
    opts: { dry?: boolean; applyLabel?: string; lane?: string; steps?: boolean; noTarget?: boolean } = {},
  ) => (
    <div className="flex flex-wrap items-center gap-2 py-1" title={tip}>
      <span className="w-56 text-xs text-gray-300">{label}</span>
      {opts.dry !== false && (
        <button
          type="button"
          disabled={busy !== null || (!opts.noTarget && needsTarget)}
          onClick={() => run(tool, false, { lane: opts.lane, steps: opts.steps ? steps : undefined })}
          className={btn}
        >
          Dry run
        </button>
      )}
      <button
        type="button"
        disabled={busy !== null || (!opts.noTarget && needsTarget) || (opts.steps === true && !steps.trim())}
        onClick={() => run(tool, true, { lane: opts.lane, steps: opts.steps ? steps : undefined })}
        className={applyBtn}
      >
        {opts.applyLabel ?? 'Apply'}
      </button>
    </div>
  );

  return (
    <section className="rounded-lg border border-gray-700 bg-gray-800/60 p-4">
      <h2 className="mb-1 flex flex-wrap items-center gap-2 text-sm font-semibold text-white">
        Post-publish & tools
        <span className="relative">
          <button
            type="button"
            onClick={() => setOrderOpen((o) => !o)}
            aria-haspopup="dialog"
            aria-expanded={orderOpen}
            className="rounded border border-gray-600 px-1.5 py-0.5 text-[11px] font-normal text-gray-300 hover:bg-gray-700"
            title="The full release, in order"
          >
            ℹ Release order
          </button>
          {orderOpen && (
            <>
              {/* click-away backdrop (same pattern as the nav menus) */}
              <div className="fixed inset-0 z-30" onClick={() => setOrderOpen(false)} />
              <div className="absolute left-0 z-40 mt-1 w-96 rounded border border-gray-700 bg-gray-900 p-3 text-xs font-normal text-gray-300 shadow-lg">
                <ol className="list-decimal space-y-1 pl-4">
                  <li>
                    Release prep “Ready to publish” queues <code>publish_docs</code>.
                  </li>
                  <li>Dry-run it in the Job inbox, then Apply.</li>
                  <li>
                    The apply stamps <code>published=</code> on the rung’s row in the family
                    Trello card, and moves the card to 12 · Live only when EVERY rung is stamped.
                  </li>
                  <li>Post-publish sequence: bump · Trello → Live · DocIDs · snapshot.</li>
                  <li>Local-copy / S3 wrappers as needed.</li>
                </ol>
                <p className="mt-2 text-gray-400">
                  Every apply still rides the scripts’ own gates. The guided path through all of
                  it is the{' '}
                  <span className="text-gray-200">Release Wizard</span> — “Publish family…” /
                  “Publish…” on the Publishing Queue, or “Open in wizard →” from a selection.
                </p>
              </div>
            </>
          )}
        </span>
      </h2>
      <p className="mb-2 text-xs text-gray-400">
        <span className="font-semibold text-gray-300">You only need this AFTER a publish has
        been applied</span>: the numbered buttons are the follow-up chores for a trip that just
        went live (cache-bust the app, move its Trello card, log it, refresh the prod
        snapshot), and the rest are occasional media/S3 utilities. Nothing here publishes
        content — that happens in the Job inbox below. Type the trip/family into the target
        box first; every button dry-runs by default and results land in the inbox as{' '}
        <code>tool</code> jobs (long runs finish in the background — Refresh).
      </p>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <input
          value={target}
          onChange={(e) => setTarget(e.target.value)}
          placeholder="cid / family (e.g. Melrose_A12_EN or Melrose)"
          className="w-72 rounded border border-gray-600 bg-gray-900 px-2 py-1.5 text-sm text-gray-100"
        />
        <input
          value={steps}
          onChange={(e) => setSteps(e.target.value)}
          placeholder="stage9 --steps (e.g. subs,ogg,s3)"
          className="w-52 rounded border border-gray-600 bg-gray-900 px-2 py-1.5 text-xs text-gray-100"
        />
      </div>
      <p className="text-[11px] uppercase tracking-wide text-gray-500">Post-publish, in order</p>
      {row(
        '1 · Bump version',
        'Runs BumpContentVersion.py --prod — forces app clients to re-fetch content. Writes immediately; Dry run only prints the command.',
        'bump_version',
        { noTarget: true },
      )}
      {row(
        '2 · Trello → Live',
        'Moves the trip’s family card to lane 12 (Live on App) via trello_move.py --strict.',
        'trello_move',
        { lane: '12', applyLabel: 'Move' },
      )}
      {row(
        '3 · Log DocID',
        'Appends a dated “published” line to Content_DocIDs.md in the Scripts repo.',
        'docids_append',
      )}
      {row(
        '4 · Refresh snapshot',
        'Runs publish_inbox.py snapshot so drift checks compare against current prod.',
        'snapshot',
        { dry: false, applyLabel: 'Run' },
      )}
      <p className="mt-2 text-[11px] uppercase tracking-wide text-gray-500">Local copies & S3</p>
      {row(
        'Trip docs',
        'Builds local reference docs (tripdocs_local.py); Apply also uploads.',
        'tripdocs',
      )}
      {row(
        '4K stills → S3',
        'Builds and (Apply) uploads static 4K panoramas to S3.',
        'static_pic_4k',
      )}
      {row('Thumbnails → R2', 'Uploads scene thumbnails to R2.', 'upload_thumbs')}
      {row(
        'Stage 10b',
        'Recall quizzes + keyword copy + verify for a family. Never moves Trello cards.',
        'stage10b',
      )}
      {row(
        'Stage 9 re-run',
        'Re-runs stage9_finalise for the target; requires the steps box.',
        'stage9_finalise',
        { steps: true },
      )}
      <p className="mt-2 text-[11px] uppercase tracking-wide text-gray-500">Standing checks</p>
      {/* Released-trips button/tooltip text vs staging. Drift is EXPECTED until the
          next VR app version ships (dave, 2026-08-22): sync GRADUALLY, per family as
          its lower-level rungs release — do not run the blank-target all-sweep yet. */}
      {row(
        'Group text sync',
        'Syncs TripGroup button/tooltip text to prod. Blank target = ALL groups — hold off until the VR app update ships.',
        'group_text_drift',
        { applyLabel: 'Sync' },
      )}
    </section>
  );
};

/** The workstation Publish console (docs/post-approval-admin-spec.md §4) — only
 * useful on the instance running with REVIEW_APP_PUBLISHER=1 (the one machine with
 * the production key). Sequences the Scripts-repo publish tools over the R2 job
 * bus: dry-run first, apply behind a second confirmation. */
const PublisherPage = () => {
  const [mode, setMode] = useState<boolean | null>(null);
  const [jobs, setJobs] = useState<BusJob[]>([]);
  const [runBusy, setRunBusy] = useState<string | null>(null);
  const [confirmApply, setConfirmApply] = useState<string | null>(null);
  const [openLog, setOpenLog] = useState<string | null>(null);

  const [qKind, setQKind] = useState<BusJobKind>('publish_docs');
  const [qTarget, setQTarget] = useState('');
  const [qNote, setQNote] = useState('');
  const [qBusy, setQBusy] = useState(false);

  const [gateBusy, setGateBusy] = useState(false);
  const [gateLog, setGateLog] = useState<string | null>(null);

  const load = useCallback(() => {
    api
      .pipelineJobs()
      .then((r) => {
        setJobs(r.jobs);
        setMode(r.publisher_mode);
      })
      .catch((e: unknown) => {
        toast.error(errText(e, 'Failed to load jobs'));
        setMode(false);
      });
  }, []);

  useEffect(load, [load]);

  const run = (jobId: string, apply: boolean) => {
    setRunBusy(jobId);
    setConfirmApply(null);
    api
      .runPipelineJob(jobId, apply, apply)
      .then((j) => {
        toast[j.status === 'failed' ? 'error' : 'success'](
          `${apply ? 'Apply' : 'Dry run'} finished: ${j.status} — see the log.`,
        );
        setOpenLog(jobId);
        load();
      })
      .catch((e: unknown) => toast.error(errText(e, 'Run failed')))
      .finally(() => setRunBusy(null));
  };

  const queue = () => {
    const target = qTarget.trim();
    if (!target) return;
    setQBusy(true);
    api
      .queueBusJob(qKind, target, qNote.trim())
      .then(() => {
        toast.success('Job queued.');
        setQTarget('');
        setQNote('');
        load();
      })
      .catch((e: unknown) => toast.error(errText(e, 'Queue failed')))
      .finally(() => setQBusy(false));
  };

  const gate = () => {
    setGateBusy(true);
    setGateLog(null);
    api
      .gateReport()
      .then((r) => setGateLog(r.log))
      .catch((e: unknown) => toast.error(errText(e, 'Gate report failed')))
      .finally(() => setGateBusy(false));
  };

  return (
    <div className="min-h-screen">
      <NavBar title="Publisher" subtitle="Staging → production (workstation console)" />
      <main className="mx-auto max-w-review space-y-6 px-4 py-6">
        {mode === null && <p className="text-gray-400">Loading…</p>}
        {mode === false && (
          <div className="rounded-lg border border-amber-800 bg-amber-900/20 p-4 text-sm text-amber-100">
            This instance is <span className="font-semibold">not the publisher</span> — it can
            queue and view jobs (from the trip pages) but never execute them. The console runs
            on the workstation via <code>scripts\publisher.cmd</code>{' '}
            (<code>REVIEW_APP_PUBLISHER=1</code>, where the production key lives).
          </div>
        )}

        {mode && (
          <>
            <ReleasesPanel />

            <RecentlyPublished />

            {/* Pre-flight gate */}
            <section className="rounded-lg border border-gray-700 bg-gray-800/60 p-4">
              <h2 className="mb-1 text-sm font-semibold text-white">Pre-flight audio gate</h2>
              <p className="mb-2 text-xs text-gray-400">
                Read-only sweep of every staging TripGroup → READY / BLOCKED /
                ALREADY-BROKEN-LIVE. Slow (an S3 audio check per rung) — minutes, not seconds.
              </p>
              <button
                type="button"
                disabled={gateBusy}
                onClick={gate}
                className="rounded border border-sky-600 px-3 py-1.5 text-sm text-sky-300 hover:bg-sky-900/30 disabled:opacity-50"
              >
                {gateBusy ? 'Sweeping…' : 'Run gate report'}
              </button>
              {gateLog && (
                <pre className="mt-3 max-h-96 overflow-auto whitespace-pre-wrap rounded bg-black/40 p-2 text-[11px] text-gray-300">
                  {gateLog}
                </pre>
              )}
            </section>

            {/* Queue a job locally */}
            <section className="rounded-lg border border-gray-700 bg-gray-800/60 p-4">
              <h2 className="mb-2 text-sm font-semibold text-white">Queue a job</h2>
              <div className="flex flex-wrap items-center gap-2">
                <select
                  value={qKind}
                  onChange={(e) => setQKind(e.target.value as BusJobKind)}
                  className="rounded border border-gray-600 bg-gray-900 px-2 py-1.5 text-sm text-gray-100"
                >
                  {/* "tool" is server-minted only (the ToolsPanel) — the queue endpoint refuses it. */}
                  {(Object.keys(KIND_LABEL) as BusJobKind[])
                    .filter((k) => k !== 'tool')
                    .map((k) => (
                      <option key={k} value={k}>
                        {KIND_LABEL[k]}
                      </option>
                    ))}
                </select>
                <input
                  value={qTarget}
                  onChange={(e) => setQTarget(e.target.value)}
                  placeholder={
                    qKind === 'publish_pin'
                      ? 'TripLocation id (e.g. Kyoto)'
                      : qKind === 'add_to_location'
                        ? 'TripGroup id'
                        : 'Trip content id'
                  }
                  className="w-56 rounded border border-gray-600 bg-gray-900 px-2 py-1.5 text-sm text-gray-100"
                />
                <input
                  value={qNote}
                  onChange={(e) => setQNote(e.target.value)}
                  placeholder="note…"
                  className="w-48 rounded border border-gray-600 bg-gray-900 px-2 py-1.5 text-sm text-gray-100"
                />
                <button
                  type="button"
                  disabled={qBusy || !qTarget.trim()}
                  onClick={queue}
                  className="rounded bg-custom-green px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
                >
                  Queue
                </button>
              </div>
            </section>

            <ToolsPanel onRan={load} />

            {/* Inbox */}
            <section className="rounded-lg border border-gray-700 bg-gray-800/60 p-4">
              <h2 className="mb-3 text-sm font-semibold text-white">
                Job inbox <span className="font-normal text-gray-400">({jobs.length})</span>
                <button
                  type="button"
                  onClick={load}
                  className="ml-3 rounded border border-gray-600 px-2 py-0.5 text-xs font-normal text-gray-300 hover:bg-gray-700"
                >
                  Refresh
                </button>
              </h2>
              {jobs.length === 0 && <p className="text-sm text-gray-500">No jobs on the bus.</p>}
              <ul className="space-y-1.5">
                {jobs.map((j) => (
                  <li key={j.id} className="rounded border border-gray-700 bg-gray-900/40 p-2 text-xs">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium text-white ${STATUS_CLS[j.status]}`}>
                        {j.status}
                      </span>
                      <span className="font-medium text-gray-200">{j.trip_id}</span>
                      <span className="rounded bg-gray-700 px-1.5 py-0.5 text-[10px] text-gray-300">
                        {j.kind}
                      </span>
                      <span className="text-gray-500">
                        {j.requested_by} · {new Date(j.requested_at * 1000).toLocaleString()}
                      </span>
                      {j.note && <span className="text-gray-400">— {j.note}</span>}
                      {/* "tool" jobs are executed by /pipeline/tool itself (background
                          thread) — /pipeline/run has no dispatch for them, so offering
                          Dry run / Apply here would only 422. */}
                      {j.status !== 'done' && j.kind !== 'tool' && (
                        <span className="ml-auto flex items-center gap-1.5">
                          <button
                            type="button"
                            disabled={runBusy === j.id}
                            onClick={() => run(j.id, false)}
                            className="rounded border border-sky-600 px-2 py-0.5 text-[11px] text-sky-300 hover:bg-sky-900/30 disabled:opacity-50"
                            title="Field-level plan/diff, no production write"
                          >
                            {runBusy === j.id ? 'Running…' : 'Dry run'}
                          </button>
                          {confirmApply === j.id ? (
                            <>
                              <span className="font-semibold text-rose-300">
                                write to PRODUCTION?
                              </span>
                              <button
                                type="button"
                                disabled={runBusy === j.id}
                                onClick={() => run(j.id, true)}
                                className="rounded bg-rose-700 px-2 py-0.5 text-[11px] font-semibold text-white hover:bg-rose-600 disabled:opacity-50"
                              >
                                Apply now
                              </button>
                              <button
                                type="button"
                                onClick={() => setConfirmApply(null)}
                                className="text-[11px] text-gray-400 underline"
                              >
                                cancel
                              </button>
                            </>
                          ) : (
                            <button
                              type="button"
                              disabled={runBusy === j.id}
                              onClick={() => setConfirmApply(j.id)}
                              className="rounded border border-rose-700 px-2 py-0.5 text-[11px] text-rose-300 hover:bg-rose-900/30 disabled:opacity-50"
                              title="Write to production (second confirmation follows)"
                            >
                              Apply…
                            </button>
                          )}
                        </span>
                      )}
                      {j.log && (
                        <button
                          type="button"
                          onClick={() => setOpenLog(openLog === j.id ? null : j.id)}
                          className="text-[11px] text-gray-400 underline hover:text-gray-200"
                        >
                          {openLog === j.id ? 'hide log' : 'log'}
                        </button>
                      )}
                    </div>
                    {openLog === j.id && j.log && (
                      <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap rounded bg-black/40 p-2 text-[11px] text-gray-300">
                        {j.log}
                      </pre>
                    )}
                  </li>
                ))}
              </ul>
            </section>

          </>
        )}
      </main>
    </div>
  );
};

export default PublisherPage;
