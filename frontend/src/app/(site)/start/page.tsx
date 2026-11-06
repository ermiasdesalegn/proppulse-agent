import type { Metadata } from "next";
import StartForm from "@/components/auth/StartForm";

export const metadata: Metadata = { title: "Start your agency" };

export default function StartPage() {
  return <StartForm />;
}
