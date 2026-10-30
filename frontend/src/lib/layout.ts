/** Reads "Living room 5.5 x 4.2" lines into rooms with a size. Lines that do not fit are returned so the broker can fix them. */
export function parseRoomLines(text: string): { rooms: { name: string; w: number; d: number }[]; bad: string[] } {
  const rooms: { name: string; w: number; d: number }[] = [];
  const bad: string[] = [];
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line) continue;
    const m = line.match(/^(.+?)[\s:,-]+(\d+(?:[.,]\d+)?)\s*(?:m)?\s*[x×*]\s*(\d+(?:[.,]\d+)?)\s*m?$/i);
    const w = m ? Number(m[2].replace(",", ".")) : NaN;
    const d = m ? Number(m[3].replace(",", ".")) : NaN;
    if (!m || !(w > 0.4) || !(d > 0.4) || w > 100 || d > 100) { bad.push(line); continue; }
    rooms.push({ name: m[1].trim().slice(0, 40), w, d });
  }
  return { rooms, bad };
}
