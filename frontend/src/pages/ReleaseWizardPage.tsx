import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { toast } from 'react-toastify';
import {
  api,
  ApiError,
  type BusJob,
  type ReleaseBatch,
  type ReleaseBoard,
  type ReleaseGroup,
  type ReleaseGroupDiff,
} from '../api';
import NavBar from '../components/NavBar';

const errText = (e: unknown, fallback: string): string =>
  e instanceof ApiError ? e.detail || e.code : fallback;

type StepState = 'todo' | 'dry_done' | 'done' | 'skipped' | 'failed';

interface StepDef {
  key: string;
  title: string;
  why: string;
  optional: boolean;
  run: 'job' | 'tool';
  kind?: 'publish_docs' | 'add_to_location' | 'publish_pin';
  tool?: string;
  target: string;
  applyOnly?: boolean;
}

/** Poll one bus job until it leaves `queued` (tools settle in a background
 * thread server-side). */
const awaitJob = async (jobId: string, target: string): Promise<BusJob | null> => {
  for (let i = 0; i < 40; i++) {
    const { jobs } = await api.pipelineJobs(target);
    const j = jobs.find((x) => x.id === jobId);
    if (j && j.status !== 'queued') return j;
    await new Promise((r) => setTimeout(r, 2000));
  }
  return null;
};

/** The lines worth reading first in a failed log (Tier-1 triage aid). */
const failureLines = (log: string): string[] =>
  log
    .split('\n')
    .filter((l) => /missing|not found|no such|!!|FAIL|BLOCKED|error/i.test(l))
    .slice(0, 12);

/** Tier-1 remedy tools for a failed step: the usual fixes for verify/missing-file
 * classes, each a one-click re-run of an existing whitelisted tool. */
const REMEDIES: { tool: string; label: string; targetKind: 'trip' | 'family' }[] = [
  { tool: 'stage10b', label: 'Re-run stage 10b (recall/keywords/verify)', targetKind: 'family' },
  { tool: 'upload_thumbs', label: 'Re-upload thumbnails', targetKind: 'trip' },
  { tool: 'static_pic_4k', label: 'Rebuild 4K stills → S3', targetKind: 'trip' },
  { tool: 'tripdocs', label: 'Rebuild trip reference docs', targetKind: 'trip' },
];

const VR_LS_PREFIX = 'vrcheck:';

/** One wizard step card: dry-run → read → confirm → apply (or skip), with
 * Tier-1 remedies + the Tier-2 Claude handoff on failure. */
const Step = ({
  n,
  def,
  state,
  log,
  jobId,
  busy,
  vrDone,
  familyBase,
  onDry,
  onApply,
  onSkip,
  onRemedy,
}: {
  n: number;
  def: StepDef;
  state: StepState;
  log: string;
  jobId: string | null;
  busy: boolean;
  vrDone: boolean;
  familyBase: string;
  onDry: () => void;
  onApply: () => void;
  onSkip: () => void;
  onRemedy: (tool: string, target: string) => void;
}) => {
  const [confirming, setConfirming] = useState(false);
  const chip =
    state === 'done'
      ? 'bg-emerald-700 text-white'
      : state === 'skipped'
        ? 'bg-gray-600 text-gray-200'
        : state === 'failed'
          ? 'bg-red-700 text-white'
          : state === 'dry_done'
            ? 'bg-amber-600 text-white'
            : 'bg-gray-700 text-gray-300';
  const fails = state === 'failed' ? failureLines(log) : [];
  return (
    <section className="rounded-lg border border-gray-700 bg-gray-800/60 p-4">
      <div className="mb-1 flex flex-wrap items-center gap-2">
        <span className={`rounded px-2 py-0.5 text-[11px] font-semibold uppercase ${chip}`}>
          {state === 'todo' ? `step ${n}` : state.replace('_', ' ')}
        </span>
        <h2 className="text-sm font-semibold text-white">{def.title}</h2>
        {def.optional && <span className="text-[10px] uppercase text-gray-500">if needed</span>}
      </div>
      <p className="mb-2 text-xs text-gray-400">{def.why}</p>
      {state !== 'done' && state !== 'skipped' && (
        <div className="flex flex-wrap items-center gap-2">
          {!def.applyOnly && (
            <button
              type="button"
              disabled={busy}
              onClick={onDry}
              className="rounded border border-sky-600 px-3 py-1.5 text-xs text-sky-300 hover:bg-sky-900/30 disabled:opacity-50"
            >
              {busy ? 'Running…' : 'Dry run'}
            </button>
          )}
          {confirming ? (
            <>
              <span className="text-xs text-amber-300">This writes for real — sure?</span>
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  setConfirming(false);
                  onApply();
                }}
                className="rounded bg-red-700 px-3 py-1.5 text-xs font-semibold text-white hover:bg-red-600 disabled:opacity-50"
              >
                Yes — apply
              </button>
              <button
                type="button"
                onClick={() => setConfirming(false)}
                className="rounded border border-gray-600 px-2 py-1.5 text-xs text-gray-300"
              >
                Cancel
              </button>
            </>
          ) : (
            <button
              type="button"
              disabled={
                busy || !vrDone || (!def.applyOnly && state !== 'dry_done' && state !== 'failed')
              }
              onClick={() => setConfirming(true)}
              className="rounded bg-custom-green px-3 py-1.5 text-xs font-semibold text-white hover:opacity-90 disabled:opacity-50"
              title={
                !vrDone
                  ? 'Finish the VR staging check (step 0) first'
                  : !def.applyOnly && state === 'todo'
                    ? 'Dry-run first — read what it will do'
                    : undefined
              }
            >
              Apply
            </button>
          )}
          {def.optional && (
            <button
              type="button"
              disabled={busy}
              onClick={onSkip}
              className="rounded border border-gray-600 px-2.5 py-1.5 text-xs text-gray-300 hover:bg-gray-700"
            >
              Skip — not needed
            </button>
          )}
        </div>
      )}
      {fails.length > 0 && (
        <div className="mt-2 rounded border border-red-900 bg-red-950/30 p-2">
          <p className="mb-1 text-[11px] font-semibold uppercase text-red-300">
            What failed (extract)
          </p>
          <pre className="whitespace-pre-wrap text-[11px] text-red-200">{fails.join('\n')}</pre>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            {REMEDIES.map((r) => (
              <button
                key={r.tool}
                type="button"
                disabled={busy}
                onClick={() => onRemedy(r.tool, r.targetKind === 'family' ? familyBase : def.target)}
                className="rounded border border-gray-600 px-2 py-1 text-[11px] text-gray-300 hover:bg-gray-700 disabled:opacity-50"
                title="Common remedy — runs the existing tool, then Dry-run this step again"
              >
                {r.label}
              </button>
            ))}
            {jobId && (
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  api
                    .investigateJob(jobId)
                    .then((r) =>
                      toast.success(`Claude (Opus, high effort) opened on ${r.bundle}`),
                    )
                    .catch((e: unknown) => toast.error(errText(e, 'Could not launch Claude')));
                }}
                className="rounded bg-violet-700 px-2.5 py-1 text-[11px] font-semibold text-white hover:bg-violet-600 disabled:opacity-50"
                title="Opens a new terminal running claude --model opus --effort high, pre-briefed with this job's full log and context"
              >
                Investigate with Claude (Opus · high)
              </button>
            )}
          </div>
        </div>
      )}
      {log && (
        <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap rounded bg-black/40 p-2 text-[11px] text-gray-300">
          {log}
        </pre>
      )}
    </section>
  );
};

/** Guided release for one rung (`/publisher/release/:tripId`), a whole family
 * (`/publisher/release-family/:tgId` — tick the rungs to ship), a whole tile
 * (`/publisher/release-location/:locName`) or a saved release batch
 * (`/publisher/release-batch/:batchId`). Step 0 is the human VR check in staging
 * (wizard-only gate, admin trusted — no server enforcement); every action reuses
 * the existing bus jobs / whitelisted tools. */
const ReleaseWizardPage = () => {
  const { tripId = '', tgId = '', locName = '', batchId = '' } = useParams();
  const familyMode = !!tgId;
  const locMode = !!locName;
  const batchMode = !!batchId;
  const [batch, setBatch] = useState<ReleaseBatch | null>(null);
  const [batchLoaded, setBatchLoaded] = useState(false);
  const [board, setBoard] = useState<ReleaseBoard | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [diff, setDiff] = useState<ReleaseGroupDiff | null>(null);
  const [vrTicks, setVrTicks] = useState<Set<string>>(new Set());
  const [states, setStates] = useState<Record<string, StepState>>({});
  const [logs, setLogs] = useState<Record<string, string>>({});
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const jobIds = useRef<Record<string, string>>({});

  useEffect(() => {
    api
      .listReleases()
      .then(setBoard)
      .catch((e: unknown) => toast.error(errText(e, 'Failed to load release data')));
  }, []);

  // Batch scope: the batch list is the only read endpoint — find ours in it.
  useEffect(() => {
    if (!batchMode) return;
    api
      .listReleaseBatches()
      .then((r) => setBatch(r.batches.find((b) => String(b.id) === batchId) ?? null))
      .catch((e: unknown) => toast.error(errText(e, 'Failed to load the release batch')))
      .finally(() => setBatchLoaded(true));
  }, [batchMode, batchId]);

  /** The in-scope groups: one (single/family), every in-flight family on the
   * tile (locMode), or every family the batch resolves to (batchMode). */
  const groups: ReleaseGroup[] = useMemo(() => {
    if (!board) return [];
    if (batchMode) {
      if (!batch) return [];
      const gids = new Set(batch.resolved.group_ids);
      const tids = new Set(batch.resolved.trip_ids);
      return board.groups.filter(
        (g) => gids.has(g.tg_id) || g.rungs.some((r) => tids.has(r.trip_id)),
      );
    }
    if (locMode)
      return board.groups.filter((g) => g.locations.some((l) => l.name === locName));
    if (familyMode) {
      const g = board.groups.find((x) => x.tg_id === tgId);
      return g ? [g] : [];
    }
    const g = board.groups.find((x) => x.rungs.some((r) => r.trip_id === tripId));
    return g ? [g] : [];
  }, [board, batchMode, batch, locMode, locName, familyMode, tgId, tripId]);
  const group = groups[0] ?? null;
  const allRungs = useMemo(() => groups.flatMap((g) => g.rungs), [groups]);
  /** Scopes that can span several families — they list rungs per group, skip the
   * single-group diff and ask for one whole-scope tile-text VR check. */
  const multiGroup = locMode || batchMode;
  const scopeKey = locMode ? `loc:${locName}` : batchMode ? `batch:${batchId}` : '';

  // Default selection: single mode = the rung; family/tile = every READY rung;
  // batch = every READY rung the batch actually resolves to (a family pulled in
  // by a PARTIAL trip pick must not pre-tick its excluded siblings — the batch
  // is the contract of what ships).
  useEffect(() => {
    if (groups.length === 0) return;
    const batchTids = new Set(batch?.resolved.trip_ids ?? []);
    setSelected(
      new Set(
        familyMode || locMode || batchMode
          ? allRungs
              .filter((r) => r.status === 'ready' && (!batchMode || batchTids.has(r.trip_id)))
              .map((r) => r.trip_id)
          : [tripId],
      ),
    );
  }, [groups, allRungs, familyMode, locMode, batchMode, batch, tripId]);

  // Staging→prod diff drives the VR-check wording (group text vs trip content).
  // Tile mode skips the per-group diff and always asks for the tile-text check.
  useEffect(() => {
    if (!group || multiGroup) return;
    api
      .releaseGroupDiff(group.tg_id)
      .then(setDiff)
      .catch(() => setDiff(null));
  }, [group, multiGroup]);

  // Prefill VR ticks done recently (wizard-only convenience; localStorage).
  useEffect(() => {
    if (!group) return;
    const ticks = new Set<string>();
    for (const key of [...selected, `group:${scopeKey || group.tg_id}`]) {
      try {
        const at = Number(localStorage.getItem(VR_LS_PREFIX + key) ?? 0);
        if (Date.now() - at < 24 * 3600 * 1000) ticks.add(key);
      } catch {
        /* storage unavailable — start unticked */
      }
    }
    setVrTicks(ticks);
  }, [group, selected, scopeKey]);

  const groupTextChanged =
    multiGroup ||
    (diff?.changed ?? []).some((c) => !c.field.startsWith('trips[') || c.field.includes('].'));
  const vrItems: { key: string; label: string; sub: string }[] = useMemo(() => {
    if (!group) return [];
    const items = [...selected].sort().map((tid) => {
      const live = allRungs.find((r) => r.trip_id === tid)?.status === 'live';
      return {
        key: tid,
        label: `Checked ${tid} in the headset (staging)`,
        sub: live
          ? 're-release of a live rung — play the changed parts'
          : 'new to production — play it through, quiz and keywords included',
      };
    });
    if (groupTextChanged)
      items.push({
        key: `group:${scopeKey || group.tg_id}`,
        label: 'Checked the tile button text / tooltips in the headset',
        sub: locMode
          ? 'whole-tile release — check every family button on the tile'
          : batchMode
            ? 'whole-batch release — check every family button the batch ships'
            : 'the group text differs from production (see the diff on the Releases board)',
      });
    return items;
  }, [group, allRungs, selected, groupTextChanged, locMode, batchMode, scopeKey]);
  const vrDone = vrItems.length > 0 && vrItems.every((i) => vrTicks.has(i.key));

  const toggleVr = (key: string) => {
    setVrTicks((prev) => {
      const next = new Set(prev);
      try {
        // Keep localStorage in step BOTH ways: an untick must clear the stored
        // stamp, or the prefill effect silently re-ticks it (re-arming Apply)
        // on the next selection change.
        if (next.has(key)) localStorage.removeItem(VR_LS_PREFIX + key);
        else localStorage.setItem(VR_LS_PREFIX + key, String(Date.now()));
      } catch {
        /* per-viewer convenience only */
      }
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const steps: StepDef[] = useMemo(() => {
    if (!group) return [];
    const loc = locMode ? locName : (group.locations[0]?.name ?? '');
    // publish_pin's bus-job target must be the TripLocation DOC ID, not the
    // display name (they diverge on ~1 in 6 docs, and a name with a space is
    // refused at queue time). Tile mode: the first in-scope doc bearing the
    // tile's name (same-named sibling docs, e.g. Alps ×3, share one tile label
    // — the dry run shows which doc the pin actually targets).
    const locId = locMode
      ? (groups
          .flatMap((g) => g.locations)
          .find((l) => l.name === locName)?.id ?? '')
      : (group.locations[0]?.id ?? '');
    const rungSteps: StepDef[] = [...selected].sort().map((tid) => ({
      key: `docs:${tid}`,
      title: `Publish ${tid} (staging → production)`,
      why:
        'Writes the Trip doc + the production TripGroup as a PARTIAL group (live rungs + this one, text from ' +
        'staging). On apply the family Trello card is stamped published= (and moves to Live when every rung is).',
      optional: false,
      run: 'job',
      kind: 'publish_docs',
      target: tid,
    }));
    const first = [...selected].sort()[0] ?? tripId;
    // One add-to-location step per in-scope group that has a selected rung
    // (tile mode can involve several families; single/family = one).
    const locSteps: StepDef[] = groups
      .filter((g) => g.rungs.some((r) => selected.has(r.trip_id)))
      .map((g) => ({
        key: `location:${g.tg_id}`,
        title: `Make the tile show ${g.tg_id} (add group to TripLocation)`,
        why: "Needed ONLY when the publish plan warned 'no production TripLocation lists this group' — a family's first release.",
        optional: true,
        run: 'job',
        kind: 'add_to_location',
        target: g.tg_id,
      }));
    return [
      ...rungSteps,
      ...locSteps,
      {
        key: 'pin',
        title: `Publish the map pin${loc ? ` (${loc})` : ''}`,
        why: 'Copies the staging pin to production. Skip when the location is already pinned live and unchanged.',
        optional: true,
        run: 'job',
        kind: 'publish_pin',
        target: locId,
      },
      {
        key: 'bump',
        title: 'Bump the PROD content version (cache-bust)',
        why: 'Makes headsets re-sync content on next launch. No preview — the script has no dry-run.',
        optional: false,
        run: 'tool',
        tool: 'bump_version',
        target: '',
        applyOnly: true,
      },
      {
        key: 'docids',
        title: 'Log the release (Content_DocIDs.md)',
        why: 'Appends one dated line to the curated release log in the Scripts repo.',
        optional: false,
        run: 'tool',
        tool: 'docids_append',
        // A batch can span families, so its log line is stamped per rung (`first`)
        // rather than for a scope name the DocIDs log has no row for.
        target: locMode ? locName : familyMode ? group.tg_id : first,
        applyOnly: true,
      },
      {
        key: 'snapshot',
        title: 'Refresh the prod snapshot',
        why: 'Re-exports production state to the bus so the drift indicators and the Releases diff see the new reality.',
        optional: false,
        run: 'tool',
        tool: 'snapshot',
        target: first,
        applyOnly: true,
      },
    ];
  }, [group, groups, selected, familyMode, locMode, locName, tripId]);

  const setStep = (key: string, state: StepState, log?: string) => {
    setStates((s) => ({ ...s, [key]: state }));
    if (log !== undefined) setLogs((l) => ({ ...l, [key]: log }));
  };

  const jobFor = async (def: StepDef): Promise<string> => {
    if (jobIds.current[def.key]) return jobIds.current[def.key];
    // Never find-or-queue with a blank target: pipelineJobs('') lists EVERY job,
    // so the find could latch onto an unrelated job of the same kind (and the
    // queue would 422 on the empty id anyway).
    if (!def.target)
      throw new ApiError(0, 'no_target', 'This step has no target (family not on a TripLocation yet?) — skip it.');
    const { jobs } = await api.pipelineJobs(def.target);
    const existing = jobs.find(
      (j) => j.kind === def.kind && (j.status === 'queued' || j.status === 'dry_run'),
    );
    const job = existing ?? (await api.queueBusJob(def.kind!, def.target, 'release wizard'));
    jobIds.current[def.key] = job.id;
    return job.id;
  };

  const runStep = async (def: StepDef, apply: boolean) => {
    setBusyKey(def.key);
    try {
      let settled: BusJob | null = null;
      if (def.run === 'job') {
        const id = await jobFor(def);
        settled = await api.runPipelineJob(id, apply, apply);
      } else {
        const j = await api.runTool({ tool: def.tool!, target: def.target, apply });
        jobIds.current[def.key] = j.id;
        settled = j.status === 'queued' ? await awaitJob(j.id, j.trip_id) : j;
      }
      const log = settled?.log ?? '(no log yet — check the Publisher inbox)';
      if (!settled || settled.status === 'failed') {
        setStep(def.key, 'failed', log);
        toast.error(`${def.title}: failed — see the extract + remedies.`);
      } else if (apply) {
        setStep(def.key, 'done', log);
        toast.success(`${def.title}: applied.`);
      } else {
        setStep(def.key, 'dry_done', log);
      }
    } catch (e: unknown) {
      toast.error(errText(e, `${def.title} failed to run`));
      setStep(def.key, 'failed');
    } finally {
      setBusyKey(null);
    }
  };

  const runRemedy = (tool: string, target: string) => {
    api
      .runTool({ tool, target, apply: true })
      .then((j) => toast.success(`${tool} started (job ${j.id}) — re-dry-run the step when it lands.`))
      .catch((e: unknown) => toast.error(errText(e, `${tool} failed to start`)));
  };

  if (!board || (batchMode && !batchLoaded))
    return <p className="mx-auto max-w-review px-4 py-8 text-gray-400">Loading…</p>;
  if (!group) {
    return (
      <div className="min-h-screen">
        <NavBar title="Release" backTo="/publisher" backLabel="Publisher" />
        <main className="mx-auto max-w-review px-4 py-8">
          <p className="text-rose-400">
            {batchMode
              ? `batch ${batch?.name ?? batchId}`
              : locMode
                ? `tile ${locName}`
                : familyMode
                  ? tgId
                  : tripId}{' '}
            is not on the release board — it needs release activity (release prep / completed
            review) to appear.
          </p>
        </main>
      </div>
    );
  }

  const doneCount = steps.filter((s) => ['done', 'skipped'].includes(states[s.key] ?? '')).length;
  // stage10b --families takes TripGroup DOC IDS verbatim (Jedburgh1_TownAbbey
  // AND legacy A._A. Milne…_Trip alike) — never strip the _Trip suffix.
  const familyBase = group.tg_id;

  return (
    <div className="min-h-screen">
      <NavBar
        title={`Release — ${
          batchMode
            ? `batch ${batch?.name ?? batchId}`
            : locMode
              ? `tile ${locName}`
              : familyMode
                ? group.tg_id
                : tripId
        }`}
        subtitle={`${multiGroup ? `${groups.length} families` : group.tg_id} · ${doneCount}/${steps.length} steps complete`}
        backTo="/publisher"
        backLabel="Publisher"
      />
      <main className="mx-auto max-w-review space-y-4 px-4 py-6">
        {(familyMode || multiGroup) && (
          <section className="rounded-lg border border-gray-700 bg-gray-800/60 p-4">
            <h2 className="mb-1 text-sm font-semibold text-white">What ships</h2>
            <p className="mb-2 text-xs text-gray-400">
              Tick the rungs to release. Ready rungs are pre-ticked; a live rung can be
              re-released; anything else releases at your own risk.
            </p>
            <div className="space-y-3">
              {groups.map((g) => (
                <div key={g.tg_id}>
                  {multiGroup && (
                    <p className="mb-1 text-xs font-semibold text-gray-300">{g.tg_id}</p>
                  )}
                  <ul className="space-y-1">
                    {g.rungs.map((r) => (
                      <li key={r.trip_id} className="flex flex-wrap items-center gap-2 text-xs">
                        <input
                          type="checkbox"
                          checked={selected.has(r.trip_id)}
                          onChange={() =>
                            setSelected((prev) => {
                              const next = new Set(prev);
                              if (next.has(r.trip_id)) next.delete(r.trip_id);
                              else next.add(r.trip_id);
                              return next;
                            })
                          }
                        />
                        <span className="text-gray-200">{r.trip_id}</span>
                        <span className="text-gray-500">{r.status.replace('_', ' ')}</span>
                        {r.finalised === 'restale' && (
                          <span className="text-amber-300" title="Re-approved since the last stage-9 finalise — re-finalise first">
                            re-finalise pending
                          </span>
                        )}
                        {r.recall_quiz === 'missing' && (
                          <span className="text-amber-300" title="Quiz-eligible but no recallQuiz on staging — run stage 10b">
                            recall quiz missing
                          </span>
                        )}
                        {r.four_k === 'missing' && (
                          <span className="text-amber-300" title="Static scenes without a 4K-webapp record — run static_pic_4k">
                            4K stills missing
                          </span>
                        )}
                        {r.keyword_copy === 'missing' && (
                          <span className="text-amber-300" title="Leveled keywords not copied to this EN rung — stage 10b step 2">
                            EN keywords not copied
                          </span>
                        )}
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          </section>
        )}

        {/* Step 0 — the human VR check (wizard-only gate; admin trusted) */}
        <section
          className={`rounded-lg border p-4 ${
            vrDone ? 'border-emerald-800 bg-emerald-900/10' : 'border-amber-700 bg-amber-900/10'
          }`}
        >
          <h2 className="mb-1 text-sm font-semibold text-white">
            Step 0 — VR check in staging {vrDone ? '✓' : ''}
          </h2>
          <p className="mb-2 text-xs text-gray-400">
            Everything below stays dry-run-only until each item is confirmed. Ticks remembered
            for 24h on this machine.
          </p>
          <ul className="space-y-1.5">
            {vrItems.map((i) => (
              <li key={i.key}>
                <label className="flex items-start gap-2 text-xs text-gray-200">
                  <input
                    type="checkbox"
                    checked={vrTicks.has(i.key)}
                    onChange={() => toggleVr(i.key)}
                    className="mt-0.5"
                  />
                  <span>
                    {i.label}
                    <span className="block text-[11px] text-gray-500">{i.sub}</span>
                  </span>
                </label>
              </li>
            ))}
            {vrItems.length === 0 && (
              <li className="text-xs text-gray-500">Select at least one rung to release.</li>
            )}
          </ul>
        </section>

        {steps.map((def, i) => (
          <Step
            key={def.key}
            n={i + 1}
            def={def}
            state={states[def.key] ?? 'todo'}
            log={logs[def.key] ?? ''}
            jobId={jobIds.current[def.key] ?? null}
            busy={busyKey === def.key}
            vrDone={vrDone}
            familyBase={familyBase}
            onDry={() => void runStep(def, false)}
            onApply={() => void runStep(def, true)}
            onSkip={() => setStep(def.key, 'skipped')}
            onRemedy={runRemedy}
          />
        ))}
        <p className="text-xs text-gray-500">
          Optional follow-ups (trip reference docs, 4K panoramas, thumbnails, stage-9 re-runs)
          live in the Publisher’s{' '}
          <Link to="/publisher" className="text-sky-400 hover:underline">
            Post-publish &amp; tools
          </Link>{' '}
          rack.
        </p>
      </main>
    </div>
  );
};

export default ReleaseWizardPage;
