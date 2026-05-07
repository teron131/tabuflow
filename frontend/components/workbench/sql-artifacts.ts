import type { SqlArtifact } from "@/lib/api";

export function isSqlArtifactView(sqlArtifact: SqlArtifact) {
	return sqlArtifact.type === "view" || sqlArtifact.kind.includes("view");
}
