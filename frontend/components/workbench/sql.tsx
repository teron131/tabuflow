import { sqlKeywords } from "./constants";

export function clamp(value: number, min: number, max: number) {
	return Math.min(Math.max(value, min), max);
}

export function queryContentHeight(sqlText: string) {
	const lineCount = Math.max(sqlText.split("\n").length, 1);
	return clamp(34 + 20 + lineCount * 19, 122, 292);
}

export function renderHighlightedSql(sqlText: string) {
	const pattern =
		/(--.*$|'[^']*'|\b\d+(?:\.\d+)?\b|\b[A-Za-z_][\w$]*\b|[(),.;*>=<+-])/gm;
	const parts: React.ReactNode[] = [];
	let lastIndex = 0;
	let partIndex = 0;

	for (const match of sqlText.matchAll(pattern)) {
		if (match.index === undefined) continue;
		if (match.index > lastIndex) {
			parts.push(
				<span key={partIndex++}>{sqlText.slice(lastIndex, match.index)}</span>,
			);
		}
		const token = match[0];
		const upperToken = token.toUpperCase();
		const className = token.startsWith("--")
			? "sql-comment"
			: sqlKeywords.has(upperToken)
				? "sql-keyword"
				: token.startsWith("'")
					? "sql-string"
					: /^\d/.test(token)
						? "sql-number"
						: /^[(),.;*>=<+-]$/.test(token)
							? "sql-punctuation"
							: "sql-identifier";
		parts.push(
			<span className={className} key={partIndex++}>
				{token}
			</span>,
		);
		lastIndex = match.index + token.length;
	}
	if (lastIndex < sqlText.length) {
		parts.push(<span key={partIndex++}>{sqlText.slice(lastIndex)}</span>);
	}
	return parts;
}
