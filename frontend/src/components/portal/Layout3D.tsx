"use client";

import { createElement, useEffect, useState } from "react";

/**
 * The broker's 3D model of a home, to turn and zoom with a finger or the mouse. The viewer (Google's <model-viewer>) is loaded only on
 * this page and only in the browser; until it is ready, and wherever it cannot run, the drawn picture of the same model shows instead.
 */
export default function Layout3D({ id, title }: { id: string; title: string }) {
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);
  const model = `/api/me/listings/${id}/model.glb`;
  const picture = `/api/me/listings/${id}/preview.png`;

  useEffect(() => {
    let alive = true;
    import("@google/model-viewer")
      .then(() => alive && setReady(true))
      .catch(() => alive && setFailed(true));
    return () => { alive = false; };
  }, []);

  return (
    <div>
      <div className="relative overflow-hidden rounded-2xl border border-line bg-gradient-to-b from-white to-raised/60">
        {ready && !failed ? (
          createElement("model-viewer", {
            src: model,
            poster: picture,
            alt: `3D model of ${title}`,
            "camera-controls": "",
            "auto-rotate": "",
            "auto-rotate-delay": "1500",
            "rotation-per-second": "18deg",
            "interaction-prompt": "auto",
            "shadow-intensity": "0.8",
            "camera-orbit": "35deg 60deg auto",
            "touch-action": "pan-y",
            style: { width: "100%", height: "26rem", background: "transparent" },
          })
        ) : (
          // eslint-disable-next-line @next/next/no-img-element -- drawn on demand by the backend and sent through this site with the buyer's sign-in
          <img src={picture} alt={`3D layout of ${title}`} loading="lazy" className="mx-auto max-h-[26rem] w-auto p-3" />
        )}
      </div>
      <p className="mt-2 flex flex-wrap items-center justify-between gap-2 text-sm text-muted">
        <span>{ready && !failed ? "Drag to turn the model, pinch or scroll to zoom." : "Built by our broker from the floor plan."}</span>
        <a href={model} download className="font-medium text-accent underline-offset-4 hover:underline">Download 3D model (.glb)</a>
      </p>
    </div>
  );
}
