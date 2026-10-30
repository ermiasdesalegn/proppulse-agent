import type { Metadata } from "next";
import SignupForm from "@/components/auth/SignupForm";
import { safeNext } from "@/lib/access";

export const metadata: Metadata = { title: "Create your account" };

export default function SignupPage({ searchParams }: { searchParams: { next?: string } }) {
  return <SignupForm next={safeNext(searchParams.next)} />;
}
