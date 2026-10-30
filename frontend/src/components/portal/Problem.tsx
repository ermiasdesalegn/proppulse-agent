import { buttonClass } from "@/components/site/ui";
import { SignOutButton } from "./PortalControls";

/** Shown instead of a page when its data could not be loaded. Says what to do, not what broke. */
export default function Problem({ status }: { status: number }) {
  const signIn = status === 401 || status === 403;
  return (
    <div role="alert" className="mx-auto max-w-lg rounded-2xl border border-line bg-white px-8 py-12 text-center">
      <h1 className="display text-2xl font-medium">{signIn ? "Please sign in again" : "We could not load this just now"}</h1>
      <p className="mt-2 text-muted">
        {signIn ? "We could not confirm your sign-in. Signing in again takes a few seconds." : "The listings service did not answer. Please try again in a moment."}
      </p>
      <div className="mt-6">
        {signIn ? <SignOutButton className={buttonClass("primary")}>Sign in again</SignOutButton> : <a href="" className={buttonClass("primary")}>Try again</a>}
      </div>
    </div>
  );
}
