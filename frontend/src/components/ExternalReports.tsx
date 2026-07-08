import { useState } from 'react';
import { toast } from 'react-toastify';
import { api, ApiError, type ExternalReport, type ExternalReportStatus } from '../api';
import { useAuth } from '../authContext';

const STATUS_CLS: Record<ExternalReportStatus, string> = {
  open: 'bg-rose-700 text-white',
  acknowledged: 'bg-amber-600 text-white',
  resolved: 'bg-emerald-700 text-white',
};

/** Amber panel of stage-4b field reports (from the customer web/VR apps) for one scene
 * (or the trip level when sceneIndex is null). Rendered next to the SceneDesc; admin
 * gets acknowledge/resolve, reviewers see them read-only. Renders nothing when empty. */
const ExternalReports = ({
  reports,
  onUpdate,
}: {
  reports: ExternalReport[];
  onUpdate: (r: ExternalReport) => void;
}) => {
  const { user } = useAuth();
  const isAdmin = user?.role === 'admin';
  const [busy, setBusy] = useState<string | null>(null);

  if (reports.length === 0) return null;

  const setStatus = (r: ExternalReport, status: ExternalReportStatus) => {
    setBusy(r.id);
    api
      .setExternalReportStatus(r.id, status)
      .then(onUpdate)
      .catch((e: unknown) => toast.error(`Failed: ${e instanceof ApiError ? e.detail : 'network error'}`))
      .finally(() => setBusy(null));
  };

  return (
    <div className="rounded-lg border border-rose-800/60 bg-rose-900/10 p-3">
      <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-rose-300">
        Field reports ({reports.length}) — from the web/VR app
      </p>
      <ul className="space-y-2">
        {reports.map((r) => (
          <li key={r.id} className="rounded border border-gray-700 bg-gray-900/40 p-2">
            <div className="flex flex-wrap items-center gap-2">
              <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${STATUS_CLS[r.status]}`}>
                {r.status}
              </span>
              {r.source && (
                <span className="rounded bg-gray-700 px-1.5 py-0.5 text-[10px] uppercase text-gray-300">
                  {r.source}
                </span>
              )}
              {r.categories.map((c) => (
                <span key={c} className="rounded bg-rose-900/50 px-1.5 py-0.5 text-[10px] text-rose-200">
                  {c.replace(/_/g, ' ')}
                </span>
              ))}
              <span className="text-[11px] text-gray-500">
                {r.reporter}
                {r.created_at ? ` · ${new Date(r.created_at * 1000).toLocaleString()}` : ''}
              </span>
            </div>
            {r.body && <p className="mt-1 whitespace-pre-wrap text-xs text-gray-300">{r.body}</p>}
            {isAdmin && (
              <div className="mt-2 flex gap-2">
                {r.status === 'open' && (
                  <button
                    type="button"
                    disabled={busy === r.id}
                    onClick={() => setStatus(r, 'acknowledged')}
                    className="rounded border border-amber-600 px-2 py-0.5 text-[11px] text-amber-300 hover:bg-amber-900/30 disabled:opacity-50"
                  >
                    Acknowledge
                  </button>
                )}
                {r.status !== 'resolved' && (
                  <button
                    type="button"
                    disabled={busy === r.id}
                    onClick={() => setStatus(r, 'resolved')}
                    className="rounded border border-emerald-600 px-2 py-0.5 text-[11px] text-emerald-300 hover:bg-emerald-900/30 disabled:opacity-50"
                  >
                    Resolve
                  </button>
                )}
                {r.status === 'resolved' && (
                  <button
                    type="button"
                    disabled={busy === r.id}
                    onClick={() => setStatus(r, 'open')}
                    className="rounded border border-gray-600 px-2 py-0.5 text-[11px] text-gray-300 hover:bg-gray-700 disabled:opacity-50"
                  >
                    Re-open
                  </button>
                )}
              </div>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
};

export default ExternalReports;
