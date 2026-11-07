import type { Metadata } from "next";
import AlertsSwitch from "@/components/portal/AlertsSwitch";
import Problem from "@/components/portal/Problem";
import { portalGet } from "@/lib/portal-server";

export const metadata: Metadata = { title: "Alerts" };

export default async function AlertsPage() {
  const { status, data } = await portalGet<{ enabled: boolean }>("/alerts");
  if (!data) return <Problem status={status} />;
  return (
    <div className="space-y-6">
      <div>
        <h1 className="display text-4xl font-medium">Alerts</h1>
        <p className="mt-1 text-muted">Messages when a home that suits your search drops in price or a new one is listed.</p>
      </div>
      <AlertsSwitch enabled={data.enabled} />
    </div>
  );
}
