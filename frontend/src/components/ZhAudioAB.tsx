import { useEffect, useRef } from 'react';

/** Self-reloading audio row — mirrors AudioReview's internal AudioRow (reloads
 * whenever its src URL changes so a swapped take is never auditioned from a
 * cached response). Kept local/duplicated rather than importing from
 * AudioReview to keep the `_ZH` A/B path fully isolated from the splice/
 * coverage engine (see review-app-chinese-review.md: "A/B is explicitly
 * temporary; keep it isolated + presence-driven so removal is trivial"). */
const AbRow = ({ label, src }: { label: string; src: string | null }) => {
  const ref = useRef<HTMLAudioElement | null>(null);
  useEffect(() => {
    ref.current?.load();
  }, [src]);
  if (!src) return null;
  return (
    <div className="min-w-0 flex-1">
      <span className="mb-1 block text-xs font-medium text-gray-400">{label}</span>
      <audio ref={ref} controls preload="none" src={src} className="h-8 w-full" />
    </div>
  );
};

interface ZhAudioABProps {
  v2: string | null;
  v3: string | null;
}

/**
 * Side-by-side V2/V3 audition for the temporary Mandarin A/B trips (two
 * ElevenLabs versions of the same voice) — review-app-chinese-review.md Part
 * 3. No splice/regenerate/coverage controls here; the reviewer just listens
 * per field and picks a trip-wide winner via the review header's preferred-
 * version control. Each side no-ops independently if that one take is
 * missing; renders nothing if neither exists (the backend nulls both once a
 * session isn't in A/B mode, so this only ever mounts with at least one).
 */
const ZhAudioAB = ({ v2, v3 }: ZhAudioABProps) => {
  if (!v2 && !v3) return null;
  return (
    <div className="flex gap-3 rounded border border-gray-700 bg-gray-900/40 p-2">
      <AbRow label="V2" src={v2} />
      <AbRow label="V3" src={v3} />
    </div>
  );
};

export default ZhAudioAB;
