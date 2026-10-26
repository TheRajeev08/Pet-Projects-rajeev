import { createCanvas, CanvasError, joinSession } from "@github/copilot-sdk/extension";
import { hashFor, resolveAccount, summarize, VIEWS } from "./model.mjs";
import {
    checkActionInput, createRefreshCoordinator, pulsePaths, readBuildStatus, readModel, readWorkflowId, setActionState,
    startCanvasServer,
} from "./server.mjs";

const paths = pulsePaths();
const servers = new Map();
let session;

async function currentModel() {
    try {
        return (await readModel(paths.model)).model;
    } catch {
        return null;
    }
}

const refresh = createRefreshCoordinator({
    modelPath: paths.model,
    launch: async () => {
        if (!session) throw new Error("The Copilot session is not connected yet.");
        const workflowId = await readWorkflowId(paths, await currentModel());
        if (!workflowId) throw new Error(`No refresh automation is configured. Add "workflow_id" to ${paths.config}.`);
        await session.send({
            prompt: `I clicked Refresh in my Customer Pulse canvas. Run the existing refresh automation now with the run_workflow tool and workflow_id "${workflowId}". Report whether the run started and its session link here. Do not run a different workflow, reimplement the queries, commit customer data, or claim the refresh is complete before the snapshot is rebuilt. The canvas detects the new snapshot itself.`,
        });
    },
});

/** Deep link into the canvas: a tab, optionally with one account's drawer open (or a search when the name is ambiguous). */
function locationFor(input, model) {
    const view = VIEWS.includes(input?.view) ? input.view : "overview";
    const account = typeof input?.account === "string" ? input.account.trim() : "";
    if (!account) return hashFor({ view });
    const id = model ? resolveAccount(model, account) : null;
    return id ? hashFor({ view, account: id }) : hashFor({ view, query: account });
}

function canvasError(error, fallback) {
    return error instanceof CanvasError ? error : new CanvasError(error.code || fallback, error.message);
}

session = await joinSession({
    canvases: [
        createCanvas({
            id: "customer-pulse",
            displayName: "Customer Pulse",
            description: "Morning view of seat changes, risks and opportunities across GHE, GHAS and Copilot for your Salesforce-owned accounts.",
            inputSchema: {
                type: "object",
                properties: {
                    view: { type: "string", enum: VIEWS, description: "Tab to show. Defaults to overview." },
                    account: { type: "string", description: "Salesforce account id or name whose detail panel should open." },
                },
                additionalProperties: false,
            },
            actions: [
                {
                    name: "get_summary",
                    description: "Read today's portfolio KPIs, open actions, recent seat changes and data freshness from the local snapshot.",
                    handler: async () => {
                        try {
                            const { model, revision } = await readModel(paths.model);
                            refresh.observe(revision);
                            return { ...summarize(model), last_build: await readBuildStatus(paths.status), refresh: refresh.status() };
                        } catch (error) {
                            throw canvasError(error, "dashboard_unavailable");
                        }
                    },
                },
                {
                    name: "request_refresh",
                    description: "Ask Copilot to run the configured Customer Pulse refresh automation. Never starts on its own.",
                    handler: async () => {
                        try {
                            return await refresh.request();
                        } catch (error) {
                            throw canvasError(error, "refresh_failed");
                        }
                    },
                },
                {
                    name: "set_action_state",
                    description: "Mark an action (id from get_summary) done or dismissed, or pass state null to reopen it.",
                    inputSchema: {
                        type: "object",
                        properties: {
                            id: { type: "string" },
                            state: { type: ["string", "null"], enum: ["done", "dismissed", null] },
                        },
                        required: ["id", "state"],
                        additionalProperties: false,
                    },
                    handler: async ({ input }) => {
                        try {
                            const { id, state } = checkActionInput(input);
                            return await setActionState({ paths, id, state });
                        } catch (error) {
                            throw canvasError(error, "action_state_failed");
                        }
                    },
                },
            ],
            open: async ({ instanceId, input }) => {
                let entry = servers.get(instanceId);
                if (!entry) {
                    entry = startCanvasServer({ paths, refresh });
                    servers.set(instanceId, entry);
                    entry.catch(() => servers.delete(instanceId));
                }
                const server = await entry;
                const model = input?.account ? await currentModel() : null;
                return { title: "Customer Pulse", status: "My Salesforce accounts", url: `${server.url}${locationFor(input, model)}` };
            },
            onClose: async ({ instanceId }) => {
                const entry = servers.get(instanceId);
                if (!entry) return;
                servers.delete(instanceId);
                await (await entry).close();
            },
        }),
    ],
});
