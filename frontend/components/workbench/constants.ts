import type { RoundingSettings } from "./types";

export const defaultRounding: RoundingSettings = {
	enabled: true,
	digits: 2,
};

export const roundingDigits = {
	min: 0,
	max: 6,
	step: 1,
};

export const modelOptions = [
	"gpt-5.4-nano",
	"gpt-5.4-mini",
	"gpt-5.4",
	"gpt-5.5",
];

export const sqlKeywords = new Set([
	"SELECT",
	"FROM",
	"WHERE",
	"GROUP",
	"BY",
	"ORDER",
	"LIMIT",
	"JOIN",
	"AS",
	"AND",
	"OR",
	"COUNT",
	"SUM",
	"AVG",
	"MIN",
	"MAX",
	"DISTINCT",
	"DESC",
	"ASC",
]);
