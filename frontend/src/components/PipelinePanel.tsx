import { useCallback, useEffect, useState } from 'react';
import { toast } from 'react-toastify';
import { api, ApiError, type BusJob, type DriftResponse } from '../api';

const STATUS_CLS: Record<BusJob['status'], string> = {
  queued: 'bg-blue-600',
  dry_run: 'bg-amber-600',
  done: 'bg-emerald-700',
  failed: 'bg-red-700',
};

/** Admin pipeline panel for an APPROVED trip: staging-vs-live drift (from the bus
 * prod snapshot), this trip's publish jobs, and "Request publish". Jobs only queue
 * here — execution is a human act on the workstation (publisher mode /
 * publish_inbox.py), the one machine with the production key. */
const PipelinePanel = ({ tripId }: { tripId: string }) => {
  const [jobs, setJobs] = useState<BusJob[]>([]);
  const [publisherMode, setPublisherMode] = useState(false);
  const [drift, setDrift] = useState<DriftResponse | null>(null);
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [runBusy, setRunBusy] = useState<string | null>(null);
  const [openLog, setOpenLog] = useState<string | null>(null);

  const load = useCallback(() => {
    api
      .pipelineJobs(tripId)
      .then((r) => {
        setJobs(r.jobs);
        setPublisherMode(r.publisher_mode);
      })
      .catch(() => {});
    api
      .drift(tripId)
      .then(setDrift)
      .catch(() => {});
  }, [tripId]);

  useEffect(() => {
    load();
  }, [load]);

  const queue = () => {
    setBusy(true);
    api
      .queuePublish(tripId, note.trim())
      .then(() => {
        toast.success('Publish request queued — run it from the workstation (publisher mode / publish_inbox.py).');
        setNote('');
        load();
      })
      .catch((e: unknown) => toast.error(`Queue failed: ${e instanceof ApiError ? e.detail : 'network error'}`))
      .finally(() => setBusy(false));
  };

  const dryRun = (jobId: string) => {
    setRunBusy(jobId);
    api
      .runPipelineJob(jobId)
      .then((j) => {
        toast.success(`Dry run finished (${j.status}) — see the log.`);
        setOpenLog(jobId);
        load();
      })
      .catch((e: unknown) => toast.error(`Run failed: ${e instanceof ApiError ? e.detail : 'network error'}`))
      .finally(() => setRunBusy(null));
  };

  return (
    <section className="rounded-lg border border-sky-800/60 bg-gray-800/60 p-4">
      <h2 className="mb-1 text-sm font-semibold text-white">Pipeline · publish to live</h2>

      <p className="mb-3 text-xs text-gray-400">
        {drift === null || drift.snapshot_at === null ? (
          <>No production snapshot on the bus yet — drift unknown (workstation: <code>publish_inbox.py snapshot {tripId}</code>).</>
        ) : drift.fields_differ && drift.fields_differ.length > 0 ? (
          <>
            <span className="font-medium text-amber-300">{drift.fields_differ.length} field
            {drift.fields_differ.length === 1 ? '' : 's'} differ from live</span>{' '}
            (snapshot {new Date(drift.snapshot_at * 1000).toLocaleString()}):{' '}
            <span className="text-gray-500">{drift.fields_differ.slice(0, 6).join(', ')}{drift.fields_differ.length > 6 ? ', …' : ''}</span>
          </>
        ) : (
          <span className="text-emerald-400">
            In sync with live (snapshot {new Date(drift.snapshot_at * 1000).toLocaleString()}).
          </span>
        )}
      </p>

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <input
          type="text"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="Optional note (why this publish)"
          className="w-full max-w-xs rounded border border-gray-700 bg-gray-900 px-2 py-1 text-base sm:text-sm"
        />
        <button
          type="button"
          disabled={busy}
          onClick={queue}
          className="rounded bg-sky-700 px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
        >
          {busy ? 'Queuing…' : 'Request publish (text)'}
        </button>
      </div>

      {jobs.length > 0 && (
        <ul className="space-y-1.5">
          {jobs.map((j) => (
            <li key={j.id} className="rounded border border-gray-700 bg-gray-900/40 p-2 text-xs">
              <div className="flex flex-wrap items-center gap-2">
                <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium text-white ${STATUS_CLS[j.status]}`}>
                  {j.status}
                </span>
                <span className="text-gray-400">{j.id}</span>
                <span className="text-gray-500">
                  by {j.requested_by} · {new Date(j.requested_at * 1000).toLocaleString()}
                </span>
                {j.note && <span className="text-gray-400">— {j.note}</span>}
                {publisherMode && j.status === 'queued' && (
                  <button
                    type="button"
                    disabled={runBusy === j.id}
                    onClick={() => dryRun(j.id)}
                    className="rounded border border-sky-600 px-2 py-0.5 text-[11px] text-sky-300 hover:bg-sky-900/30 disabled:opacity-50"
                    title="Dry run: field-level diff, no production write"
                  >
                    {runBusy === j.id ? 'Running…' : 'Dry run'}
                  </button>
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
                <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap rounded bg-black/40 p-2 text-[11px] text-gray-300">
                  {j.log}
                </pre>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
};

export default PipelinePanel;
