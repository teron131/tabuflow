import type { SqlResult } from "@/lib/api";

function keyedRows(rows: Array<Record<string, unknown>>, columns: string[]) {
	const seen = new Map<string, number>();
	return rows.map((row) => {
		const baseKey = columns
			.map((column) => JSON.stringify(row[column] ?? null))
			.join("\u001f");
		const occurrence = seen.get(baseKey) ?? 0;
		seen.set(baseKey, occurrence + 1);
		return {
			key: occurrence ? `${baseKey}\u001f${occurrence}` : baseKey,
			row,
		};
	});
}

export function ResultTable({ result }: { result: SqlResult | null }) {
	if (!result) {
		return (
			<div className="empty-state">Run SQL to populate the result grid.</div>
		);
	}
	if (result.status === "error") {
		return (
			<div className="error-state">{result.message || result.error_type}</div>
		);
	}
	const columns = result.columns || [];
	const rows = result.rows || [];
	const keyedResultRows = keyedRows(rows, columns);
	if (!columns.length) {
		return (
			<div className="empty-state">{result.summary || "Query completed."}</div>
		);
	}
	const tableMinWidth = `max(100%, ${columns.length * 180}px)`;
	return (
		<div className="table-scroll">
			<table style={{ width: tableMinWidth }}>
				<thead>
					<tr>
						{columns.map((column) => (
							<th key={column}>{column}</th>
						))}
					</tr>
				</thead>
				<tbody>
					{keyedResultRows.map(({ key, row }) => (
						<tr key={key}>
							{columns.map((column) => (
								<td key={column}>{String(row[column] ?? "")}</td>
							))}
						</tr>
					))}
				</tbody>
			</table>
		</div>
	);
}
