// Explicit destination required; never uses CP_HOME/HOME or any live dashboard.
import path from "node:path";
import { createWorkspaceHome } from "./workspace-fixture.mjs";
import { createRefreshCoordinator, pulsePaths, startCanvasServer } from "../server.mjs";

if (!process.argv[2] || !path.isAbsolute(process.argv[2])) throw new Error("Provide an absolute synthetic preview directory.");
const home = process.argv[2];
createWorkspaceHome(home);
const paths = pulsePaths(home);
const refresh = createRefreshCoordinator({ modelPath: paths.model, launch: async () => { throw new Error("Synthetic preview: live refresh is disabled."); } });
const server = await startCanvasServer({ paths, refresh });
console.log(JSON.stringify({ url: server.url, home, synthetic: true }));
for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, async () => { await server.close(); process.exit(0); });
