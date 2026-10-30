"use client";

import { useEffect, useRef, useState } from "react";
import { coverSrc, type PortalUnit } from "@/lib/portal";

/** The featured home's own photo over the hero drawing. If it cannot load, it steps aside and the drawing shows. */
export default function HeroPhoto({ unit }: { unit?: Pick<PortalUnit, "cover" | "title"> }) {
  const [state, setState] = useState<"loading" | "shown" | "failed">("loading");
  const img = useRef<HTMLImageElement>(null);
  const src = coverSrc(unit?.cover);

  // The page arrives with the <img> already in it, so the photo can finish (or fail) before this script runs and its load event is
  // missed. Look at the image itself once the script is running.
  useEffect(() => {
    const el = img.current;
    if (el?.complete) setState(el.naturalWidth > 0 ? "shown" : "failed");
  }, [src]);

  if (!src || state === "failed") return null;
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
        onLoad={() => setState("shown")}
        onError={() => setState("failed")}
        className={`absolute inset-0 h-full w-full object-cover transition-opacity duration-700 ${state === "shown" ? "opacity-100" : "opacity-0"}`}
      />
      {/* A warm wash at the edges keeps the floating cards readable and the photo in the page's palette. */}
      <div aria-hidden="true" className={`absolute inset-0 bg-gradient-to-t from-[#3b2a10]/45 via-transparent to-[#3b2a10]/15 transition-opacity duration-700 ${state === "shown" ? "opacity-100" : "opacity-0"}`} />
    </>
  );
}
