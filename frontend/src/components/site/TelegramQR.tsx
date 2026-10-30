import QRCode from "qrcode";

/** The Telegram bot buyers can talk to instead of (or as well as) the website. NEXT_PUBLIC_TELEGRAM_BOT overrides the default name. */
export const TELEGRAM_BOT = (process.env.NEXT_PUBLIC_TELEGRAM_BOT || "prop_plus_bot").replace(/^@/, "");
export const TELEGRAM_URL = `https://t.me/${TELEGRAM_BOT}`;

/** A QR code that opens the bot, drawn on the server as SVG (no image request, sharp at any size), with a link for phone users. */
export default async function TelegramQR({ tone = "light", className = "" }: { tone?: "light" | "dark"; className?: string }) {
  const svg = await QRCode.toString(TELEGRAM_URL, { type: "svg", margin: 1, errorCorrectionLevel: "M", color: { dark: "#1c1917", light: "#ffffff" } });
  const muted = tone === "dark" ? "text-white/65" : "text-muted";
  return (
    <div className={`flex items-center gap-4 ${className}`}>
      <a href={TELEGRAM_URL} target="_blank" rel="noopener noreferrer" aria-label={`Open @${TELEGRAM_BOT} on Telegram`}
         className="block h-28 w-28 shrink-0 overflow-hidden rounded-xl bg-white p-1.5 shadow-sm ring-1 ring-black/5 [&>svg]:h-full [&>svg]:w-full"
         dangerouslySetInnerHTML={{ __html: svg }} />
      <div className="min-w-0">
        <p className="text-sm font-semibold">Prefer Telegram?</p>
        <p className={`mt-1 text-sm leading-snug ${muted}`}>Scan with your phone to talk to the same assistant there. No account needed, and voice notes work too.</p>
        <a href={TELEGRAM_URL} target="_blank" rel="noopener noreferrer" className="mt-2 inline-block text-sm font-medium text-[#2aabee] underline-offset-4 hover:underline">
          Open @{TELEGRAM_BOT} &#8599;
        </a>
      </div>
    </div>
  );
}
