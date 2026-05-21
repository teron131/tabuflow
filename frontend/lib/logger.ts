type LogLevel = "debug" | "info" | "warn" | "error";

const LOG_LEVEL_ORDER: Record<LogLevel, number> = {
	debug: 10,
	info: 20,
	warn: 30,
	error: 40,
};

function parseLogLevel(value: string | undefined): LogLevel | null {
	const normalizedValue = value?.trim().toLowerCase();
	if (
		normalizedValue === "debug" ||
		normalizedValue === "info" ||
		normalizedValue === "warn" ||
		normalizedValue === "error"
	) {
		return normalizedValue;
	}
	return null;
}

const configuredLogLevel =
	parseLogLevel(process.env.NEXT_PUBLIC_TABUFLOW_LOG_LEVEL) ||
	parseLogLevel(process.env.TABUFLOW_LOG_LEVEL) ||
	(process.env.NODE_ENV === "production" ? "warn" : "info");

function writeLog(
	level: LogLevel,
	scope: string,
	message: string,
	context?: unknown,
) {
	if (LOG_LEVEL_ORDER[level] < LOG_LEVEL_ORDER[configuredLogLevel]) {
		return;
	}

	const prefix = `[tabuflow:${scope}] ${message}`;
	if (context === undefined) {
		console[level](prefix);
		return;
	}
	console[level](
		prefix,
		context instanceof Error
			? {
					name: context.name,
					message: context.message,
					stack: context.stack,
				}
			: context,
	);
}

export function createLogger(scope: string) {
	return {
		debug: (message: string, context?: unknown) =>
			writeLog("debug", scope, message, context),
		info: (message: string, context?: unknown) =>
			writeLog("info", scope, message, context),
		warn: (message: string, context?: unknown) =>
			writeLog("warn", scope, message, context),
		error: (message: string, context?: unknown) =>
			writeLog("error", scope, message, context),
	};
}
