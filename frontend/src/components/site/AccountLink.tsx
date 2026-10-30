"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { buttonClass } from "./ui";

/** "Sign in" for a visitor, "My portal" for someone already signed in. /login passes a signed-in person straight on to their own home. */
export default function AccountLink() {
  const [signedIn, setSignedIn] = useState(false);
  // Supabase keeps its sign-in in a cookie named sb-<project>-auth-token; reading its name needs none of the sign-in code.
  useEffect(() => setSignedIn(/(^|; )sb-[^=]*-auth-token/.test(document.cookie)), []);
  return (
    <>
      {!signedIn && <Link href="/signup" className={`${buttonClass("ghost")} hidden sm:inline-flex`}>Create account</Link>}
      <Link href="/login" className={buttonClass("primary")}>{signedIn ? "My portal" : "Sign in"}</Link>
    </>
  );
}
