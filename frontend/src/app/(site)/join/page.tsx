import type { Metadata } from "next";
import JoinForm from "@/components/auth/JoinForm";

export const metadata: Metadata = { title: "Join your team" };

export default function JoinPage({ searchParams }: { searchParams: { token?: string } }) {
  const token = (searchParams.token ?? "").trim();
  return <JoinForm token={token.length >= 20 && token.length <= 200 ? token : null} />;
}
