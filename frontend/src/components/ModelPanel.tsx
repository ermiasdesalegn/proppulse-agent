"use client";

import { useEffect, useRef, useState } from "react";
import Select from "./Select";
import { Button, Card, ErrorNote, Pill } from "./ui";
import { api, errorMessage, post, put } from "@/lib/api";
import { fmtEtb } from "@/lib/format";
import { parseRoomLines } from "@/lib/layout";
import type { Property, UnitLayout } from "@/lib/types";
import { useApi } from "@/lib/useApi";

const PLAN_TYPES = ["image/png", "image/jpeg", "image/webp"];
const PLAN_MAX_BYTES = 5_000_000;
const EXAMPLE = "Living room 5.5 x 4.2\nKitchen 3.2 x 3\nMaster bedroom 4.2 x 3.6\nBedroom 2 3.6 x 3.2\nBath 2.4 x 1.8\nBalcony 4 x 1.6";

function readAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("The file could not be read."));
    reader.onload = () => resolve(String(reader.result).split(",")[1] ?? "");
    reader.readAsDataURL(file);
  });
}

const num = (v: string) => (v.trim() === "" || Number.isNaN(Number(v)) ? 0 : Number(v));

export default function ModelPanel() {
  const properties = useApi<Property[]>("properties");
  const [propertyId, setPropertyId] = useState("");
  const [layout, setLayout] = useState<UnitLayout | null>(null);
  const [saved, setSaved] = useState(false);       // the picture shown matches what is saved
  const [version, setVersion] = useState(0);       // changes after every save so the browser fetches the new picture
  const [lines, setLines] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const unit = (properties.data ?? []).find((p) => p.id === propertyId);

  useEffect(() => {
    setLayout(null); setSaved(false); setError(null); setNote(null); setLines("");
    if (!propertyId) return;
    let stale = false;
    api<{ layout: UnitLayout | null }>(`properties/${propertyId}/layout`)
      .then((r) => { if (!stale && r.layout) { setLayout(r.layout); setSaved(true); setVersion((v) => v + 1); } })
      .catch((e) => { if (!stale) setError(errorMessage(e)); });
    return () => { stale = true; };
  }, [propertyId]);

  async function run<T>(what: string, job: () => Promise<T>): Promise<T | undefined> {
    setBusy(what); setError(null); setNote(null);
    try { return await job(); } catch (e) { setError(errorMessage(e)); } finally { setBusy(null); }
    return undefined;
  }

  async function readPlan(file: File | undefined) {
    if (!file || !propertyId) return;
    if (!PLAN_TYPES.includes(file.type)) { setError("The floor plan must be a PNG, JPEG or WebP image."); return; }
    if (file.size > PLAN_MAX_BYTES) { setError("The floor plan is larger than 5 MB."); return; }
    const result = await run("read", async () => post<UnitLayout>(`properties/${propertyId}/layout/read-plan`, { image_base64: await readAsBase64(file), mime: file.type }));
    if (fileInput.current) fileInput.current.value = "";
    if (result) { setLayout(result); setSaved(false); setNote("This is what was read from the plan. Check the room names and sizes, fix anything wrong, then save."); }
  }

  async function placeRooms() {
    const { rooms, bad } = parseRoomLines(lines);
    if (bad.length) { setError(`These lines need a name and a size like "Bedroom 4 x 3.5": ${bad.slice(0, 3).join(" | ")}`); return; }
    if (!rooms.length) { setError("Type at least one room."); return; }
    const result = await run("pack", () => post<UnitLayout>("layouts/pack", { title: unit?.title ?? "", rooms }));
    if (result) { setLayout(result); setSaved(false); setNote("The rooms are placed side by side. Change x and y to move a room if the real plan is different."); }
  }

  async function save() {
    if (!layout || !propertyId) return;
    const result = await run("save", () => put<UnitLayout>(`properties/${propertyId}/layout`, { ...layout, title: layout.title || unit?.title || "" }));
    if (result) { setLayout(result); setSaved(true); setVersion((v) => v + 1); setNote("Saved. Buyers who ask for this unit's details are now sent the 3D view and the model file."); }
  }

  async function remove() {
    if (!propertyId) return;
    const result = await run("delete", () => api<{ deleted: boolean }>(`properties/${propertyId}/layout`, { method: "DELETE" }));
    if (result) { setLayout(null); setSaved(false); setNote("The 3D model was removed from this unit."); }
  }

  function setRoom(i: number, patch: Partial<UnitLayout["rooms"][number]>) {
    if (!layout) return;
    setLayout({ ...layout, rooms: layout.rooms.map((r, n) => (n === i ? { ...r, ...patch } : r)) });
    setSaved(false);
  }

  const cell = "w-full rounded-md border border-line bg-bg px-2 py-1 text-xs outline-none focus:border-accent";
  const available = (properties.data ?? []).filter((p) => p.status === "AVAILABLE");

  return (
    <Card title="3D model of a unit" action={<Pill tone="accent">Buyer view</Pill>}>
      <div className="space-y-4 p-4">
        <p className="text-sm text-muted">
          Give a unit its rooms and it gets a 3D model. Read them from a floor-plan picture, or type them in. When a buyer asks for that unit&apos;s details on Telegram, they are sent a picture of the model and the 3D file.
        </p>
        <div>
          <label htmlFor="model-unit" className="block text-sm font-medium">Unit</label>
          <div className="mt-1 max-w-xl"><Select id="model-unit" value={propertyId} onChange={setPropertyId} placeholder="Choose a unit..." options={available.map((p) => ({ value: p.id, label: p.title, hint: `${p.sub_city} - ${fmtEtb(Number(p.price_etb))}` }))} /></div>
        </div>

        {propertyId && (
          <div className="grid gap-4 lg:grid-cols-2">
            <div className="space-y-3 rounded-lg border border-line bg-raised p-3">
              <p className="text-xs font-semibold uppercase tracking-wide text-muted">1. Give it the rooms</p>
              <div>
                <label htmlFor="model-plan" className="block text-sm font-medium">Floor plan picture</label>
                <input id="model-plan" ref={fileInput} type="file" accept={PLAN_TYPES.join(",")} disabled={busy !== null} onChange={(e) => void readPlan(e.target.files?.[0])} className="mt-1 text-xs" />
                <p className="mt-1 text-xs text-muted">{busy === "read" ? "Reading the plan..." : "PNG, JPEG or WebP, up to 5 MB. The rooms it finds are shown for you to check."}</p>
              </div>
              <div>
                <label htmlFor="model-rooms" className="block text-sm font-medium">Or type the rooms, one per line</label>
                <textarea id="model-rooms" value={lines} onChange={(e) => setLines(e.target.value)} rows={5} spellCheck={false} placeholder={EXAMPLE}
                  className="mt-1 w-full rounded-md border border-line bg-bg px-3 py-2 text-xs outline-none focus:border-accent" />
                <div className="mt-2"><Button disabled={busy !== null || !lines.trim()} onClick={() => void placeRooms()}>{busy === "pack" ? "Placing..." : "Place the rooms"}</Button></div>
              </div>
            </div>

            <div className="space-y-3 rounded-lg border border-line bg-raised p-3">
              <p className="text-xs font-semibold uppercase tracking-wide text-muted">2. Check, save and see it</p>
              {layout && saved ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={`/api/proxy/properties/${propertyId}/preview.png?v=${version}`} alt={`3D view of ${layout.title || unit?.title}`} className="w-full rounded-lg border border-line" />
              ) : (
                <p className="rounded-lg border border-dashed border-line px-3 py-10 text-center text-sm text-muted">{layout ? "Save to see the 3D picture." : "No rooms yet."}</p>
              )}
              {layout && (
                <div className="flex flex-wrap items-center gap-2">
                  <Button variant="primary" disabled={busy !== null || saved} onClick={() => void save()}>{busy === "save" ? "Saving..." : saved ? "Saved" : "Save and build the 3D view"}</Button>
                  {saved && <a href={`/api/proxy/properties/${propertyId}/model.glb`} download className="rounded-lg border border-white/10 px-3.5 py-1.5 text-sm font-medium text-zinc-300 hover:bg-white/5 hover:text-white">Download the 3D file (.glb)</a>}
                  {saved && <Button variant="danger" disabled={busy !== null} onClick={() => void remove()}>Remove</Button>}
                </div>
              )}
            </div>
          </div>
        )}

        {error && <ErrorNote>{error}</ErrorNote>}
        {note && <p role="status" className="rounded-md border border-good/30 bg-good/10 px-3 py-2 text-sm text-good">{note}</p>}

        {layout && (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[34rem] text-left text-xs">
              <caption className="sr-only">Rooms of the unit, in metres</caption>
              <thead className="text-muted"><tr><th className="py-1 pr-2 font-medium">Room</th><th className="px-2 font-medium">From left (m)</th><th className="px-2 font-medium">From top (m)</th><th className="px-2 font-medium">Width (m)</th><th className="px-2 font-medium">Depth (m)</th><th /></tr></thead>
              <tbody>
                {layout.rooms.map((r, i) => (
                  <tr key={i}>
                    <td className="py-1 pr-2"><input aria-label={`Room ${i + 1} name`} className={cell} value={r.name} maxLength={40} onChange={(e) => setRoom(i, { name: e.target.value })} /></td>
                    {(["x", "y", "w", "d"] as const).map((k) => (
                      <td key={`${i}-${k}-${r[k]}`} className="px-2"><input aria-label={`${r.name} ${k}`} className={cell} inputMode="decimal" defaultValue={r[k]} onBlur={(e) => setRoom(i, { [k]: num(e.target.value) })} /></td>
                    ))}
                    <td className="pl-2"><button type="button" aria-label={`Remove ${r.name}`} className="text-muted hover:text-bad" onClick={() => { setLayout({ ...layout, rooms: layout.rooms.filter((_, n) => n !== i) }); setSaved(false); }}>Remove</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-muted">
              <Button variant="ghost" onClick={() => { setLayout({ ...layout, rooms: [...layout.rooms, { name: "New room", x: 0, y: 0, w: 3, d: 3 }] }); setSaved(false); }}>Add a room</Button>
              <label className="flex items-center gap-2">Wall height (m)
                <input className={`${cell} w-16`} inputMode="decimal" defaultValue={layout.wall_height_m} onBlur={(e) => { setLayout({ ...layout, wall_height_m: Math.min(6, Math.max(2, num(e.target.value) || 2.8)) }); setSaved(false); }} />
              </label>
            </div>
          </div>
        )}
      </div>
    </Card>
  );
}
