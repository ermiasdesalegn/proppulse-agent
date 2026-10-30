import { redirect } from "next/navigation";

// Placeholder until the public landing page is built: send the visitor on to the console (the middleware asks for a sign-in first).
export default function Home() {
  redirect("/console");
}
