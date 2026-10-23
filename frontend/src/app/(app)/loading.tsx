import { Skeleton } from "@/components/ui";

/** Shown inside the content area while the next page loads. The sidebar and the top bar stay where they are and stay clickable. */
export default function Loading() {
  return (
    <div className="space-y-6" role="status" aria-label="Loading the page">
      <div className="space-y-2">
        <Skeleton className="h-8 w-56" />
        <Skeleton className="h-4 w-full max-w-xl" />
      </div>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {[0, 1, 2].map((i) => (
          <div key={i} className="card rounded-xl border p-5">
            <Skeleton className="h-4 w-24" />
            <Skeleton className="mt-4 h-8 w-32" />
            <Skeleton className="mt-3 h-3 w-40" />
          </div>
        ))}
      </div>
      <div className="card rounded-xl border p-5">
        <Skeleton className="h-5 w-40" />
        <div className="mt-5 space-y-3"><Skeleton className="h-4 w-full" /><Skeleton className="h-4 w-5/6" /><Skeleton className="h-4 w-2/3" /></div>
      </div>
    </div>
  );
}
