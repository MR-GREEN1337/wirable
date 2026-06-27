import { RunView } from "./RunView";

export const metadata = {
  title: "Run",
};

export default async function RunPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ domain?: string }>;
}) {
  const { id } = await params;
  const { domain } = await searchParams;
  return <RunView runId={id} domain={domain ?? ""} />;
}
