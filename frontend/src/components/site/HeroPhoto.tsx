"use client";

import { useEffect, useRef, useState } from "react";
import { coverSrc, type PortalUnit } from "@/lib/portal";

/**
 * The featured home's own photo, or the drawing (`fallback`) when there is none or it cannot load. The drawing is not drawn behind the
 * photo: while the photo loads, the frame's own warm background shows, so a reload never flashes the drawing.
 */
export default function HeroPhoto({ unit, fallback }: { unit?: Pick<PortalUnit, "cover" | "title">; fallback: React.ReactNode }) {
  const [failed, setFailed] = useState(false);
  const img = useRef<HTMLImageElement>(null);
  const src = coverSrc(unit?.cover);

  // The page arrives with the <img> already in it, so the photo can fail before this script runs and its error event is missed.
  useEffect(() => {
    if (img.current?.complete && img.current.naturalWidth === 0) setFailed(true);
  }, [src]);

  if (!src || failed) return <>{fallback}</>;
  return (
    <>
      {/* eslint-disable-next-line @next/next/no-img-element -- pictures come from developer websites, which Next's image optimiser is not set up to fetch */}
      <img
        ref={img}
        src={src}
        alt={unit?.title ?? ""}
        decoding="async"
        fetchPriority="high"
        referrerPolicy="no-referrer"
        onError={() => setFailed(true)}
        className="absolute inset-0 h-full w-full object-cover"
      />
      {/* A warm wash at the edges keeps the floating cards readable and the photo in the page's palette. */}
      <div aria-hidden="true" className="absolute inset-0 bg-gradient-to-t from-[#3b2a10]/45 via-transparent to-[#3b2a10]/15" />
    </>
  );
}
