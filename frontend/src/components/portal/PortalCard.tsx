import ListingCard from "@/components/site/ListingCard";
import type { PortalUnit } from "@/lib/portal";
import { SaveButton } from "./PortalControls";

/** A listing card with a heart in its corner. The heart sits beside the link, not inside it, so tapping it never opens the home. */
export default function PortalCard({ unit, saved }: { unit: PortalUnit; saved: boolean }) {
  return (
    <div className="relative">
      <ListingCard unit={unit} />
      <div className="absolute right-3 top-3"><SaveButton id={unit.id} saved={saved} /></div>
    </div>
  );
}
