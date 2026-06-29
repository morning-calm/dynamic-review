import { memo, useEffect, useRef, useState } from 'react';

interface VimeoPlayerProps {
  videoId: string | null;
  isStaticImage: boolean;
  imageUrl: string | null;
  sceneIndex: number;
}

/**
 * Renders a scene's media. Static 360 stills show as an <img>. Vimeo videos are
 * lazy-mounted: a poster is shown until the card scrolls into view, then the
 * iframe is created (keeps ~20 iframes from all loading at once).
 */
const VimeoPlayer = ({ videoId, isStaticImage, imageUrl, sceneIndex }: VimeoPlayerProps) => {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [inView, setInView] = useState(false);

  useEffect(() => {
    if (isStaticImage || !videoId) return;
    const el = containerRef.current;
    if (!el) return;
    if (typeof IntersectionObserver === 'undefined') {
      setInView(true);
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setInView(true);
          observer.disconnect();
        }
      },
      { rootMargin: '200px' },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [isStaticImage, videoId]);

  if (isStaticImage && imageUrl) {
    return (
      <img
        src={imageUrl}
        alt={`Scene ${sceneIndex} still`}
        loading="lazy"
        className="w-full rounded border border-gray-700 bg-black object-contain"
      />
    );
  }

  if (!videoId) {
    return (
      <div className="flex aspect-video w-full items-center justify-center rounded border border-gray-700 bg-gray-800 text-xs text-gray-500">
        No video for this scene
      </div>
    );
  }

  return (
    <div ref={containerRef} className="aspect-video w-full overflow-hidden rounded border border-gray-700 bg-black">
      {inView ? (
        <iframe
          src={`https://player.vimeo.com/video/${videoId}?badge=0&autopause=0`}
          className="h-full w-full"
          allow="autoplay; fullscreen; picture-in-picture; clipboard-write"
          referrerPolicy="strict-origin-when-cross-origin"
          title={`Scene ${sceneIndex} video`}
        />
      ) : (
        <div className="flex h-full w-full items-center justify-center">
          <div className="flex h-14 w-14 items-center justify-center rounded-full bg-custom-green/80">
            <svg className="ml-1 h-7 w-7 text-white" fill="currentColor" viewBox="0 0 24 24" aria-hidden="true">
              <path d="M8 5v14l11-7z" />
            </svg>
          </div>
        </div>
      )}
    </div>
  );
};

export default memo(VimeoPlayer);
