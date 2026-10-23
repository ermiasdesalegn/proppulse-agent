"use client";

import { useEffect, useRef } from "react";

/** The page background: gradient mesh, drifting orbs and a soft spotlight that follows the pointer. Purely decorative. */
export default function Backdrop() {
  const spot = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let frame = 0;
    const move = (e: PointerEvent) => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        spot.current?.style.setProperty("--mx", `${e.clientX}px`);
        spot.current?.style.setProperty("--my", `${e.clientY}px`);
      });
    };
    window.addEventListener("pointermove", move, { passive: true });
    return () => { window.removeEventListener("pointermove", move); cancelAnimationFrame(frame); };
  }, []);
  return (
    <div className="backdrop" aria-hidden="true">
      <div className="orb a" /><div className="orb b" /><div className="orb c" />
      <div ref={spot} className="spotlight" />
    </div>
  );
}
