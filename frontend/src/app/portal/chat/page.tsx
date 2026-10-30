import type { Metadata } from "next";
import ChatClient from "@/components/portal/ChatClient";
import type { PortalUnitDetail } from "@/lib/portal";
import { portalGet } from "@/lib/portal-server";

export const metadata: Metadata = { title: "Concierge" };

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export default async function ChatPage({ searchParams }: { searchParams: { about?: string } }) {
  // ?about=<home> comes from the "Ask the assistant" button on a home. The title is read here so the page can say what is being asked about.
  const id = typeof searchParams.about === "string" && UUID.test(searchParams.about) ? searchParams.about : null;
  const unit = id ? (await portalGet<PortalUnitDetail>(`/listings/${id}`)).data : null;
  const about = unit && unit.status === "AVAILABLE" ? { id: unit.id, title: unit.title } : null;

  return (
    <div className="space-y-5">
      <div>
        <h1 className="display text-4xl font-medium">Concierge</h1>
        <p className="mt-1 text-muted">Ask about any home, compare options or book a visit. Our broker can step in whenever you want a person.</p>
      </div>
      <ChatClient about={about} />
    </div>
  );
}
