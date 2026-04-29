import type { UIMessage } from "ai";

export type BackendChatResponse = {
	status: string;
	mode?: string;
	content?: string;
	artifact?: unknown;
	detail?: {
		message?: string;
		mode?: string;
	};
};

export type WorkbenchTools = {
	backendChat: {
		input: { message: string; model?: string };
		output: BackendChatResponse;
	};
};

export type WorkbenchMessage = UIMessage<unknown, never, WorkbenchTools>;
