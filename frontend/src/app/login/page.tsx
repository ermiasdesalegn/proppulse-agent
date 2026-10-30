import type { Metadata } from "next";
import LoginForm from "@/components/auth/LoginForm";
import { safeNext } from "@/lib/access";

export const metadata: Metadata = { title: "Sign in" };

export default function LoginPage({ searchParams }: { searchParams: { next?: string; notice?: string } }) {
  return <LoginForm next={safeNext(searchParams.next)} notice={searchParams.notice ?? null} />;
}
