import type { Target } from "@/lib/api";

export function isTargetView(target: Target) {
	return target.type === "view" || target.kind.includes("view");
}
